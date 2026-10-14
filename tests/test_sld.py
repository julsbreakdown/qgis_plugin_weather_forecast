import xml.etree.ElementTree as ET
from datetime import UTC, datetime

import numpy as np
import pytest

from wxforecast import colors, wind
from wxforecast.reader import Forecast
from wxforecast.sld import to_sld

NS = {"sld": "http://www.opengis.net/sld", "ogc": "http://www.opengis.net/ogc"}
TIME = datetime(2026, 10, 12, tzinfo=UTC)


@pytest.fixture(autouse=True)
def default_styles(monkeypatch):
    monkeypatch.delenv(colors.ENV_VAR, raising=False)


def forecast(variable, unit, values, direction=None):
    values = np.asarray(values, dtype=np.float32)
    return Forecast(
        "m", variable, unit, TIME, TIME, values, (0.0, 1.0, 0.0, 50.0, 0.0, -1.0), direction=direction
    )


def entries(path):
    root = ET.parse(path).getroot()
    color_map = root.find(".//sld:RasterSymbolizer/sld:ColorMap", NS)
    return color_map.get("type"), [e.attrib for e in color_map.findall("sld:ColorMapEntry", NS)]


def test_raster_ramp_matches_the_tiles_legend(tmp_path):
    t2m = forecast("temperature_2m", "degree_Celsius", [[-3.0, 8.0], [np.nan, 1.0]])

    path = to_sld(t2m, tmp_path / "t2m.sld")

    kind, items = entries(path)
    legend = colors.legend("temperature", -3.0, 8.0, "degree_Celsius")
    assert kind == "ramp"
    assert [e["color"] for e in items] == legend["colors"]
    assert [float(e["quantity"]) for e in items] == pytest.approx([-3 + 11 * p for p in legend["positions"]])
    assert items[0]["label"] == "-3 °C" and {e["opacity"] for e in items} == {"1"}
    assert ET.parse(path).getroot().find(".//sld:SourceChannelName", NS).text == "1"
    assert [p.name for p in tmp_path.iterdir()] == ["t2m.sld"]


def test_alpha_becomes_opacity_and_fixed_range_kind_by_name(tmp_path):
    _, items = entries(to_sld("cloud", tmp_path / "cloud.sld"))

    assert [float(e["quantity"]) for e in items] == [0, 10, 50, 80, 100]
    assert [e["opacity"] for e in items] == ["0", "0.2353", "0.6275", "0.8235", "0.9216"]


def test_discrete_kind_uses_exact_values(tmp_path):
    kind, items = entries(to_sld("snow_flag", tmp_path / "s.sld"))

    assert kind == "values"
    assert [(e["quantity"], e["opacity"], e["label"]) for e in items] == [
        ("0.0", "0", "no"),
        ("1.0", "0.8235", "snow"),
    ]


def test_values_outside_the_valid_range_are_transparent(tmp_path):
    _, items = entries(to_sld("cloud_ceiling", tmp_path / "c.sld"))

    quantities = [float(e["quantity"]) for e in items]
    assert quantities == sorted(quantities) and quantities[-2] == 19000 > quantities[-3] == 8000
    assert (items[-2]["opacity"], items[-1]["opacity"]) == (items[-3]["opacity"], "0")
    _, frozen = entries(to_sld("frozen_fraction", tmp_path / "f.sld"))
    assert float(frozen[1]["quantity"]) == -0.1 and frozen[0]["opacity"] == "0"


def test_data_range_kind_needs_a_forecast(tmp_path):
    with pytest.raises(ValueError, match="takes its range from the data"):
        to_sld("temperature", tmp_path / "x.sld")
    with pytest.raises(ValueError, match="unknown kind"):
        to_sld("nope", tmp_path / "x.sld")


def test_flow_lines_sld_has_the_qgis_classes(tmp_path):
    speed, direction = wind.speed_direction(np.full((40, 60), 6.0), np.full((40, 60), 2.0))
    wind_10m = forecast("wind_10m", "m s-1", speed, direction)

    root = ET.parse(to_sld(wind_10m, tmp_path / "flow.sld", flow=True)).getroot()

    high = max(s for _, s, _ in wind.flow_lines(wind_10m))
    classes = colors.flow_classes(high, "m s-1")
    rules = root.findall(".//sld:Rule", NS)
    assert [r.find("sld:Title", NS).text for r in rules] == [label for *_, label in classes]
    assert [r.find(".//sld:CssParameter[@name='stroke']", NS).text for r in rules] == [
        c for _, _, c, _ in classes
    ]
    first = rules[0].find(".//ogc:PropertyIsGreaterThanOrEqualTo/ogc:Literal", NS)
    last = rules[-1].find(".//ogc:PropertyIsLessThanOrEqualTo/ogc:Literal", NS)
    assert (float(first.text), float(last.text)) == (0.0, pytest.approx(high))
    assert rules[1].find(".//ogc:PropertyIsGreaterThan", NS) is not None
    with pytest.raises(ValueError, match="wind Forecast"):
        to_sld(forecast("temperature_2m", "K", [[1.0]]), tmp_path / "x.sld", flow=True)
