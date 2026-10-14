"""Map a lon/lat bbox to array slices and a GeoTIFF geotransform on a regular lat/lon grid."""

import math
from dataclasses import dataclass

import numpy as np

# Slack in cell units so a bbox edge lying on a cell edge does not grab the neighbouring cell.
_EPS = 1e-6

Bbox = tuple[float, float, float, float]


@dataclass(frozen=True)
class Window:
    rows: slice
    cols: tuple[slice, ...]
    geotransform: tuple[float, ...]


def window(lat: np.ndarray, lon: np.ndarray, bbox: Bbox | None) -> Window:
    """Cells covering `bbox` (west, south, east, north), snapped outward; `west > east` crosses 180.

    Read `cols` in order and concatenate them along the longitude axis to get one north-up image.
    Raises ValueError if the grid is not regular and north-up or if the bbox misses it.
    """
    lat = np.asarray(lat, dtype=np.float64)
    lon = np.asarray(lon, dtype=np.float64)
    dy = _step(lat, "latitude")
    dx = _step(lon, "longitude")
    if dy >= 0:
        raise ValueError("latitude must be descending (north-up grid)")
    if dx <= 0:
        raise ValueError("longitude must be ascending")
    top = float(lat[0]) - dy / 2
    x0 = float(lon[0]) - dx / 2

    if bbox is None:
        return Window(slice(0, len(lat)), (slice(0, len(lon)),), (x0, dx, 0.0, top, 0.0, dy))

    west, south, east, north = (float(v) for v in bbox)
    if south > north:
        raise ValueError(f"bbox south {south} is above north {north}")
    rows = _rows(top, -dy, len(lat), south, north)
    cols, origin = _cols(x0, dx, len(lon), west, east)
    return Window(rows, cols, (origin, dx, 0.0, top + rows.start * dy, 0.0, dy))


def _step(axis: np.ndarray, name: str) -> float:
    if axis.ndim != 1 or len(axis) < 2:
        raise ValueError(f"{name} must be a 1-D axis of at least 2 values")
    steps = np.diff(axis)
    step = float(steps.mean())
    if not np.allclose(steps, step, rtol=0, atol=abs(step) * 1e-3):
        raise ValueError(f"{name} is not regularly spaced")
    return step


def _rows(top: float, size: float, count: int, south: float, north: float) -> slice:
    start = max(math.floor((top - north) / size + _EPS), 0)
    stop = min(math.ceil((top - south) / size - _EPS), count)
    if start >= stop:
        bottom = top - count * size
        raise ValueError(f"bbox latitudes {south:g}..{north:g} are outside the grid ({bottom:g}..{top:g})")
    return slice(start, stop)


def _cols(x0: float, dx: float, count: int, west: float, east: float) -> tuple[tuple[slice, ...], float]:
    width = east - west if west <= east else east - west + 360
    width = min(width, 360.0)
    lo = x0 + (west - x0) % 360
    hi = lo + width

    if math.isclose(count * dx, 360, rel_tol=1e-6):
        start = math.floor((lo - x0) / dx + _EPS)
        stop = min(math.ceil((hi - x0) / dx - _EPS), start + count)
        stop = max(stop, start + 1)
        cols = (slice(start, stop),) if stop <= count else (slice(start, count), slice(0, stop - count))
        return cols, west + (x0 + start * dx - lo)

    x1 = x0 + count * dx
    shifts = [s for s in (0.0, 360.0) if lo < x1 + s and hi > x0 + s]
    if not shifts:
        raise ValueError(f"bbox longitudes {west:g}..{east:g} are outside the grid ({x0:g}..{x1:g})")
    if len(shifts) == 2:
        return (slice(0, count),), west + (x0 - lo)
    shift = shifts[0]
    start = max(math.floor((lo - shift - x0) / dx + _EPS), 0)
    stop = min(max(math.ceil((hi - shift - x0) / dx - _EPS), start + 1), count)
    return (slice(start, stop),), west + (x0 + shift + start * dx - lo)
