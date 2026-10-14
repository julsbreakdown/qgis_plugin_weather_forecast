import numpy as np
import pytest

from wxforecast.grid import Window, window

GFS_LAT = np.linspace(90, -90, 721)
GFS_LON = np.linspace(-180, 179.75, 1440)
EAST_LON = np.arange(0, 360, 1.0)
DEG_LAT = np.arange(90, -91, -1.0)
EU_LAT = np.linspace(70.5, 29.5, 657)
EU_LON = np.linspace(-23.5, 62.5, 1377)


def read(w: Window, lat: np.ndarray, lon: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    lats, lons = np.meshgrid(lat, lon, indexing="ij")
    return tuple(np.concatenate([a[w.rows, c] for c in w.cols], axis=1) for a in (lats, lons))


def assert_georeferenced(w: Window, lat: np.ndarray, lon: np.ndarray) -> None:
    lats, lons = read(w, lat, lon)
    x0, dx, _, y0, _, dy = w.geotransform
    rows, cols = lats.shape
    centre_y = y0 + (np.arange(rows) + 0.5) * dy
    centre_x = x0 + (np.arange(cols) + 0.5) * dx
    np.testing.assert_allclose(lats, np.broadcast_to(centre_y[:, None], lats.shape), atol=1e-9)
    offset = (lons - centre_x[None, :] + 180) % 360 - 180
    np.testing.assert_allclose(offset, 0, atol=1e-9)


def cells(w: Window) -> tuple[int, int]:
    return w.rows.stop - w.rows.start, sum(c.stop - c.start for c in w.cols)


def test_whole_grid_uses_cell_corners():
    w = window(GFS_LAT, GFS_LON, None)
    assert w.rows == slice(0, 721)
    assert w.cols == (slice(0, 1440),)
    assert w.geotransform == (-180.125, 0.25, 0.0, 90.125, 0.0, -0.25)
    assert all(type(v) is float for v in w.geotransform)
    assert_georeferenced(w, GFS_LAT, GFS_LON)


def test_bbox_on_cell_edges_takes_exactly_those_cells():
    w = window(GFS_LAT, GFS_LON, (-15.125, 34.875, 35.125, 70.125))
    assert w.rows == slice(80, 221)
    assert w.cols == (slice(660, 861),)
    assert w.geotransform[0] == pytest.approx(-15.125)
    assert w.geotransform[3] == pytest.approx(70.125)
    assert_georeferenced(w, GFS_LAT, GFS_LON)


def test_bbox_snaps_outward():
    w = window(GFS_LAT, GFS_LON, (-15.0, 35.0, 35.0, 70.0))
    x0, dx, _, y0, _, dy = w.geotransform
    rows, cols = cells(w)
    assert x0 <= -15.0 and x0 + cols * dx >= 35.0
    assert y0 >= 70.0 and y0 + rows * dy <= 35.0
    assert (rows, cols) == (141, 201)
    assert_georeferenced(w, GFS_LAT, GFS_LON)


def test_point_bbox_gives_one_cell():
    w = window(GFS_LAT, GFS_LON, (6.1, 46.2, 6.1, 46.2))
    assert cells(w) == (1, 1)
    lats, lons = read(w, GFS_LAT, GFS_LON)
    assert (lats[0, 0], lons[0, 0]) == (46.25, 6.0)


def test_antimeridian_reads_as_one_continuous_image():
    w = window(GFS_LAT, GFS_LON, (170.0, -50.0, -170.0, -30.0))
    assert w.cols == (slice(1400, 1440), slice(0, 41))
    assert w.geotransform[0] == pytest.approx(169.875)
    assert_georeferenced(w, GFS_LAT, GFS_LON)
    _, lons = read(w, GFS_LAT, GFS_LON)
    assert np.all(np.diff(np.unwrap(lons[0], period=360)) == pytest.approx(0.25))


def test_bbox_in_the_seam_cell_of_a_global_grid():
    w = window(GFS_LAT, GFS_LON, (179.9, 0.0, -179.9, 1.0))
    assert w.cols == (slice(0, 1),)
    assert_georeferenced(w, GFS_LAT, GFS_LON)


def test_full_turn_wider_than_the_grid_is_capped():
    w = window(GFS_LAT, GFS_LON, (-200.0, -90.0, 200.0, 90.0))
    assert cells(w) == (721, 1440)
    assert_georeferenced(w, GFS_LAT, GFS_LON)


def test_grid_from_0_to_360_with_negative_bbox():
    w = window(DEG_LAT, EAST_LON, (-10.0, 40.0, 10.0, 50.0))
    assert w.cols == (slice(350, 360), slice(0, 11))
    assert w.geotransform[0] == pytest.approx(-10.5)
    assert_georeferenced(w, DEG_LAT, EAST_LON)


def test_bbox_partly_outside_is_clipped_to_the_grid():
    w = window(EU_LAT, EU_LON, (-40.0, 20.0, 0.0, 80.0))
    assert w.rows == slice(0, 657)
    assert w.cols == (slice(0, 377),)
    assert w.geotransform[0] == pytest.approx(-23.53125)
    assert w.geotransform[3] == pytest.approx(70.53125)
    assert_georeferenced(w, EU_LAT, EU_LON)


def test_regional_grid_with_antimeridian_bbox_on_far_side():
    w = window(EU_LAT, EU_LON, (60.0, 40.0, -170.0, 50.0))
    assert w.cols == (slice(1336, 1377),)
    assert_georeferenced(w, EU_LAT, EU_LON)


def test_regional_grid_hit_on_both_sides_returns_whole_width():
    w = window(EU_LAT, EU_LON, (50.0, 40.0, 0.0, 50.0))
    assert w.cols == (slice(0, 1377),)
    assert_georeferenced(w, EU_LAT, EU_LON)


@pytest.mark.parametrize(
    "bbox",
    [(100.0, 40.0, 120.0, 50.0), (0.0, 75.0, 10.0, 80.0), (0.0, 10.0, 10.0, 20.0)],
)
def test_bbox_outside_the_grid_raises(bbox):
    with pytest.raises(ValueError, match="outside the grid"):
        window(EU_LAT, EU_LON, bbox)


def test_south_above_north_raises():
    with pytest.raises(ValueError, match="above north"):
        window(GFS_LAT, GFS_LON, (0.0, 50.0, 10.0, 40.0))


def test_ascending_latitude_is_rejected():
    with pytest.raises(ValueError, match="descending"):
        window(GFS_LAT[::-1], GFS_LON, None)


def test_irregular_axis_is_rejected():
    with pytest.raises(ValueError, match="regularly spaced"):
        window(GFS_LAT, np.array([0.0, 1.0, 3.0]), None)
