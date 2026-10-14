# wxforecast

Fetch one weather forecast map (one model, one variable, one run, one lead time) from the
[dynamical.org](https://dynamical.org) Icechunk repositories and write it as a Cloud Optimized GeoTIFF,
coloured PMTiles, wind flow lines and SLD styles for GeoServer.

Models and variables come from the STAC catalog `https://stac.dynamical.org/catalog.json`. Data is read
straight from the public Icechunk repositories over HTTPS with zarr: no account, no download of the
whole archive, only the chunks covering the requested area.

Used by the QGIS plugin in `qgis/` and by Python DAGs. The library has no QGIS dependency, keeps no
global state, logs through `logging` and is safe to call from a worker thread.

## Supported data

Read from the STAC catalog at run time, so a model or variable added there shows up without a release.
Checked on 2026-10-14; grids, chunking, read costs and quirks are in [docs/data.md](docs/data.md).

| model id | grid | runs | leads |
|---|---|---|---|
| `noaa-gfs-forecast` | 0.25°, global | every 6 h | 0-384 h: hourly to 120 h, then 3-hourly |
| `ecmwf-aifs-single-forecast` | 0.25°, global | every 6 h | 0-360 h, 6-hourly |
| `dwd-icon-eu-forecast-5-day` | 0.0625°, Europe (23.5°W-62.5°E, 29.5-70.5°N) | every 6 h | 0-120 h: hourly to 78 h, then 3-hourly |

Not served: `*-virtual` collections, ensembles (GEFS, ECMWF ENS), projected grids (HRRR, HRDPS) and
analyses. `list_models()` is the authority.

Variables, as listed by `Model.display_variables` (units as stored; legends relabel Pa as hPa and
kg m-2 s-1 as mm/h):

| variable | unit | GFS | AIFS | ICON-EU |
|---|---|---|---|---|
| `temperature_2m` | °C | ✓ | ✓ | ✓ |
| `temperature_80m` | °C | ✓ | - | - |
| `temperature_850hpa`, `temperature_925hpa` | °C | - | ✓ | - |
| `maximum_temperature_2m`, `minimum_temperature_2m` (1) | °C | ✓ | - | - |
| `dew_point_temperature_2m` | °C | - | ✓ | ✓ |
| `relative_humidity_2m` | % | ✓ | - | ✓ |
| `precipitation_surface` (1) | kg m-2 s-1 | ✓ | ✓ | ✓ |
| `precipitable_water_atmosphere` | kg m-2 | ✓ | - | ✓ |
| `percent_frozen_precipitation_surface` | % | ✓ | - | - |
| `categorical_rain/snow/freezing_rain/ice_pellets_surface` | 0 or 1 | ✓ | - | - |
| `snow_thickness_surface`, `snow_water_equivalent_surface` | m | - | - | ✓ |
| `total_cloud_cover_atmosphere` (1) | % | ✓ | ✓ | ✓ |
| `cloud_cover_low`, `cloud_cover_medium`, `cloud_cover_high` | % | - | - | ✓ |
| `geopotential_height_cloud_ceiling` | m | ✓ | - | - |
| `pressure_reduced_to_mean_sea_level`, `pressure_surface` | Pa | ✓ | ✓ | ✓ |
| `pressure_80m` | Pa | ✓ | - | - |
| `geopotential_height_500hpa`, `_850hpa`, `_925hpa` | m | - | ✓ | - |
| `wind_10m` (2) | m s-1 | ✓ | ✓ | ✓ |
| `wind_80m` (2) | m s-1 | ✓ | - | - |
| `wind_100m` (2) | m s-1 | ✓ | ✓ | - |
| `wind_gust_10m` | m s-1 | - | - | ✓ |
| `downward_short_wave_radiation_flux_surface`, `downward_long_wave_radiation_flux_surface` | W m-2 | ✓ | ✓ | - |
| `downward_direct_short_wave_radiation_flux_surface`, `downward_diffuse_short_wave_radiation_flux_surface` | W m-2 | - | - | ✓ |
| `convective_available_potential_energy_atmosphere` | J kg-1 | - | - | ✓ |

(1) Averaged since the previous step: NaN at lead 0.
(2) Calculated from `wind_u_<level>` and `wind_v_<level>` (see Wind); the components stay fetchable.

## Install

Requires Python 3.12+ and [uv](https://docs.astral.sh/uv/). GDAL (Python bindings `osgeo.gdal`) is only
needed to write GeoTIFFs; it is taken from the system packages.

```sh
uv venv --system-site-packages   # exposes the system osgeo.gdal
uv sync
```

## Library

```python
from datetime import timedelta

import wxforecast

forecast = wxforecast.fetch(
    "noaa-gfs-forecast",
    "temperature_2m",
    bbox=(-10.0, 35.0, 30.0, 60.0),  # west, south, east, north; west > east crosses 180
    lead=timedelta(hours=24),        # None: valid time nearest now
)                                    # run=None: latest run with data
print(forecast.init_time, forecast.valid_time, forecast.unit, forecast.values.shape)
wxforecast.to_geotiff(forecast, "out/t2m.tif")
```

An unknown model, variable, run or lead raises `ValueError` listing what exists (leads as
`0-120 h every 1 h, then 120-384 h every 3 h`). With `run=None`, a run still being written (some of its
chunks empty where the previous run has data) is skipped for the previous one. `fetch` raises `ValueError` when the map has no data at all in the bbox, e.g. a period variable
(precipitation, cloud cover averages, maximum or minimum temperature) at lead 0, which is always NaN.

`Forecast.values` is a north-up float32 array with NaN as nodata, georeferenced by
`Forecast.geotransform` in `EPSG:4326`. Times are timezone-aware UTC. `wxforecast.list_models()` and
`wxforecast.model(model_id)` describe what is available; `Model.display_variables` is the list to show a
user (wind components hidden). `Variable.calculated` is true for a variable derived at read time from
`Variable.components` (today the wind per level); every other variable is raw, read as stored.

### Time steps

```python
steps = wxforecast.fetch_range("noaa-gfs-forecast", "temperature_2m", bbox=(-5.0, 42.0, 8.5, 51.5),
                               days=2, every=timedelta(hours=6))   # list[Forecast], oldest first
for step in steps:
    wxforecast.to_geotiff(step, f"out/t2m/{wxforecast.step_name(step)}.tif")  # temperature_2m_20261012T06
```

`fetch_range` returns every step of one run (same run selection as `fetch`) valid from the step nearest now
to now + `days`. `every` thins on valid time: a step is kept once `every` has passed since the last kept one
(GFS is hourly to 120 h, then 3-hourly; steps are never invented). The lead range is read in one zarr slice
per variable, so 9 steps cost about the same chunks as one (GFS over France: 5.5 s for 9 steps, 6.0 s for
one). Steps without data are dropped (logged); `ValueError` if all are. `fetch` is the one-step case.

- `to_flow_lines(steps, path)` writes one GeoPackage of every step, with a `valid_time` DateTime (UTC) field.
- `to_pmtiles_steps(steps, folder, areas, ...)` writes `<variable>_<YYYYMMDDTHH>.pmtiles` per step, all over
  one value range so colours compare across time (in each archive's legend), then `index.json`: model,
  variable, unit, init_time, legend, `steps` (valid_time, lead_hours, file). Progress covers all steps.

### Wind

Each `wind_u_<level>` and `wind_v_<level>` pair of a model gives one variable `wind_<level>` (`wind_10m`,
`wind_80m`, `wind_100m` in GFS), unit of the components, long name like `10 metre wind`. Fetching it reads
both components at the same run, lead and window and returns a `Forecast` whose `values` are the speed and
whose `direction` holds degrees clockwise from north the wind blows FROM (meteorological: a westerly is
270). `direction` is `None` for every other variable. The components stay fetchable by name.

```python
wind = wxforecast.fetch("noaa-gfs-forecast", "wind_10m", bbox=(-15.0, 35.0, 20.0, 60.0))
wxforecast.to_geotiff(wind, "out/wind.tif")          # band 1 speed, band 2 direction
wxforecast.to_flow_lines(wind, "out/flow.gpkg")  # streamlines, fields speed, phase
```

Directions are never interpolated: tiles resample u and v (rebuilt from speed and direction), then
compute speed and direction per pixel.

### PMTiles

```python
areas = [
    wxforecast.bbox_wkt((-25.0, 34.0, 45.0, 72.0)),
    *wxforecast.areas_from_vector("france.gpkg"),  # WKT in EPSG:4326, one per polygon
]
forecast = wxforecast.fetch("noaa-gfs-forecast", "temperature_2m", bbox=wxforecast.areas_bbox(areas))
wxforecast.to_pmtiles(forecast, "out/t2m.pmtiles", areas, min_zoom=0, clip=True)  # max_zoom: from the grid
```

Coloured 256 px PNG tiles in Web Mercator (z/x/y), only where the areas are (WKT polygons in EPSG:4326,
made valid and merged once), from `min_zoom` to `max_zoom`. `max_zoom=None` (the default) is
`wxforecast.native_zoom(forecast.geotransform)`: the first zoom whose pixels are no wider than a grid cell,
plus 2, so every model gets tiles down to its own resolution (GFS 0.25°: 5; a 0.025° grid: 8), never below
`min_zoom`. `clip=True` makes pixels outside the polygons
transparent. `progress(done, total)` is called after each tile; returning False stops with
`wxforecast.Cancelled` and leaves no file. Colours come from the style table (see Styles),
over the kind's range unless `vmin`/`vmax` are given. The archive metadata `wxforecast` holds model,
variable, unit, times and the legend (`kind`, `min`, `max`, `colors`, `positions`, `alpha`,
`interpolation`, `display_unit`, `display_factor`). Limits: zoom 0 to 12, about 100 000 tiles (estimated before any
tile is drawn), no bbox across the antimeridian. Past the native zoom, tiles only interpolate further.

Wind tiles are coloured by speed with dark, white-haloed arrows on top: one per 32 x 32 px cell, centred
in it and sampled from the resampled u and v there, so arrows line up across tile edges and never cross
one. They point where the wind blows to; length runs from 10 px at 0 to 28 px at the legend max. With
`clip`, arrows centred outside the polygons are skipped and the rest are cut at the edge. The metadata
gains `wxforecast.arrows` (spacing, lengths, convention).

### Styles

Styles live in one TOML file shipped with the package, `src/wxforecast/styles.toml`, read by
`wxforecast.colors` (stdlib `tomllib`) and used by both the PMTiles colouring and the QGIS plugin.
`colors.load_styles(styles=None)` returns the styles by kind; `variable_kind(name, unit)` returns the first
kind, in file order, whose `names` patterns or `units` match, else `other`. Data are never converted:
`display_unit` only relabels legends (Pa as hPa, kg m-2 s-1 as mm/h).

One `[kind]` table per kind; every key is optional except a ramp (`colors` or `stops`) for a new kind:

| key | value |
|---|---|
| `title`, `icon` | legend title; icon name, an SVG of `qgis/wxforecast_qgis/icons/` (unknown: `generic`) |
| `names`, `units` | case-insensitive glob patterns on the variable name (`"*cloud_cover*"`); exact units |
| `colors` | evenly spaced `#rrggbb` colours, opaque |
| `stops` | `[position, colour, alpha?, label?]`, positions ascending strictly from 0 to 1, alpha 0..255 (default 255), label for exact classes; alpha 0 on the first stop makes 0 transparent |
| `range` | `"data"` (default: data min and max) or `[min, max]` in data units |
| `anchor` | with a data range: `"zero"` (start at 0 at most) or `"symmetric"` (-m..m) |
| `valid_min`, `valid_max` | values outside are nodata (fill values) |
| `discrete` | `true`: exact classes, each value takes its nearest stop (categorical 0/1) |

An override file changes only what it names: a known kind is updated key by key (a new `colors` or `stops`
replaces the ramp), a new kind is matched before the defaults. It is taken from the `styles` argument
(`to_pmtiles(..., styles="my_styles.toml")`, `variable_kind(..., styles=...)`), else the environment
variable `WXFORECAST_STYLES`, else none. Errors (unknown key, bad colour, stops not ascending, bad range)
raise `ValueError` naming the file and kind. An edited file is read again on the next call.

```toml
[cloud]
stops = [[0, "#ffffff", 0], [1, "#ffffff", 200]]  # plain white, transparent at 0 %

[soil_moisture]
icon = "generic"
names = ["soil_moisture*"]
range = [0, 0.5]
colors = ["#8c510a", "#f6e8c3", "#01665e"]
```

| kind | variables | ramp | range | transparency |
|---|---|---|---|---|
| `temperature` | `*temperature*` (2 m, 80 m, 850/925 hPa, min, max, dew point) | blue, white, red | data | opaque |
| `precipitation` | `precipitation_surface` | light to dark blues | 0 to data max | 0 transparent, light rain faint |
| `cloud` | `total_cloud_cover_atmosphere`, `cloud_cover_low/medium/high` | gray to white | 0 to 100 % | 0 fully transparent (holes), alpha rising to 235 at overcast |
| `cloud_ceiling` | `geopotential_height_cloud_ceiling` | dark gray (low) to light (high) | 0 to 8000 m | values above 19000 m (no ceiling fill) are nodata |
| `humidity` | `relative_humidity_2m` | browns, greens, blue-green | 0 to 100 % | opaque |
| `precipitable_water` | `precipitable_water_atmosphere` | yellow, green, blue | 0 to data max | opaque |
| `pressure` | `pressure_reduced_to_mean_sea_level`, `pressure_surface`, `pressure_80m` | purple, white, orange | data | opaque |
| `wind` | `wind_<level>` (calculated), `wind_gust_10m` | white to purple | 0 to data max | opaque |
| `wind_component` | `wind_u_<level>`, `wind_v_<level>` | purple, white, green | symmetric around 0 | opaque |
| `radiation` | `*radiation_flux*` | black, purple, orange, yellow | data | opaque |
| `cape` | `convective_available_potential_energy_atmosphere` | yellow, orange, red, purple | 0 to 3000 J/kg | 0 transparent |
| `height` | `geopotential_height_500/850/925hpa` | dark green to white | data | opaque |
| `snow` | `snow_thickness_surface`, `snow_water_equivalent_surface` | white, light blue, purple | 0 to data max | 0 transparent |
| `frozen_fraction` | `percent_frozen_precipitation_surface` | light blue to purple | 0 to 100 % | 0 transparent, below -0.1 (fill) nodata |
| `rain_flag`, `snow_flag`, `freezing_rain_flag`, `ice_pellets_flag` | `categorical_*_surface` | one colour (blue, pale lilac, magenta, orange) | 0 or 1, exact | 0 transparent |
| `flow` | QGIS wind flow lines (matches no variable) | cyan, yellow, red | 0 to layer max, 7 classes | opaque |
| `other` | anything else | dark blue to yellow (cividis-like) | data | opaque |

## Use in a DAG (GeoServer)

The functions are plain and stateless, so any orchestrator can call them as tasks. The library writes files
only; it never talks to GeoServer.

- `to_geotiff` writes a Cloud Optimized GeoTIFF (DEFLATE with floating point predictor, 256 px blocks,
  internal overviews; wind: band 2 direction, overviews by nearest cell), ready for a GeoServer GeoTIFF store.
- `to_flow_lines` writes a GeoPackage layer `flow` (LineString, EPSG:4326, Real fields `speed`, `phase`).
- `to_sld(forecast, path)` writes an SLD 1.0 RasterSymbolizer ColorMap from the same styles as the tiles
  and QGIS (stops at their data values, opacity from alpha, exact values for discrete kinds, values outside
  the kind's valid range transparent). `to_sld("cloud", path)` works for a kind with a fixed range.
  `to_sld(wind, path, flow=True)` styles the flow lines: one LineSymbolizer per speed class, the 7 classes
  and colours of the QGIS flow layer (no animation).

```python
from datetime import datetime

import wxforecast

EUROPE = (-15.0, 35.0, 35.0, 70.0)


def forecast_task(run: datetime, out: str) -> None:
    t2m = wxforecast.fetch("noaa-gfs-forecast", "temperature_2m", bbox=EUROPE, run=run)
    wxforecast.to_geotiff(t2m, f"{out}/t2m.tif")
    wxforecast.to_sld(t2m, f"{out}/t2m.sld")
    wind = wxforecast.fetch("noaa-gfs-forecast", "wind_10m", bbox=EUROPE, run=run)
    wxforecast.to_geotiff(wind, f"{out}/wind.tif")
    wxforecast.to_flow_lines(wind, f"{out}/flow.gpkg")
    wxforecast.to_sld(wind, f"{out}/wind.sld")
    wxforecast.to_sld(wind, f"{out}/flow.sld", flow=True)
```

For a time dimension, write one COG per step with `step_name` into one folder and publish it as an
ImageMosaic store with time enabled, from these two files in the folder (valid times are UTC; run
GeoServer with `-Duser.timezone=GMT`):

```properties
# indexer.properties
TimeAttribute=time
Schema=*the_geom:Polygon,location:String,time:java.util.Date
PropertyCollectors=TimestampFileNameExtractorSPI[timeregex](time)
# timeregex.properties
regex=[0-9]{8}T[0-9]{2},format=yyyyMMdd'T'HH
```

The flow GeoPackage of several steps serves a vector time dimension on `valid_time`.

Then publish the files with the GeoServer REST API in your DAG. Pass an explicit `run` so a retried task
reads the same data. Every output is written under a unique temporary name next to the target and renamed,
so a failed task never leaves a partial file and two writers never collide.

## Tests

```sh
uv run pytest -m 'not network'   # offline, in-memory zarr stores
uv run pytest -m network         # reads GFS over France from dynamical.org
uv run ruff check . && uv run ruff format --check src tests qgis
```
