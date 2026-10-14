"""Write a Forecast as coloured Web Mercator PNG tiles in a PMTiles archive, over bboxes or polygons."""

from __future__ import annotations

import json
import logging
import math
import uuid
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np
from pmtiles.tile import Compression, TileType, zxy_to_tileid
from pmtiles.writer import Writer

from wxforecast import colors, wind
from wxforecast.geotiff import atomic, mem_dataset, srs
from wxforecast.grid import Bbox

if TYPE_CHECKING:
    from osgeo import ogr

    from wxforecast.reader import Forecast

logger = logging.getLogger(__name__)

TILE = 256
MAX_ZOOM = 12
MAX_TILES = 100_000
NATIVE_ZOOM_MARGIN = 2
MERCATOR_LAT = 85.0511287798
HALF_WORLD = 20037508.342789244
WORLD = (-180.0, -MERCATOR_LAT, 180.0, MERCATOR_LAT)


class Cancelled(Exception):
    """to_pmtiles stopped because its progress callback returned False."""


def bbox_wkt(bbox: Bbox) -> str:
    """Polygon WKT (EPSG:4326) of a (west, south, east, north) bbox; it must not cross the antimeridian."""
    west, south, east, north = bbox
    if west > east:
        raise ValueError("bboxes crossing the antimeridian are not supported for tiles, split them in two")
    return f"POLYGON(({west} {south},{east} {south},{east} {north},{west} {north},{west} {south}))"


def areas_from_vector(path: str | Path, layer: str | None = None) -> list[str]:
    """WKT in EPSG:4326 of every polygon feature of a vector file (GeoPackage, GeoJSON, Shapefile...)."""
    from osgeo import ogr, osr

    with ogr.ExceptionMgr(useExceptions=True), osr.ExceptionMgr(useExceptions=True):
        source = ogr.Open(str(path))
        lyr = source.GetLayerByName(layer) if layer else source.GetLayer(0)
        crs = lyr.GetSpatialRef()
        transform = osr.CoordinateTransformation(crs, srs("EPSG:4326")) if crs else None
        areas = []
        for feature in lyr:
            geometry = feature.GetGeometryRef()
            if geometry is None or geometry.IsEmpty():
                continue
            geometry = geometry.Clone()
            if transform:
                geometry.Transform(transform)
            areas.append(geometry.ExportToWkt())
    if not areas:
        raise ValueError(f"no polygon in {path}")
    return areas


def areas_bbox(areas: list[str]) -> Bbox:
    """Bbox of the areas, to pass to fetch()."""
    from osgeo import ogr

    with ogr.ExceptionMgr(useExceptions=True):
        envelopes = [_geometry(wkt, index).GetEnvelope() for index, wkt in enumerate(areas)]
    return (
        min(e[0] for e in envelopes),
        max(min(e[2] for e in envelopes), -90.0),
        max(e[1] for e in envelopes),
        min(max(e[3] for e in envelopes), 90.0),
    )


def native_zoom(geotransform: tuple[float, ...]) -> int:
    """First zoom whose pixels are no wider than a grid cell, plus NATIVE_ZOOM_MARGIN, capped at MAX_ZOOM."""
    cell = min(abs(geotransform[1]), abs(geotransform[5]))
    zoom = max(math.ceil(math.log2(360 / (TILE * cell))), 0) + NATIVE_ZOOM_MARGIN
    return min(zoom, MAX_ZOOM)


def to_pmtiles(
    forecast: Forecast,
    path: str | Path,
    areas: list[str],
    min_zoom: int = 0,
    max_zoom: int | None = None,
    clip: bool = True,
    vmin: float | None = None,
    vmax: float | None = None,
    styles: str | Path | None = None,
    progress: Callable[[int, int], bool] | None = None,
) -> Path:
    """Tiles touching the areas (WKT polygons in EPSG:4326) from min_zoom to max_zoom; `clip` makes pixels
    outside the polygons transparent. `max_zoom` None is the forecast's `native_zoom`, at least min_zoom.

    `styles` is a style override file (see `colors.load_styles`). A wind variable is coloured by speed
    with arrows drawn on top, pointing where the wind blows to. `progress(done, total)` is called after
    each tile; returning False stops and raises Cancelled, leaving no file.
    """
    from osgeo import gdal, ogr, osr

    if not areas:
        raise ValueError("at least one area is needed")
    if max_zoom is None:
        max_zoom = max(native_zoom(forecast.geotransform), min_zoom)
        logger.info("max zoom %d from the %s grid", max_zoom, forecast.model)
    if not 0 <= min_zoom <= max_zoom <= MAX_ZOOM:
        raise ValueError(f"zoom levels must satisfy 0 <= min <= max <= {MAX_ZOOM}")
    kind = colors.variable_kind(forecast.variable, forecast.unit, styles)
    vmin, vmax = _limits(forecast.values, kind, vmin, vmax, styles)
    zooms = range(min_zoom, max_zoom + 1)

    written = 0
    with (
        gdal.ExceptionMgr(useExceptions=True),
        ogr.ExceptionMgr(useExceptions=True),
        osr.ExceptionMgr(useExceptions=True),
    ):
        shape = _multipolygon([_union(areas).Intersection(ogr.CreateGeometryFromWkt(bbox_wkt(WORLD)))])
        if shape.IsEmpty():
            raise ValueError(f"the areas are outside the Web Mercator latitudes (+-{MERCATOR_LAT:.2f})")
        bounds = shape.GetEnvelope()
        parts = [part.GetEnvelope() for part in _polygons(shape)]
        estimate = sum(min(_count(z, bounds), sum(_count(z, p) for p in parts)) for z in zooms)
        if estimate > MAX_TILES:
            raise ValueError(f"about {estimate} tiles exceed the limit of {MAX_TILES}, lower the max zoom")
        shape.Transform(osr.CoordinateTransformation(srs("EPSG:4326"), srs("EPSG:3857")))
        prepared = shape.CreatePreparedGeometry()
        plan = sorted(
            (z, x, y)
            for z in zooms
            for x, y in _candidates(z, bounds, parts)
            if prepared.Intersects(_box(z, x, y))
        )
        mask = _mask(shape) if clip else None
        source = _source(forecast, kind, styles)
        with atomic(path) as tmp, open(tmp, "wb") as f:
            writer = Writer(f)
            for done, (z, x, y) in enumerate(plan, start=1):
                png = _tile(source, z, x, y, prepared, mask, kind, vmin, vmax, styles)
                if png is not None:
                    writer.write_tile(zxy_to_tileid(z, x, y), png)
                    written += 1
                if progress is not None and not progress(done, len(plan)):
                    raise Cancelled(f"cancelled after {done} of {len(plan)} tiles")
            if not written:
                raise ValueError("no tile has data inside the areas")
            metadata = _metadata(forecast, kind, vmin, vmax, styles)
            writer.finalize(_header(bounds, min_zoom, max_zoom), metadata)
    logger.info("wrote %s: %d tiles", path, written)
    return Path(path)


def to_pmtiles_steps(
    forecasts: list[Forecast],
    folder: str | Path,
    areas: list[str],
    min_zoom: int = 0,
    max_zoom: int | None = None,
    clip: bool = True,
    vmin: float | None = None,
    vmax: float | None = None,
    styles: str | Path | None = None,
    progress: Callable[[int, int], bool] | None = None,
) -> Path:
    """One archive per step, `<variable>_<YYYYMMDDTHH>.pmtiles` (see `step_name`), all coloured over one
    value range so colours compare across time, then `index.json` listing them; returns its path.

    `progress(done, total)` counts the tiles of every step.
    """
    from wxforecast.reader import step_name

    if not forecasts:
        raise ValueError("at least one forecast is needed")
    first = forecasts[0]
    kind = colors.variable_kind(first.variable, first.unit, styles)
    vmin, vmax = _limits(np.stack([f.values for f in forecasts]), kind, vmin, vmax, styles)
    folder = Path(folder)
    steps = []
    for k, forecast in enumerate(forecasts):

        def step_progress(done: int, total: int, k: int = k) -> bool:
            return progress is None or progress(k * total + done, len(forecasts) * total)

        name = f"{step_name(forecast)}.pmtiles"
        to_pmtiles(
            forecast, folder / name, areas, min_zoom, max_zoom, clip, vmin, vmax, styles, step_progress
        )
        lead_hours = (forecast.valid_time - forecast.init_time).total_seconds() / 3600
        steps.append({"valid_time": forecast.valid_time.isoformat(), "lead_hours": lead_hours, "file": name})
    index = {
        "model": first.model,
        "variable": first.variable,
        "unit": first.unit,
        "init_time": first.init_time.isoformat(),
        "legend": colors.legend(kind, vmin, vmax, first.unit, styles),
        "steps": steps,
    }
    with atomic(folder / "index.json") as tmp:
        tmp.write_text(json.dumps(index, indent=2))
    return folder / "index.json"


def _limits(values: np.ndarray, kind: str, vmin, vmax, styles) -> tuple[float, float]:
    low, high = colors.value_range(values, kind, styles)
    vmin, vmax = float(low if vmin is None else vmin), float(high if vmax is None else vmax)
    if not (math.isfinite(vmin) and math.isfinite(vmax) and vmin < vmax):
        raise ValueError(f"vmin {vmin:g} and vmax {vmax:g} must be finite with vmin < vmax")
    return vmin, vmax


def _geometry(wkt: str, index: int) -> ogr.Geometry:
    from osgeo import ogr

    try:
        return ogr.CreateGeometryFromWkt(wkt)
    except RuntimeError as error:
        raise ValueError(f"area {index + 1} is not valid WKT: {error}") from error


def _union(areas: list[str]) -> ogr.Geometry:
    """Union of the polygons of the areas, each made valid first."""

    valid = []
    for index, wkt in enumerate(areas):
        geometry = _geometry(wkt, index).GetLinearGeometry()
        geometry.FlattenTo2D()
        valid.append(geometry.MakeValid())
    polygons = _multipolygon(valid)
    if polygons.IsEmpty():
        raise ValueError("the areas hold no polygon")
    return polygons.UnaryUnion()


def _multipolygon(geometries: list[ogr.Geometry]) -> ogr.Geometry:
    """The polygons of the geometries, dropping lines and points (e.g. from MakeValid or Intersection)."""
    from osgeo import ogr

    multi = ogr.Geometry(ogr.wkbMultiPolygon)
    for geometry in geometries:
        for polygon in _polygons(geometry):
            multi.AddGeometry(polygon)
    return multi


def _polygons(geometry: ogr.Geometry) -> Iterator[ogr.Geometry]:
    from osgeo import ogr

    if ogr.GT_Flatten(geometry.GetGeometryType()) == ogr.wkbPolygon:
        yield geometry
    else:
        for i in range(geometry.GetGeometryCount()):
            yield from _polygons(geometry.GetGeometryRef(i))


def _source(forecast: Forecast, kind: str, styles):
    """One band, or for wind the u and v bands: an angle must never be interpolated.

    Values outside the kind's valid range become NaN first, so resampling never blends a fill value in.
    """
    if forecast.direction is not None:
        return mem_dataset(list(wind.components(forecast.values, forecast.direction)), forecast.geotransform)
    values = np.where(colors.valid(forecast.values, kind, styles), forecast.values, np.nan)
    return mem_dataset([values.astype(np.float32)], forecast.geotransform)


def _tile_range(z: int, envelope: tuple[float, ...]) -> tuple[int, int, int, int]:
    """x0, x1, y0, y1 of the tiles covering an OGR envelope (west, east, south, north) in degrees."""
    x0, y0 = _tile_of(envelope[0], envelope[3], z)
    x1, y1 = _tile_of(envelope[1], envelope[2], z)
    return x0, x1, y0, y1


def _count(z: int, envelope: tuple[float, ...]) -> int:
    x0, x1, y0, y1 = _tile_range(z, envelope)
    return (x1 - x0 + 1) * (y1 - y0 + 1)


def _candidates(z: int, bounds: tuple[float, ...], parts: list[tuple[float, ...]]) -> set[tuple[int, int]]:
    """Tiles of the overall envelope, or of each part's envelope when that is fewer tiles."""
    envelopes = [bounds] if _count(z, bounds) <= sum(_count(z, p) for p in parts) else parts
    tiles = set()
    for envelope in envelopes:
        x0, x1, y0, y1 = _tile_range(z, envelope)
        tiles.update((x, y) for x in range(x0, x1 + 1) for y in range(y0, y1 + 1))
    return tiles


def _tile_of(lon: float, lat: float, z: int) -> tuple[int, int]:
    n = 1 << z
    x = int((lon + 180) / 360 * n)
    y = int((1 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2 * n)
    return min(max(x, 0), n - 1), min(max(y, 0), n - 1)


def _bounds(z: int, x: int, y: int) -> tuple[float, float, float, float]:
    size = 2 * HALF_WORLD / (1 << z)
    return (
        -HALF_WORLD + x * size,
        HALF_WORLD - (y + 1) * size,
        -HALF_WORLD + (x + 1) * size,
        HALF_WORLD - y * size,
    )


def _box(z: int, x: int, y: int) -> ogr.Geometry:
    from osgeo import ogr

    minx, miny, maxx, maxy = _bounds(z, x, y)
    return ogr.CreateGeometryFromWkt(
        f"POLYGON(({minx} {miny},{maxx} {miny},{maxx} {maxy},{minx} {maxy},{minx} {miny}))"
    )


def _mask(shape: ogr.Geometry):
    """In-memory vector dataset holding the clip shape (EPSG:3857), to rasterize per tile."""
    from osgeo import ogr

    data = ogr.GetDriverByName("MEM").CreateDataSource("mask")
    layer = data.CreateLayer("mask", srs("EPSG:3857"), ogr.wkbUnknown)
    feature = ogr.Feature(layer.GetLayerDefn())
    feature.SetGeometry(shape)
    layer.CreateFeature(feature)
    return data


def _tile(source, z, x, y, prepared, mask, kind, vmin, vmax, styles) -> bytes | None:
    from osgeo import gdal

    bounds = _bounds(z, x, y)
    warped = gdal.Warp(
        "",
        source,
        format="MEM",
        outputBounds=bounds,
        width=TILE,
        height=TILE,
        dstSRS="EPSG:3857",
        resampleAlg="bilinear",
        dstNodata=float("nan"),
        outputType=gdal.GDT_Float32,
    )
    bands = [warped.GetRasterBand(i + 1).ReadAsArray() for i in range(warped.RasterCount)]
    inside = None
    if mask is not None and not prepared.Contains(_box(z, x, y)):
        inside = gdal.Rasterize(
            "",
            mask,
            format="MEM",
            outputBounds=bounds,
            width=TILE,
            height=TILE,
            burnValues=[1],
            outputType=gdal.GDT_Byte,
        ).ReadAsArray()
    if len(bands) == 2:
        rgba = colors.colorize(np.hypot(*bands), kind, vmin, vmax, styles)
        rgba = wind.draw_arrows(rgba, *bands, vmax, inside)
    else:
        rgba = colors.colorize(bands[0], kind, vmin, vmax, styles)
    if inside is not None:
        rgba[..., 3] *= inside
    if not rgba[..., 3].any():
        return None
    return _png(rgba)


def _png(rgba: np.ndarray) -> bytes:
    from osgeo import gdal, gdal_array

    name = f"/vsimem/{uuid.uuid4().hex}.png"
    gdal.Translate(name, gdal_array.OpenArray(np.ascontiguousarray(np.moveaxis(rgba, -1, 0))), format="PNG")
    try:
        return bytes(gdal.VSIGetMemFileBuffer_unsafe(name))
    finally:
        gdal.Unlink(name)


def _header(bounds: tuple[float, ...], min_zoom: int, max_zoom: int) -> dict:
    west, east, south, north = bounds
    return {
        "tile_type": TileType.PNG,
        "tile_compression": Compression.NONE,
        "min_zoom": min_zoom,
        "max_zoom": max_zoom,
        "min_lon_e7": round(west * 1e7),
        "min_lat_e7": round(south * 1e7),
        "max_lon_e7": round(east * 1e7),
        "max_lat_e7": round(north * 1e7),
        "center_zoom": min_zoom,
        "center_lon_e7": round((west + east) / 2 * 1e7),
        "center_lat_e7": round((south + north) / 2 * 1e7),
    }


def _metadata(forecast: Forecast, kind: str, vmin: float, vmax: float, styles) -> dict:
    return {
        "name": f"{forecast.model} {forecast.variable} {forecast.valid_time:%Y-%m-%d %H:%M} UTC",
        "type": "overlay",
        "format": "png",
        "attribution": "dynamical.org",
        "wxforecast": {
            "model": forecast.model,
            "variable": forecast.variable,
            "unit": forecast.unit,
            "init_time": forecast.init_time.isoformat(),
            "valid_time": forecast.valid_time.isoformat(),
            "legend": colors.legend(kind, vmin, vmax, forecast.unit, styles),
            **({"arrows": _arrows_legend()} if forecast.direction is not None else {}),
        },
    }


def _arrows_legend() -> dict:
    return {
        "points": "to, where the wind blows",
        "spacing_px": wind.ARROW_SPACING,
        "length_px": [wind.ARROW_MIN_LENGTH, wind.ARROW_MAX_LENGTH],
        "length_scale": "linear in speed from 0 to the legend max",
    }
