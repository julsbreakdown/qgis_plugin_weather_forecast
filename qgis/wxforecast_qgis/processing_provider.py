from collections.abc import Callable
from datetime import timedelta
from functools import partial
from pathlib import Path

import numpy as np
from osgeo import gdal
from qgis.core import (
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsProcessing,
    QgsProcessingAlgorithm,
    QgsProcessingContext,
    QgsProcessingException,
    QgsProcessingLayerPostProcessorInterface,
    QgsProcessingOutputLayerDefinition,
    QgsProcessingOutputMultipleLayers,
    QgsProcessingOutputVectorLayer,
    QgsProcessingParameterBoolean,
    QgsProcessingParameterExtent,
    QgsProcessingParameterFeatureSource,
    QgsProcessingParameterFileDestination,
    QgsProcessingParameterFolderDestination,
    QgsProcessingParameterNumber,
    QgsProcessingParameterRasterDestination,
    QgsProcessingParameterString,
    QgsProcessingProvider,
    QgsProcessingUtils,
    QgsProject,
    QgsProperty,
)

import wxforecast

from . import indicators, temporal
from .extent import to_bbox
from .i18n import tr
from .icons import icon, svg_path
from .style import ANIMATED, RANGE, apply_flow_style, apply_style, styles_path, variable_kind

WGS84 = QgsCoordinateReferenceSystem("EPSG:4326")
DEFAULT_MODEL = "noaa-gfs-forecast"
DEFAULT_VARIABLE = "temperature_2m"
MAX_LEAD_HOURS = 840
MAX_DAYS = 16
MAX_EVERY_HOURS = 240
WHOLE_WORLD = (-180.0, -90.0, 180.0, 90.0)
# Processing holds post-processors by raw pointer: keep each alive until it has run.
_STYLERS: set["_Styler"] = set()


class WxForecastProvider(QgsProcessingProvider):
    def id(self) -> str:
        return "wxforecast"

    def name(self) -> str:
        return tr("Weather forecast")

    def icon(self):
        return icon("forecast")

    def svgIconPath(self) -> str:
        return svg_path("forecast")

    def loadAlgorithms(self) -> None:
        self.addAlgorithm(FetchForecastAlgorithm())
        self.addAlgorithm(ExportPmtilesAlgorithm())


class _ForecastAlgorithm(QgsProcessingAlgorithm):
    def _add_forecast_parameters(self) -> None:
        self.addParameter(QgsProcessingParameterString("MODEL", tr("Model id"), DEFAULT_MODEL))
        self.addParameter(QgsProcessingParameterString("VARIABLE", tr("Variable"), DEFAULT_VARIABLE))
        self.addParameter(
            QgsProcessingParameterNumber(
                "LEAD_HOURS",
                tr(
                    "Map time: hours after the latest run "
                    "(empty: forecast nearest now; ignored with a time range)"
                ),
                QgsProcessingParameterNumber.Type.Integer,
                optional=True,
                minValue=0,
                maxValue=MAX_LEAD_HOURS,
            )
        )
        self.addParameter(
            QgsProcessingParameterNumber(
                "DAYS",
                tr(
                    "Time range: days ahead (0: single time, one map; else every step from now to now + days)"
                ),
                QgsProcessingParameterNumber.Type.Integer,
                0,
                minValue=0,
                maxValue=MAX_DAYS,
            )
        )
        self.addParameter(
            QgsProcessingParameterNumber(
                "EVERY_HOURS",
                tr("Step interval in hours, time range only (empty: all steps)"),
                QgsProcessingParameterNumber.Type.Integer,
                optional=True,
                minValue=1,
                maxValue=MAX_EVERY_HOURS,
            )
        )
        self.addParameter(QgsProcessingParameterExtent("EXTENT", tr("Extent"), optional=True))

    def _fetch(self, parameters, context, feedback, bbox, days: int) -> list:
        """The forecast steps of `days` of time range (0: one map at the map time)."""
        model = self.parameterAsString(parameters, "MODEL", context)
        variable = self.parameterAsString(parameters, "VARIABLE", context)
        feedback.pushInfo(
            tr("Fetching {variable} from {model}, bbox {bbox}").format(
                variable=variable, model=model, bbox=bbox or tr("whole world")
            )
        )
        try:
            if days:
                every = _hours(self, parameters, "EVERY_HOURS", context)
                forecasts = wxforecast.fetch_range(model, variable, bbox=bbox, days=days, every=every)
            else:
                lead = _hours(self, parameters, "LEAD_HOURS", context)
                forecasts = [wxforecast.fetch(model, variable, bbox=bbox, lead=lead)]
        except (OSError, ValueError) as exc:
            raise QgsProcessingException(str(exc)) from exc
        first, last = forecasts[0], forecasts[-1]
        feedback.pushInfo(
            tr(
                "Run {init:%Y-%m-%d %H:%M} UTC, {n} step(s) valid "
                "{first:%Y-%m-%d %H:%M} to {last:%Y-%m-%d %H:%M} UTC"
            ).format(init=first.init_time, n=len(forecasts), first=first.valid_time, last=last.valid_time)
        )
        return forecasts

    def _bbox(self, parameters, context):
        if not parameters.get("EXTENT"):
            return None
        extent = self.parameterAsExtent(parameters, "EXTENT", context)
        return to_bbox(extent, self.parameterAsExtentCrs(parameters, "EXTENT", context))


class FetchForecastAlgorithm(_ForecastAlgorithm):
    def icon(self):
        return icon("fetch")

    def svgIconPath(self) -> str:
        return svg_path("fetch")

    def name(self) -> str:
        return "fetchforecast"

    def displayName(self) -> str:
        return tr("Fetch forecast")

    def shortHelpString(self) -> str:
        return tr(
            "Fetches one forecast map from dynamical.org into a styled GeoTIFF. No extent: whole world. "
            "Time range (days > 0): one GeoTIFF per step and one VRT stacking them, loaded as a single "
            "layer that shows each step over its time range in the Temporal Controller. "
            "A wind variable (e.g. wind_10m) gives the speed raster, with a direction band, and a "
            "GeoPackage of animated flow lines next to it, both loaded in one group."
        )

    def createInstance(self):
        return FetchForecastAlgorithm()

    def initAlgorithm(self, config=None) -> None:
        self._add_forecast_parameters()
        self.addParameter(
            QgsProcessingParameterBoolean(
                "ANIMATE_FLOW",
                tr("Animated wind flow lines (moving dashes, redrawn continuously: may be slow)"),
                True,
            )
        )
        self.addParameter(QgsProcessingParameterRasterDestination("OUTPUT", tr("Forecast")))
        self.addOutput(QgsProcessingOutputVectorLayer("FLOW", tr("Wind flow lines")))
        self.addOutput(QgsProcessingOutputMultipleLayers("STEPS", tr("Forecast steps")))

    def processAlgorithm(self, parameters, context, feedback):
        days = self.parameterAsInt(parameters, "DAYS", context)
        forecasts = self._fetch(parameters, context, feedback, self._bbox(parameters, context), days)
        path = self.parameterAsOutputLayer(parameters, "OUTPUT", context)
        model = self.parameterAsString(parameters, "MODEL", context)
        forecast = forecasts[0]
        components = tuple(wxforecast.model(model).variables[forecast.variable].components)
        kind = variable_kind(forecast.variable, forecast.unit)
        animated = self.parameterAsBoolean(parameters, "ANIMATE_FLOW", context)
        if days:
            return _load_steps(forecasts, days, Path(path), context, (kind, components), animated)
        path = str(wxforecast.to_geotiff(forecast, path))
        name = f"{forecast.variable} {forecast.valid_time:%Y-%m-%d %H:%M} UTC"
        results = {"OUTPUT": path}
        if forecast.direction is not None:
            tif = Path(path)
            results["FLOW"] = str(wxforecast.to_flow_lines(forecast, tif.with_name(f"{tif.stem}_flow.gpkg")))
        if not context.willLoadLayerOnCompletion(path):
            return results
        details = context.layerToLoadOnCompletionDetails(path)
        tag = (forecast.variable, kind, components, name, forecast.unit)
        details.setPostProcessor(_Styler(False, tag))
        details.name = name
        if "FLOW" in results:
            details.name = f"speed ({forecast.unit})" if forecast.unit else "speed"
            details.groupName = name
            flow = QgsProcessingContext.LayerDetails("flow", context.project(), "FLOW")
            flow.layerTypeHint = QgsProcessingUtils.LayerHint.Vector
            flow.groupName = name
            flow.layerSortKey = details.layerSortKey + 1
            flow.setPostProcessor(_Styler(True, tag, partial(_set_animated, animated=animated)))
            context.addLayerToLoadOnCompletion(results["FLOW"], flow)
        return results


def _hours(algorithm, parameters, name: str, context) -> timedelta | None:
    if parameters.get(name) in (None, ""):
        return None
    return timedelta(hours=algorithm.parameterAsInt(parameters, name, context))


def _load_steps(
    forecasts: list, days: int, base: Path, context, kind_components: tuple, animated: bool
) -> dict:
    """One COG per step next to `base` and one VRT stacking their first band, loaded as a single layer
    showing each band over its step's time range; wind adds one flow GeoPackage of all steps."""
    first = forecasts[0]
    paths = [
        str(wxforecast.to_geotiff(f, base.with_name(f"{base.stem}_{wxforecast.step_name(f)}.tif")))
        for f in forecasts
    ]
    starts = [f.valid_time for f in forecasts]
    ends = temporal.step_ends(starts)
    vrt = str(base.with_suffix(".vrt"))
    stack = gdal.BuildVRT(vrt, paths, separate=True, bandList=[1])
    for band, start in enumerate(starts, 1):
        stack.GetRasterBand(band).SetDescription(f"{start:%Y-%m-%dT%H:%M}Z")
    stack = None
    results = {"OUTPUT": vrt, "STEPS": paths}
    if first.direction is not None:
        results["FLOW"] = str(wxforecast.to_flow_lines(forecasts, base.with_name(f"{base.stem}_flow.gpkg")))
    if not context.willLoadLayerOnCompletion(str(base)):
        return results
    kind, components = kind_components
    frame = min(end - start for start, end in zip(starts, ends, strict=True))
    low, high = wxforecast.colors.value_range(np.stack([f.values for f in forecasts]), kind, styles_path())
    name = f"{first.variable} {first.init_time:%Y-%m-%d %H:%M} UTC +{days} d"
    tag = (first.variable, kind, components, name, first.unit)

    def setup_raster(layer):
        layer.setCustomProperty(RANGE, f"{low!r},{high!r}")
        temporal.set_raster_steps(layer, starts, ends)
        temporal.show_in_controller(starts[0], ends[-1], frame)

    details = QgsProcessingContext.LayerDetails(name, context.project(), "OUTPUT")
    details.setPostProcessor(_Styler(False, tag, setup_raster))
    loads = {vrt: details}
    if "FLOW" in results:
        details.name = f"speed ({first.unit})" if first.unit else "speed"
        details.groupName = name
        flow = QgsProcessingContext.LayerDetails("flow", context.project(), "FLOW")
        flow.layerTypeHint = QgsProcessingUtils.LayerHint.Vector
        flow.groupName = name
        flow.layerSortKey = details.layerSortKey + 1
        flow.setPostProcessor(_Styler(True, tag, partial(_set_animated, animated=animated, steps=True)))
        loads[results["FLOW"]] = flow
    context.setLayersToLoadOnCompletion(loads)
    return results


def _set_animated(layer, animated: bool, steps: bool = False) -> None:
    layer.setCustomProperty(ANIMATED, animated)
    if steps:
        temporal.set_flow_steps(layer)


def _tag(layer, variable: str, kind: str, components: tuple[str, ...], group_name: str, unit: str) -> None:
    """Tag a loaded layer; a calculated variable also tags the group Processing created for it."""
    indicators.tag(layer, variable, kind, components, unit)
    project = layer.project() or QgsProject.instance()
    node = project.layerTreeRoot().findLayer(layer.id())
    group = node.parent() if node is not None else None
    if components and group is not None and group.parent() is not None and group.name() == group_name:
        indicators.tag(group, variable, kind, components, unit)
    indicators.refresh()


class _Styler(QgsProcessingLayerPostProcessorInterface):
    """Styles and tags a loaded output: the flow lines if `flow`, else the forecast raster; `setup(layer)`
    runs first (time range, shared value range)."""

    def __init__(self, flow: bool, tag: tuple, setup: Callable | None = None):
        super().__init__()
        self.flow = flow
        self.tag = tag
        self.setup = setup
        _STYLERS.add(self)

    def postProcessLayer(self, layer, context: QgsProcessingContext, feedback) -> None:
        variable, kind, components, name, unit = self.tag
        if self.setup is not None:
            self.setup(layer)
        if self.flow:
            apply_flow_style(layer, unit)
        else:
            apply_style(layer, variable, unit)
        _tag(layer, variable, kind, components, name, unit)
        _STYLERS.discard(self)


class ExportPmtilesAlgorithm(_ForecastAlgorithm):
    def icon(self):
        return icon("pmtiles")

    def svgIconPath(self) -> str:
        return svg_path("pmtiles")

    def name(self) -> str:
        return "exportpmtiles"

    def displayName(self) -> str:
        return tr("Export forecast to PMTiles")

    def shortHelpString(self) -> str:
        return tr(
            "Fetches one forecast map and writes coloured Web Mercator PNG tiles into a PMTiles archive "
            "(days > 0: one archive per step plus index.json in the folder, one shared value range), "
            "over an extent, polygons, or both. With 'Clip to polygons', pixels outside are transparent. "
            "The legend (min, max, colours) is in the archive metadata under 'wxforecast'. "
            "A wind variable (e.g. wind_10m) is coloured by speed with arrows drawn where the wind blows to."
        )

    def createInstance(self):
        return ExportPmtilesAlgorithm()

    def initAlgorithm(self, config=None) -> None:
        self._add_forecast_parameters()
        self.addParameter(
            QgsProcessingParameterFeatureSource(
                "POLYGONS", tr("Polygons"), [QgsProcessing.SourceType.TypeVectorPolygon], optional=True
            )
        )
        self.addParameter(
            QgsProcessingParameterNumber(
                "MIN_ZOOM",
                tr("Min zoom"),
                QgsProcessingParameterNumber.Type.Integer,
                0,
                minValue=0,
                maxValue=12,
            )
        )
        self.addParameter(
            QgsProcessingParameterNumber(
                "MAX_ZOOM",
                tr("Max zoom (empty: from the model grid)"),
                QgsProcessingParameterNumber.Type.Integer,
                optional=True,
                minValue=0,
                maxValue=12,
            )
        )
        self.addParameter(QgsProcessingParameterBoolean("CLIP", tr("Clip to polygons"), True))
        self.addParameter(
            QgsProcessingParameterFileDestination(
                "OUTPUT", tr("PMTiles (days = 0)"), "PMTiles (*.pmtiles)", QgsProcessing.TEMPORARY_OUTPUT
            )
        )
        self.addParameter(
            QgsProcessingParameterFolderDestination(
                "OUTPUT_FOLDER", tr("PMTiles folder, one per step (days > 0)"), QgsProcessing.TEMPORARY_OUTPUT
            )
        )

    def processAlgorithm(self, parameters, context, feedback):
        areas = []
        bbox = self._bbox(parameters, context)
        try:
            if parameters.get("EXTENT"):
                areas.append(wxforecast.bbox_wkt(bbox or WHOLE_WORLD))
            source = self.parameterAsSource(parameters, "POLYGONS", context)
            if source is not None:
                transform = QgsCoordinateTransform(source.sourceCrs(), WGS84, context.transformContext())
                for feature in source.getFeatures():
                    geometry = feature.geometry()
                    if not geometry.isEmpty():
                        geometry.transform(transform)
                        areas.append(geometry.asWkt())
            if not areas:
                raise QgsProcessingException(tr("Give an extent, polygons, or both."))
            feedback.pushInfo(tr("{n} area(s)").format(n=len(areas)))
            days = self.parameterAsInt(parameters, "DAYS", context)
            output, unused = ("OUTPUT_FOLDER", "OUTPUT") if days else ("OUTPUT", "OUTPUT_FOLDER")
            if _has_path(parameters, unused):
                raise QgsProcessingException(
                    tr(
                        "A time range (days > 0) writes one PMTiles per step: "
                        "set the PMTiles folder, not the file."
                    )
                    if days
                    else tr("One map (days = 0) writes one PMTiles: set the PMTiles file, not the folder.")
                )
            destination = self.parameterAsFileOutput(parameters, output, context)
            min_zoom = self.parameterAsInt(parameters, "MIN_ZOOM", context)
            max_zoom = None
            if parameters.get("MAX_ZOOM") not in (None, ""):
                max_zoom = self.parameterAsInt(parameters, "MAX_ZOOM", context)
            # Max zoom empty: the grid's zoom is unknown before the fetch, so min zoom is the lower bound.
            lowest, name = (min_zoom, "min") if max_zoom is None else (max_zoom, "max")
            count = wxforecast.estimate_tiles(areas, min_zoom, lowest, hint=f"lower the {name} zoom")
            if max_zoom is not None:
                note = (
                    tr("About {count} tiles per step; the limit covers all steps, checked after the fetch")
                    if days
                    else tr("About {count} tiles")
                )
                feedback.pushInfo(note.format(count=count))
            forecasts = self._fetch(parameters, context, feedback, wxforecast.areas_bbox(areas), days)
            write = wxforecast.to_pmtiles_steps if days else wxforecast.to_pmtiles
            path = write(
                forecasts if days else forecasts[0],
                destination,
                areas,
                min_zoom,
                max_zoom,
                clip=self.parameterAsBool(parameters, "CLIP", context),
                styles=styles_path(),
                progress=lambda done, total: _progress(feedback, done, total),
            )
        except wxforecast.Cancelled:
            return {}
        except ValueError as exc:
            raise QgsProcessingException(str(exc)) from exc
        feedback.pushInfo(tr("Wrote {path}").format(path=path))
        return {output: str(path.parent if days else path)}


def _has_path(parameters, name: str) -> bool:
    value = parameters.get(name)
    if isinstance(value, QgsProcessingOutputLayerDefinition):
        value = value.sink
    if isinstance(value, QgsProperty):
        value = value.staticValue()
    return value not in (None, "", QgsProcessing.TEMPORARY_OUTPUT)


def _progress(feedback, done: int, total: int) -> bool:
    feedback.setProgress(100 * done / total)
    return not feedback.isCanceled()
