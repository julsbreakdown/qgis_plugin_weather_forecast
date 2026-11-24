# Weather Forecast QGIS plugin

Weather forecast maps in QGIS: one map at a time, or a time range played with the Temporal Controller.
Uses the `wxforecast` library of this project. QGIS 3.40 or later (tested on 4.2). English and French,
following the QGIS language.

## Install

From the project root, create the library venv once (it sees the system GDAL and numpy), then link the
plugin into your QGIS profile and enable **Weather Forecast** in Plugins > Manage and Install Plugins:

```sh
uv venv --system-site-packages && uv sync
ln -sfn "$PWD/qgis/wxforecast_qgis" ~/.local/share/QGIS/QGIS4/profiles/default/python/plugins/
```

Replace `default` with your profile (Settings > User Profiles). The venv must use the same Python minor
version as QGIS. The plugin finds the venv and `src/` through the symlink; for another layout, list the
paths, separated by `:`, in `WXFORECAST_SITE_PACKAGES`. Uninstall: delete the symlink.

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

Wind adds a group with the speed raster and animated flow lines (a GeoPackage of streamlines whose dashes
move downwind, coloured by speed). Direction is meteorological: where the wind blows from (westerly = 270).

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
