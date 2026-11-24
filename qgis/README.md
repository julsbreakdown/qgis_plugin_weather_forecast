# Weather Forecast QGIS plugin

Weather forecast maps in QGIS: one map at a time, or a time range played with the Temporal Controller.
Uses the `wxforecast` library of this project. QGIS 3.40 or later (tested on 4.2). English and French,
following the QGIS language.

## Install

**Before using it, the plugin needs extra Python packages** (`icechunk`, `zarr`, `pmtiles`) that QGIS does
not ship. QGIS has no built-in way to install them yet
([QEP 202](https://github.com/qgis/QGIS-Enhancement-Proposals/issues/202)), so the plugin relies on
[qpip](https://plugins.qgis.org/plugins/a00_qpip/), the plugin recommended there:

1. Download `wxforecast_qgis-<version>.zip` from the
   [latest release](https://github.com/julsbreakdown/qgis_plugin_weather_forecast/releases/latest).
2. In QGIS: Plugins > Manage and Install Plugins > Install from ZIP.
3. QGIS offers to install qpip with it (declared in `plugin_dependencies`); accept. qpip then lists the
   missing packages and installs them with QGIS's own Python.

Without qpip, install them yourself with the Python QGIS uses, then restart QGIS:

- Windows: in the **OSGeo4W Shell** (see the
  [PyQGIS documentation](https://docs.qgis.org/latest/en/docs/pyqgis_developer_cookbook/plugins/plugins.html)),
  `python -m pip install icechunk zarr pmtiles`
- Linux: `python3 -m pip install --user --break-system-packages icechunk zarr pmtiles` (Debian and Ubuntu
  mark the system Python as externally managed; the flag only allows the `--user` install)
- macOS: `/Applications/QGIS.app/Contents/MacOS/bin/python3 -m pip install --user icechunk zarr pmtiles`
  (adjust the app name, e.g. `QGIS-LTR.app`)

Until they are installed, the plugin only shows a message saying what is missing.

Development: link the checkout instead; the plugin then uses the project venv and `src/`:

```sh
uv venv --system-site-packages && uv sync
ln -sfn "$PWD/qgis/wxforecast_qgis" ~/.local/share/QGIS/QGIS4/profiles/default/python/plugins/
```

`python3 qgis/build_zip.py` builds the zip in `dist/`, with the library bundled. Release: push a tag
`vX.Y.Z`; once the tests pass, CI publishes a GitHub release with the zip at that version.

## Use

Click **Weather forecast** in the toolbar to open the panel.

1. **Model** and **Variable**. A gear badge marks calculated variables (wind speed from its u and v
   components).
2. **Time**:
   - **Single time**: **Map time** is `now` (forecast nearest the current time) or hours after the latest
     run.
   - **Time range**: every step from now to now + **Duration** (1-16 days), or one every **Step interval**
     hours. The steps are loaded as **one layer**: a VRT stacking one GeoTIFF per step, band k shown during
     step k (temporal mode "fixed range per band"). All steps share one colour scale, and the Temporal
     Controller is set up to play them.
3. **Extent**: canvas, layer, bookmark, drawn or typed, any CRS, across the antimeridian too. Empty: whole
   world.
4. **Fetch** runs in the background and can be cancelled.

Wind adds a group with the speed raster and flow lines (a GeoPackage of streamlines coloured by speed).
**Animated wind**, shown only for wind (default on), makes their dashes move downwind; the layer is then
redrawn continuously, which can be slow on large extents or long time ranges. Off, the dashes stay still.
Processing: `ANIMATE_FLOW`. Direction is meteorological: where the wind blows from (westerly = 270).

Colours come from the library styles (`src/wxforecast/styles.toml`), so a layer looks like its PMTiles.
**Styles** picks an override TOML file and restyles the project's forecast layers. Clouds, rain and snow
are transparent at 0, so the map shows through.

Layers and groups are tagged with their variable (custom properties `wxforecast/*`, saved in the project)
and show their kind icon in the Layers panel. Errors go to the message bar and the **Weather Forecast** log
tab.

## Processing

Provider **Weather forecast** in the Processing Toolbox (batch, Model Builder, `qgis_process`):

- **Fetch forecast** (`wxforecast:fetchforecast`): the panel's parameters (`LEAD_HOURS`, `DAYS`,
  `EVERY_HOURS`, `EXTENT`). `OUTPUT` is a GeoTIFF, or with `DAYS` > 0 the VRT over the step GeoTIFFs
  (listed in `STEPS`). Wind adds `FLOW`.
- **Export forecast to PMTiles** (`wxforecast:exportpmtiles`): coloured Web Mercator tiles over an extent
  and/or polygons, optionally clipped. `OUTPUT` is one `.pmtiles`, or with `DAYS` > 0 `OUTPUT_FOLDER` gets
  one archive per step plus `index.json`. At most about 100 000 tiles per export, checked before the
  download when possible.

```sh
qgis_process run wxforecast:exportpmtiles -- POLYGONS=france.gpkg MAX_ZOOM=6 OUTPUT=out/t2m.pmtiles
```

## Smoke test

Headless and offline (catalog and reads faked):

```sh
QT_QPA_PLATFORM=offscreen PYTHONPATH=/usr/share/qgis/python python3 qgis/smoke_test.py
```
