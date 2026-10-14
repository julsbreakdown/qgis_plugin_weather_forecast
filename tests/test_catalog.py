import io
import json
import threading
from pathlib import Path

import pytest

from wxforecast import catalog

FIXTURES = Path(__file__).parent / "fixtures" / "stac"


@pytest.fixture
def offline(monkeypatch):
    requested = []

    def fake_urlopen(url, timeout):
        requested.append(url)
        assert timeout == catalog.TIMEOUT_SECONDS
        name = "catalog" if url == catalog.CATALOG_URL else url.rstrip("/").split("/")[-2]
        return io.BytesIO((FIXTURES / f"{name}.json").read_bytes())

    monkeypatch.setattr(catalog, "urlopen", fake_urlopen)
    catalog.cache_clear()
    yield requested
    catalog.cache_clear()


def test_list_models_keeps_deterministic_lat_lon_forecasts_only(offline):
    assert catalog.list_models() == ["noaa-gfs-forecast"]


def test_virtual_collections_are_never_fetched(offline):
    catalog.list_models()
    assert not [url for url in offline if "-virtual" in url]


def test_model_parses_gfs(offline):
    gfs = catalog.model("noaa-gfs-forecast")
    assert gfs.title == "NOAA GFS forecast"
    assert (
        gfs.href == "https://dynamical-noaa-gfs.s3.us-west-2.amazonaws.com/noaa-gfs-forecast/v0.2.7.icechunk"
    )
    assert gfs.dimensions == ("init_time", "lead_time", "latitude", "longitude")
    assert gfs.variables["temperature_2m"] == catalog.Variable(
        "temperature_2m", "degree_Celsius", "2 metre temperature"
    )
    assert gfs.variables["precipitation_surface"].unit == "kg m-2 s-1"


def load(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.json").read_text())


@pytest.mark.parametrize(
    ("name", "forecast"),
    [
        ("noaa-gfs-forecast", True),
        ("noaa-gfs-analysis", False),
        ("noaa-hrrr-forecast-48-hour", False),
        ("noaa-gefs-forecast-35-day", False),
        ("ecmwf-ifs-ens-forecast-46-day-daily-1-5-degree", False),
    ],
)
def test_is_forecast(name, forecast):
    assert catalog.is_forecast(load(name)) is forecast


def test_dimensions_follow_the_variable_order():
    gefs = catalog.parse(load("noaa-gefs-forecast-35-day"))
    assert gefs.dimensions == ("init_time", "ensemble_member", "lead_time", "latitude", "longitude")


def test_variables_on_other_dimensions_are_dropped():
    ifs = catalog.parse(load("ecmwf-ifs-ens-forecast-46-day-daily-1-5-degree"))
    assert ifs.dimensions == ("init_time", "lead_time", "ensemble_member", "latitude", "longitude")
    assert sorted(ifs.variables) == ["average_temperature_2m", "precipitation_surface"]


@pytest.mark.parametrize("model_id", ["noaa-gfs-analysis", "noaa-gefs-forecast-35-day", "nope"])
def test_unknown_or_non_served_model_lists_the_served_ones(offline, model_id):
    with pytest.raises(ValueError, match=f"unknown model '{model_id}'; available: noaa-gfs-forecast$"):
        catalog.model(model_id)


def test_catalog_is_cached(offline):
    catalog.list_models()
    count = len(offline)
    catalog.model("noaa-gfs-forecast")
    catalog.list_models()
    assert len(offline) == count


def test_failed_download_is_retried_only_after_a_pause(offline, monkeypatch):
    monkeypatch.setattr(catalog, "_load", lambda url: offline.append(url) or _raise(OSError("down")))

    for _ in range(3):
        with pytest.raises(OSError, match="down"):
            catalog.list_models()
    assert len(offline) == 1, "the failure is memoised"

    clock = catalog.time.monotonic() + catalog.RETRY_SECONDS + 1
    monkeypatch.setattr(catalog.time, "monotonic", lambda: clock)
    with pytest.raises(OSError):
        catalog.list_models()
    assert len(offline) == 2


def test_failed_refresh_serves_the_previous_copy(offline, monkeypatch):
    assert catalog.list_models() == ["noaa-gfs-forecast"]
    later = catalog.time.monotonic() + catalog.CACHE_SECONDS + 1
    monkeypatch.setattr(catalog.time, "monotonic", lambda: later)
    monkeypatch.setattr(catalog, "_load", lambda url: _raise(OSError("down")))

    assert catalog.list_models() == ["noaa-gfs-forecast"]


def test_download_happens_outside_any_lock(offline, monkeypatch):
    started, release = threading.Event(), threading.Event()
    load = catalog._load

    def slow_load(url):
        if url == catalog.CATALOG_URL and not started.is_set():
            started.set()
            release.wait(5)
        return load(url)

    monkeypatch.setattr(catalog, "_load", slow_load)
    slow = threading.Thread(target=catalog.list_models)
    slow.start()
    started.wait(5)
    assert catalog.list_models() == ["noaa-gfs-forecast"], "a second caller is not blocked by the first"
    release.set()
    slow.join()


def test_malformed_collection_is_skipped(offline, monkeypatch, caplog):
    load = catalog._load
    dims = {name: {} for name in ("init_time", "lead_time", "latitude", "longitude")}
    broken = {"id": "broken", "cube:dimensions": dims}
    monkeypatch.setattr(catalog, "_load", lambda url: broken if "gefs" in url else load(url))

    assert catalog.list_models() == ["noaa-gfs-forecast"]
    assert "skipping malformed collection" in caplog.text


def test_cache_clear_downloads_again(offline):
    catalog.list_models()
    count = len(offline)
    catalog.cache_clear()
    catalog.list_models()
    assert len(offline) == 2 * count


def _raise(error):
    raise error


@pytest.mark.network
def test_live_catalog_has_gfs():
    catalog.cache_clear()
    ids = catalog.list_models()
    assert "noaa-gfs-forecast" in ids
    assert not [i for i in ids if "-ens-" in i or "gefs" in i]
    gfs = catalog.model("noaa-gfs-forecast")
    assert gfs.href.startswith("https://")
    assert {"temperature_2m", "wind_u_10m", "wind_v_10m"} <= gfs.variables.keys()
