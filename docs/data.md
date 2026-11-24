# Data reference

Source: STAC catalog `https://stac.dynamical.org/catalog.json`; each child collection carries
`cube:dimensions`, `cube:variables` (with `unit`) and an `icechunk-https` asset (the repository href).
Measured on 2026-10-12.

## Models served

`list_models()` keeps collections that have `init_time`, `lead_time`, `latitude` and `longitude`, and skips:

- `*-virtual` collections: chunks are references into the source GRIB files, read through the `gribberish`
  codec (not a dependency) with one chunk per whole global field, so a bbox saves nothing.
- ensembles (`ensemble_member` dimension: `noaa-gefs-*`, `ecmwf-*-ens-*`): listed in STAC but not served yet.
- projected grids (`x`, `y`: HRRR, HRDPS): `grid.window` needs 1-D latitude and longitude axes.
- analyses (`time` instead of `init_time` and `lead_time`).

| id | grid | runs | leads | chunk (init, lead, lat, lon) | shard |
|---|---|---|---|---|---|
| `noaa-gfs-forecast` | 0.25°, global, 721 x 1440 | every 6 h | 0-384 h: hourly to 120 h, then 3-hourly (209) | 1, 105, 121, 121 (5.9 MiB raw) | 1, 210, 726, 726 |
| `ecmwf-aifs-single-forecast` | 0.25°, global, 721 x 1440 | every 6 h | 0-360 h, 6-hourly (61) | 1, 61, 241, 240 (13.5 MiB raw) | 1, 61, 723, 1440 |
| `dwd-icon-eu-forecast-5-day` | 0.0625°, Europe 23.5°W-62.5°E, 29.5-70.5°N, 657 x 1377 | every 6 h | 0-120 h: hourly to 78 h, then 3-hourly (93) | 1, 93, 219, 153 (11.9 MiB raw) | 1, 93, 657, 1377 |

Dimension order of every data variable: `(init_time, lead_time, latitude, longitude)`. If a collection
mixes dimension orders (pressure-level variables), `Model.variables` keeps only those on the most common one.

Axes, as stored (raw zarr, no CF decoding):

- `init_time`: float seconds since 1970-01-01 UTC. `lead_time`: float seconds.
- `latitude`: descending (90 to -90 for global grids), cell centres.
- `longitude`: ascending, -180 to 179.75 for global grids (no 180 column), cell centres.
- data: float32, NaN = missing.

## Variables for a weather map

Same names and units in all three models.

| variable | unit | note |
|---|---|---|
| `temperature_2m` | `degree_Celsius` | |
| `wind_u_10m`, `wind_v_10m` | `m s-1` | eastward, northward; served as derived `wind_10m` |
| `precipitation_surface` | `kg m-2 s-1` | rate averaged since the previous step; x 3600 for mm/h. NaN at lead 0 |
| `pressure_reduced_to_mean_sea_level` | `Pa` | / 100 for hPa |
| `total_cloud_cover_atmosphere` | `percent` | averaged since the previous step. NaN at lead 0 |

Wind: every `wind_u_<level>` / `wind_v_<level>` pair (10 m in all three models, 80 m and 100 m in GFS,
100 m in AIFS) becomes `wind_<level>` in `Model.variables`: speed `hypot(u, v)`, direction
`atan2(-u, -v)` in degrees clockwise from north the wind blows from; tiles interpolate u and v, never the
angle.

## Read cost

A chunk is the smallest unit fetched and decompressed. One map (one init, one lead) still pulls whole chunks,
which hold every lead of the chunk (105 for GFS). Cost therefore scales with the number of spatial chunks
the bbox touches, not with its pixel count. Shards are only the storage objects; zarr reads chunks inside
them by byte range.

GFS `temperature_2m`, one init and lead, zarr + icechunk over HTTPS, fresh session per read, 3 runs:

| bbox | output | chunks touched | read time |
|---|---|---|---|
| global | 721 x 1440 | 72 (6 x 12) | 17.0-18.5 s |
| Europe `-15,35,35,70` | 141 x 201 | 6 (2 x 3) | 2.9-5.7 s |
| antimeridian `170,-50,-170,-30` | 81 x 81 | 4 (2 x 2, in 2 col slices) | 4.0-5.6 s |
| small `6,45,11,48` | 13 x 21 | 1 | 1.6-2.3 s |

Repository open (STAC asset to zarr group) adds about 1.5 s. Raising `async.concurrency` from 10 to 64 did
not speed up the global read (15.1-16.0 s), so it is bandwidth or decompression bound.

## Quirks

- **Newest run briefly empty.** Each run is two-phase: an `Expand dataset` commit appends the new
  `init_time` (data all NaN), then worker commits fill it about 2 minutes later. Checked on the
  `Expand dataset` snapshot of 2026-10-12 05:38 UTC: newest init all NaN. On `main` after the update, the
  newest init had data for every lead (GFS 209/209, ICON-EU 93/93, AIFS 61/61).
  `fetch(run=None)` reads the newest run; when a chunk of the window is empty there but has data in the
  previous run, the newest run is taken as partly written and the previous one is used (up to 4 runs).
- **Latency.** The 00 UTC run landed at 05:40 UTC (GFS), 06:14 UTC (AIFS), 03:54 UTC (ICON-EU).
- `ingested_forecast_length` is all NaN in GFS, so it cannot tell which runs are complete.
- Averaged and accumulated variables are NaN at lead 0; probe the lead actually wanted, not lead 0.
- `geopotential_height_cloud_ceiling` (mask above 19000 m) and `percent_frozen_precipitation_surface`
  (mask below -0.1) carry a no-data range instead of NaN.
- Store hrefs are versioned (`.../v0.2.7.icechunk`); always take them from STAC, never hardcode them.
