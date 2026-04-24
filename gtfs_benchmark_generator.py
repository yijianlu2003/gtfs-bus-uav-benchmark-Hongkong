"""
GTFS-derived Benchmark Instance Generator for Bus-UAV Collaborative Delivery.

Generates benchmark instances from Hong Kong GTFS data (real bus corridors,
real headways, real stop coordinates). Follows the methodology of:
  Osorio and Ouyang (2025), "To ride or to fly: Optimal freight-on-transit
  operations using drones", Transportation Research Part E.

This generator is fully self-contained and does NOT depend on any external
solver module. It outputs instances as plain Python dicts and CSV files.

Usage:
    # Generate all default instances (4 corridors x 3 scenarios x 3 seeds)
    python gtfs_benchmark_generator.py

    # Custom output directory and scenarios
    python gtfs_benchmark_generator.py --output-dir ./my_instances --scenarios small medium

    # Custom seeds and bus speed
    python gtfs_benchmark_generator.py --seeds 42,100,200 --bus-speed 20.0

Output:
    - Per-instance summary CSVs in <output-dir>/<corridor_id>/
    - corridor_summary.csv with corridor metadata
    - instance_registry.csv as the master instance list
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import random
from collections import defaultdict
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Dict, List, Tuple, Optional

# ---------------------------------------------------------------------------
# Physical constants (DJI FlyCart 30, dual-battery configuration)
# ---------------------------------------------------------------------------
UAV_CRUISE_SPEED_KMH = 54.0       # 15 m/s representative cruise speed
UAV_MAX_PAYLOAD_KG = 30.0          # Dual-battery payload
DUAL_BATTERY_ENERGY_WH = 2.0 * 1984.4  # 2 x DB2000
EMPTY_RANGE_KM = 28.0              # Official empty-load range
FULL_LOAD_RANGE_KM = 16.0          # Official 30-kg full-load range
FIXED_ENERGY_PER_KM = DUAL_BATTERY_ENERGY_WH / EMPTY_RANGE_KM       # ~141.743 Wh/km
LOAD_ENERGY_PER_KM_PER_KG = (
    DUAL_BATTERY_ENERGY_WH / FULL_LOAD_RANGE_KM - FIXED_ENERGY_PER_KM
) / UAV_MAX_PAYLOAD_KG  # ~3.544 Wh/km/kg
SERVICE_TIME_MIN = 1.5
SERVICE_ENERGY_WH = 12.0
TURNOVER_TIME_MIN = 3.0
MAX_PARCEL_WEIGHT_KG = 2.0
MIN_PARCEL_WEIGHT_KG = 0.3
LAMBDAS = (1.0, 0.01, 0.25, 0.8)

# ---------------------------------------------------------------------------
# Scenario presets
# ---------------------------------------------------------------------------
SCENARIO_PRESETS = {
    "small":  dict(n_orders=6,  n_uavs=2, max_sorties=3),
    "medium": dict(n_orders=10, n_uavs=3, max_sorties=4),
    "large":  dict(n_orders=16, n_uavs=4, max_sorties=5),
}

# ---------------------------------------------------------------------------
# Corridor definitions (extracted from HK GTFS)
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class CorridorStop:
    station_id: int
    route_seq: int
    stop_id: str
    name_en: str
    lat: float
    lon: float
    cumulative_km: float


@dataclass(frozen=True)
class CorridorDef:
    corridor_id: str
    route_name: str
    agency: str
    direction: str
    stops: Tuple[CorridorStop, ...]
    am_peak_headway_min: float
    corridor_length_km: float
    planning_horizon_min: float
    gtfs_source: str


def load_corridor_from_csv(csv_path: Path) -> CorridorDef:
    """Load a corridor definition from a hk_*_stops.csv file."""
    rows = []
    with open(csv_path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append(CorridorStop(
                station_id=int(row["station_id"]),
                route_seq=int(row.get("route_seq", 0)),
                stop_id=row["stop_id"],
                name_en=row["name_en"],
                lat=float(row["lat"]),
                lon=float(row["lon"]),
                cumulative_km=float(row["cumulative_km"]),
            ))

    # Infer corridor metadata from filename
    stem = csv_path.stem  # e.g., hk_14d_stops
    parts = stem.split("_")
    route_code = parts[1].upper()  # 14D, 235M, etc.

    # Route-specific metadata (from GTFS analysis)
    corridor_meta = {
        "14D": dict(route_name="KMB 14D", agency="KMB", direction="Yau Tong -> Choi Hung",
                     am_peak_headway_min=5.0, planning_horizon_min=90.0,
                     gtfs_source="https://static.data.gov.hk/td/pt-headway-en/gtfs.zip"),
        "235M": dict(route_name="KMB 235M", agency="KMB", direction="On Yam Estate -> Kwai Fong Station",
                      am_peak_headway_min=8.0, planning_horizon_min=90.0,
                      gtfs_source="https://static.data.gov.hk/td/pt-headway-en/gtfs.zip"),
        "31M": dict(route_name="KMB 31M", agency="KMB", direction="Shek Lei -> Kwai Fong Station",
                     am_peak_headway_min=10.0, planning_horizon_min=90.0,
                     gtfs_source="https://static.data.gov.hk/td/pt-headway-en/gtfs.zip"),
        "42M": dict(route_name="KMB 42M", agency="KMB", direction="Tsuen Wan Discovery Park -> Tsing Yi Cheung Wang",
                     am_peak_headway_min=7.0, planning_horizon_min=90.0,
                     gtfs_source="https://static.data.gov.hk/td/pt-headway-en/gtfs.zip"),
    }

    meta = corridor_meta.get(route_code, dict(
        route_name=route_code, agency="KMB", direction="Unknown",
        am_peak_headway_min=10.0, planning_horizon_min=90.0,
        gtfs_source="https://static.data.gov.hk/td/pt-headway-en/gtfs.zip",
    ))

    corridor_len = rows[-1].cumulative_km if rows else 0.0

    return CorridorDef(
        corridor_id=f"HK-{route_code}",
        route_name=meta["route_name"],
        agency=meta["agency"],
        direction=meta["direction"],
        stops=tuple(rows),
        am_peak_headway_min=meta["am_peak_headway_min"],
        corridor_length_km=corridor_len,
        planning_horizon_min=meta["planning_horizon_min"],
        gtfs_source=meta["gtfs_source"],
    )


# ---------------------------------------------------------------------------
# Haversine and bus trip generation
# ---------------------------------------------------------------------------
def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Compute haversine distance between two lat/lon points in km."""
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2
         + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2))
         * math.sin(dlon / 2) ** 2)
    return R * 2 * math.asin(a ** 0.5)


def generate_bus_trips_from_corridor(
    corridor: CorridorDef,
    bus_speed_kmh: float = 24.0,
) -> List[Dict]:
    """Generate bus trips along the corridor at the GTFS-specified headway.

    Trips are generated in both directions (hub1->hub2 and hub2->hub1).
    GTFS trips at different departure times are NOT dominated -- they serve
    different time slots and are all retained.
    """
    stops = corridor.stops
    headway = corridor.am_peak_headway_min
    horizon = corridor.planning_horizon_min
    corridor_len = corridor.corridor_length_km

    if corridor_len < 0.01 or headway <= 0:
        return []

    # Bus travel time across corridor
    bus_travel_time = (corridor_len / bus_speed_kmh) * 60  # minutes

    trips = []
    for direction in [1, 2]:
        t = 0.0
        trip_id = 0
        while t + bus_travel_time <= horizon + headway:
            trip_id += 1
            if direction == 1:
                station_departures = {}
                for s in stops:
                    frac = s.cumulative_km / corridor_len
                    dep_time = t + frac * bus_travel_time
                    station_departures[s.station_id] = round(dep_time, 4)
                dest_hub = 2
            else:
                station_departures = {}
                for s in stops:
                    frac = 1.0 - s.cumulative_km / corridor_len
                    dep_time = t + frac * bus_travel_time
                    station_departures[s.station_id] = round(dep_time, 4)
                dest_hub = 1

            arrival_time = t + bus_travel_time
            trips.append({
                "trip_id": len(trips),
                "direction": direction,
                "start_time": round(t, 4),
                "arrival_time": round(arrival_time, 4),
                "dest_hub": dest_hub,
                "station_departures": {int(k): v for k, v in station_departures.items()},
            })
            t += headway

    return trips


# ---------------------------------------------------------------------------
# Instance generator
# ---------------------------------------------------------------------------
def generate_gtfs_instance(
    corridor: CorridorDef,
    scenario: str,
    seed: int = 42,
    bus_speed_kmh: float = 24.0,
) -> Dict:
    """Generate a single Bus-UAV instance from a GTFS corridor.

    Returns a dict containing all instance parameters. This dict can be:
    - Directly used in your own solver code
    - Exported to CSV via export_instance_summary()
    - Serialized to JSON via instance_to_json()
    """
    rng = random.Random(seed)
    preset = SCENARIO_PRESETS[scenario]

    stops = corridor.stops
    n_stations = len(stops)
    n_orders = preset["n_orders"]
    n_uavs = preset["n_uavs"]
    max_sorties = preset["max_sorties"]
    headway = corridor.am_peak_headway_min
    corridor_len = corridor.corridor_length_km
    horizon = corridor.planning_horizon_min

    stations = list(range(1, n_stations + 1))
    uavs = list(range(1, n_uavs + 1))
    sorties = list(range(1, max_sorties + 1))

    # Station positions (from GTFS cumulative_km)
    HUB_NODE = {1: "H1", 2: "H2"}
    position = {HUB_NODE[1]: 0.0, HUB_NODE[2]: corridor_len}
    for s in stops:
        position[s.station_id] = s.cumulative_km

    # Generate orders
    orders = list(range(1, n_orders + 1))
    demand_weight = {}
    release_time = {}
    feasible_lockers = {}

    for k in orders:
        demand_weight[k] = round(rng.uniform(MIN_PARCEL_WEIGHT_KG, MAX_PARCEL_WEIGHT_KG), 2)
        release_time[k] = round(rng.uniform(0, 0.6 * horizon), 2)
        ranked = sorted(stations, key=lambda j: abs(position[j] - rng.uniform(0, corridor_len)))
        n_lockers = min(rng.choice([2, 3]), n_stations)
        feasible_lockers[k] = sorted(ranked[:n_lockers])

    # Locker capacity
    locker_capacity = {}
    total_weight = sum(demand_weight.values())
    avg_weight = total_weight / n_stations
    for j in stations:
        local = sum(demand_weight[k] for k in orders if j in feasible_lockers.get(k, []))
        locker_capacity[j] = round(max(5.0, 0.5 * local + 0.5 * avg_weight), 2)

    # UAV parameters
    payload_capacity = {u: UAV_MAX_PAYLOAD_KG for u in uavs}
    battery_capacity = {u: DUAL_BATTERY_ENERGY_WH for u in uavs}
    service_time = {j: SERVICE_TIME_MIN for j in stations}
    service_energy = {j: SERVICE_ENERGY_WH for j in stations}
    turnover_time = {1: TURNOVER_TIME_MIN, 2: TURNOVER_TIME_MIN}

    # Arc parameters
    uav_speed_kpm = UAV_CRUISE_SPEED_KMH / 60.0  # km/min

    HUBS = (1, 2)

    # Build monotone arcs
    arcs = {1: [], 2: []}
    for h in HUBS:
        for i in stations:
            for j in stations:
                if h == 1 and i <= j:
                    arcs[h].append((i, j))
                elif h == 2 and i >= j:
                    arcs[h].append((i, j))
        for j in stations:
            arcs[h].append((HUB_NODE[h], j))

    arc_time = {}
    arc_alpha = {}
    arc_beta = {}
    for h in HUBS:
        for i, j in arcs[h]:
            if i in position and j in position:
                dist = abs(position[j] - position[i])
            elif i == HUB_NODE[h] and j in position:
                dist = position[j] if h == 1 else (corridor_len - position[j])
            else:
                continue
            arc_time[(h, i, j)] = round(dist / uav_speed_kpm, 4)
            arc_alpha[(h, i, j)] = round(FIXED_ENERGY_PER_KM * dist, 4)
            arc_beta[(h, i, j)] = round(LOAD_ENERGY_PER_KM_PER_KG * dist, 4)

    return_time = {}
    return_energy = {}
    for j in stations:
        for h in HUBS:
            dist = abs(position[j] - position[HUB_NODE[h]])
            return_time[(j, h)] = round(dist / uav_speed_kpm, 4)
            return_energy[(j, h)] = round((FIXED_ENERGY_PER_KM + 0.6) * dist, 4)

    # Generate bus trips
    trips = generate_bus_trips_from_corridor(corridor, bus_speed_kmh)

    # Build station_to_trips mapping
    station_to_trips = defaultdict(list)
    for trip in trips:
        for j, dep_time in trip["station_departures"].items():
            station_to_trips[j].append(trip["trip_id"])

    big_m = horizon + 60.0
    initial_hub = {u: 1 if u % 2 == 1 else 2 for u in uavs}

    return {
        "instance_name": f"{corridor.corridor_id}_{scenario}_seed{seed}",
        "corridor_id": corridor.corridor_id,
        "route_name": corridor.route_name,
        "agency": corridor.agency,
        "direction": corridor.direction,
        "scenario": scenario,
        "seed": seed,
        "gtfs_source": corridor.gtfs_source,
        "am_peak_headway_min": corridor.am_peak_headway_min,
        "corridor_length_km": corridor.corridor_length_km,
        "planning_horizon_min": horizon,
        "n_stations": n_stations,
        "n_orders": n_orders,
        "n_uavs": n_uavs,
        "max_sorties": max_sorties,
        "n_trips": len(trips),
        "bus_speed_kmh": bus_speed_kmh,
        "stations": stations,
        "orders": orders,
        "uavs": uavs,
        "sorties": sorties,
        "position": {str(k): v for k, v in position.items()},
        "demand_weight": demand_weight,
        "release_time": release_time,
        "feasible_lockers": {str(k): v for k, v in feasible_lockers.items()},
        "locker_capacity": locker_capacity,
        "payload_capacity": payload_capacity,
        "battery_capacity": battery_capacity,
        "service_time": service_time,
        "arc_time": {f"({h},{i},{j})": v for (h, i, j), v in arc_time.items()},
        "arc_alpha": {f"({h},{i},{j})": v for (h, i, j), v in arc_alpha.items()},
        "arc_beta": {f"({h},{i},{j})": v for (h, i, j), v in arc_beta.items()},
        "return_time": {f"({j},{h})": v for (j, h), v in return_time.items()},
        "return_energy": {f"({j},{h})": v for (j, h), v in return_energy.items()},
        "service_energy": service_energy,
        "turnover_time": turnover_time,
        "trips": trips,
        "station_to_trips": {str(k): v for k, v in station_to_trips.items()},
        "big_m": big_m,
        "lambdas": list(LAMBDAS),
        "initial_hub": initial_hub,
    }


def instance_to_json(inst_dict: Dict, output_path: Path) -> Path:
    """Serialize an instance dict to a JSON file."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(inst_dict, f, indent=2, ensure_ascii=False)
    return output_path


def load_instance_from_json(json_path: Path) -> Dict:
    """Load an instance dict from a JSON file."""
    with open(json_path, "r", encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# Summary export (CSV)
# ---------------------------------------------------------------------------
def export_instance_summary(inst_dict: Dict, output_dir: Path) -> Path:
    """Export a human-readable summary CSV for one instance."""
    output_dir.mkdir(parents=True, exist_ok=True)
    name = inst_dict["instance_name"]
    path = output_dir / f"{name}_summary.csv"

    fieldnames = ["type", "id", "position_km", "locker_capacity", "service_time",
                  "service_energy", "demand_weight", "release_time", "feasible_lockers"]
    rows = []
    for j in inst_dict["stations"]:
        pos = inst_dict["position"][str(j)]
        rows.append({
            "type": "station",
            "id": j,
            "position_km": round(pos, 4) if isinstance(pos, float) else pos,
            "locker_capacity": inst_dict["locker_capacity"][j],
            "service_time": inst_dict["service_time"][j],
            "service_energy": inst_dict["service_energy"][j],
            "demand_weight": "",
            "release_time": "",
            "feasible_lockers": "",
        })
    for k in inst_dict["orders"]:
        rows.append({
            "type": "order",
            "id": k,
            "position_km": "",
            "locker_capacity": "",
            "service_time": "",
            "service_energy": "",
            "demand_weight": inst_dict["demand_weight"][k],
            "release_time": inst_dict["release_time"][k],
            "feasible_lockers": ";".join(str(j) for j in inst_dict["feasible_lockers"][str(k)]),
        })

    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    return path


def export_corridor_summary(corridors: List[CorridorDef], output_dir: Path) -> Path:
    """Export a summary table of all corridors."""
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / "corridor_summary.csv"

    rows = []
    for c in corridors:
        rows.append({
            "corridor_id": c.corridor_id,
            "route_name": c.route_name,
            "agency": c.agency,
            "direction": c.direction,
            "n_stops": len(c.stops),
            "corridor_length_km": round(c.corridor_length_km, 3),
            "am_peak_headway_min": c.am_peak_headway_min,
            "planning_horizon_min": c.planning_horizon_min,
            "gtfs_source": c.gtfs_source,
        })

    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)

    return path


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate GTFS-derived Bus-UAV benchmark instances.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python gtfs_benchmark_generator.py
  python gtfs_benchmark_generator.py --output-dir ./my_instances --scenarios small medium
  python gtfs_benchmark_generator.py --seeds 42,100,200 --bus-speed 20.0
  python gtfs_benchmark_generator.py --format json
        """)
    parser.add_argument("--corridor-dir", type=str, default="corridor_data",
                        help="Directory containing hk_*_stops.csv files")
    parser.add_argument("--output-dir", type=str, default="instances",
                        help="Output directory for generated instances")
    parser.add_argument("--scenarios", nargs="+", default=["small", "medium", "large"],
                        choices=["small", "medium", "large"],
                        help="Demand scenarios to generate")
    parser.add_argument("--seeds", type=str, default="42,123,456",
                        help="Comma-separated random seeds for replicates")
    parser.add_argument("--bus-speed", type=float, default=24.0,
                        help="Bus speed in km/h (default: 24.0)")
    parser.add_argument("--format", type=str, default="csv", choices=["csv", "json", "both"],
                        help="Output format: csv (summary only), json (full instance), or both")
    args = parser.parse_args()

    corridor_dir = Path(args.corridor_dir)
    output_dir = Path(args.output_dir)
    seeds = [int(s) for s in args.seeds.split(",")]

    # Load all corridors
    corridor_files = sorted(corridor_dir.glob("hk_*_stops.csv"))
    if not corridor_files:
        raise ValueError(f"No corridor files found in {corridor_dir}")

    corridors = []
    for cf in corridor_files:
        corridor = load_corridor_from_csv(cf)
        corridors.append(corridor)
        print(f"Loaded corridor: {corridor.corridor_id} ({corridor.route_name}), "
              f"{len(corridor.stops)} stops, {corridor.corridor_length_km:.2f} km, "
              f"headway={corridor.am_peak_headway_min} min")

    # Export corridor summary
    export_corridor_summary(corridors, output_dir)
    print(f"\nCorridor summary -> {output_dir / 'corridor_summary.csv'}")

    # Generate instances
    all_summaries = []
    total = len(corridors) * len(args.scenarios) * len(seeds)
    count = 0

    for corridor in corridors:
        for scenario in args.scenarios:
            for seed in seeds:
                inst = generate_gtfs_instance(corridor, scenario, seed=seed, bus_speed_kmh=args.bus_speed)
                inst_name = inst["instance_name"]

                if args.format in ("csv", "both"):
                    export_instance_summary(inst, output_dir / corridor.corridor_id)
                if args.format in ("json", "both"):
                    instance_to_json(inst, output_dir / corridor.corridor_id / f"{inst_name}.json")

                count += 1

                all_summaries.append({
                    "corridor_id": corridor.corridor_id,
                    "scenario": scenario,
                    "seed": seed,
                    "instance_name": inst_name,
                    "n_stations": inst["n_stations"],
                    "n_orders": inst["n_orders"],
                    "n_uavs": inst["n_uavs"],
                    "max_sorties": inst["max_sorties"],
                    "n_trips": inst["n_trips"],
                    "corridor_length_km": round(corridor.corridor_length_km, 3),
                    "am_peak_headway_min": corridor.am_peak_headway_min,
                    "total_demand_kg": round(sum(inst["demand_weight"].values()), 2),
                    "planning_horizon_min": inst["planning_horizon_min"],
                })

                print(f"  [{count}/{total}] {inst_name}: "
                      f"{inst['n_stations']} stations, {inst['n_orders']} orders, "
                      f"{inst['n_trips']} trips")

    # Save master instance list
    master_path = output_dir / "instance_registry.csv"
    with open(master_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=all_summaries[0].keys())
        writer.writeheader()
        writer.writerows(all_summaries)

    print(f"\nGenerated {total} instances across {len(corridors)} corridors x "
          f"{len(args.scenarios)} scenarios x {len(seeds)} seeds")
    print(f"Instance registry -> {master_path}")


if __name__ == "__main__":
    main()
