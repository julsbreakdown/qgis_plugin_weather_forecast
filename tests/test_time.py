import json
from datetime import UTC, datetime, timedelta

import numpy as np
import pytest
import zarr
from zarr.storage import MemoryStore

from wxforecast import catalog, reader, wind
from wxforecast.reader import Forecast, fetch_range, step_name
from wxforecast.tiles import bbox_wkt, to_pmtiles_steps

ogr = pytest.importorskip("osgeo.ogr")
pmtiles_reader = pytest.importorskip("pmtiles.reader")

RUN = datetime(2026, 10, 12, 0, tzinfo=UTC)
LEADS_H = [0, 1, 2, 3, 4, 5, 6, 9, 12, 15, 18, 21, 24]
LAT, LON = np.arange(60.0, 29.0, -1.0), np.arange(-20.0, 30.0, 1.0)
DIMS = ("init_time", "lead_time", "latitude", "longitude")


@pytest.fixture
def store(monkeypatch):
    group = zarr.open_group(MemoryStore(), mode="w")
    axes = {
        "init_time": np.array([RUN.timestamp()]),
        "lead_time": np.array(LEADS_H, dtype=float) * 3600,
        "latitude": LAT,
        "longitude": LON,
    }
    for name, values in axes.items():
        group.create_array(name, data=values, dimension_names=(name,))
    shape = (1, len(LEADS_H), len(LAT), len(LON))
    data = np.broadcast_to(np.array(LEADS_H, dtype=np.float32)[None, :, None, None], shape)
    for name, scale in (("temperature_2m", 1.0), ("wind_u_10m", 1.0), ("wind_v_10m", 0.0)):
        array = group.create_array(
            name,
            shape=shape,
            dtype=np.float32,
            chunks=(1, 13, 16, 25),
            fill_value=np.nan,
            dimension_names=DIMS,
        )
        array[:] = data * scale
    variables = {n: catalog.Variable(n, "m s-1", n) for n in ("temperature_2m", "wind_u_10m", "wind_v_10m")}
    variables |= catalog.wind_variables(variables)
    model = catalog.Model("fake", "Fake", "https://example.invalid", DIMS, variables)
    monkeypatch.setattr(reader.catalog, "model", lambda model_id: model)
    monkeypatch.setattr(reader, "_open_href", lambda href: zarr.open_group(group.store, mode="r"))
    monkeypatch.setattr(reader, "_utcnow", lambda: RUN + timedelta(hours=2.4))
    return group


@pytest.fixture
def reads(monkeypatch):
    calls = []
    get = zarr.Array.__getitem__

    def counting(self, key):
        if self.ndim == 4:
            calls.append(self.name)
        return get(self, key)

    monkeypatch.setattr(zarr.Array, "__getitem__", counting)
    return calls


def hours(forecasts):
    return [(f.valid_time - RUN).total_seconds() / 3600 for f in forecasts]


def test_steps_from_nearest_now_to_now_plus_days_across_the_step_change(store, reads):
    forecasts = fetch_range("fake", "temperature_2m", days=0.5, run=RUN)

    assert hours(forecasts) == [2, 3, 4, 5, 6, 9, 12]
    assert [float(f.values[0, 0]) for f in forecasts] == [2, 3, 4, 5, 6, 9, 12]
    assert reads == ["/temperature_2m"], "one zarr read for all the steps"
    assert {f.init_time for f in forecasts} == {RUN}


def test_every_thins_on_valid_time_without_inventing_steps(store):
    forecasts = fetch_range("fake", "temperature_2m", days=0.5, every=timedelta(hours=3), run=RUN)

    assert hours(forecasts) == [2, 5, 9, 12]


def test_latest_run_and_wind_read_each_component_once(store, reads):
    forecasts = fetch_range("fake", "wind_10m", days=0.25)

    assert hours(forecasts) == [2, 3, 4, 5, 6]
    assert sorted(reads) == ["/wind_u_10m", "/wind_v_10m"]
    np.testing.assert_allclose(forecasts[-1].values, 6.0)
    np.testing.assert_allclose(forecasts[-1].direction, 270.0)


def test_all_nan_steps_are_dropped_and_all_empty_is_an_error(store):
    store["temperature_2m"][0, 3] = np.nan

    assert hours(fetch_range("fake", "temperature_2m", days=0.25, run=RUN)) == [2, 4, 5, 6]

    store["temperature_2m"][:] = np.nan
    with pytest.raises(ValueError, match="no data at run 2026-10-12T00:00Z lead 2 h"):
        fetch_range("fake", "temperature_2m", days=0.25, run=RUN)


def step(valid_hours: float, value: float) -> Forecast:
    values = np.full((30, 50), value, dtype=np.float32)
    values[0, 0] = value + 1
    valid = RUN + timedelta(hours=valid_hours)
    return Forecast(
        "fake", "temperature_2m", "degree_Celsius", RUN, valid, values, (-20.5, 1, 0, 60.5, 0, -1)
    )


def test_pmtiles_per_step_share_one_range_and_an_index(tmp_path):
    forecasts = [step(3, 10.0), step(6, 20.0)]
    progress = []

    index_path = to_pmtiles_steps(
        forecasts,
        tmp_path / "t2m",
        [bbox_wkt((0, 40, 10, 50))],
        0,
        2,
        progress=lambda *a: progress.append(a) or True,
    )

    index = json.loads(index_path.read_text())
    assert sorted(p.name for p in index_path.parent.iterdir()) == [
        "index.json",
        "temperature_2m_20261012T03.pmtiles",
        "temperature_2m_20261012T06.pmtiles",
    ]
    assert (index["legend"]["min"], index["legend"]["max"]) == (10.0, 21.0)
    assert index["steps"] == [
        {
            "valid_time": "2026-10-12T03:00:00+00:00",
            "lead_hours": 3.0,
            "file": "temperature_2m_20261012T03.pmtiles",
        },
        {
            "valid_time": "2026-10-12T06:00:00+00:00",
            "lead_hours": 6.0,
            "file": "temperature_2m_20261012T06.pmtiles",
        },
    ]
    assert (index["model"], index["variable"], index["init_time"]) == (
        "fake",
        "temperature_2m",
        RUN.isoformat(),
    )
    for item in index["steps"]:
        with open(index_path.parent / item["file"], "rb") as f:
            legend = pmtiles_reader.Reader(pmtiles_reader.MmapSource(f)).metadata()["wxforecast"]["legend"]
        assert (legend["min"], legend["max"]) == (10.0, 21.0)
    done, totals = zip(*progress, strict=True)
    assert list(done) == list(range(1, len(progress) + 1)) and set(totals) == {len(progress)}


def test_step_name_is_the_utc_valid_time():
    assert step_name(step(27, 0.0)) == "temperature_2m_20261013T03"


def test_flow_lines_of_several_steps_carry_their_valid_time(tmp_path):
    speed, direction = wind.speed_direction(np.full((30, 50), 5.0), np.full((30, 50), 1.0))
    forecasts = [
        Forecast(
            "fake",
            "wind_10m",
            "m s-1",
            RUN,
            RUN + timedelta(hours=h),
            speed,
            (-20.5, 1, 0, 60.5, 0, -1),
            direction=direction,
        )
        for h in (3, 6)
    ]

    path = wind.to_flow_lines(forecasts, tmp_path / "flow.gpkg")

    with ogr.ExceptionMgr(useExceptions=True):
        data = ogr.Open(str(path))
        layer = data.GetLayerByName("flow")
        times = {tuple(feature.GetFieldAsDateTime("valid_time")) for feature in layer}
    assert times == {(2026, 10, 12, 3, 0, 0.0, 100), (2026, 10, 12, 6, 0, 0.0, 100)}
    assert layer.GetFeatureCount() == 2 * len(wind.flow_lines(forecasts[0]))
