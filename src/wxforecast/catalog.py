"""Forecast models published in the dynamical.org STAC catalog."""

import json
import logging
import re
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from urllib.request import urlopen

logger = logging.getLogger(__name__)

CATALOG_URL = "https://stac.dynamical.org/catalog.json"
TIMEOUT_SECONDS = 30
CACHE_SECONDS = 3600
RETRY_SECONDS = 60
WIND_U = re.compile(r"wind_u_(?P<level>.+)")

# No lock: callers racing on an expired cache may both download, but never wait on one another.
_cache: tuple[float, dict[str, "Model"]] | None = None
_failure: tuple[float, Exception] | None = None


@dataclass(frozen=True)
class Variable:
    """`components` names the eastward and northward variables a derived wind variable is read from."""

    name: str
    unit: str
    long_name: str
    components: tuple[str, ...] = ()

    @property
    def calculated(self) -> bool:
        """Derived from `components` at read time; a raw variable is read as stored."""
        return bool(self.components)


@dataclass(frozen=True)
class Model:
    id: str
    title: str
    href: str
    dimensions: tuple[str, ...]
    variables: dict[str, Variable]

    @property
    def display_variables(self) -> dict[str, Variable]:
        """Variables to offer a user: wind components are replaced by their derived wind variable."""
        hidden = {name for v in self.variables.values() for name in v.components}
        return {name: v for name, v in self.variables.items() if name not in hidden}


def list_models() -> list[str]:
    """Ids of the deterministic forecast models on a lat/lon grid, sorted."""
    return sorted(_models())


def model(model_id: str) -> Model:
    """The forecast model `model_id`; ValueError listing the served models if it is not one of them."""
    models = _models()
    if model_id not in models:
        raise ValueError(f"unknown model {model_id!r}; available: {', '.join(sorted(models))}")
    return models[model_id]


def cache_clear() -> None:
    """Forget the catalog: the next call downloads it again."""
    global _cache, _failure
    _cache = _failure = None


def _models() -> dict[str, Model]:
    """The catalog, cached CACHE_SECONDS. A failed refresh serves the previous copy; without one it
    raises, and keeps raising that error for RETRY_SECONDS before trying again."""
    global _cache, _failure
    now = time.monotonic()
    cache, failure = _cache, _failure
    if cache is not None and now - cache[0] < CACHE_SECONDS:
        return cache[1]
    if failure is not None and now - failure[0] < RETRY_SECONDS:
        if cache is not None:
            return cache[1]
        raise failure[1]
    try:
        cache = (now, _fetch_models())
    except (OSError, ValueError, KeyError) as error:
        _failure = (now, error)
        if cache is None:
            raise
        logger.warning("catalog refresh failed, serving the previous copy: %s", error)
        return cache[1]
    _cache, _failure = cache, None
    return cache[1]


def _fetch_models() -> dict[str, Model]:
    catalog = _load(CATALOG_URL)
    hrefs = [
        link["href"]
        for link in catalog["links"]
        if link["rel"] == "child" and not link["href"].rstrip("/").rsplit("/", 2)[-2].endswith("-virtual")
    ]
    with ThreadPoolExecutor(max_workers=8) as pool:
        models = [m for m in pool.map(_collection_model, hrefs) if m is not None]
    return {m.id: m for m in models}


def _collection_model(href: str) -> "Model | None":
    """The collection's model, None if it is not a served forecast or is malformed (logged)."""
    try:
        collection = _load(href)
        return parse(collection) if is_forecast(collection) else None
    except (KeyError, TypeError, ValueError, IndexError) as error:
        logger.warning("skipping malformed collection %s: %r", href, error)
        return None


def is_forecast(collection: dict) -> bool:
    """A non-virtual, non-ensemble collection with init_time, lead_time and a lat/lon grid."""
    if collection["id"].endswith("-virtual"):
        return False
    dims = collection.get("cube:dimensions", {}).keys()
    return {"init_time", "lead_time", "latitude", "longitude"} <= dims and "ensemble_member" not in dims


def parse(collection: dict) -> Model:
    """Model from a STAC collection; keeps only the variables on the most common dimension order."""
    data = {k: v for k, v in collection["cube:variables"].items() if v.get("type", "data") == "data"}
    dims = Counter(tuple(v["dimensions"]) for v in data.values()).most_common(1)[0][0]
    variables = {
        name: Variable(name, v.get("unit", ""), v.get("long_name", name))
        for name, v in data.items()
        if tuple(v["dimensions"]) == dims
    }
    variables |= wind_variables(variables)
    return Model(
        id=collection["id"],
        title=collection["title"],
        href=collection["assets"]["icechunk-https"]["href"],
        dimensions=dims,
        variables=dict(sorted(variables.items())),
    )


def wind_variables(variables: dict[str, Variable]) -> dict[str, Variable]:
    """One `wind_<level>` per `wind_u_<level>` and `wind_v_<level>` pair: speed and direction."""
    derived = {}
    for name, u in variables.items():
        match = WIND_U.fullmatch(name)
        if match is None or f"wind_v_{match['level']}" not in variables:
            continue
        level = match["level"]
        v_name = f"wind_v_{level}"
        long_name = re.sub(r"\s*U wind component$", " wind", u.long_name, flags=re.IGNORECASE)
        if long_name == u.long_name:
            long_name = f"{level} wind"
        derived[f"wind_{level}"] = Variable(f"wind_{level}", u.unit, long_name, (name, v_name))
    return derived


def _load(url: str) -> dict:
    with urlopen(url, timeout=TIMEOUT_SECONDS) as response:
        return json.load(response)
