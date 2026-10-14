from datetime import UTC, datetime

import numpy as np
import pytest

from wxforecast.geotiff import to_geotiff
from wxforecast.reader import Forecast

gdal = pytest.importorskip("osgeo.gdal")


@pytest.fixture
def forecast():
    values = np.arange(300 * 400, dtype=np.float32).reshape(300, 400) / 100
    values[0, 0] = np.nan
    return Forecast(
        model="noaa-gfs-forecast",
        variable="temperature_2m",
        unit="degree_Celsius",
        init_time=datetime(2026, 10, 12, 0, tzinfo=UTC),
        valid_time=datetime(2026, 10, 12, 9, tzinfo=UTC),
        values=values,
        geotransform=(-10.125, 0.25, 0.0, 55.125, 0.0, -0.25),
    )


def test_round_trip(tmp_path, forecast):
    path = to_geotiff(forecast, tmp_path / "sub" / "t2m.tif")

    assert path == tmp_path / "sub" / "t2m.tif"
    assert [p.name for p in path.parent.iterdir()] == ["t2m.tif"]
    with gdal.ExceptionMgr(useExceptions=True):
        dataset = gdal.Open(str(path))
        assert dataset.GetGeoTransform() == forecast.geotransform
        assert dataset.GetProjection().endswith('AUTHORITY["EPSG","4326"]]')
        band = dataset.GetRasterBand(1)
        assert band.DataType == gdal.GDT_Float32
        assert np.isnan(band.GetNoDataValue())
        assert band.GetDescription() == "temperature_2m"
        structure = dataset.GetMetadata("IMAGE_STRUCTURE")
        assert (structure["LAYOUT"], structure["COMPRESSION"], structure["PREDICTOR"]) == (
            "COG",
            "DEFLATE",
            "3",
        )
        assert band.GetOverviewCount() == 1
        assert dataset.GetMetadata() == {
            "AREA_OR_POINT": "Area",
            "model": "noaa-gfs-forecast",
            "variable": "temperature_2m",
            "unit": "degree_Celsius",
            "init_time": "2026-10-12T00:00:00+00:00",
            "valid_time": "2026-10-12T09:00:00+00:00",
        }
        np.testing.assert_array_equal(band.ReadAsArray(), forecast.values)


def test_overwrites_existing_file(tmp_path, forecast):
    path = tmp_path / "t2m.tif"
    path.write_bytes(b"stale")

    to_geotiff(forecast, path)

    with gdal.ExceptionMgr(useExceptions=True):
        assert gdal.Open(str(path)).RasterXSize == 400


def test_failure_leaves_no_partial_file(tmp_path, forecast):
    bad = Forecast(**{**forecast.__dict__, "crs": "not-a-crs"})

    with pytest.raises(RuntimeError):
        to_geotiff(bad, tmp_path / "x.tif")

    assert not list(tmp_path.iterdir())


def test_concurrent_writers_use_distinct_temporary_files(tmp_path, forecast):
    from wxforecast.geotiff import atomic

    with atomic(tmp_path / "t.tif") as first, atomic(tmp_path / "t.tif") as second:
        assert first != second and first.parent.parent == second.parent.parent == tmp_path
        first.write_bytes(b"1")
        second.write_bytes(b"2")
    assert [p.name for p in tmp_path.iterdir()] == ["t.tif"]
