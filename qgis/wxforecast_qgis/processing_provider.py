from collections.abc import Callable
from datetime import timedelta
from functools import partial
from pathlib import Path

import numpy as np
from qgis.core import (
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsProcessing,
    QgsProcessingAlgorithm,
    QgsProcessingContext,
    QgsProcessingException,
    QgsProcessingLayerPostProcessorInterface,
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
)

import wxforecast

from . import indicators, temporal
from .extent import to_bbox
from .icons import icon, svg_path
from .style import RANGE, apply_flow_style, apply_style, styles_path, variable_kind

WGS84 = QgsCoordinateReferenceSystem("EPSG:4326")
DEFAULT_MODEL = "noaa-gfs-forecast"
DEFAULT_VARIABLE = "temperature_2m"
# Processing holds post-processors by raw pointer: keep each alive until it has run.
_STYLERS: set["_Styler"] = set()


class WxForecastProvider(QgsProcessingProvider):
    def id(self) -> str:
        return "wxforecast"

    def name(self) -> str:
        return "Weather forecast"

    def icon(self):
        return icon("forecast")

    def svgIconPath(self) -> str:
        return svg_path("forecast")

    def loadAlgorithms(self) -> None:
        self.addAlgorithm(FetchForecastAlgorithm())
        self.addAlgorithm(ExportPmtilesAlgorithm())


class _ForecastAlgorithm(QgsProcessingAlgorithm):
    def _add_forecast_parameters(self) -> None:
        self.addParameter(QgsProcessingParameterString("MODEL", "Model id", DEFAULT_MODEL))
        self.addParameter(QgsProcessingParameterString("VARIABLE", "Variable", DEFAULT_VARIABLE))
        self.addParameter(
            QgsProcessingParameterNumber(
                "LEAD_HOURS",
                "Lead time in hours after the latest run (empty: valid time nearest now)",
                QgsProcessingParameterNumber.Type.Integer,
                optional=True,
                minValue=0,
                maxValue=840,
            )
        )
        self.addParameter(
            QgsProcessingParameterNumber(
                "DAYS",
                "Days ahead (0: one step; else every step from now, lead ignored)",
                QgsProcessingParameterNumber.Type.Integer,
                0,
                minValue=0,
                maxValue=16,
            )
        )
        self.addParameter(
            QgsProcessingParameterNumber(
                "EVERY_HOURS",
                "Every (hours) between kept steps (empty: all steps)",
                QgsProcessingParameterNumber.Type.Integer,
                optional=True,
                minValue=1,
                maxValue=240,
            )
        )
        self.addParameter(QgsProcessingParameterExtent("EXTENT", "Extent", optional=True))

    def _fetch(self, parameters, context, feedback, bbox) -> tuple[list, int]:
        """The forecast steps and the days asked (0: one step at the lead)."""
        model = self.parameterAsString(parameters, "MODEL", context)
        variable = self.parameterAsString(parameters, "VARIABLE", context)
        days = self.parameterAsInt(parameters, "DAYS", context)
        feedback.pushInfo(f"Fetching {variable} from {model}, bbox {bbox or 'whole world'}")
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
            f"Run {first.init_time:%Y-%m-%d %H:%M} UTC, {len(forecasts)} step(s) valid "
            f"{first.valid_time:%Y-%m-%d %H:%M} to {last.valid_time:%Y-%m-%d %H:%M} UTC"
        )
        return forecasts, days

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
        return "Fetch forecast"

    def shortHelpString(self) -> str:
        return (
            "Fetches one forecast map from dynamical.org into a styled GeoTIFF. No extent: whole world. "
            "Days ahead > 0: one GeoTIFF per step in one group, each with its time range for the Temporal "
            "Controller, all styled over one value range. "
            "A wind variable (e.g. wind_10m) gives the speed raster, with a direction band, and a "
            "GeoPackage of animated flow lines next to it, both loaded in one group."
        )

    def createInstance(self):
        return FetchForecastAlgorithm()

    def initAlgorithm(self, config=None) -> None:
        self._add_forecast_parameters()
        self.addParameter(QgsProcessingParameterRasterDestination("OUTPUT", "Forecast"))
        self.addOutput(QgsProcessingOutputVectorLayer("FLOW", "Wind flow lines"))
        self.addOutput(QgsProcessingOutputMultipleLayers("STEPS", "Forecast steps"))

    def processAlgorithm(self, parameters, context, feedback):
        forecasts, days = self._fetch(parameters, context, feedback, self._bbox(parameters, context))
        path = self.parameterAsOutputLayer(parameters, "OUTPUT", context)
        model = self.parameterAsString(parameters, "MODEL", context)
        forecast = forecasts[0]
        components = tuple(wxforecast.model(model).variables[forecast.variable].components)
        kind = variable_kind(forecast.variable, forecast.unit)
        if days:
            return _load_steps(forecasts, days, Path(path), context, (kind, components))
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
            flow.setPostProcessor(_Styler(True, tag))
            context.addLayerToLoadOnCompletion(results["FLOW"], flow)
        return results


def _hours(algorithm, parameters, name: str, context) -> timedelta | None:
    if parameters.get(name) in (None, ""):
        return None
    return timedelta(hours=algorithm.parameterAsInt(parameters, name, context))


def _load_steps(forecasts: list, days: int, base: Path, context, kind_components: tuple) -> dict:
    """One COG per step next to `base`, loaded in one group with each its time range and one value
    range; wind adds one flow GeoPackage of all steps."""
    first = forecasts[0]
    paths = [
        str(wxforecast.to_geotiff(f, base.with_name(f"{base.stem}_{wxforecast.step_name(f)}.tif")))
        for f in forecasts
    ]
    results = {"OUTPUT": paths[0], "STEPS": paths}
    if first.direction is not None:
        results["FLOW"] = str(wxforecast.to_flow_lines(forecasts, base.with_name(f"{base.stem}_flow.gpkg")))
    if not context.willLoadLayerOnCompletion(str(base)):
        return results
    kind, components = kind_components
    starts = [f.valid_time for f in forecasts]
    ends = temporal.step_ends(starts)
    frame = min(end - start for start, end in zip(starts, ends, strict=True))
    low, high = wxforecast.colors.value_range(np.stack([f.values for f in forecasts]), kind, styles_path())
    group = f"{first.variable} {first.init_time:%Y-%m-%d %H:%M} UTC +{days} d"
    tag = (first.variable, kind, components, group, first.unit)
    label = "speed" if first.direction is not None else first.variable

    def setup_raster(layer, start, end):
        layer.setCustomProperty(RANGE, f"{low!r},{high!r}")
        temporal.set_raster_step(layer, start, end)
        temporal.show_in_controller(starts[0], ends[-1], frame)

    loads = {}
    for k, (path, start, end) in enumerate(zip(paths, starts, ends, strict=True)):
        name = f"{label} {start:%Y-%m-%d %H:%M} UTC"
        details = QgsProcessingContext.LayerDetails(name, context.project(), "OUTPUT")
        details.groupName = group
        details.layerSortKey = len(paths) - k
        details.setPostProcessor(_Styler(False, tag, partial(setup_raster, start=start, end=end)))
        loads[path] = details
    if "FLOW" in results:
        flow = QgsProcessingContext.LayerDetails("flow", context.project(), "FLOW")
        flow.layerTypeHint = QgsProcessingUtils.LayerHint.Vector
        flow.groupName = group
        flow.layerSortKey = len(paths) + 1
        flow.setPostProcessor(_Styler(True, tag, partial(temporal.set_flow_steps, starts=starts, ends=ends)))
        loads[results["FLOW"]] = flow
    context.setLayersToLoadOnCompletion(loads)
    return results


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
        return "Export forecast to PMTiles"

    def shortHelpString(self) -> str:
        return (
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
                "POLYGONS", "Polygons", [QgsProcessing.SourceType.TypeVectorPolygon], optional=True
            )
        )
        self.addParameter(
            QgsProcessingParameterNumber(
                "MIN_ZOOM", "Min zoom", QgsProcessingParameterNumber.Type.Integer, 0, minValue=0, maxValue=12
            )
        )
        self.addParameter(
            QgsProcessingParameterNumber(
                "MAX_ZOOM",
                "Max zoom (empty: from the model grid)",
                QgsProcessingParameterNumber.Type.Integer,
                optional=True,
                minValue=0,
                maxValue=12,
            )
        )
        self.addParameter(QgsProcessingParameterBoolean("CLIP", "Clip to polygons", True))
        self.addParameter(
            QgsProcessingParameterFileDestination(
                "OUTPUT", "PMTiles (days = 0)", "PMTiles (*.pmtiles)", QgsProcessing.TEMPORARY_OUTPUT
            )
        )
        self.addParameter(
            QgsProcessingParameterFolderDestination(
                "OUTPUT_FOLDER", "PMTiles folder, one per step (days > 0)", QgsProcessing.TEMPORARY_OUTPUT
            )
        )

    def processAlgorithm(self, parameters, context, feedback):
        areas = []
        bbox = self._bbox(parameters, context)
        try:
            if bbox:
                areas.append(wxforecast.bbox_wkt(bbox))
            source = self.parameterAsSource(parameters, "POLYGONS", context)
            if source is not None:
                transform = QgsCoordinateTransform(source.sourceCrs(), WGS84, context.transformContext())
                for feature in source.getFeatures():
                    geometry = feature.geometry()
                    if not geometry.isEmpty():
                        geometry.transform(transform)
                        areas.append(geometry.asWkt())
            if not areas:
                raise QgsProcessingException("Give an extent, polygons, or both.")
            feedback.pushInfo(f"{len(areas)} area(s)")
            forecasts, days = self._fetch(parameters, context, feedback, wxforecast.areas_bbox(areas))
            if days:
                write, output, target = wxforecast.to_pmtiles_steps, "OUTPUT_FOLDER", forecasts
                destination = self.parameterAsFileOutput(parameters, "OUTPUT_FOLDER", context)
            else:
                write, output, target = wxforecast.to_pmtiles, "OUTPUT", forecasts[0]
                destination = self.parameterAsFileOutput(parameters, "OUTPUT", context)
            path = write(
                target,
                destination,
                areas,
                self.parameterAsInt(parameters, "MIN_ZOOM", context),
                None
                if parameters.get("MAX_ZOOM") in (None, "")
                else self.parameterAsInt(parameters, "MAX_ZOOM", context),
                clip=self.parameterAsBool(parameters, "CLIP", context),
                styles=styles_path(),
                progress=lambda done, total: _progress(feedback, done, total),
            )
        except wxforecast.Cancelled:
            return {}
        except ValueError as exc:
            raise QgsProcessingException(str(exc)) from exc
        feedback.pushInfo(f"Wrote {path}")
        return {output: str(path.parent if days else path)}


def _progress(feedback, done: int, total: int) -> bool:
    feedback.setProgress(100 * done / total)
    return not feedback.isCanceled()
