import json
import os
import re
from pathlib import Path

import numpy as np
import pytest

from wxforecast import catalog, colors
from wxforecast.colors import colorize, limits, load_styles, value_range, variable_kind

FIXTURES = Path(__file__).parent / "fixtures" / "stac"


@pytest.fixture(autouse=True)
def default_styles(monkeypatch):
    monkeypatch.delenv(colors.ENV_VAR, raising=False)


# Every variable of GFS, ICON-EU and AIFS (`Model.display_variables` plus wind components) on 2026-10-12.
KINDS = {
    ("categorical_freezing_rain_surface", "1"): "freezing_rain_flag",
    ("categorical_ice_pellets_surface", "1"): "ice_pellets_flag",
    ("categorical_rain_surface", "1"): "rain_flag",
    ("categorical_snow_surface", "1"): "snow_flag",
    ("cloud_cover_high", "percent"): "cloud",
    ("cloud_cover_low", "percent"): "cloud",
    ("cloud_cover_medium", "percent"): "cloud",
    ("total_cloud_cover_atmosphere", "percent"): "cloud",
    ("convective_available_potential_energy_atmosphere", "J kg-1"): "cape",
    ("dew_point_temperature_2m", "degree_Celsius"): "temperature",
    ("maximum_temperature_2m", "degree_Celsius"): "temperature",
    ("minimum_temperature_2m", "degree_Celsius"): "temperature",
    ("temperature_2m", "degree_Celsius"): "temperature",
    ("temperature_80m", "degree_Celsius"): "temperature",
    ("temperature_850hpa", "degree_Celsius"): "temperature",
    ("temperature_925hpa", "degree_Celsius"): "temperature",
    ("downward_diffuse_short_wave_radiation_flux_surface", "W m-2"): "radiation",
    ("downward_direct_short_wave_radiation_flux_surface", "W m-2"): "radiation",
    ("downward_long_wave_radiation_flux_surface", "W m-2"): "radiation",
    ("downward_short_wave_radiation_flux_surface", "W m-2"): "radiation",
    ("geopotential_height_500hpa", "m"): "height",
    ("geopotential_height_850hpa", "m"): "height",
    ("geopotential_height_925hpa", "m"): "height",
    ("geopotential_height_cloud_ceiling", "m"): "cloud_ceiling",
    ("percent_frozen_precipitation_surface", "percent"): "frozen_fraction",
    ("precipitable_water_atmosphere", "kg m-2"): "precipitable_water",
    ("precipitation_surface", "kg m-2 s-1"): "precipitation",
    ("pressure_80m", "Pa"): "pressure",
    ("pressure_reduced_to_mean_sea_level", "Pa"): "pressure",
    ("pressure_surface", "Pa"): "pressure",
    ("relative_humidity_2m", "percent"): "humidity",
    ("snow_thickness_surface", "m"): "snow",
    ("snow_water_equivalent_surface", "m"): "snow",
    ("wind_10m", "m s-1"): "wind",
    ("wind_80m", "m s-1"): "wind",
    ("wind_100m", "m s-1"): "wind",
    ("wind_gust_10m", "m s-1"): "wind",
    ("wind_u_10m", "m s-1"): "wind_component",
    ("wind_v_100m", "m s-1"): "wind_component",
}


@pytest.mark.parametrize(("name", "unit", "kind"), [(*key, kind) for key, kind in KINDS.items()])
def test_every_model_variable_has_its_own_kind(name, unit, kind):
    assert variable_kind(name, unit) == kind


def test_fixture_variables_have_a_known_kind():
    gfs = catalog.parse(json.loads((FIXTURES / "noaa-gfs-forecast.json").read_text()))
    kinds = {name: variable_kind(name, v.unit) for name, v in gfs.variables.items()}
    assert kinds == {
        "precipitation_surface": "precipitation",
        "pressure_reduced_to_mean_sea_level": "pressure",
        "temperature_2m": "temperature",
        "total_cloud_cover_atmosphere": "cloud",
        "wind_10m": "wind",
        "wind_u_10m": "wind_component",
        "wind_v_10m": "wind_component",
    }
    assert gfs.variables["wind_10m"].calculated
    assert not gfs.variables["wind_u_10m"].calculated


def test_every_kind_has_a_style_and_unknown_is_other():
    assert set(KINDS.values()) | {"other", "flow"} == load_styles().keys()
    assert variable_kind("soil_moisture", "m3 m-3") == "other"
    for style in load_styles().values():
        positions = [stop.position for stop in style.stops]
        assert positions[0] == 0 and positions[-1] == 1 and positions == sorted(positions)


def test_cloud_is_white_to_gray_with_holes_at_zero():
    rgba = colorize(np.array([[0.0, 5.0, 50.0, 100.0, np.nan]]), "cloud", *value_range(np.zeros(1), "cloud"))
    assert rgba[0, 0, 3] == 0
    assert 0 < rgba[0, 1, 3] < rgba[0, 2, 3] < rgba[0, 3, 3] < 255
    assert rgba[0, 4, 3] == 0
    assert all(len(set(px[:3])) == 1 for px in rgba[0, 1:4])
    assert rgba[0, 1, 0] < rgba[0, 3, 0] == 255


@pytest.mark.parametrize("kind", ["precipitation", "cloud", "cape", "snow", "frozen_fraction", "rain_flag"])
def test_precipitation_and_cloud_like_kinds_are_transparent_at_zero(kind):
    assert load_styles()[kind].transparent_at_min
    vmin, vmax = limits(kind, 0.0, 10.0)
    assert vmin == 0
    assert colorize(np.array([[0.0]]), kind, vmin, vmax)[0, 0, 3] == 0


def test_categorical_is_exact_two_classes():
    style = load_styles()["snow_flag"]
    assert style.discrete and style.value_range == (0.0, 1.0)
    rgba = colorize(np.array([[0.0, 0.3, 0.7, 1.0]]), "snow_flag", 0, 1)
    assert rgba[0, :, 3].tolist() == [0, 0, 210, 210]
    assert rgba[0, 2].tolist() == rgba[0, 3].tolist()
    assert colors.legend("snow_flag", 0, 1, "1")["interpolation"] == "discrete"


def test_fill_values_outside_the_valid_range_are_nodata():
    ceiling = np.array([[0.0, 20032.0]])
    assert value_range(ceiling, "cloud_ceiling") == (0.0, 8000.0)
    assert colorize(ceiling, "cloud_ceiling", 0, 8000)[0, :, 3].tolist() == [235, 0]
    frozen = np.array([[-50.0, 0.0, 100.0]])
    assert colorize(frozen, "frozen_fraction", 0, 100)[0, :, 3].tolist() == [0, 0, 255]


def test_data_ranges_follow_the_anchor():
    values = np.array([[-3.0, 8.0, np.nan]])
    assert value_range(values, "temperature") == (-3.0, 8.0)
    assert value_range(values, "wind_component") == (-8.0, 8.0)
    assert value_range(np.array([2.0, 5.0]), "wind") == (0.0, 5.0)
    assert value_range(np.array([np.nan]), "pressure") == (0.0, 1.0)
    assert value_range(np.array([4.0, 4.0]), "pressure") == (4.0, 5.0)


def test_display_units_never_touch_data():
    assert colors.display_unit("Pa") == ("hPa", 0.01)
    assert colors.display_unit("kg m-2 s-1") == ("mm/h", 3600.0)
    assert colors.display_unit("furlong") == ("furlong", 1.0)
    legend = colors.legend("pressure", 98000.0, 103000.0, "Pa")
    assert (legend["min"], legend["display_unit"]) == (98000.0, "hPa")
    assert len(legend["colors"]) == len(legend["alpha"]) == len(legend["positions"])


@pytest.mark.parametrize(
    ("low", "high"), [(1.7976931348623157e308, -1.7976931348623157e308), (np.nan, 3.0), (np.inf, np.inf)]
)
def test_no_data_statistics_never_make_a_ramp(low, high):
    assert limits("temperature", low, high) == (0.0, 1.0)
    assert limits("wind", low, high) == (0.0, 1.0)
    assert value_range(np.full((2, 2), np.nan, dtype=np.float32), "temperature") == (0.0, 1.0)


def write(tmp_path, text, name="styles.toml"):
    path = tmp_path / name
    path.write_text(text)
    return path


def test_override_changes_some_kinds_and_keeps_the_rest(tmp_path):
    path = write(
        tmp_path,
        """
[cloud]
stops = [[0, "#000000", 0], [1, "#ff0000"]]

[soil]
title = "Soil moisture"
icon = "generic"
names = ["soil_*"]
range = [0, 1]
colors = ["#8c510a", "#01665e"]

[wind]
names = ["wind_*", "gust_*"]
""",
    )
    styles = load_styles(path)
    assert next(iter(styles)) == "soil"
    assert styles["cloud"].colors == ["#000000", "#ff0000"]
    assert styles["cloud"].value_range == (0.0, 100.0) and styles["cloud"].icon == "cloud"
    assert styles["temperature"] == load_styles()["temperature"]
    assert variable_kind("soil_moisture", "m3 m-3", path) == "soil"
    assert variable_kind("gust_max", "", path) == "wind"
    assert styles["wind"].colors == load_styles()["wind"].colors


def test_env_var_selects_the_override(tmp_path, monkeypatch):
    path = write(tmp_path, '[other]\ncolors = ["#111111", "#222222"]\n')
    monkeypatch.setenv(colors.ENV_VAR, str(path))
    assert load_styles()["other"].colors == ["#111111", "#222222"]
    path.write_text('[other]\ncolors = ["#333333", "#444444"]\n')
    os.utime(path, ns=(path.stat().st_mtime_ns + 10**9,) * 2)
    assert load_styles()["other"].colors == ["#333333", "#444444"], "an edited file is read again"


@pytest.mark.parametrize(
    ("text", "error"),
    [
        ('[cloud]\ncolour = "#ffffff"\n', "unknown key"),
        ('[cloud]\ncolors = ["#fff", "#000000"]\n', "bad colour '#fff'"),
        ('[cloud]\nstops = [[0, "#000000"], [0.6, "#000000"], [0.4, "#000000"], [1, "#000000"]]\n', "ascend"),
        ('[cloud]\nstops = [[0.1, "#000000"], [1, "#000000"]]\n', "from 0 to 1"),
        ('[cloud]\nstops = [[0, "#000000", 300], [1, "#000000"]]\n', "alpha"),
        ("[cloud]\nrange = [5, 1]\n", "range"),
        ('[cloud]\nanchor = "middle"\n', "anchor"),
        ('[new_kind]\nnames = ["x"]\n', "needs 'stops' or 'colors'"),
        ("cloud = 3\n", "must be a [table]"),
        ("[cloud\n", "styles.toml"),
    ],
)
def test_bad_override_is_rejected_with_a_clear_error(tmp_path, text, error):
    with pytest.raises(ValueError, match=re.escape(error)):
        load_styles(write(tmp_path, text))
