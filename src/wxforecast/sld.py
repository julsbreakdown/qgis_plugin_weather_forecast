"""SLD 1.0 styles for GeoServer, from the same styles.toml as the PMTiles and the QGIS layers."""

from __future__ import annotations

import math
from pathlib import Path
from typing import TYPE_CHECKING
from xml.sax.saxutils import escape, quoteattr

from wxforecast import colors, wind
from wxforecast.geotiff import atomic

if TYPE_CHECKING:
    from wxforecast.reader import Forecast

FLOW_STROKE_WIDTH = 1.5
HEADER = """<?xml version="1.0" encoding="UTF-8"?>
<StyledLayerDescriptor version="1.0.0" xmlns="http://www.opengis.net/sld"
    xmlns:ogc="http://www.opengis.net/ogc" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
    xsi:schemaLocation="http://www.opengis.net/sld http://schemas.opengis.net/sld/1.0.0/StyledLayerDescriptor.xsd">
"""


def to_sld(
    source: Forecast | str, path: str | Path, styles: str | Path | None = None, flow: bool = False
) -> Path:
    """SLD of a forecast raster (band 1), or of a kind with a fixed range given by name.

    The ColorMap holds the kind's stops at their data values with their opacity, as a ramp (exact values
    for a discrete kind); values outside the kind's valid range are transparent. `flow=True` with a wind
    Forecast styles its flow lines GeoPackage instead: one LineSymbolizer per speed class, the classes of
    the QGIS flow layer.
    """
    if flow:
        if isinstance(source, str) or source.direction is None:
            raise ValueError("flow=True needs a wind Forecast")
        name, title, rules = (
            "flow",
            colors.load_styles(styles)[colors.FLOW_KIND].title,
            _flow_rules(source, styles),
        )
    else:
        name, title, rules = _raster_rule(source, styles)
    text = (
        f"{HEADER}  <NamedLayer>\n    <Name>{escape(name)}</Name>\n    <UserStyle>\n"
        f"      <Title>{escape(title)}</Title>\n      <FeatureTypeStyle>\n{rules}"
        "      </FeatureTypeStyle>\n    </UserStyle>\n  </NamedLayer>\n</StyledLayerDescriptor>\n"
    )
    with atomic(path) as tmp:
        tmp.write_text(text, encoding="utf-8")
    return Path(path)


def _raster_rule(source: Forecast | str, styles) -> tuple[str, str, str]:
    all_styles = colors.load_styles(styles)
    if isinstance(source, str):
        if source not in all_styles:
            raise ValueError(f"unknown kind {source!r}; known: {', '.join(all_styles)}")
        kind, unit, value_range = source, "", all_styles[source].value_range
        if value_range is None:
            raise ValueError(f"kind {kind!r} takes its range from the data, pass a Forecast")
        low, high = value_range
    else:
        kind, unit = colors.variable_kind(source.variable, source.unit, styles), source.unit
        low, high = colors.value_range(source.values, kind, styles)
    style = all_styles[kind]
    entries = [
        (value, stop.color, stop.alpha, label)
        for value, stop, label in colors.ramp(kind, low, high, unit, styles)
    ]
    valid_min, valid_max = style.valid
    if valid_min is not None and valid_min <= entries[0][0]:
        first = entries[0]
        entries[:0] = [
            (math.nextafter(valid_min, -math.inf), first[1], 0, ""),
            (valid_min, first[1], first[2], ""),
        ]
    if valid_max is not None and valid_max >= entries[-1][0]:
        last = entries[-1]
        entries += [(valid_max, last[1], last[2], ""), (math.nextafter(valid_max, math.inf), last[1], 0, "")]
    color_map = "".join(
        f"              <ColorMapEntry color={quoteattr(color)} quantity={quoteattr(repr(float(value)))} "
        f"opacity={quoteattr(f'{alpha / 255:.4g}')} label={quoteattr(label)}/>\n"
        for value, color, alpha, label in entries
    )
    rule = (
        "        <Rule>\n          <RasterSymbolizer>\n            <Opacity>1.0</Opacity>\n"
        "            <ChannelSelection><GrayChannel><SourceChannelName>1</SourceChannelName></GrayChannel>"
        "</ChannelSelection>\n"
        f'            <ColorMap type="{"values" if style.discrete else "ramp"}">\n{color_map}'
        "            </ColorMap>\n          </RasterSymbolizer>\n        </Rule>\n"
    )
    return kind, style.title, rule


def _flow_rules(forecast: Forecast, styles) -> str:
    """Classes from 0 to the fastest traced line, as QGIS does from the layer's maximum `speed`."""
    high = max(max((speed for _, speed, _ in wind.flow_lines(forecast)), default=0.0), 1e-9)
    rules = []
    for i, (low, upper, color, label) in enumerate(colors.flow_classes(high, forecast.unit, styles)):
        # QGIS graduated classes include their upper bound; the first also includes its lower bound.
        above = "PropertyIsGreaterThanOrEqualTo" if i == 0 else "PropertyIsGreaterThan"
        rules.append(
            f"        <Rule>\n          <Name>speed {i + 1}</Name>\n"
            f"          <Title>{escape(label)}</Title>\n"
            f"          <ogc:Filter><ogc:And>{_compare(above, low)}"
            f"{_compare('PropertyIsLessThanOrEqualTo', upper)}</ogc:And></ogc:Filter>\n"
            "          <LineSymbolizer><Stroke>"
            f'<CssParameter name="stroke">{color}</CssParameter>'
            f'<CssParameter name="stroke-width">{FLOW_STROKE_WIDTH}</CssParameter>'
            "</Stroke></LineSymbolizer>\n        </Rule>\n"
        )
    return "".join(rules)


def _compare(operator: str, value: float) -> str:
    return (
        f"<ogc:{operator}><ogc:PropertyName>speed</ogc:PropertyName>"
        f"<ogc:Literal>{float(value)!r}</ogc:Literal></ogc:{operator}>"
    )
