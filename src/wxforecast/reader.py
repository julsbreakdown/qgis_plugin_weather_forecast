"""Read one forecast map from a dynamical.org Icechunk repository."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from itertools import pairwise

import icechunk
import numpy as np
import zarr

from wxforecast import catalog, grid, wind

logger = logging.getLogger(__name__)

RUN_CANDIDATES = 4
DIMENSIONS = {"init_time", "lead_time", "latitude", "longitude"}


@dataclass(frozen=True, eq=False)
class Forecast:
    """For a wind variable `values` is the speed and `direction` the degrees clockwise from north the wind
    blows from; `direction` is None for every other variable."""

    model: str
    variable: str
    unit: str
    init_time: datetime
    valid_time: datetime
    values: np.ndarray
    geotransform: tuple[float, ...]
    crs: str = "EPSG:4326"
    direction: np.ndarray | None = None


def step_name(forecast: Forecast) -> str:
    """`<variable>_<YYYYMMDDTHH>` of the valid time (UTC): a stable file stem per step."""
    return f"{forecast.variable}_{forecast.valid_time.astimezone(UTC):%Y%m%dT%H}"


def _open_href(href: str) -> zarr.Group:
    repo = icechunk.Repository.open(icechunk.http_storage(href))
    return zarr.open_group(repo.readonly_session("main").store, mode="r")


def _utcnow() -> datetime:
    return datetime.now(UTC)


def fetch(
    model_id: str = "noaa-gfs-forecast",
    variable: str = "temperature_2m",
    bbox: tuple[float, float, float, float] | None = None,
    run: datetime | None = None,
    lead: timedelta | None = None,
) -> Forecast:
    """One map: `lead` after `run` (None: the latest run with data), lead None: valid time nearest now."""
    return _fetch(model_id, variable, bbox, run, lambda t, leads: [_lead_index(t, leads, lead)])[0]


def fetch_range(
    model_id: str = "noaa-gfs-forecast",
    variable: str = "temperature_2m",
    bbox: tuple[float, float, float, float] | None = None,
    days: float = 1.0,
    every: timedelta | None = None,
    run: datetime | None = None,
) -> list[Forecast]:
    """Every step of one run valid from the step nearest now to now + `days`, oldest first.

    `every` thins the steps on valid time (a step is kept once `every` has passed since the last kept
    one); steps are never invented. All-NaN steps are dropped (logged); ValueError if all are.
    """

    def pick(init_time: float, leads: np.ndarray) -> list[int]:
        start = _lead_index(init_time, leads, None)
        valid = init_time + leads
        end = max(valid[start], _utcnow().timestamp() + days * 86400)
        steps, kept = [], -np.inf
        for j in range(start, len(leads)):
            if valid[j] > end:
                break
            if every is None or valid[j] - kept >= every.total_seconds():
                steps.append(j)
                kept = valid[j]
        return steps

    return _fetch(model_id, variable, bbox, run, pick)


def _fetch(model_id, variable, bbox, run, pick: Callable[[float, np.ndarray], list[int]]) -> list[Forecast]:
    model = catalog.model(model_id)
    if variable not in model.variables:
        available = ", ".join(sorted(model.variables))
        raise ValueError(f"unknown variable {variable!r} for {model_id}; available: {available}")
    _check_dimensions(model.dimensions, model_id)

    names = model.variables[variable].components or (variable,)
    root = _open_href(model.href)
    arrays = [root[name] for name in names]
    # Raw zarr does no CF decoding: init_time is epoch seconds, lead_time seconds.
    init_times = np.asarray(root["init_time"][:], dtype=np.float64)
    lead_times = np.asarray(root["lead_time"][:], dtype=np.float64)
    win = grid.window(root["latitude"][:], root["longitude"][:], bbox)

    if run is None:
        i, steps, values = _latest_run_with_data(arrays, win, init_times, lead_times, pick)
    else:
        i = _run_index(init_times, run)
        steps = pick(init_times[i], lead_times)
        values = [_read(array, i, steps, win) for array in arrays]
    init_time = _utc(init_times[i])
    forecasts = []
    for k, j in enumerate(steps):
        step = [v[k] for v in values]
        valid_time = init_time + timedelta(seconds=float(lead_times[j]))
        if not np.isfinite(step[0]).any():
            logger.info("dropped %s valid %s: no data", variable, valid_time.isoformat())
            continue
        direction = None
        if len(step) == 2:
            step[0], direction = wind.speed_direction(*step)
        forecasts.append(
            Forecast(
                model=model_id,
                variable=variable,
                unit=model.variables[variable].unit,
                init_time=init_time,
                valid_time=valid_time,
                values=step[0],
                geotransform=tuple(win.geotransform),
                direction=direction,
            )
        )
    if not forecasts:
        raise ValueError(_no_data_message(variable, init_times[i], lead_times, steps[0]))
    logger.info(
        "fetched %s %s init %s, %d step(s) from %s, shape %s",
        model_id,
        variable,
        init_time.isoformat(),
        len(forecasts),
        forecasts[0].valid_time.isoformat(),
        forecasts[0].values.shape,
    )
    return forecasts


def _read(array: zarr.Array, i: int, steps: list[int], win: grid.Window) -> np.ndarray:
    """(step, lat, lon) float32 values; one zarr read per column slice covers all the steps."""
    dims = _dimension_names(array)
    leads = slice(steps[0], steps[-1] + 1)
    order = [d for d in dims if d != "init_time"]
    axes = [order.index(d) for d in ("lead_time", "latitude", "longitude")]
    parts = [np.transpose(array[_index(dims, i, leads, win.rows, cols)], axes) for cols in win.cols]
    values = np.concatenate(parts, axis=2) if len(parts) > 1 else parts[0]
    return np.asarray(values[np.asarray(steps) - steps[0]], dtype=np.float32)


def _dimension_names(array: zarr.Array) -> tuple[str, ...]:
    return _check_dimensions(array.metadata.dimension_names or (), array.name)


def _check_dimensions(names: tuple[str, ...], label: str) -> tuple[str, ...]:
    names = tuple(names)
    if set(names) != DIMENSIONS:
        raise ValueError(f"{label}: unsupported dimensions {names}; expected {sorted(DIMENSIONS)}")
    return names


def _index(dims: tuple[str, ...], i: int, leads: slice, rows: slice, cols: slice) -> tuple:
    by_name = {"init_time": i, "lead_time": leads, "latitude": rows, "longitude": cols}
    return tuple(by_name[name] for name in dims)


def _latest_run_with_data(
    arrays: list[zarr.Array],
    win: grid.Window,
    init_times: np.ndarray,
    lead_times: np.ndarray,
    pick: Callable[[float, np.ndarray], list[int]],
) -> tuple[int, list[int], list[np.ndarray]]:
    """The newest run whose window is fully written: a chunk empty in a run but not in the previous run
    marks the run as partly written (workers fill a new run chunk by chunk)."""

    def read(i: int) -> tuple[int, list[int], list[np.ndarray], set]:
        steps = pick(init_times[i], lead_times)
        values = [_read(array, i, steps, win) for array in arrays]
        empty = set().union(*(_empty_chunks(v, win, a) for v, a in zip(values, arrays, strict=True)))
        return i, steps, values, empty

    current = read(len(init_times) - 1)
    for _ in range(RUN_CANDIDATES - 1):
        if not current[3] or current[0] == 0:
            break
        previous = read(current[0] - 1)
        if current[3] <= previous[3]:
            break
        logger.info("run %s is partly written, trying the previous one", _utc(init_times[current[0]]))
        current = previous
    return current[0], current[1], current[2]


def _empty_chunks(values: np.ndarray, win: grid.Window, array: zarr.Array) -> set[tuple[int, int]]:
    """Spatial chunks touched by the window that hold no finite value there at any step."""
    dims = _dimension_names(array)
    rows = np.arange(win.rows.start, win.rows.stop) // array.chunks[dims.index("latitude")]
    cols = np.concatenate([np.arange(c.start, c.stop) for c in win.cols])
    cols //= array.chunks[dims.index("longitude")]
    finite = np.isfinite(values).any(axis=0)
    return {
        (int(r), int(c))
        for r in np.unique(rows)
        for c in np.unique(cols)
        if not finite[np.ix_(rows == r, cols == c)].any()
    }


def _no_data_message(variable: str, init_time: float, lead_times: np.ndarray, j: int) -> str:
    hours = lead_times / 3600
    run = f"{_utc(init_time):%Y-%m-%dT%H:%M}Z"
    where = f"{variable} has no data at run {run} lead {hours[j]:g} h in this area"
    if j == 0 and len(hours) > 1:
        return (
            f"{where}: period variables (averages, accumulations, min/max) start at the first step, "
            f"pick lead >= {hours[1]:g} h"
        )
    return f"{where}: the run may not be written yet, pick an earlier run or another lead"


def _run_index(init_times: np.ndarray, run: datetime) -> int:
    run = run.replace(tzinfo=UTC) if run.tzinfo is None else run.astimezone(UTC)
    matches = np.flatnonzero(init_times == run.timestamp())
    if matches.size == 0:
        latest = ", ".join(f"{_utc(t):%Y-%m-%dT%H:%M}Z" for t in init_times[-5:])
        raise ValueError(f"run {run:%Y-%m-%dT%H:%M}Z not available; latest runs: {latest}")
    return int(matches[0])


def _lead_index(init_time: float, lead_times: np.ndarray, lead: timedelta | None) -> int:
    if lead is None:
        return int(np.abs(init_time + lead_times - _utcnow().timestamp()).argmin())
    matches = np.flatnonzero(lead_times == lead.total_seconds())
    if matches.size == 0:
        wanted = lead.total_seconds() / 3600
        raise ValueError(f"lead {wanted:g} h not available; leads: {_describe_leads(lead_times / 3600)}")
    return int(matches[0])


def _describe_leads(hours: np.ndarray) -> str:
    """Lead hours as runs of equal steps, e.g. "0-120 h every 1 h, then 120-384 h every 3 h"."""
    if len(hours) < 2:
        return ", ".join(f"{h:g} h" for h in hours)
    groups: list[list[float]] = []
    for a, b in pairwise(hours.tolist()):
        if groups and groups[-1][2] == b - a:
            groups[-1][1] = b
        else:
            groups.append([a, b, b - a])
    return ", then ".join(f"{first:g}-{last:g} h every {step:g} h" for first, last, step in groups)


def _utc(seconds: float) -> datetime:
    return datetime.fromtimestamp(float(seconds), UTC)
