"""Wind speed and direction from u/v components, arrows drawn in tiles and traced flow lines."""

from __future__ import annotations

import json
import logging
from datetime import UTC
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from wxforecast.reader import Forecast

logger = logging.getLogger(__name__)

ARROW_SPACING = 32
ARROW_MIN_LENGTH = 10.0
ARROW_MAX_LENGTH = 28.0
ARROW_WIDTH = 1.6
ARROW_COLOR = (25, 25, 25)
HALO_COLOR = (255, 255, 255)
HALO_WIDTH = 1.5
FLOW_SEEDS_ACROSS = 45
FLOW_STEPS = 60
DIRECTION_CONVENTION = "degrees clockwise from north, direction the wind blows from"


def speed_direction(u: np.ndarray, v: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Speed and meteorological direction (degrees clockwise from north the wind blows FROM)."""
    u = np.asarray(u, dtype=np.float32)
    v = np.asarray(v, dtype=np.float32)
    speed = np.hypot(u, v)
    direction = np.degrees(np.arctan2(-u, -v)) % 360
    return speed.astype(np.float32), direction.astype(np.float32)


def components(speed: np.ndarray, direction: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Eastward and northward components back from speed and meteorological direction."""
    radians = np.radians(direction)
    return (-speed * np.sin(radians)).astype(np.float32), (-speed * np.cos(radians)).astype(np.float32)


def draw_arrows(
    rgba: np.ndarray, u: np.ndarray, v: np.ndarray, vmax: float, mask: np.ndarray | None = None
) -> np.ndarray:
    """Composite arrows pointing downwind onto a north-up RGBA tile, one per ARROW_SPACING cell.

    `u` and `v` are already resampled to the tile pixels; an arrow is sampled at its cell centre, so
    with a tile size that is a multiple of ARROW_SPACING arrows line up across tile edges. Arrows whose
    centre is nodata or outside `mask` (0 = outside) are skipped.
    """
    rows, cols = u.shape
    half = ARROW_SPACING // 2
    cu = u[half::ARROW_SPACING, half::ARROW_SPACING].astype(np.float64)
    cv = v[half::ARROW_SPACING, half::ARROW_SPACING].astype(np.float64)
    speed = np.hypot(cu, cv)
    keep = np.isfinite(speed) & (speed > 0)
    if mask is not None:
        keep &= mask[half::ARROW_SPACING, half::ARROW_SPACING] > 0
    if not keep.any():
        return rgba
    with np.errstate(invalid="ignore", divide="ignore"):
        dx, dy = np.where(keep, cu / speed, 0), np.where(keep, -cv / speed, 0)
    fraction = np.clip(np.nan_to_num(speed) / vmax if vmax > 0 else 0, 0, 1)
    length = ARROW_MIN_LENGTH + (ARROW_MAX_LENGTH - ARROW_MIN_LENGTH) * fraction

    def per_pixel(cell: np.ndarray) -> np.ndarray:
        return np.repeat(np.repeat(cell, ARROW_SPACING, axis=0), ARROW_SPACING, axis=1)[:rows, :cols]

    dx, dy, length, keep = per_pixel(dx), per_pixel(dy), per_pixel(length), per_pixel(keep)
    y, x = np.mgrid[0:rows, 0:cols] + 0.5
    x = x - ((x // ARROW_SPACING) * ARROW_SPACING + half)
    y = y - ((y // ARROW_SPACING) * ARROW_SPACING + half)
    tip = np.stack([dx, dy]) * length / 2
    head = np.minimum(6.0, length * 0.45)
    cos_wing, sin_wing = np.cos(np.radians(150)), np.sin(np.radians(150))
    wings = [
        tip + head * np.stack([dx * cos_wing - dy * sign * sin_wing, dx * sign * sin_wing + dy * cos_wing])
        for sign in (1, -1)
    ]
    point = np.stack([x, y])
    distance = np.minimum.reduce(
        [_segment_distance(point, -tip, tip), *(_segment_distance(point, tip, w) for w in wings)]
    )
    distance = np.where(keep, distance, np.inf)

    out = rgba.astype(np.float64)
    for width, color in ((ARROW_WIDTH + 2 * HALO_WIDTH, HALO_COLOR), (ARROW_WIDTH, ARROW_COLOR)):
        cover = np.clip(width / 2 + 0.5 - distance, 0, 1)
        alpha = out[..., 3] / 255
        out_alpha = cover + alpha * (1 - cover)
        with np.errstate(invalid="ignore", divide="ignore"):
            for channel in range(3):
                blended = color[channel] * cover + out[..., channel] * alpha * (1 - cover)
                out[..., channel] = np.where(out_alpha > 0, blended / out_alpha, out[..., channel])
        out[..., 3] = out_alpha * 255
    return out.round().astype(np.uint8)


def _segment_distance(point: np.ndarray, start: np.ndarray, end: np.ndarray) -> np.ndarray:
    along = end - start
    squared = (along**2).sum(axis=0)
    with np.errstate(invalid="ignore", divide="ignore"):
        t = np.clip(np.nan_to_num(((point - start) * along).sum(axis=0) / squared), 0, 1)
    return np.hypot(*(point - start - t * along))


def _bilinear(field: np.ndarray, row: np.ndarray, col: np.ndarray) -> np.ndarray:
    r0 = np.clip(np.floor(row).astype(int), 0, field.shape[0] - 2)
    c0 = np.clip(np.floor(col).astype(int), 0, field.shape[1] - 2)
    fr, fc = row - r0, col - c0
    top = field[r0, c0] * (1 - fc) + field[r0, c0 + 1] * fc
    bottom = field[r0 + 1, c0] * (1 - fc) + field[r0 + 1, c0 + 1] * fc
    return top * (1 - fr) + bottom * fr


def flow_lines(
    forecast: Forecast, across: int = FLOW_SEEDS_ACROSS, seed: int = 0
) -> list[tuple[np.ndarray, float, float]]:
    """Streamlines traced from jittered seeds: (lon/lat vertices (n, 2), mean speed, dash phase 0..10).

    The fastest wind in the field moves one grid cell per step, so line length shows relative speed.
    """
    if forecast.direction is None:
        raise ValueError(f"{forecast.variable} is not a wind variable")
    u, v = components(forecast.values.astype(np.float64), forecast.direction.astype(np.float64))
    if u.shape[0] < 2 or u.shape[1] < 2 or not np.isfinite(forecast.values).any():
        return []
    scale = 1 / max(float(np.nanmax(forecast.values)), 1e-9)
    x0, dx, _, y0, _, dy = forecast.geotransform
    rng = np.random.default_rng(seed)
    stride = max(1, round(u.shape[1] / across))
    rows, cols = np.meshgrid(
        np.arange(0, u.shape[0], stride), np.arange(0, u.shape[1], stride), indexing="ij"
    )
    row = (rows.ravel() + rng.uniform(0, stride, rows.size)).clip(0, u.shape[0] - 1)
    col = (cols.ravel() + rng.uniform(0, stride, cols.size)).clip(0, u.shape[1] - 1)

    path = np.full((FLOW_STEPS + 1, row.size, 2), np.nan)
    path[0] = np.stack([row, col], axis=1)
    alive = np.ones(row.size, dtype=bool)
    for i in range(1, FLOW_STEPS + 1):
        su, sv = _bilinear(u, row, col), _bilinear(v, row, col)
        alive &= np.isfinite(su) & np.isfinite(sv)
        # East cells shrink by cos(lat) on a lon/lat grid.
        cos_lat = np.maximum(np.cos(np.radians(y0 + (row + 0.5) * dy)), 0.05)
        col = col + scale * np.where(alive, su, 0) / cos_lat
        row = row - scale * np.where(alive, sv, 0) * np.sign(-dy)
        alive &= (row >= 0) & (row <= u.shape[0] - 1) & (col >= 0) & (col <= u.shape[1] - 1)
        alive &= np.isfinite(_bilinear(u, row, col))
        path[i] = np.where(alive[:, None], np.stack([row, col], axis=1), np.nan)

    lines = []
    for k in range(row.size):
        points = path[:, k][np.isfinite(path[:, k, 0])]
        if len(points) < 3:
            continue
        speed = float(np.nanmean(np.hypot(_bilinear(u, *points.T), _bilinear(v, *points.T))))
        vertices = np.stack([x0 + (points[:, 1] + 0.5) * dx, y0 + (points[:, 0] + 0.5) * dy], axis=1)
        lines.append((vertices, speed, float(rng.uniform(0, 10))))
    return lines


def to_flow_lines(
    forecasts: Forecast | list[Forecast], path: str | Path, across: int = FLOW_SEEDS_ACROSS
) -> Path:
    """GeoPackage layer `flow` of wind streamlines of one or several steps, with fields `speed` (mean
    along the line), `phase` and `valid_time` (UTC) of the line's step."""
    from osgeo import gdal

    from wxforecast.geotiff import atomic

    if not isinstance(forecasts, list):
        forecasts = [forecasts]
    # The sentinel (filtered out below) keeps the fields typed when no line was traced.
    features = [_line(None, -1.0, -1.0, "1970-01-01T00:00:00Z")]
    for forecast in forecasts:
        valid_time = f"{forecast.valid_time.astimezone(UTC):%Y-%m-%dT%H:%M:%S}Z"
        features += [
            _line(v.tolist(), speed, phase, valid_time) for v, speed, phase in flow_lines(forecast, across)
        ]
    geojson = json.dumps({"type": "FeatureCollection", "features": features})
    with gdal.ExceptionMgr(useExceptions=True), atomic(path) as tmp:
        gdal.VectorTranslate(
            str(tmp),
            geojson,
            format="GPKG",
            layerName="flow",
            geometryType="LINESTRING",
            dstSRS="EPSG:4326",
            where="speed >= 0",
        )
    logger.info("%d flow lines", len(features) - 1)
    return Path(path)


def _line(coordinates: list | None, speed: float, phase: float, valid_time: str) -> dict:
    return {
        "type": "Feature",
        "properties": {"speed": speed, "phase": phase, "valid_time": valid_time},
        "geometry": coordinates and {"type": "LineString", "coordinates": coordinates},
    }
