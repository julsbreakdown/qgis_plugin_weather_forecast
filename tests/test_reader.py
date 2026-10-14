from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import numpy as np
import pytest
import zarr
from zarr.storage import MemoryStore

from wxforecast import reader

RUN0 = datetime(2026, 10, 10, 0, tzinfo=UTC)
RUNS = 4
LEADS_H = np.arange(0, 49, 3)
LAT = np.arange(90.0, -90.1, -10.0)
LON = np.arange(-180.0, 180.0, 10.0)


@pytest.fixture
def source():
    rng = np.random.default_rng(0)
    data = rng.normal(15.0, 8.0, (RUNS, len(LEADS_H), len(LAT), len(LON))).astype(np.float32)
    data[-1] = np.nan
    return data


@pytest.fixture
def store(source):
    group = zarr.open_group(MemoryStore(), mode="w")
    init = np.array([(RUN0 + timedelta(hours=6 * i)).timestamp() for i in range(RUNS)], dtype=np.int64)
    axes = {
        "init_time": init,
        "lead_time": (LEADS_H * 3600).astype(np.float64),
        "latitude": LAT,
        "longitude": LON,
    }
    for name, values in axes.items():
        group.create_array(name, data=values, dimension_names=(name,))
    array = group.create_array(
        "temperature_2m",
        shape=source.shape,
        dtype=np.float32,
        chunks=(1, 6, 10, 10),
        fill_value=np.nan,
        dimension_names=("init_time", "lead_time", "latitude", "longitude"),
    )
    array[:] = source
    return zarr.open_group(group.store, mode="r")


@pytest.fixture
def fake(monkeypatch, store):
    variables = {
        "temperature_2m": reader.catalog.Variable("temperature_2m", "degree_Celsius", "2 m"),
        "wind_u_10m": reader.catalog.Variable("wind_u_10m", "m s-1", "10 m u wind"),
    }
    model = SimpleNamespace(
        id="fake-model",
        href="https://example.invalid/repo",
        dimensions=("init_time", "lead_time", "latitude", "longitude"),
        variables=variables,
    )
    monkeypatch.setattr(reader.catalog, "model", lambda model_id: model)
    monkeypatch.setattr(reader, "_open_href", lambda href: store)
    return model


def at(hours: float) -> datetime:
    return RUN0 + timedelta(hours=hours)


def test_defaults_pick_latest_run_with_data_and_lead_nearest_now(monkeypatch, fake, source):
    monkeypatch.setattr(reader, "_utcnow", lambda: at(12 + 19.4))

    forecast = reader.fetch("fake-model", "temperature_2m")

    assert forecast.init_time == at(12)
    assert forecast.valid_time == at(12 + 18)
    assert forecast.values.dtype == np.float32
    np.testing.assert_array_equal(forecast.values, source[2, 6])
    assert forecast.unit == "degree_Celsius"
    assert forecast.model == "fake-model"
    assert forecast.crs == "EPSG:4326"
    assert forecast.geotransform == (-185.0, 10.0, 0.0, 95.0, 0.0, -10.0)


def test_explicit_run_and_lead(monkeypatch, fake, source):
    forecast = reader.fetch("fake-model", "temperature_2m", run=at(6), lead=timedelta(hours=9))

    assert forecast.init_time == at(6)
    assert forecast.valid_time == at(15)
    np.testing.assert_array_equal(forecast.values, source[1, 3])


def test_naive_run_is_utc(fake, source):
    forecast = reader.fetch("fake-model", "temperature_2m", run=datetime(2026, 10, 10, 6), lead=timedelta(0))

    assert forecast.init_time == at(6)


def test_lead_none_with_explicit_run_clamps_to_last_lead(monkeypatch, fake, source):
    monkeypatch.setattr(reader, "_utcnow", lambda: at(500))

    forecast = reader.fetch("fake-model", "temperature_2m", run=at(0))

    assert forecast.valid_time == at(48)


def test_bbox_window_reads_rows_and_concatenates_antimeridian_columns(fake, source):
    bbox = (165.0, -10.0, -165.0, 10.0)

    forecast = reader.fetch("fake-model", "temperature_2m", bbox=bbox, run=at(0), lead=timedelta(0))

    expected = np.concatenate([source[0, 0, 8:11, 35:36], source[0, 0, 8:11, 0:2]], axis=1)
    np.testing.assert_array_equal(forecast.values, expected)
    assert forecast.geotransform == (165.0, 10.0, 0.0, 15.0, 0.0, -10.0)


def test_unknown_variable_lists_available(fake):
    with pytest.raises(ValueError, match="available: temperature_2m, wind_u_10m"):
        reader.fetch("fake-model", "snow")


def test_ensemble_model_is_rejected_before_opening_the_store(monkeypatch, fake):
    monkeypatch.setattr(
        fake, "dimensions", ("init_time", "lead_time", "ensemble_member", "latitude", "longitude")
    )
    monkeypatch.setattr(reader, "_open_href", lambda href: pytest.fail("store opened"))

    with pytest.raises(ValueError, match="unsupported dimensions"):
        reader.fetch("fake-model", "temperature_2m")


def test_unknown_run(fake):
    with pytest.raises(ValueError, match="run"):
        reader.fetch("fake-model", "temperature_2m", run=at(1))


def test_unknown_lead(fake):
    with pytest.raises(ValueError, match="lead"):
        reader.fetch("fake-model", "temperature_2m", run=at(0), lead=timedelta(hours=1))


def test_no_run_with_data_falls_back_to_newest(monkeypatch, fake, store):
    monkeypatch.setattr(reader, "_utcnow", lambda: at(0))
    monkeypatch.setattr(reader, "RUN_CANDIDATES", 1)

    with pytest.raises(ValueError, match="no data at run 2026-10-10T18:00Z lead 0 h"):
        reader.fetch("fake-model", "temperature_2m")


def test_all_nan_lead_zero_explains_period_variables(fake, store):
    zarr.open_group(store.store.with_read_only(False), mode="a")["temperature_2m"][0, 0] = np.nan

    with pytest.raises(ValueError, match=r"lead 0 h in this area: period variables .* pick lead >= 3 h"):
        reader.fetch("fake-model", "temperature_2m", run=at(0), lead=timedelta(0))


def test_all_nan_later_lead_says_run_not_written(fake):
    with pytest.raises(ValueError, match="lead 9 h in this area: the run may not be written yet"):
        reader.fetch("fake-model", "temperature_2m", run=at(18), lead=timedelta(hours=9))


@pytest.mark.network
def test_fetch_gfs_over_france():
    forecast = reader.fetch("noaa-gfs-forecast", "temperature_2m", bbox=(-5.0, 42.0, 8.5, 51.5))

    rows, cols = forecast.values.shape
    assert 30 < rows < 50
    assert 45 < cols < 65
    assert np.isfinite(forecast.values).all()
    assert -30.0 < float(forecast.values.min()) < float(forecast.values.max()) < 45.0
    assert forecast.valid_time - forecast.init_time >= timedelta(0)
    assert abs(forecast.valid_time - datetime.now(UTC)) < timedelta(hours=4)


def writable(store):
    return zarr.open_group(store.store.with_read_only(False), mode="a")["temperature_2m"]


def test_partly_written_newest_run_is_skipped(monkeypatch, fake, store, source):
    monkeypatch.setattr(reader, "_utcnow", lambda: at(18))
    newest = source[2].copy()
    newest[:, :10, :10] = np.nan
    writable(store)[3] = newest

    forecast = reader.fetch("fake-model", "temperature_2m")

    assert forecast.init_time == at(12)
    assert np.isfinite(forecast.values).all()


def test_chunk_empty_in_every_run_is_no_data_not_a_partial_run(monkeypatch, fake, store, source):
    monkeypatch.setattr(reader, "_utcnow", lambda: at(18))
    data = source.copy()
    data[3] = source[2]
    data[:, :, :10, :10] = np.nan
    writable(store)[:] = data

    forecast = reader.fetch("fake-model", "temperature_2m")

    assert forecast.init_time == at(18)
    assert np.isnan(forecast.values[:10, :10]).all() and np.isfinite(forecast.values[10:]).all()


def test_unknown_lead_lists_leads_in_hours_with_the_step_change():
    hours = np.array([*range(0, 121), *range(123, 385, 3)], dtype=float)

    with pytest.raises(ValueError) as error:
        reader._lead_index(0.0, hours * 3600, timedelta(hours=121))

    assert str(error.value) == "lead 121 h not available; leads: 0-120 h every 1 h, then 120-384 h every 3 h"


def test_unknown_run_lists_the_latest_runs(fake):
    with pytest.raises(
        ValueError, match=r"run 2026-10-10T01:00Z not available; latest runs: 2026-10-10T00:00Z, "
    ):
        reader.fetch("fake-model", "temperature_2m", run=at(1))
