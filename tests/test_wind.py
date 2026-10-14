import json
import math
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import pytest
import zarr
from zarr.storage import MemoryStore

from wxforecast import catalog, reader, wind
from wxforecast.geotiff import to_geotiff
from wxforecast.reader import Forecast
from wxforecast.tiles import HALF_WORLD, TILE, bbox_wkt, to_pmtiles

gdal = pytest.importorskip("osgeo.gdal")
ogr = pytest.importorskip("osgeo.ogr")
osr = pytest.importorskip("osgeo.osr")
pmtiles_reader = pytest.importorskip("pmtiles.reader")

FIXTURES = Path(__file__).parent / "fixtures" / "stac"
RUN = datetime(2026, 10, 12, 0, tzinfo=UTC)
GEOTRANSFORM = (-20.125, 0.25, 0.0, 60.125, 0.0, -0.25)


def wind_forecast(u: float, v: float, shape=(120, 200)) -> Forecast:
    speed, direction = wind.speed_direction(np.full(shape, u), np.full(shape, v))
    return Forecast(
        model="noaa-gfs-forecast",
        variable="wind_10m",
        unit="m s-1",
        init_time=RUN,
        valid_time=RUN + timedelta(hours=6),
        values=speed,
        geotransform=GEOTRANSFORM,
        direction=direction,
    )


@pytest.mark.parametrize(
    ("u", "v", "direction"),
    [(5.0, 0.0, 270.0), (-5.0, 0.0, 90.0), (0.0, 5.0, 180.0), (0.0, -5.0, 0.0), (3.0, 3.0, 225.0)],
)
def test_direction_is_where_the_wind_blows_from(u, v, direction):
    speed, got = wind.speed_direction(np.array([u]), np.array([v]))
    assert speed[0] == pytest.approx(math.hypot(u, v))
    assert got[0] % 360 == pytest.approx(direction, abs=1e-4)
    back_u, back_v = wind.components(speed, got)
    assert (back_u[0], back_v[0]) == (pytest.approx(u, abs=1e-5), pytest.approx(v, abs=1e-5))


def test_catalog_derives_wind_and_hides_components():
    gfs = catalog.parse(json.loads((FIXTURES / "noaa-gfs-forecast.json").read_text()))

    assert gfs.variables["wind_10m"] == catalog.Variable(
        "wind_10m", "m s-1", "10 metre wind", ("wind_u_10m", "wind_v_10m")
    )
    assert {"wind_u_10m", "wind_v_10m"} <= gfs.variables.keys()
    shown = gfs.display_variables
    assert "wind_10m" in shown
    assert not [name for name in shown if name.startswith(("wind_u_", "wind_v_"))]
    assert "temperature_2m" in shown


def test_unpaired_component_is_not_derived():
    variables = {"wind_u_10m": catalog.Variable("wind_u_10m", "m s-1", "10 metre U wind component")}
    assert catalog.wind_variables(variables) == {}


@pytest.fixture
def wind_store(monkeypatch):
    lat, lon = np.arange(60.0, 29.0, -1.0), np.arange(-20.0, 30.0, 1.0)
    group = zarr.open_group(MemoryStore(), mode="w")
    axes = {
        "init_time": np.array([RUN.timestamp()]),
        "lead_time": np.array([0.0, 6 * 3600.0]),
        "latitude": lat,
        "longitude": lon,
    }
    for name, values in axes.items():
        group.create_array(name, data=values, dimension_names=(name,))
    shape = (1, 2, len(lat), len(lon))
    for name, value in (("wind_u_10m", 10.0), ("wind_v_10m", 0.0)):
        array = group.create_array(
            name,
            shape=shape,
            dtype=np.float32,
            fill_value=np.nan,
            dimension_names=("init_time", "lead_time", "latitude", "longitude"),
        )
        array[:] = np.full(shape, value, dtype=np.float32)
    names = ("wind_u_10m", "wind_v_10m")
    variables = {name: catalog.Variable(name, "m s-1", name) for name in names}
    variables |= catalog.wind_variables(variables)
    model = catalog.Model(
        "fake",
        "Fake",
        "https://example.invalid",
        ("init_time", "lead_time", "latitude", "longitude"),
        variables,
    )
    monkeypatch.setattr(reader.catalog, "model", lambda model_id: model)
    monkeypatch.setattr(reader, "_open_href", lambda href: zarr.open_group(group.store, mode="r"))


def test_fetch_wind_returns_speed_and_direction(wind_store):
    forecast = reader.fetch("fake", "wind_10m", bbox=(0, 40, 10, 50), run=RUN, lead=timedelta(hours=6))

    assert forecast.variable == "wind_10m"
    assert forecast.unit == "m s-1"
    np.testing.assert_allclose(forecast.values, 10.0)
    np.testing.assert_allclose(forecast.direction, 270.0)
    assert forecast.direction.shape == forecast.values.shape


def test_fetch_component_still_works(wind_store):
    forecast = reader.fetch("fake", "wind_u_10m", run=RUN, lead=timedelta(0))

    np.testing.assert_allclose(forecast.values, 10.0)
    assert forecast.direction is None


def test_geotiff_has_speed_and_direction_bands(tmp_path):
    path = to_geotiff(wind_forecast(10.0, 0.0), tmp_path / "w.tif")

    with gdal.ExceptionMgr(useExceptions=True):
        dataset = gdal.Open(str(path))
        assert dataset.RasterCount == 2
        speed, direction = dataset.GetRasterBand(1), dataset.GetRasterBand(2)
        assert (speed.GetDescription(), speed.GetUnitType()) == ("wind_10m", "m s-1")
        assert (direction.GetDescription(), direction.GetUnitType()) == ("direction", "degree")
        assert dataset.GetMetadata()["direction"] == wind.DIRECTION_CONVENTION
        np.testing.assert_allclose(direction.ReadAsArray(), 270.0)


def test_eastward_flow_lines_run_east_along_their_row(tmp_path):
    forecast = wind_forecast(5.0, 0.0, shape=(120, 200))

    lines = wind.flow_lines(forecast)

    assert len(lines) > 1000
    for vertices, speed, phase in lines:
        assert np.all(np.diff(vertices[:, 0]) > 0)
        np.testing.assert_allclose(vertices[:, 1], vertices[0, 1])
        assert speed == pytest.approx(5.0) and 0 <= phase < 10
    complete = [vertices for vertices, _, _ in lines if len(vertices) == wind.FLOW_STEPS + 1]
    assert complete
    for vertices in complete:
        expected = wind.FLOW_STEPS * 0.25 / math.cos(math.radians(vertices[0, 1]))
        assert vertices[-1, 0] - vertices[0, 0] == pytest.approx(expected, rel=0.01)


def test_northward_flow_lines_run_north_and_stay_in_the_grid():
    forecast = wind_forecast(0.0, 3.0)

    lines = wind.flow_lines(forecast)

    west, north = GEOTRANSFORM[0], GEOTRANSFORM[3]
    for vertices, _, _ in lines:
        assert np.all(np.diff(vertices[:, 1]) > 0)
        assert vertices[:, 0].min() > west and vertices[:, 1].max() < north


def test_flow_lines_stop_at_nodata():
    forecast = wind_forecast(5.0, 0.0)
    forecast.values[:, 100:] = np.nan

    lines = wind.flow_lines(forecast)

    edge = GEOTRANSFORM[0] + 100 * 0.25
    assert lines and max(vertices[:, 0].max() for vertices, _, _ in lines) < edge


def test_flow_lines_are_reproducible_and_written_to_geopackage(tmp_path):
    forecast = wind_forecast(2.0, 2.0)
    assert [v.tolist() for v, _, _ in wind.flow_lines(forecast)] == [
        v.tolist() for v, _, _ in wind.flow_lines(forecast)
    ]

    path = wind.to_flow_lines(forecast, tmp_path / "f.gpkg")

    with ogr.ExceptionMgr(useExceptions=True):
        data = ogr.Open(str(path))
        layer = data.GetLayerByName("flow")
        assert layer.GetFeatureCount() == len(wind.flow_lines(forecast))
        assert layer.GetGeomType() == ogr.wkbLineString
        feature = layer.GetNextFeature()
        assert feature["speed"] == pytest.approx(2 * math.sqrt(2))
    assert [p.name for p in tmp_path.iterdir()] == ["f.gpkg"]


def test_flow_lines_reject_other_variables():
    forecast = wind_forecast(1.0, 0.0)
    with pytest.raises(ValueError, match="not a wind variable"):
        wind.flow_lines(Forecast(**{**forecast.__dict__, "variable": "t", "direction": None}))


def _tiles(path):
    with open(path, "rb") as f:
        archive = pmtiles_reader.Reader(pmtiles_reader.MmapSource(f))
        return archive.metadata(), dict(pmtiles_reader.all_tiles(archive.get_bytes))


def _rgba(png):
    with gdal.ExceptionMgr(useExceptions=True):
        gdal.FileFromMemBuffer("/vsimem/wind.png", png)
        try:
            return np.moveaxis(gdal.Open("/vsimem/wind.png").ReadAsArray(), 0, -1)
        finally:
            gdal.Unlink("/vsimem/wind.png")


def _dark(rgba):
    return (rgba[..., :3].max(axis=-1) < 80) & (rgba[..., 3] > 0)


def _heads(dark, axis):
    """Per arrow cell, spread of dark pixels across `axis` on the far half minus the near half."""
    half = wind.ARROW_SPACING // 2
    spreads = []
    for row in range(0, TILE, wind.ARROW_SPACING):
        for col in range(0, TILE, wind.ARROW_SPACING):
            cell = dark[row : row + wind.ARROW_SPACING, col : col + wind.ARROW_SPACING]
            if axis == 1:
                cell = cell.T
            ys, xs = np.nonzero(cell)
            if xs.size:
                spreads.append(np.ptp(ys[xs > half]) - np.ptp(ys[xs < half]))
    return spreads


def test_eastward_wind_arrows_point_east(tmp_path):
    path = to_pmtiles(
        wind_forecast(10.0, 0.0), tmp_path / "w.pmtiles", [bbox_wkt((-5, 40, 5, 50))], 4, 4, clip=False
    )

    metadata, tiles = _tiles(path)
    assert metadata["wxforecast"]["arrows"]["spacing_px"] == wind.ARROW_SPACING
    assert metadata["wxforecast"]["legend"]["colors"][0] == "#f7fcfd"
    rgba = _rgba(tiles[(4, 8, 5)])
    spreads = _heads(_dark(rgba), axis=0)
    assert len(spreads) == (TILE // wind.ARROW_SPACING) ** 2
    assert all(spread >= 3 for spread in spreads)


def test_northward_wind_arrows_point_north(tmp_path):
    path = to_pmtiles(
        wind_forecast(0.0, 10.0), tmp_path / "n.pmtiles", [bbox_wkt((-5, 40, 5, 50))], 4, 4, clip=False
    )

    rgba = _rgba(_tiles(path)[1][(4, 8, 5)])
    # Pixel rows grow southward, so the head sits in the near half.
    assert all(spread <= -3 for spread in _heads(_dark(rgba), axis=1))


def test_arrows_never_cross_the_clip(tmp_path):
    z = 5
    triangle = "POLYGON((0 40,10 40,0 50,0 40))"

    tiles = _tiles(to_pmtiles(wind_forecast(10.0, 5.0), tmp_path / "c.pmtiles", [triangle], z, z))[1]

    pixel = 2 * HALF_WORLD / (1 << z) / TILE
    with ogr.ExceptionMgr(useExceptions=True), osr.ExceptionMgr(useExceptions=True):
        polygon = ogr.CreateGeometryFromWkt(triangle)
        polygon.Transform(osr.CoordinateTransformation(_srs(4326), _srs(3857)))
        visible = ogr.Geometry(ogr.wkbMultiPoint)
        arrow_pixels = 0
        for (_, x, y), png in tiles.items():
            rgba = _rgba(png)
            arrow_pixels += int(_dark(rgba).sum())
            for row, col in zip(*np.nonzero(rgba[..., 3]), strict=True):
                point = ogr.Geometry(ogr.wkbPoint)
                point.AddPoint_2D(
                    -HALF_WORLD + (x * TILE + col + 0.5) * pixel, HALF_WORLD - (y * TILE + row + 0.5) * pixel
                )
                visible.AddGeometry(point)
        assert arrow_pixels > 0
        assert visible.Difference(polygon.Buffer(pixel)).IsEmpty()


def _srs(epsg):
    srs = osr.SpatialReference()
    srs.ImportFromEPSG(epsg)
    srs.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    return srs


def test_draw_arrows_skips_masked_and_nodata_centres():
    rgba = np.zeros((64, 64, 4), dtype=np.uint8)
    u = np.full((64, 64), 5.0, dtype=np.float32)
    v = np.zeros_like(u)
    u[:32, 32:] = np.nan
    mask = np.ones((64, 64), dtype=np.uint8)
    mask[32:, :32] = 0

    out = wind.draw_arrows(rgba, u, v, 10.0, mask)

    drawn = [bool(out[r : r + 32, c : c + 32, 3].any()) for r in (0, 32) for c in (0, 32)]
    assert drawn == [True, False, False, True]


def test_flow_lines_geopackage_types_even_without_lines(tmp_path):
    cases = {"some": wind_forecast(2.0, 1.0), "none": wind_forecast(2.0, 1.0, shape=(1, 1))}
    for name, forecast in cases.items():
        path = wind.to_flow_lines(forecast, tmp_path / f"{name}.gpkg")
        with ogr.ExceptionMgr(useExceptions=True):
            data = ogr.Open(str(path))
            layer = data.GetLayerByName("flow")
            fields = [(f.GetName(), f.GetTypeName()) for f in layer.schema]
            assert fields == [("speed", "Real"), ("phase", "Real"), ("valid_time", "DateTime")]
            assert layer.GetGeomType() == ogr.wkbLineString
            assert layer.GetSpatialRef().GetAuthorityCode(None) == "4326"
            assert (layer.GetFeatureCount() > 0) == (name == "some")
