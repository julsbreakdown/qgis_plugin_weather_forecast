import json
import math
import time
from datetime import UTC, datetime

import numpy as np
import pytest

from wxforecast import tiles
from wxforecast.colors import colorize
from wxforecast.reader import Forecast
from wxforecast.tiles import Cancelled, areas_bbox, areas_from_vector, bbox_wkt, to_pmtiles

gdal = pytest.importorskip("osgeo.gdal")
reader = pytest.importorskip("pmtiles.reader")


@pytest.fixture
def forecast():
    lat = np.arange(60, 30, -0.25)
    lon = np.arange(-20, 30, 0.25)
    values = np.tile(lat[:, None], (1, len(lon))).astype(np.float32)
    values[:4, :4] = np.nan
    return Forecast(
        model="noaa-gfs-forecast",
        variable="temperature_2m",
        unit="degree_Celsius",
        init_time=datetime(2026, 10, 12, 0, tzinfo=UTC),
        valid_time=datetime(2026, 10, 12, 9, tzinfo=UTC),
        values=values,
        geotransform=(-20.125, 0.25, 0.0, 60.125, 0.0, -0.25),
    )


def _tiles(path):
    with open(path, "rb") as f:
        archive = reader.Reader(reader.MmapSource(f))
        return archive.header(), archive.metadata(), dict(reader.all_tiles(archive.get_bytes))


def _rgba(png):
    with gdal.ExceptionMgr(useExceptions=True):
        gdal.FileFromMemBuffer("/vsimem/test.png", png)
        try:
            return gdal.Open("/vsimem/test.png").ReadAsArray()
        finally:
            gdal.Unlink("/vsimem/test.png")


def test_colorize_ends_and_nodata():
    rgba = colorize(np.array([[0.0, 10.0, np.nan]]), "temperature", 0, 10)
    assert rgba[0, 0].tolist() == [0x21, 0x66, 0xAC, 255]
    assert rgba[0, 1].tolist() == [0xB2, 0x18, 0x2B, 255]
    assert rgba[0, 2, 3] == 0


def test_bbox_area_writes_every_zoom(tmp_path, forecast):
    path = to_pmtiles(forecast, tmp_path / "t.pmtiles", [bbox_wkt((0, 40, 10, 50))], 0, 4)

    header, metadata, tiles = _tiles(path)
    assert (header["min_zoom"], header["max_zoom"]) == (0, 4)
    assert sorted({z for z, _, _ in tiles}) == [0, 1, 2, 3, 4]
    assert (0, 0, 0) in tiles
    assert metadata["wxforecast"]["legend"]["min"] == pytest.approx(30.25)
    assert json.dumps(metadata)
    assert [p.name for p in tmp_path.iterdir()] == ["t.pmtiles"]


@pytest.mark.parametrize(("cell", "expected"), [(0.25, 5), (1.5, 2), (0.025, 8), (0.0001, tiles.MAX_ZOOM)])
def test_native_zoom_follows_the_grid(cell, expected):
    assert tiles.native_zoom((-20.0, cell, 0.0, 60.0, 0.0, -cell)) == expected


def test_max_zoom_defaults_to_the_native_zoom(tmp_path, forecast):
    area = [bbox_wkt((0, 40, 10, 50))]

    header = _tiles(to_pmtiles(forecast, tmp_path / "a.pmtiles", area))[0]
    raised = _tiles(to_pmtiles(forecast, tmp_path / "b.pmtiles", area, min_zoom=6))[0]

    assert (header["min_zoom"], header["max_zoom"]) == (0, 5)
    assert (raised["min_zoom"], raised["max_zoom"]) == (6, 6)


def test_clip_makes_outside_transparent(tmp_path, forecast):
    triangle = "POLYGON((0 40,10 40,0 50,0 40))"

    clipped = _tiles(to_pmtiles(forecast, tmp_path / "c.pmtiles", [triangle], 3, 3))[2]
    full = _tiles(to_pmtiles(forecast, tmp_path / "f.pmtiles", [triangle], 3, 3, clip=False))[2]

    assert clipped.keys() == full.keys()
    alpha_clipped = sum(int((_rgba(png)[3] > 0).sum()) for png in clipped.values())
    alpha_full = sum(int((_rgba(png)[3] > 0).sum()) for png in full.values())
    assert 0 < alpha_clipped < alpha_full


def test_areas_from_vector_and_bbox(tmp_path):
    path = tmp_path / "a.geojson"
    path.write_text(
        json.dumps(
            {
                "type": "FeatureCollection",
                "features": [
                    {
                        "type": "Feature",
                        "properties": {},
                        "geometry": {"type": "Polygon", "coordinates": ring},
                    }
                    for ring in (
                        [[[0, 40], [2, 40], [2, 42], [0, 40]]],
                        [[[5, 45], [8, 45], [8, 47], [5, 45]]],
                    )
                ],
            }
        )
    )

    areas = areas_from_vector(path)

    assert len(areas) == 2 and areas[0].startswith("POLYGON")
    assert areas_bbox(areas) == (0, 40, 8, 47)


def test_rejects_bad_requests(tmp_path, forecast):
    with pytest.raises(ValueError, match="at least one area"):
        to_pmtiles(forecast, tmp_path / "x.pmtiles", [])
    with pytest.raises(ValueError, match="zoom"):
        to_pmtiles(forecast, tmp_path / "x.pmtiles", [bbox_wkt((0, 40, 1, 41))], 3, 2)
    with pytest.raises(ValueError, match="antimeridian"):
        bbox_wkt((170, -10, -170, 10))
    with pytest.raises(ValueError, match="no tile has data"):
        to_pmtiles(forecast, tmp_path / "x.pmtiles", [bbox_wkt((100, 0, 110, 10))], 4, 4)
    assert not list(tmp_path.iterdir())


def test_many_small_polygons_are_merged_once(tmp_path, forecast):
    squares = [
        bbox_wkt((x, y, x + 0.1, y + 0.1)) for x in np.arange(-5, 5, 0.2) for y in np.arange(40, 52, 0.2)
    ]
    assert len(squares) == 3000

    started = time.perf_counter()
    path = to_pmtiles(forecast, tmp_path / "many.pmtiles", squares, 0, 6)

    assert time.perf_counter() - started < 5
    assert _tiles(path)[2]


def test_tile_limit_is_checked_before_planning(tmp_path, forecast, monkeypatch):
    monkeypatch.setattr(tiles, "_candidates", lambda *_: pytest.fail("tiles enumerated"))

    with pytest.raises(ValueError, match="exceed the limit"):
        to_pmtiles(forecast, tmp_path / "x.pmtiles", [bbox_wkt((-180, -85, 180, 85))], 0, 12)


def test_header_bounds_stay_in_the_world(tmp_path, forecast):
    header = _tiles(to_pmtiles(forecast, tmp_path / "w.pmtiles", [bbox_wkt((-200, 40, 200, 50))], 0, 1))[0]

    assert (header["min_lon_e7"], header["max_lon_e7"]) == (-1_800_000_000, 1_800_000_000)


def test_invalid_polygon_is_made_valid_and_bad_wkt_is_named(tmp_path, forecast):
    bowtie = "POLYGON((0 40,10 50,10 40,0 50,0 40))"

    assert _tiles(to_pmtiles(forecast, tmp_path / "b.pmtiles", [bowtie], 3, 3))[2]
    with pytest.raises(ValueError, match="area 2 is not valid WKT"):
        to_pmtiles(forecast, tmp_path / "x.pmtiles", [bowtie, "POLYGON((0 40"], 3, 3)


@pytest.mark.parametrize(("vmin", "vmax"), [(5, 5), (8, 2), (np.nan, 1), (0, np.inf)])
def test_bad_value_ranges_are_rejected(tmp_path, forecast, vmin, vmax):
    with pytest.raises(ValueError, match="vmin < vmax"):
        to_pmtiles(forecast, tmp_path / "x.pmtiles", [bbox_wkt((0, 40, 10, 50))], 3, 3, vmin=vmin, vmax=vmax)


def test_numpy_limits_are_stored_as_plain_floats(tmp_path, forecast):
    path = to_pmtiles(
        forecast,
        tmp_path / "n.pmtiles",
        [bbox_wkt((0, 40, 10, 50))],
        3,
        3,
        vmin=np.float32(31),
        vmax=np.int64(59),
    )

    assert _tiles(path)[1]["wxforecast"]["legend"]["max"] == 59.0


def test_progress_reports_every_tile_and_can_cancel(tmp_path, forecast):
    calls = []
    to_pmtiles(
        forecast,
        tmp_path / "p.pmtiles",
        [bbox_wkt((0, 40, 10, 50))],
        0,
        3,
        progress=lambda *a: calls.append(a) or True,
    )
    assert calls[-1][0] == calls[-1][1] == len(calls) > 4

    with pytest.raises(Cancelled):
        to_pmtiles(
            forecast,
            tmp_path / "c.pmtiles",
            [bbox_wkt((0, 40, 10, 50))],
            0,
            3,
            progress=lambda done, total: done < 3,
        )
    assert [p.name for p in tmp_path.iterdir()] == ["p.pmtiles"]


def test_a_nan_cell_lands_on_its_tile_pixel(tmp_path, forecast):
    lon, lat, z = 2.25, 48.75, 7
    values = np.full_like(forecast.values, 10.0)
    values[int((60.125 - lat) / 0.25), int((lon + 20.125) / 0.25)] = np.nan
    hole = Forecast(**{**forecast.__dict__, "values": values})

    tiles_by_id = _tiles(
        to_pmtiles(hole, tmp_path / "h.pmtiles", [bbox_wkt((-5, 45, 10, 52))], z, z, clip=False)
    )[2]

    n = 1 << z
    x, y = (lon + 180) / 360 * n, (1 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2 * n
    rows, cols = np.nonzero(_rgba(tiles_by_id[(z, int(x), int(y))])[3] == 0)
    assert rows.size and abs(rows.mean() - (y % 1) * 256) < 1 and abs(cols.mean() - (x % 1) * 256) < 1
