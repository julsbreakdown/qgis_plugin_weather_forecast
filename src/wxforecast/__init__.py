"""Fetch weather forecast maps from dynamical.org Icechunk repositories; write GeoTIFFs or PMTiles."""

from wxforecast.catalog import list_models, model

__all__ = [
    "list_models",
    "model",
]
