from qgis.core import (
    Qgis,
    QgsBilinearRasterResampler,
    QgsClassificationEqualInterval,
    QgsColorRampShader,
    QgsGradientColorRamp,
    QgsGradientStop,
    QgsGraduatedSymbolRenderer,
    QgsLineSymbol,
    QgsProperty,
    QgsRasterLayer,
    QgsRasterRange,
    QgsRasterShader,
    QgsRendererRange,
    QgsSettings,
    QgsSimpleLineSymbolLayer,
    QgsSingleBandPseudoColorRenderer,
    QgsSymbolAnimationSettings,
    QgsSymbolLayer,
    QgsVectorLayer,
)
from qgis.PyQt.QtGui import QColor

from wxforecast import colors

STYLES_SETTING = "wxforecast/styles"
RANGE = "wxforecast/range"


def styles_path() -> str | None:
    """The style override file of the QGIS setting; None: $WXFORECAST_STYLES, else the defaults."""
    return QgsSettings().value(STYLES_SETTING, "", type=str) or None


def save_styles_path(path: str) -> None:
    QgsSettings().setValue(STYLES_SETTING, path.strip())


def styles() -> dict:
    return colors.load_styles(styles_path())


def variable_kind(variable: str, unit: str) -> str:
    return colors.variable_kind(variable, unit, styles_path())


def _color(stop) -> QColor:
    color = QColor(stop.color)
    color.setAlpha(stop.alpha)
    return color


def _ramp(style) -> QgsGradientColorRamp:
    return QgsGradientColorRamp(
        _color(style.stops[0]),
        _color(style.stops[-1]),
        style.discrete,
        [QgsGradientStop(stop.position, _color(stop)) for stop in style.stops[1:-1]],
    )


def _nodata_ranges(valid: tuple[float | None, float | None]) -> list[QgsRasterRange]:
    """Fill values outside the valid range, as user nodata ranges (NaN bound = unbounded)."""
    low, high = valid
    ranges = []
    if low is not None:
        ranges.append(QgsRasterRange(float("nan"), low, QgsRasterRange.BoundsType.IncludeMin))
    if high is not None:
        ranges.append(QgsRasterRange(high, float("nan"), QgsRasterRange.BoundsType.IncludeMax))
    return ranges


def apply_style(layer: QgsRasterLayer, variable: str, unit: str) -> str:
    """Pseudocolor renderer from the library style of the variable's kind; returns the kind.

    The value range is the layer's custom property RANGE ("low,high", shared by the steps of a time
    series) if set, else the kind's range over the layer's own min and max.
    """
    path = styles_path()
    kind = colors.variable_kind(variable, unit, path)
    style = colors.load_styles(path)[kind]
    provider = layer.dataProvider()
    provider.setUserNoDataValue(1, _nodata_ranges(style.valid))
    shared = layer.customProperty(RANGE)
    if shared:
        low, high = (float(v) for v in str(shared).split(","))
    else:
        stats = provider.bandStatistics(1, Qgis.RasterBandStatistic.Min | Qgis.RasterBandStatistic.Max)
        low, high = colors.limits(kind, stats.minimumValue, stats.maximumValue, path)
    items = [
        QgsColorRampShader.ColorRampItem(value, _color(stop), label)
        for value, stop, label in colors.ramp(kind, low, high, unit, path)
    ]
    function = QgsColorRampShader(low, high)
    function.setColorRampType(
        Qgis.ShaderInterpolationMethod.Exact if style.discrete else Qgis.ShaderInterpolationMethod.Linear
    )
    function.setColorRampItemList(items)
    function.setSourceColorRamp(_ramp(style))
    shader = QgsRasterShader(low, high)
    shader.setRasterShaderFunction(function)
    renderer = QgsSingleBandPseudoColorRenderer(provider, 1, shader)
    renderer.setClassificationMin(low)
    renderer.setClassificationMax(high)
    layer.setRenderer(renderer)
    if not style.discrete and style.valid == (None, None):
        # Provider bilinear resampling still drew coarse grids as squares (QGIS 4.2).
        layer.resampleFilter().setZoomedInResampler(QgsBilinearRasterResampler())
    layer.triggerRepaint()
    return kind


FLOW_TRAIL_ALPHA = 70
FLOW_DASH_MM = (7.0, 7.0)


def _flow_symbol(color: QColor, high: float) -> QgsLineSymbol:
    """A faint trail and dashes moving downwind, paced by speed, in one class colour."""
    trail_color = QColor(color)
    trail_color.setAlpha(FLOW_TRAIL_ALPHA)
    trail = QgsSimpleLineSymbolLayer(trail_color, 0.2)
    dash = QgsSimpleLineSymbolLayer(QColor(color), 0.45)
    dash.setUseCustomDashPattern(True)
    dash.setCustomDashVector(list(FLOW_DASH_MM))
    period = sum(FLOW_DASH_MM)
    dash.setDataDefinedProperty(
        QgsSymbolLayer.Property.DashPatternOffset,
        QgsProperty.fromExpression(
            f'-("phase" / 10 * {period:g} + @symbol_frame * scale_linear("speed", 0, {high:g}, '
            f"{0.01 * period:g}, {0.06 * period:g})) % {period:g}"
        ),
    )
    symbol = QgsLineSymbol([trail, dash])
    animation = QgsSymbolAnimationSettings()
    animation.setIsAnimated(True)
    animation.setFrameRate(FLOW_FPS)
    symbol.setAnimationSettings(animation)
    return symbol


FLOW_FPS = 20


def apply_flow_style(layer: QgsVectorLayer, unit: str = "") -> None:
    """Animated streamlines in the library's flow classes, 0 to the layer max, so the legend shows them."""
    high = max(float(layer.maximumValue(layer.fields().indexOf("speed")) or 0), 1e-9)
    ranges = [
        QgsRendererRange(low, upper, _flow_symbol(QColor(color), high), label)
        for low, upper, color, label in colors.flow_classes(high, unit, styles_path())
    ]
    renderer = QgsGraduatedSymbolRenderer("speed", ranges)
    renderer.setSourceColorRamp(_ramp(styles()[colors.FLOW_KIND]))
    renderer.setClassificationMethod(QgsClassificationEqualInterval())
    layer.setRenderer(renderer)
    # @symbol_frame only advances on layers that redraw on a timer; loading through Processing drops it.
    layer.setAutoRefreshInterval(1000 // FLOW_FPS)
    layer.setAutoRefreshMode(Qgis.AutoRefreshMode.RedrawOnly)
    layer.triggerRepaint()
