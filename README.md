# wxforecast

[![tests](https://github.com/julsbreakdown/qgis_plugin_weather_forecast/actions/workflows/tests.yml/badge.svg)](https://github.com/julsbreakdown/qgis_plugin_weather_forecast/actions/workflows/tests.yml)

Weather forecast maps (GFS, ECMWF AIFS, DWD ICON-EU) in QGIS and Python, from the free public data of
[dynamical.org](https://dynamical.org).

- **QGIS plugin**: pick a model, a variable, and a single time or a time range; get a styled layer. A time
  range is one VRT layer (a band per step) played by the Temporal Controller. Wind comes with animated flow
  lines. See [`qgis/`](qgis/README.md).
- **Python library**: fetch a forecast over a bbox and write a Cloud Optimized GeoTIFF, PMTiles or wind
  flow lines (GeoPackage). No QGIS needed.
- **Light**: no account or API key; only the data chunks covering your area are downloaded.

![Animated wind flow lines in QGIS](assets/qgis_wind_flow.gif)

![ICON-EU total cloud cover over France in QGIS, clear sky transparent](assets/qgis_cloud_cover.jpg)

## Supported data

Read from `https://stac.dynamical.org/catalog.json` at run time; `list_models()` is the authority.
Models served (GFS, ECMWF AIFS, DWD ICON-EU), their grids, runs, leads, read costs and
quirks, and what is skipped: [docs/data.md](docs/data.md).

| variable | unit | GFS | AIFS | ICON-EU |
|---|---|---|---|---|
| `temperature_2m` | °C | ✓ | ✓ | ✓ |
| `temperature_80m` | °C | ✓ | - | - |
| `temperature_850hpa`, `_925hpa` | °C | - | ✓ | - |
| `maximum_temperature_2m`, `minimum_temperature_2m` (1) | °C | ✓ | - | - |
| `dew_point_temperature_2m` | °C | - | ✓ | ✓ |
| `relative_humidity_2m` | % | ✓ | - | ✓ |
| `precipitation_surface` (1) | kg m-2 s-1 | ✓ | ✓ | ✓ |
| `precipitable_water_atmosphere` | kg m-2 | ✓ | - | ✓ |
| `percent_frozen_precipitation_surface` | % | ✓ | - | - |
| `categorical_rain/snow/freezing_rain/ice_pellets_surface` | 0 or 1 | ✓ | - | - |
| `snow_thickness_surface`, `snow_water_equivalent_surface` | m | - | - | ✓ |
| `total_cloud_cover_atmosphere` (1) | % | ✓ | ✓ | ✓ |
| `cloud_cover_low`, `_medium`, `_high` | % | - | - | ✓ |
| `geopotential_height_cloud_ceiling` | m | ✓ | - | - |
| `pressure_reduced_to_mean_sea_level`, `pressure_surface` | Pa | ✓ | ✓ | ✓ |
| `pressure_80m` | Pa | ✓ | - | - |
| `geopotential_height_500hpa`, `_850hpa`, `_925hpa` | m | - | ✓ | - |
| `wind_10m` (2) | m s-1 | ✓ | ✓ | ✓ |
| `wind_80m` (2) | m s-1 | ✓ | - | - |
| `wind_100m` (2) | m s-1 | ✓ | ✓ | - |
| `wind_gust_10m` | m s-1 | - | - | ✓ |
| `downward_short_wave_radiation_flux_surface`, `downward_long_wave_...` | W m-2 | ✓ | ✓ | - |
| `downward_direct_short_wave_...`, `downward_diffuse_short_wave_...` | W m-2 | - | - | ✓ |
| `convective_available_potential_energy_atmosphere` | J kg-1 | - | - | ✓ |

(1) Averaged since the previous step: NaN at lead 0, so `fetch` raises there.
(2) Speed from `wind_u_<level>` and `wind_v_<level>`, plus `direction` in degrees the wind blows FROM
(westerly = 270). Components stay fetchable.

°C and % stand for the stored `degree_Celsius` and `percent` ([docs/data.md](docs/data.md)). Data keep the
stored units; legends relabel them as °C, %, hPa (from Pa) and mm/h (from kg m-2 s-1).

## Install

QGIS plugin: see [qgis/README.md](qgis/README.md#install) (a zip, plus three Python packages installed
by qpip).

Library: Python 3.12+, [uv](https://docs.astral.sh/uv/), system GDAL (`osgeo.gdal`, only for writing
files).

```sh
uv venv --system-site-packages && uv sync
```

## Use

```python
from datetime import timedelta
import wxforecast

bbox = (-10.0, 35.0, 30.0, 60.0)  # west, south, east, north; west > east crosses 180
t2m = wxforecast.fetch("noaa-gfs-forecast", "temperature_2m", bbox=bbox, lead=timedelta(hours=24))
wxforecast.to_geotiff(t2m, "out/t2m.tif")              # COG, float32, NaN nodata, EPSG:4326
wxforecast.to_sld(t2m, "out/t2m.sld")                  # GeoServer ColorMap, same colours

wind = wxforecast.fetch("noaa-gfs-forecast", "wind_10m", bbox=bbox)
wxforecast.to_geotiff(wind, "out/wind.tif")            # band 1 speed, band 2 direction
wxforecast.to_flow_lines(wind, "out/flow.gpkg")        # streamlines: speed, phase
wxforecast.to_sld(wind, "out/flow.sld", flow=True)

areas = [wxforecast.bbox_wkt(bbox), *wxforecast.areas_from_vector("france.gpkg")]
wxforecast.to_pmtiles(t2m, "out/t2m.pmtiles", areas, clip=True)

steps = wxforecast.fetch_range("noaa-gfs-forecast", "temperature_2m", bbox=bbox,
                               days=2, every=timedelta(hours=6))
wxforecast.to_pmtiles_steps(steps, "out/t2m", areas)  # one archive per step + index.json
```

- `run=None` takes the latest run with data; `lead=None` the valid time nearest now. Pass `run` explicitly
  for reproducible reads.
- Unknown model, variable, run or lead raises `ValueError` listing what exists.
- `fetch_range` reads the lead range in one slice: 9 steps cost about one.
- PMTiles: 256 px PNG tiles in Web Mercator. `max_zoom` defaults to the model's native zoom + 2 (GFS: 5);
  at most zoom 12 and about 100 000 tiles per export (`estimate_tiles` checks before fetching). No bbox
  across 180. Wind tiles have arrows. `progress(done, total)` returning False cancels.
- Outputs are written under a temporary name then renamed: no partial files.

## Styles

One TOML table per kind in [`src/wxforecast/styles.toml`](src/wxforecast/styles.toml), shared by
PMTiles, SLD and QGIS. First kind whose `names` or `units` match wins, else `other`. Override with a file
passed as `styles=` or `WXFORECAST_STYLES`: it changes only what it names.

| key | value |
|---|---|
| `title`, `icon` | legend title; SVG in `qgis/wxforecast_qgis/icons/` (default `generic`) |
| `names`, `units` | case-insensitive globs on the variable name; exact units |
| `colors` | evenly spaced `#rrggbb` |
| `stops` | `[position 0..1, colour, alpha 0..255?, label?]`, ascending |
| `range` | `"data"` (default) or `[min, max]` |
| `anchor` | with `"data"`: `"zero"` or `"symmetric"` |
| `valid_min`, `valid_max` | outside = nodata |
| `discrete` | `true`: exact classes (0/1 flags) |

## GeoServer time dimension

One COG per step named with `step_name(step)` in one folder, published as an ImageMosaic (run GeoServer
with `-Duser.timezone=GMT`):

```properties
# indexer.properties
TimeAttribute=time
Schema=*the_geom:Polygon,location:String,time:java.util.Date
PropertyCollectors=TimestampFileNameExtractorSPI[timeregex](time)
# timeregex.properties
regex=[0-9]{8}T[0-9]{2},format=yyyyMMdd'T'HH
```

Flow lines of several steps carry a `valid_time` field (UTC) for a vector time dimension; `show_from` and
`show_until` are the indexed step span QGIS filters on.

## Tests

CI runs ruff, then pytest (offline) and the headless QGIS smoke test in the `qgis/qgis:4.2` image, on every
push and pull request. Locally: `make test`, `make lint`, `make smoke`, `make zip`,
`make release V=0.2.0` (tags and pushes; CI publishes the release). Or directly:

```sh
uv run pytest -m 'not network'   # offline
uv run pytest -m network         # live GFS over France
uv run ruff check . && uv run ruff format --check src tests qgis
```
