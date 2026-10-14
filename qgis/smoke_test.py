"""Headless smoke test, run from the project root:
QT_QPA_PLATFORM=offscreen PYTHONPATH=/usr/share/qgis/python python3 qgis/smoke_test.py

Uses the real wxforecast library; only the catalog and the network read are faked.
"""

import json
import sys
import tempfile
import time
from datetime import UTC, datetime, timedelta
from itertools import pairwise
from pathlib import Path
from types import SimpleNamespace

import numpy as np
from osgeo import gdal
from qgis.core import (
    Qgis,
    QgsApplication,
    QgsCoordinateReferenceSystem,
    QgsMapRendererSequentialJob,
    QgsMapSettings,
    QgsProcessingUtils,
    QgsRasterLayer,
    QgsRectangle,
    QgsSymbolLayer,
    QgsVectorLayer,
)
from qgis.PyQt.QtCore import QCoreApplication, QSize
from qgis.PyQt.QtGui import QColor

sys.path.insert(0, str(Path(__file__).parent))
sys.path.append("/usr/share/qgis/python/plugins")

app = QgsApplication([], False)
app.initQgis()

import processing  # noqa: E402
import wxforecast_qgis  # noqa: E402, F401  (puts the library on sys.path)
from processing.core.Processing import Processing  # noqa: E402
from wxforecast_qgis import indicators, temporal  # noqa: E402
from wxforecast_qgis.extent import to_bbox  # noqa: E402
from wxforecast_qgis.icons import kind_icon, kind_svg  # noqa: E402
from wxforecast_qgis.processing_provider import WxForecastProvider  # noqa: E402
from wxforecast_qgis.style import apply_style, variable_kind  # noqa: E402

import wxforecast  # noqa: E402

gdal.UseExceptions()
out = Path(tempfile.mkdtemp(prefix="wxforecast_smoke_"))
print("wxforecast from", wxforecast.__file__)


def dummy_raster(name: str, values: np.ndarray) -> QgsRasterLayer:
    path = out / f"{name}.tif"
    rows, cols = values.shape
    tif = gdal.GetDriverByName("GTiff").Create(str(path), cols, rows, 1, gdal.GDT_Float32)
    tif.SetGeoTransform((-180, 360 / values.shape[1], 0, 90, 0, -180 / values.shape[0]))
    tif.SetProjection("EPSG:4326")
    band = tif.GetRasterBand(1)
    band.SetNoDataValue(float("nan"))
    band.WriteArray(values)
    tif = None
    layer = QgsRasterLayer(str(path), name)
    assert layer.isValid(), path
    return layer


grid = np.linspace(-20, 35, 72 * 36, dtype="float32").reshape(36, 72)
grid[0, 0] = np.nan
cover = np.clip(np.abs(grid) * 3, 0, 100)
flags = (grid > 0).astype("float32")
ceiling = np.where(grid > 30, 20032.0, np.abs(grid) * 100).astype("float32")
styled = {}
for variable, unit, expected, values in [
    ("temperature_2m", "degree_Celsius", "temperature", grid),
    ("precipitation_surface", "kg m-2 s-1", "precipitation", np.abs(grid)),
    ("wind_u_10m", "m s-1", "wind_component", grid),
    ("wind_10m", "m s-1", "wind", np.abs(grid)),
    ("total_cloud_cover_atmosphere", "percent", "cloud", cover),
    ("categorical_snow_surface", "1", "snow_flag", flags),
    ("geopotential_height_cloud_ceiling", "m", "cloud_ceiling", ceiling),
    ("soil_moisture", "m3 m-3", "other", grid),
]:
    assert variable_kind(variable, unit) == expected
    assert Path(kind_svg(expected)).stem != "generic" or expected == "other"
    layer = dummy_raster(variable, values)
    assert apply_style(layer, variable, unit) == expected
    renderer = layer.renderer()
    items = renderer.shader().rasterShaderFunction().colorRampItemList()
    styled[expected] = layer
    print(
        f"{variable}: {renderer.type()} min {renderer.classificationMin():g} "
        f"max {renderer.classificationMax():g}, {len(items)} stops, "
        f"first color {items[0].color.name()} alpha {items[0].color.alpha()}, "
        f"labels {items[0].label!r}..{items[-1].label!r}"
    )


def pixel(layer, x, y):
    block = layer.renderer().block(1, layer.extent(), 72, 36)
    return QColor.fromRgba(block.color(y, x))


cloud = styled["cloud"]
assert cloud.renderer().classificationMin() == 0 and cloud.renderer().classificationMax() == 100
first = cloud.renderer().shader().rasterShaderFunction().colorRampItemList()[0]
assert (first.value, first.color.alpha()) == (0, 0)
clear_y, clear_x = np.unravel_index(np.nanargmin(cover), cover.shape)
assert cover[clear_y, clear_x] < 0.5 and pixel(cloud, clear_x, clear_y).alpha() < 3, "a clear sky is a hole"
overcast = pixel(cloud, 71, 35)
assert overcast.alpha() > 200 and overcast.red() == overcast.green() == overcast.blue(), overcast.name()
snow = styled["snow_flag"].renderer().shader().rasterShaderFunction()
assert snow.colorRampType() == Qgis.ShaderInterpolationMethod.Exact
assert [(i.value, i.color.alpha()) for i in snow.colorRampItemList()] == [(0, 0), (1, 210)]
temperature_items = styled["temperature"].renderer().shader().rasterShaderFunction().colorRampItemList()
assert "hPa" not in temperature_items[0].label
empty = dummy_raster("all_nan", np.full(grid.shape, np.nan, dtype="float32"))
assert apply_style(empty, "maximum_temperature_2m", "degree_Celsius") == "temperature"
assert (empty.renderer().classificationMin(), empty.renderer().classificationMax()) == (0, 1)
nodata = styled["cloud_ceiling"].dataProvider().userNoDataValues(1)
assert len(nodata) == 1 and nodata[0].min() == 19000, "ceiling fill values are user nodata"
assert pixel(styled["cloud_ceiling"], 71, 35).alpha() == 0

wgs84 = QgsCoordinateReferenceSystem("EPSG:4326")
mercator = QgsCoordinateReferenceSystem("EPSG:3857")
assert to_bbox(QgsRectangle(-10, 40, 10, 50), wgs84) == (-10, 40, 10, 50)
assert to_bbox(QgsRectangle(170, -10, 190, 10), wgs84) == (170, -10, -170, 10)
assert to_bbox(QgsRectangle(-400, -100, 400, 100), wgs84) is None
europe = to_bbox(QgsRectangle(-1113194.9, 4865942.3, 1113194.9, 6446275.8), mercator)
assert [round(v, 3) for v in europe] == [-10, 40, 10, 50], europe
fiji = to_bbox(QgsRectangle(1.9e7, -3e6, 2.1e7, -1e6), mercator)
assert round(fiji[0], 2) == 170.68 and round(fiji[2], 2) == -171.35, f"crossing 180 keeps its side: {fiji}"
wide = to_bbox(QgsRectangle(-2.1e7, -5e6, 0, 5e6), mercator)
assert round(wide[0], 2) == 171.35 and abs(wide[2]) < 1e-6, wide
polar = to_bbox(QgsRectangle(-3e6, -3e6, 3e6, 3e6), QgsCoordinateReferenceSystem("EPSG:3413"))
assert polar[0] == -180 and polar[2] == 180 and polar[3] == 90, polar
print("bboxes: 3857 Fiji", fiji, "| 3413 pole", polar)

RUN = datetime(2026, 10, 12, tzinfo=UTC)
fetches = []


def variable(name, unit, long_name, components=()):
    return wxforecast.catalog.Variable(name, unit, long_name, components)


variables = {
    "temperature_2m": variable("temperature_2m", "K", "2 m temperature"),
    "wind_10m": variable("wind_10m", "m s-1", "10 metre wind", ("wind_u_10m", "wind_v_10m")),
    "wind_u_10m": variable("wind_u_10m", "m s-1", "10 metre U wind component"),
    "wind_v_10m": variable("wind_v_10m", "m s-1", "10 metre V wind component"),
}
model = wxforecast.catalog.Model("m", "Model M", "https://example.invalid", (), variables)


def fake_fetch(model_id, variable, bbox=None, run=None, lead=None):
    fetches.append(bbox)
    if variable == "broken":
        raise ValueError("no such thing")
    lat = np.arange(60, 30, -0.5)
    shape = (len(lat), 80)
    values = np.tile(lat[:, None], (1, shape[1])).astype(np.float32)
    direction = None
    if variable == "wind_10m":
        values, direction = wxforecast.wind.speed_direction(np.full(shape, 8.0), np.full(shape, 3.0))
    return wxforecast.Forecast(
        model=model_id,
        variable=variable,
        unit=variables[variable].unit,
        init_time=RUN,
        valid_time=RUN + (lead or timedelta(hours=6)),
        values=values,
        geotransform=(-20.25, 0.5, 0.0, 60.25, 0.0, -0.5),
        direction=direction,
    )


wxforecast.fetch = fake_fetch
wxforecast.list_models = lambda: ["m"]
wxforecast.model = lambda model_id: model

Processing.initialize()
provider = WxForecastProvider()
QgsApplication.processingRegistry().addProvider(provider)


def wait(condition, seconds=20):
    started = time.time()
    while not condition() and time.time() - started < seconds:
        QCoreApplication.processEvents()
        time.sleep(0.01)
    QCoreApplication.processEvents()
    assert condition(), "timed out"


from qgis.core import QgsLayerTreeModel  # noqa: E402
from qgis.gui import QgsLayerTreeView, QgsMapCanvas, QgsMessageBar  # noqa: E402
from qgis.PyQt.QtCore import Qt  # noqa: E402
from qgis.PyQt.QtWidgets import QDockWidget, QMainWindow  # noqa: E402
from wxforecast_qgis import dock as dock_module  # noqa: E402

window = QMainWindow()
bar, canvas = QgsMessageBar(window), QgsMapCanvas(window)
canvas.setDestinationCrs(wgs84)
canvas.setExtent(QgsRectangle(-10, 40, 10, 50))
project = dock_module.QgsProject.instance()
tree_model = QgsLayerTreeModel(project.layerTreeRoot())
view = QgsLayerTreeView(window)
view.setModel(tree_model)
iface = SimpleNamespace(
    mainWindow=lambda: window, messageBar=lambda: bar, mapCanvas=lambda: canvas, layerTreeView=lambda: view
)
tree_indicators = indicators.Indicators(view, project)

dock = dock_module.ForecastDock(iface)
wait(lambda: dock.catalog_task is None)
assert dock.model_combo.currentText() == "Model M", dock.status.text()
assert dock.variable_combo.currentText() == "2 m temperature (K)"
reloads = []
wxforecast.catalog.cache_clear = lambda: reloads.append(1)
dock.reload_button.click()
wait(lambda: dock.catalog_task is None)
assert reloads == [1] and dock.model_combo.count() == 1, "reload clears the library catalog cache"


def run_dock_fetch():
    dock._fetch()
    wait(lambda: dock.job is None)


dock.lead_spin.setValue(3)
assert dock.extent_widget.outputExtent().isNull(), "an empty project defaults to the whole world"
fetches.clear()
run_dock_fetch()
dock.extent_widget.setOutputExtentFromUser(QgsRectangle(-1e6, 5e6, 1e6, 6e6), mercator)
run_dock_fetch()
added = [layer.name() for layer in project.mapLayers().values()]
print("dock:", dock.status.text(), "| bboxes:", fetches, "| layers:", added)
assert fetches[0] is None
assert [round(v, 2) for v in fetches[1]] == [-8.98, 40.92, 8.98, 47.35], fetches[1]
assert added == ["temperature_2m 2026-10-12 03:00 UTC"] * 2, added
raw_layer = next(iter(project.mapLayers().values()))
assert raw_layer.customProperty("wxforecast/origin") == "raw"
assert raw_layer.customProperty("wxforecast/kind") == "temperature"
assert raw_layer.renderer().type() == "singlebandpseudocolor"
assert raw_layer.source().startswith(QgsProcessingUtils.tempFolder()), "outputs in the Processing temp folder"

combo = dock.variable_combo
entries = [
    (combo.itemData(i), combo.itemData(i, Qt.ItemDataRole.ToolTipRole), combo.itemIcon(i))
    for i in range(combo.count())
]
assert [name for name, _, _ in entries] == ["temperature_2m", "wind_10m", "wind_u_10m", "wind_v_10m"]
assert all(not item_icon.isNull() and not item_icon.pixmap(24, 24).isNull() for _, _, item_icon in entries)
assert entries[1][1] == "wind_10m: calculated from wind_u_10m, wind_v_10m", entries[1][1]
assert [tip.endswith("raw, read as stored") for _, tip, _ in entries] == [True, False, True, True]
assert entries[1][2].availableSizes() and not entries[2][2].availableSizes(), "only calculated is badged"
assert entries[1][2].cacheKey() == kind_icon("wind", True).cacheKey()

combo.setCurrentIndex(1)
run_dock_fetch()
group = project.layerTreeRoot().findGroup("wind_10m 2026-10-12 03:00 UTC")
in_group = [node.layer().name() for node in group.findLayers()] if group else []
print("wind group:", group and group.name(), "| layers:", in_group)
assert sorted(in_group) == ["flow", "speed (m s-1)"], in_group
flow = next(node.layer() for node in group.findLayers() if node.layer().name() == "flow")


def flow_animated(layer) -> bool:
    ranges = layer.renderer().ranges()
    redraws = layer.autoRefreshMode() == Qgis.AutoRefreshMode.RedrawOnly and layer.autoRefreshInterval() > 0
    return redraws and bool(ranges) and all(r.symbol().animationSettings().isAnimated() for r in ranges)


renderer = flow.renderer()
legend = [(item.label(), item.symbol().color().name()) for item in renderer.legendSymbolItems()]
flow_ranges = renderer.ranges()
symbol = flow_ranges[0].symbol()
dash = symbol.symbolLayer(1)
offset = dash.dataDefinedProperties().property(QgsSymbolLayer.Property.DashPatternOffset).expressionString()
print("flow style:", renderer.type(), legend)
assert renderer.type() == "graduatedSymbol" and renderer.classAttribute() == "speed"
assert len(legend) == 7 and len({color for _, color in legend}) == 7, legend
assert legend[0][1] == "#7fdbff" and legend[-1][1] == "#ff4136", legend
assert [color for _, color in legend] == [c for _, _, c, _ in wxforecast.colors.flow_classes(1.0, "")]
assert flow_animated(flow) and "@symbol_frame" in offset
assert symbol.symbolLayer(0).color().alpha() == 70


def flow_frame(frame: int) -> bytes:
    settings = QgsMapSettings()
    settings.setLayers([flow])
    settings.setDestinationCrs(wgs84)
    settings.setExtent(QgsRectangle(-20, 30, 20, 60))
    settings.setOutputSize(QSize(240, 160))
    settings.setFrameRate(20)
    settings.setCurrentFrame(frame)
    job = QgsMapRendererSequentialJob(settings)
    job.start()
    job.waitForFinished()
    image = job.renderedImage()
    return bytes(image.constBits().asarray(image.sizeInBytes()))


frames = [flow_frame(0), flow_frame(10), flow_frame(10)]
assert frames[0] != frames[1] and frames[1] == frames[2], "dashes move between frames"
print("flow animation: frames 0 and 10 differ")


def check_tags(group, label):
    nodes = [group, *group.findLayers()]
    tips = [[i.toolTip() for i in view.indicators(node)] for node in nodes]
    print(f"{label} indicators:", tips)
    assert all(t == ["wind_10m: calculated from wind_u_10m, wind_v_10m"] for t in tips), tips
    for node in group.findLayers():
        layer = node.layer()
        assert layer.customProperty("wxforecast/origin") == "calculated"
        assert layer.customProperty("wxforecast/kind") == "wind"
        assert layer.customProperty("wxforecast/components") == "wind_u_10m,wind_v_10m"
    assert group.customProperty("wxforecast/origin") == "calculated"


check_tags(group, "dock")
raw_node = project.layerTreeRoot().findLayer(raw_layer.id())
assert [i.toolTip() for i in view.indicators(raw_node)] == ["temperature_2m: raw, read as stored"]
tree_indicators.refresh()
assert len(view.indicators(group)) == 1, "a rescan never duplicates an indicator"

variables["broken"] = variable("broken", "", "broken")
combo.addItem("broken", "broken")
combo.setCurrentIndex(combo.count() - 1)
run_dock_fetch()
print("failing fetch:", dock.status.text())
assert "failed" in dock.status.text() and "no such thing" in dock.status.text()

saved = out / "reload.qgz"
assert project.write(str(saved))
project.clear()
assert project.read(str(saved))
group = project.layerTreeRoot().findGroup("wind_10m 2026-10-12 03:00 UTC")
check_tags(group, "reloaded")

temperature = next(layer for layer in project.mapLayers().values() if layer.name().startswith("temperature"))


def first_color(layer) -> str:
    return layer.renderer().shader().rasterShaderFunction().colorRampItemList()[0].color.name()


assert first_color(temperature) == "#2166ac"
override = out / "styles.toml"
override.write_text('[temperature]\nicon = "snow"\ncolors = ["#00ff00", "#0000ff"]\n')
dock.styles_widget.setFilePath(str(override))
print("styles override:", dock.status.text())
assert first_color(temperature) == "#00ff00" and kind_svg("temperature").endswith("snow.svg")
assert dock_module.styles_path() == str(override)
bad = out / "bad.toml"
bad.write_text('[cloud]\ncolors = ["white", "#000000"]\n')
dock.styles_widget.setFilePath(str(bad))
print("bad styles:", dock.status.text())
assert "bad colour 'white'" in dock.status.text() and dock_module.styles_path() == str(override)
dock.styles_widget.setFilePath("")
assert dock_module.styles_path() is None and first_color(temperature) == "#2166ac"
assert kind_svg("temperature").endswith("thermometer.svg")

project.removeAllMapLayers()
project.layerTreeRoot().removeAllChildren()
result = processing.runAndLoadResults(
    "wxforecast:fetchforecast", {"MODEL": "m", "VARIABLE": "wind_10m", "OUTPUT": str(out / "proc_wind.tif")}
)
group = project.layerTreeRoot().findGroup("wind_10m 2026-10-12 06:00 UTC")
loaded = [node.layer().name() for node in group.findLayers()] if group else []
print("processing:", sorted(result), "| group layers:", loaded)
assert result["FLOW"].endswith("proc_wind_flow.gpkg"), result
assert loaded == ["flow", "speed (m s-1)"], loaded
assert flow_animated(group.findLayers()[0].layer())
check_tags(group, "processing")

steps = []
result = processing.run(
    "wxforecast:exportpmtiles",
    {
        "MODEL": "m",
        "VARIABLE": "temperature_2m",
        "EXTENT": "-5,5,40,50 [EPSG:4326]",
        "POLYGONS": QgsVectorLayer("Polygon?crs=EPSG:4326", "none", "memory"),
        "MAX_ZOOM": 3,
        "OUTPUT": str(out / "t.pmtiles"),
    },
)
assert Path(result["OUTPUT"]).stat().st_size > 0, result
bowtie = QgsVectorLayer("Polygon?crs=EPSG:4326", "bowtie", "memory")
import qgis.utils  # noqa: E402
from qgis.core import (  # noqa: E402
    QgsFeature,
    QgsFeatureRequest,
    QgsGeometry,
    QgsProcessingContext,
    QgsProcessingException,
    QgsTemporalNavigationObject,
    QgsVectorLayerTemporalContext,
)

feature = QgsFeature()
feature.setGeometry(QgsGeometry.fromWkt("POLYGON((0 40,10 50,10 40,0 50,0 40))"))
bowtie.dataProvider().addFeatures([feature])
bowtie_parameters = {"MODEL": "m", "POLYGONS": bowtie, "MAX_ZOOM": 3, "OUTPUT": str(out / "b.pmtiles")}
try:
    processing.run("wxforecast:exportpmtiles", bowtie_parameters)
    raise AssertionError("Processing's own invalid geometry check should stop it")
except QgsProcessingException as exc:
    print("invalid polygon, default check:", str(exc)[:60])
unchecked = QgsProcessingContext()
unchecked.setInvalidGeometryCheck(QgsFeatureRequest.InvalidGeometryCheck.GeometryNoCheck)
result = processing.run("wxforecast:exportpmtiles", bowtie_parameters, context=unchecked)
print("invalid polygon, no check: made valid by the library,", Path(result["OUTPUT"]).name)

STEP_HOURS = [3, 4, 5, 6, 9]


def fake_fetch_range(model_id, variable, bbox=None, days=1.0, every=None, run=None):
    forecasts = []
    for h in STEP_HOURS:
        f = fake_fetch(model_id, variable, bbox, lead=timedelta(hours=h))
        forecasts.append(wxforecast.Forecast(**{**f.__dict__, "values": f.values + h}))
    return forecasts


wxforecast.fetch_range = fake_fetch_range
temporal_dock = QDockWidget("Temporal Controller", window)
temporal_dock.setObjectName("Temporal Controller")
temporal_dock.hide()
navigation = QgsTemporalNavigationObject(canvas)
canvas.setTemporalController(navigation)
qgis.utils.iface = iface


def step_group(name):
    group = project.layerTreeRoot().findGroup(name)
    assert group is not None, [g.name() for g in project.layerTreeRoot().findGroups()]
    return group, [node.layer() for node in group.findLayers()]


processing.runAndLoadResults(
    "wxforecast:fetchforecast",
    {"MODEL": "m", "VARIABLE": "temperature_2m", "DAYS": 1, "OUTPUT": str(out / "steps.tif")},
)
group, layers = step_group("temperature_2m 2026-10-12 00:00 UTC +1 d")
rasters = sorted(layers, key=lambda layer: layer.temporalProperties().fixedTemporalRange().begin())
ranges = [layer.temporalProperties().fixedTemporalRange() for layer in rasters]
print("steps:", [layer.name() for layer in rasters])
assert len(rasters) == len(STEP_HOURS) and all(layer.temporalProperties().isActive() for layer in rasters)
assert all(r.includeBeginning() and not r.includeEnd() for r in ranges)
assert all(a.end() == b.begin() for a, b in pairwise(ranges)), "contiguous, no overlap"
assert [r.begin().toSecsSinceEpoch() for r in ranges] == [
    int((RUN + timedelta(hours=h)).timestamp()) for h in STEP_HOURS
]
assert ranges[-1].end().toSecsSinceEpoch() - ranges[-1].begin().toSecsSinceEpoch() == 3 * 3600
limits = {(layer.renderer().classificationMin(), layer.renderer().classificationMax()) for layer in rasters}
assert len(limits) == 1, f"one shared range: {limits}"
assert all(Path(layer.source()).name.startswith("steps_temperature_2m_2026101") for layer in rasters)
extent = navigation.temporalExtents()
assert extent.begin() == ranges[0].begin() and extent.end() == ranges[-1].end(), extent
assert navigation.frameDuration().hours() == 1 and not temporal_dock.isHidden()
print("temporal controller:", extent.begin().toString(), "to", extent.end().toString(), "by 1 h")

project.layerTreeRoot().removeAllChildren()
processing.runAndLoadResults(
    "wxforecast:fetchforecast",
    {"MODEL": "m", "VARIABLE": "wind_10m", "DAYS": 1, "OUTPUT": str(out / "w.tif")},
)
group, layers = step_group("wind_10m 2026-10-12 00:00 UTC +1 d")
flow = next(layer for layer in layers if layer.name() == "flow")
assert layers[0].name() == "flow" and len(layers) == len(STEP_HOURS) + 1
assert flow.temporalProperties().isActive() and flow_animated(flow)
check_tags(group, "time steps")
counts = []
for begin_h, end_h in ((3, 4), (6, 9), (9, 12), (9.5, 10)):
    span = temporal.time_range(RUN + timedelta(hours=begin_h), RUN + timedelta(hours=end_h))
    context = QgsVectorLayerTemporalContext()
    context.setLayer(flow)
    expression = flow.temporalProperties().createFilterString(context, span)
    counts.append(sum(1 for _ in flow.getFeatures(QgsFeatureRequest().setFilterExpression(expression))))
per_step = flow.featureCount() // len(STEP_HOURS)
assert counts == [per_step] * 4, (counts, per_step)


def temporal_frame(frame: int, hour: float) -> bytes:
    settings = QgsMapSettings()
    settings.setLayers([flow])
    settings.setDestinationCrs(wgs84)
    settings.setExtent(QgsRectangle(-20, 30, 20, 60))
    settings.setOutputSize(QSize(240, 160))
    settings.setIsTemporal(True)
    settings.setTemporalRange(
        temporal.time_range(RUN + timedelta(hours=hour), RUN + timedelta(hours=hour + 1))
    )
    settings.setFrameRate(20)
    settings.setCurrentFrame(frame)
    job = QgsMapRendererSequentialJob(settings)
    job.start()
    job.waitForFinished()
    image = job.renderedImage()
    return bytes(image.constBits().asarray(image.sizeInBytes()))


assert temporal_frame(0, 3) != temporal_frame(10, 3), "dashes still move in a fixed temporal range"
assert temporal_frame(0, 30) == temporal_frame(10, 30), "no line outside the steps"
print("flow per step:", per_step, "lines; animation runs under the temporal controller")

result = processing.run(
    "wxforecast:exportpmtiles",
    {
        "MODEL": "m",
        "VARIABLE": "temperature_2m",
        "EXTENT": "-5,5,40,50 [EPSG:4326]",
        "DAYS": 1,
        "MAX_ZOOM": 2,
        "OUTPUT_FOLDER": str(out / "pm_steps"),
    },
)
index = json.loads((Path(result["OUTPUT_FOLDER"]) / "index.json").read_text())
assert [step["lead_hours"] for step in index["steps"]] == STEP_HOURS
print("pmtiles steps:", sorted(p.name for p in Path(result["OUTPUT_FOLDER"]).iterdir()))

project.layerTreeRoot().removeAllChildren()
combo.setCurrentIndex(combo.findData("temperature_2m"))
dock.days_spin.setValue(1)
dock.every_spin.setValue(3)
run_dock_fetch()
group, layers = step_group("temperature_2m 2026-10-12 00:00 UTC +1 d")
assert len(layers) == len(STEP_HOURS) and all(layer.temporalProperties().isActive() for layer in layers)
print("dock days ahead:", dock.status.text(), len(layers), "step layers")

tree_indicators.unload()
assert not view.indicators(group)
dock.unload()

project.addMapLayer(QgsVectorLayer("Point?crs=EPSG:4326", "basemap", "memory"))
assert not dock_module.ForecastDock(iface).extent_widget.outputExtent().isNull(), "defaults to the canvas"

app.exitQgis()
print("OK")
