"""Style per variable kind, read from `styles.toml` (plus an optional override file), applied as RGBA bytes.

The QGIS plugin builds its raster renderers from the same styles, so tiles and QGIS layers look the same.
`styles` arguments are an override file path; None falls back to $WXFORECAST_STYLES, then the defaults.
"""

from __future__ import annotations

import os
import re
import tomllib
from dataclasses import dataclass
from fnmatch import fnmatchcase
from functools import lru_cache
from itertools import pairwise
from pathlib import Path

import numpy as np

DEFAULT_STYLES = Path(__file__).with_name("styles.toml")
ENV_VAR = "WXFORECAST_STYLES"
FALLBACK = "other"
FLOW_KIND = "flow"
FLOW_CLASSES = 7
KEYS = {
    "title",
    "icon",
    "names",
    "units",
    "colors",
    "stops",
    "range",
    "anchor",
    "valid_min",
    "valid_max",
    "discrete",
}
ANCHORS = {"", "zero", "symmetric"}
COLOR = re.compile(r"#[0-9a-fA-F]{6}")
DISPLAY_UNITS = {
    "Pa": ("hPa", 0.01),
    "kg m-2 s-1": ("mm/h", 3600.0),
    "degree_Celsius": ("°C", 1.0),
    "percent": ("%", 1.0),
    "m s-1": ("m/s", 1.0),
    "W m-2": ("W/m²", 1.0),
    "J kg-1": ("J/kg", 1.0),
    "kg m-2": ("mm", 1.0),
    "1": ("", 1.0),
}


@dataclass(frozen=True)
class Stop:
    """A ramp colour at `position` (0 = vmin, 1 = vmax); `label` names a class of a discrete style."""

    position: float
    color: str
    alpha: int = 255
    label: str = ""

    @property
    def rgb(self) -> tuple[int, int, int]:
        return int(self.color[1:3], 16), int(self.color[3:5], 16), int(self.color[5:7], 16)


@dataclass(frozen=True)
class Style:
    """How a kind is drawn.

    `value_range` is a fixed vmin, vmax in data units; None takes the data min and max, adjusted by
    `anchor`: "zero" starts at 0 at most, "symmetric" spans -m..m. Values outside `valid` (None = open)
    are nodata. `discrete` paints each value with its nearest stop instead of interpolating.
    """

    kind: str
    title: str
    stops: tuple[Stop, ...]
    icon: str = "generic"
    names: tuple[str, ...] = ()
    units: tuple[str, ...] = ()
    value_range: tuple[float, float] | None = None
    anchor: str = ""
    valid: tuple[float | None, float | None] = (None, None)
    discrete: bool = False

    @property
    def colors(self) -> list[str]:
        return [stop.color for stop in self.stops]

    @property
    def transparent_at_min(self) -> bool:
        return self.stops[0].alpha == 0

    def matches(self, variable: str, unit: str) -> bool:
        name = variable.lower()
        return any(fnmatchcase(name, pattern) for pattern in self.names) or unit in self.units


def load_styles(styles: str | Path | None = None) -> dict[str, Style]:
    """Styles by kind in match order: the override's new kinds first, then the defaults, each default
    kind updated key by key from the override. Cached until the override file changes."""
    path = styles or os.environ.get(ENV_VAR) or ""
    stamp = Path(path).stat().st_mtime_ns if path else 0
    return _load(str(path), stamp)


@lru_cache(maxsize=8)
def _load(override: str, stamp: int) -> dict[str, Style]:
    tables = _read(DEFAULT_STYLES)
    if override:
        extra = _read(Path(override))
        merged = {kind: table for kind, table in extra.items() if kind not in tables}
        for kind, table in tables.items():
            changes = extra.get(kind, {})
            if {"stops", "colors"} & changes.keys():
                table = {k: v for k, v in table.items() if k not in ("stops", "colors")}
            merged[kind] = table | changes
        tables = merged
    source = override or DEFAULT_STYLES
    styles = {}
    for kind, table in tables.items():
        if not {"stops", "colors"} & table.keys():
            raise ValueError(f"{source}: kind {kind!r} needs 'stops' or 'colors'")
        styles[kind] = _style(kind, table)
    if FALLBACK not in styles:
        raise ValueError(f"{source}: kind {FALLBACK!r} is required as the fallback")
    return styles


def _read(path: Path) -> dict[str, dict]:
    try:
        with open(path, "rb") as f:
            data = tomllib.load(f)
    except tomllib.TOMLDecodeError as error:
        raise ValueError(f"{path}: {error}") from error
    for kind, table in data.items():
        if not isinstance(table, dict):
            raise ValueError(f"{path}: {kind!r} must be a [table] of style keys")
        _check(f"{path}: kind {kind!r}", table)
    return data


def _check(where: str, table: dict) -> None:
    unknown = table.keys() - KEYS
    if unknown:
        raise ValueError(f"{where}: unknown key(s) {sorted(unknown)}; allowed: {sorted(KEYS)}")
    if {"stops", "colors"} <= table.keys():
        raise ValueError(f"{where}: give 'stops' or 'colors', not both")
    stops = table.get("stops") or [[0, color] for color in table.get("colors", ["#000000", "#000000"])]
    if len(stops) < 2:
        raise ValueError(f"{where}: at least 2 stops are needed")
    for stop in stops:
        if not isinstance(stop, list) or not 2 <= len(stop) <= 4:
            raise ValueError(f"{where}: a stop is [position, colour, alpha?, label?], got {stop!r}")
        if not isinstance(stop[1], str) or not COLOR.fullmatch(stop[1]):
            raise ValueError(f"{where}: bad colour {stop[1]!r}, expected #rrggbb")
        if len(stop) > 2 and not (isinstance(stop[2], int) and 0 <= stop[2] <= 255):
            raise ValueError(f"{where}: alpha must be an integer 0..255, got {stop[2]!r}")
    if "stops" in table:
        positions = [stop[0] for stop in stops]
        if not all(isinstance(p, int | float) for p in positions):
            raise ValueError(f"{where}: stop positions must be numbers")
        if positions[0] != 0 or positions[-1] != 1 or any(b <= a for a, b in pairwise(positions)):
            raise ValueError(f"{where}: stop positions must ascend strictly from 0 to 1, got {positions}")
    value_range = table.get("range", "data")
    if value_range != "data" and not (
        isinstance(value_range, list)
        and len(value_range) == 2
        and all(isinstance(v, int | float) for v in value_range)
        and value_range[0] < value_range[1]
    ):
        raise ValueError(f'{where}: range must be "data" or [min, max] with min < max')
    if table.get("anchor", "") not in ANCHORS:
        raise ValueError(f"{where}: anchor must be one of {sorted(ANCHORS)}")


def _style(kind: str, table: dict) -> Style:
    if "stops" in table:
        stops = tuple(Stop(float(s[0]), *s[1:]) for s in table["stops"])
    else:
        last = len(table["colors"]) - 1
        stops = tuple(Stop(i / last, color) for i, color in enumerate(table["colors"]))
    value_range = table.get("range", "data")
    return Style(
        kind=kind,
        title=table.get("title", kind),
        stops=stops,
        icon=table.get("icon", "generic"),
        names=tuple(table.get("names", ())),
        units=tuple(table.get("units", ())),
        value_range=None if value_range == "data" else (float(value_range[0]), float(value_range[1])),
        anchor=table.get("anchor", ""),
        valid=(table.get("valid_min"), table.get("valid_max")),
        discrete=bool(table.get("discrete", False)),
    )


def variable_kind(variable: str, unit: str, styles: str | Path | None = None) -> str:
    """The first kind whose name patterns or units match; FALLBACK otherwise."""
    for kind, style in load_styles(styles).items():
        if style.matches(variable, unit):
            return kind
    return FALLBACK


def display_unit(unit: str) -> tuple[str, float]:
    """Unit label and factor for legends only; data stay in the stored unit."""
    return DISPLAY_UNITS.get(unit, (unit, 1.0))


def limits(kind: str, low: float, high: float, styles: str | Path | None = None) -> tuple[float, float]:
    """vmin, vmax of a kind given the data min and max (ignored for a fixed range)."""
    style = load_styles(styles)[kind]
    if style.value_range is not None:
        return style.value_range
    # QGIS reports a band without data as min DBL_MAX, max -DBL_MAX: finite but inverted.
    if not (np.isfinite(low) and np.isfinite(high)) or low > high:
        return 0.0, 1.0
    if style.anchor == "zero":
        low = min(0.0, low)
    elif style.anchor == "symmetric":
        high = max(abs(low), abs(high))
        low = -high
    return low, high if high > low else low + 1


def valid(values: np.ndarray, kind: str, styles: str | Path | None = None) -> np.ndarray:
    """True where a value is data: finite and inside the kind's valid range."""
    low, high = load_styles(styles)[kind].valid
    keep = np.isfinite(values)
    with np.errstate(invalid="ignore"):
        if low is not None:
            keep &= values >= low
        if high is not None:
            keep &= values <= high
    return keep


def value_range(values: np.ndarray, kind: str, styles: str | Path | None = None) -> tuple[float, float]:
    data = values[valid(values, kind, styles)]
    if not data.size:
        return limits(kind, np.nan, np.nan, styles)
    return limits(kind, float(data.min()), float(data.max()), styles)


def colorize(
    values: np.ndarray, kind: str, vmin: float, vmax: float, styles: str | Path | None = None
) -> np.ndarray:
    """RGBA uint8 of shape (rows, cols, 4); nodata is transparent, so is any stop with alpha 0."""
    style = load_styles(styles)[kind]
    positions = np.array([stop.position for stop in style.stops])
    table = np.array([(*stop.rgb, stop.alpha) for stop in style.stops], dtype=np.float64)
    t = np.nan_to_num(np.clip((values - vmin) / (vmax - vmin), 0, 1))
    rgba = np.empty((*values.shape, 4), dtype=np.uint8)
    if style.discrete:
        nearest = np.abs(t[..., None] - positions).argmin(axis=-1)
        rgba[...] = table[nearest].round()
    else:
        for channel in range(4):
            rgba[..., channel] = np.interp(t, positions, table[:, channel]).round()
    rgba[..., 3] = np.where(valid(values, kind, styles), rgba[..., 3], 0)
    return rgba


def legend(kind: str, vmin: float, vmax: float, unit: str, styles: str | Path | None = None) -> dict:
    """JSON legend of a styled map: stops in data units, plus the display unit and factor."""
    style = load_styles(styles)[kind]
    label, factor = display_unit(unit)
    return {
        "kind": kind,
        "title": style.title,
        "min": vmin,
        "max": vmax,
        "colors": style.colors,
        "positions": [stop.position for stop in style.stops],
        "alpha": [stop.alpha for stop in style.stops],
        "interpolation": "discrete" if style.discrete else "linear",
        "display_unit": label,
        "display_factor": factor,
    }


def ramp(
    kind: str, low: float, high: float, unit: str, styles: str | Path | None = None
) -> list[tuple[float, Stop, str]]:
    """Each stop at its data value from low to high, with its legend label in the display unit."""
    label, factor = display_unit(unit)
    entries = []
    for stop in load_styles(styles)[kind].stops:
        value = low + stop.position * (high - low)
        entries.append((value, stop, stop.label or f"{value * factor:.4g} {label}".strip()))
    return entries


def flow_classes(
    high: float, unit: str, styles: str | Path | None = None
) -> list[tuple[float, float, str, str]]:
    """FLOW_CLASSES equal speed classes from 0 to `high`: (low, upper, #rrggbb, label), colours sampled
    evenly along the FLOW_KIND ramp. QGIS flow layers and their SLD both use them."""
    stops = load_styles(styles)[FLOW_KIND].stops
    positions = [stop.position for stop in stops]
    label = display_unit(unit)[0]
    step = high / FLOW_CLASSES
    classes = []
    for i in range(FLOW_CLASSES):
        low, upper = i * step, high if i == FLOW_CLASSES - 1 else (i + 1) * step
        t = i / (FLOW_CLASSES - 1)
        rgb = (int(np.interp(t, positions, [stop.rgb[c] for stop in stops]) + 0.5) for c in range(3))
        color = "#{:02x}{:02x}{:02x}".format(*rgb)
        classes.append((low, upper, color, f"{low:.3g} - {upper:.3g} {label}".strip()))
    return classes
