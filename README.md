# BusUAV-GTFS-Benchmark

A GTFS-derived benchmark library for **Bus-UAV Collaborative Delivery** optimization, built on real Hong Kong public transit data.

This library provides ready-to-use benchmark instances for researchers studying bus-drone collaborative logistics. All instances are generated from Hong Kong's official GTFS feed, following the methodology established in Osorio and Ouyang (2025), "To ride or to fly: Optimal freight-on-transit operations using drones" (Transportation Research Part E).

## Key Features

- **Real-world transit data**: Bus corridors, headways, and stop positions are extracted from Hong Kong's official GTFS feed (DATA.GOV.HK), not synthetically generated.
- **Physically calibrated UAV parameters**: All drone parameters (speed, payload, energy consumption) are derived from DJI FlyCart 30 dual-battery specifications with verifiable provenance.
- **Multiple demand scenarios**: Three scenarios (small / medium / large) per corridor with configurable random seeds for reproducibility.
- **Self-contained Python generator**: Regenerate or extend instances with a single command. No external solver dependencies required.
- **Open data license**: Hong Kong GTFS data is published under the Government Open Data Terms, permitting commercial use and redistribution with attribution.

## Quick Start

### Prerequisites

- Python 3.8+ (no external packages required -- only standard library)

### Generate All Instances

```bash
# Clone the repository
git clone https://github.com/YOUR_USERNAME/BusUAV-GTFS-Benchmark.git
cd BusUAV-GTFS-Benchmark

# Generate all default instances (4 corridors x 3 scenarios x 3 seeds = 36 instances)
python gtfs_benchmark_generator.py
```

This creates an `instances/` directory with:
- Per-instance summary CSV files organized by corridor
- `corridor_summary.csv` -- metadata for all 4 corridors
- `instance_registry.csv` -- master list of all 36 instances

### Custom Generation

```bash
# Only small and medium scenarios
python gtfs_benchmark_generator.py --scenarios small medium

# Custom seeds and bus speed
python gtfs_benchmark_generator.py --seeds 42,100,200,300 --bus-speed 20.0

# Output as JSON (full instance data, not just summaries)
python gtfs_benchmark_generator.py --format json

# Both CSV summaries and JSON files
python gtfs_benchmark_generator.py --format both

# Custom output directory
python gtfs_benchmark_generator.py --output-dir ./my_benchmark
```

### Use Instances in Your Code

```python
from gtfs_benchmark_generator import load_corridor_from_csv, generate_gtfs_instance

# Load a corridor
corridor = load_corridor_from_csv("corridor_data/hk_14d_stops.csv")

# Generate an instance
inst = generate_gtfs_instance(corridor, scenario="medium", seed=42)

# Access instance data
print(f"Stations: {inst['n_stations']}, Orders: {inst['n_orders']}, Trips: {inst['n_trips']}")
print(f"Corridor length: {inst['corridor_length_km']} km")
print(f"AM peak headway: {inst['am_peak_headway_min']} min")

# Or load from a previously generated JSON file
from gtfs_benchmark_generator import load_instance_from_json
inst = load_instance_from_json("instances/HK-14D/HK-14D_medium_seed42.json")
```

## Selected Corridors

Four bus corridors are selected from Hong Kong's KMB network, spanning different districts and operational characteristics:

| Corridor ID | Route | District | Stops | Length (km) | AM Peak Headway (min) | Direction |
|-------------|-------|----------|-------|-------------|----------------------|-----------|
| HK-14D | KMB 14D | Kwun Tong / Wong Tai Sin | 6 | 6.79 | 5.0 | Yau Tong -> Choi Hung |
| HK-235M | KMB 235M | Kwai Tsing | 10 | 3.01 | 8.0 | On Yam Estate -> Kwai Fong Station |
| HK-31M | KMB 31M | Kwai Tsing | 10 | 3.13 | 10.0 | Shek Lei -> Kwai Fong Station |
| HK-42M | KMB 42M | Tsuen Wan / Tsing Yi | 9 | 4.93 | 7.0 | Tsuen Wan Discovery Park -> Tsing Yi Cheung Wang |

**Selection criteria** (following Osorio and Ouyang, 2025):
1. Corridor linearity: end-to-end distance / cumulative path distance > 0.6
2. Moderate stop count: 6-12 stops per direction
3. AM-peak headway data availability (07:00-09:30)
4. Geographic diversity across Hong Kong districts
5. Operational suitability for smart locker placement

## Instance Design

### Scenario Parameters

| Scenario | Orders | UAVs | Max Sorties per UAV |
|----------|--------|------|---------------------|
| Small | 6 | 2 | 3 |
| Medium | 10 | 3 | 4 |
| Large | 16 | 4 | 5 |

Each scenario is replicated with 3 random seeds (42, 123, 456) by default, yielding 36 instances total.

### UAV Parameters (DJI FlyCart 30, Dual-Battery)

| Parameter | Value | Source |
|-----------|-------|--------|
| Cruise speed | 54 km/h (15 m/s) | DJI FlyCart 30 specs |
| Max payload | 30 kg | DJI FlyCart 30 (dual-battery mode) |
| Battery energy | 3,968.8 Wh | 2 x DJI DB2000 (1,984.4 Wh each) |
| Empty range | 28 km | DJI FlyCart 30 specs |
| Full-load range | 16 km @ 30 kg | DJI FlyCart 30 specs |
| Fixed energy per km | 141.743 Wh/km | Derived: 3968.8 / 28 |
| Load energy per km per kg | 3.544 Wh/km/kg | Derived from range difference |

### Demand Generation

- Order weights: Uniform(0.3, 1.9) kg, calibrated to Hongkong Post iPostal light-parcel limits
- Release times: Uniform(0, 0.6 x T_max) min within the planning horizon
- Feasible lockers: Each order is assigned to 2-3 nearest stations
- Planning horizon: 90 minutes (AM peak window)

### Bus Schedule

Bus trips are generated at the corridor's real AM-peak headway for both directions (hub1->hub2 and hub2->hub1). Trip departure times, station arrival times, and corridor traversal times are computed from GTFS-derived distances and a default bus speed of 24 km/h.

## File Structure

```
BusUAV-GTFS-Benchmark/
├── README.md                          # This file
├── LICENSE                            # MIT License
├── gtfs_benchmark_generator.py        # Instance generation script (self-contained)
├── parameter_mapping.csv              # Full parameter provenance table
├── corridor_data/                     # Raw corridor data extracted from GTFS
│   ├── hk_14d_stops.csv              # 14D stop list (6 stops, 6.79 km)
│   ├── hk_235m_stops.csv             # 235M stop list (10 stops, 3.01 km)
│   ├── hk_31m_stops.csv              # 31M stop list (10 stops, 3.13 km)
│   └── hk_42m_stops.csv              # 42M stop list (9 stops, 4.93 km)
└── instances/                         # Generated instances
    ├── corridor_summary.csv           # Corridor metadata table
    ├── instance_registry.csv          # Master instance list with parameters
    ├── HK-14D/                        # Per-corridor instance files
    ├── HK-235M/
    ├── HK-31M/
    └── HK-42M/
```

## Parameter Provenance

Every parameter in the benchmark is traceable to its source. See `parameter_mapping.csv` for the complete provenance table, which maps each model parameter to its real-world proxy, value/rule, unit, source type (GTFS data / DJI specs / calibrated), and source URL.

## Data Source

Hong Kong Transport Department GTFS Public Transport Data:
- URL: https://data.gov.hk/en-data/dataset/hk-td-tis_11-pt-headway-en
- Feed: https://static.data.gov.hk/td/pt-headway-en/gtfs.zip
- Coverage: 1,992 routes, 9,414 stops, 14 transit agencies
- License: Hong Kong Government Open Data Terms and Conditions (https://data.gov.hk/en/terms-and-conditions)

## Citation

If you use this benchmark in your research, please cite:

```bibtex
@misc{busuav_gtfs_benchmark_2026,
  title     = {BusUAV-GTFS-Benchmark: A GTFS-derived Benchmark for Bus-UAV Collaborative Delivery},
  author    = {[Your Name]},
  year      = {2026},
  url       = {https://github.com/YOUR_USERNAME/BusUAV-GTFS-Benchmark}
}
```

And the methodology reference:

```bibtex
@article{osorio2025ride,
  title     = {To ride or to fly: Optimal freight-on-transit operations using drones},
  author    = {Osorio, Juli\'an and Ouyang, Yanfeng},
  journal   = {Transportation Research Part E: Logistics and Transportation Review},
  volume    = {201},
  pages     = {104231},
  year      = {2025},
  publisher = {Elsevier}
}
```

## License

This project is licensed under the MIT License -- see the [LICENSE](LICENSE) file for details.

The Hong Kong GTFS data is licensed separately under the [Hong Kong Government Open Data Terms and Conditions](https://data.gov.hk/en/terms-and-conditions).
