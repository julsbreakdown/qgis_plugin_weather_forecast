# Weather Forecast QGIS plugin

Displays one weather forecast map from the dynamical.org Icechunk repositories in QGIS, through the
`wxforecast` library of this project. Requires QGIS 3.40 or later (tested on QGIS 4.2, Qt6).

## Install

From the project root, create the library venv once (it sees the system GDAL and numpy):

```sh
uv venv --system-site-packages && uv sync
```

Link the plugin into a QGIS profile (here `default`; QGIS shows the active one in Settings > User
Profiles), restart QGIS, then enable **Weather Forecast** in Plugins > Manage and Install Plugins:

```sh
ln -sfn "$PWD/qgis/wxforecast_qgis" ~/.local/share/QGIS/QGIS4/profiles/default/python/plugins/
```

QGIS runs on the system Python, where `icechunk`, `zarr` and `wxforecast` are not installed. On load the
plugin appends `<checkout>/.venv/lib/python3.X/site-packages` and `<checkout>/src` to `sys.path`, found from
the symlink (appended, so the numpy and GDAL shipped with QGIS keep priority). Elsewhere, set the paths,
separated by `:`, in the environment variable `WXFORECAST_SITE_PACKAGES` before starting QGIS. The venv must
use the same Python minor version as QGIS (3.14 here). Uninstall: delete the symlink.

## Use

1. Click the toolbar button **Weather forecast**: a panel opens on the right and loads the catalog.
2. Pick a **Model** and a **Variable** (long name and unit). Each entry has an icon of its kind
   (thermometer, cloud, rain drop, snowflake, gauge, humidity drop, sun, lightning, mountain, wind,
   u/v axes, generic). A calculated variable carries a small gear badge: wind is one per level (e.g.
   **10 metre wind**), calculated from its u and v components, which are listed right after it as raw
   entries. The tooltip says `calculated from wind_u_10m, wind_v_10m` or `raw, read as stored`.
3. **Lead**: `now` picks the valid time nearest now in the latest run; otherwise hours after the latest run.
4. **Days ahead** (default "one step"): every step of the latest run from now to now + days, each its own
   layer in one group `<variable> <run> UTC +<days> d`, ready for QGIS's Temporal Controller (each layer has
   its valid time range, until the next step); **Every** keeps a step only every so many hours. All steps
   are styled over one value range; wind adds one flow layer of all steps, filtered by `valid_time`. The
   Temporal Controller is set to animate the fetched range by the shortest step and its panel is shown.
   The flow dashes keep moving while it plays. Lead is ignored when days ahead is set.
5. **Extent**: QGIS's extent picker: current map canvas, a layer's extent, a bookmark, drawn on the map or
   typed, in any CRS (transformed to EPSG:4326; an extent across the antimeridian is supported). Clear it
   for **Whole world**, the default on an empty project; otherwise it starts on the current canvas.
6. **Fetch** runs the Processing algorithm **Fetch forecast** (below) as a background task (progress in the
   status bar, cancellable). The result is written to the Processing temporary folder and added as a layer
   named `<variable> <valid time> UTC`. **Reload catalog** downloads the catalog again.

Styling comes from the library styles file `wxforecast/styles.toml` (kinds, ramps, ranges and icons;
format in the main README), so a layer looks like its PMTiles. **Styles** in the panel picks an override
TOML file (QGIS setting `wxforecast/styles`; empty: `WXFORECAST_STYLES` or the defaults); choosing a file
or clicking the refresh button next to it validates the file, then restyles every forecast layer of the
project and refreshes the icons. An invalid file is reported and not applied. Each kind has its own ramp: clouds go from transparent at 0 %
(clear sky shows the map) through translucent gray to white; precipitation, CAPE, snow and the categorical
rain/snow/freezing rain/ice pellets flags are transparent at 0 (flags use exact classes); fill values
(cloud ceiling above 19000 m, frozen percent below -0.1) are set as user nodata. Continuous layers are
resampled bilinearly when zoomed in. Legend labels show display units (hPa, mm/h, °C, %), the data stay as
stored.

### Layers panel

Every layer and group the plugin adds (panel or Processing) is tagged with custom properties
`wxforecast/variable`, `wxforecast/kind`, `wxforecast/origin` (`raw` or `calculated`),
`wxforecast/components` and `wxforecast/unit`, saved in the project. The Layers panel shows an indicator next to each tagged
node: the kind icon, with the gear badge on calculated ones (the wind group, its speed and flow layers),
tooltip as in the variable list. The plugin rescans the tree when layers are added and when a project is
read, so indicators come back after a reload.

### Wind

A wind variable adds a layer group named `<variable> <valid time> UTC` holding two layers:

- **flow**: animated streamlines (the lot5 Data Adapter plugin style), a GeoPackage of lines next to the
  GeoTIFF (`<name>_flow.gpkg`) traced from about 45 seeds across, 60 steps long, with fields `speed` (mean along the line)
  and `phase`. A faint trail plus dashes that move downwind (`@symbol_frame`), paced by speed. Graduated
  renderer on `speed`: 7 equal classes from 0 to the layer's maximum (legend `0 - 1.71 m/s`...), colours
  sampled from the `flow` style ramp (cyan, yellow, red), the same classes as the library's flow SLD.
- **speed**: the GeoTIFF, band 1 speed (styled), band 2 direction.

`direction` is meteorological: degrees clockwise from north the wind blows FROM (a westerly is 270).

Errors appear in the message bar, with
the full traceback in the log panel, tab **Weather Forecast**.

## Processing

The plugin adds a **Weather forecast** provider to the Processing Toolbox, with QGIS's own parameter
widgets, batch mode, the Model Builder and `qgis_process`:

- **Fetch forecast**: model, variable, lead hours (empty: now), days ahead and every (hours) as in the
  panel (days > 0: one GeoTIFF per step next to the output, output `STEPS`), extent (empty: whole world), output
  GeoTIFF, loaded, styled and tagged like the panel's layers. A wind variable (`VARIABLE=wind_10m`) also writes the
  flow lines GeoPackage next to the GeoTIFF (output `FLOW`) and loads both in one group.
- **Export forecast to PMTiles**: same forecast parameters, an extent and/or a polygon layer (with
  "Selected features only"), min and max zoom, **Clip to polygons**, output `.pmtiles` (days ahead > 0: a
  folder `OUTPUT_FOLDER` of one archive per step plus `index.json`, one shared value range), coloured with the
  panel's styles file, with progress and cancel. Wind tiles have the arrows drawn in. Invalid polygons are
  stopped by Processing's own geometry check; with the check off, the library makes them valid.

```sh
qgis_process run wxforecast:exportpmtiles -- POLYGONS=france.gpkg MAX_ZOOM=6 OUTPUT=out/t2m.pmtiles
```

## Smoke test

Headless, without network: the real library, with the catalog and the read faked:

```sh
QT_QPA_PLATFORM=offscreen PYTHONPATH=/usr/share/qgis/python python3 qgis/smoke_test.py
```
