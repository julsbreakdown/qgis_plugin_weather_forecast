"""Write a Forecast as a Cloud Optimized GeoTIFF; shared GDAL output helpers."""

from __future__ import annotations

import logging
import shutil
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from wxforecast.reader import Forecast

logger = logging.getLogger(__name__)

# DEFLATE rather than ZSTD: every GeoServer GeoTIFF reader decodes it.
COG_OPTIONS = ["COMPRESS=DEFLATE", "PREDICTOR=YES", "BLOCKSIZE=256", "OVERVIEWS=AUTO"]


@contextmanager
def atomic(path: str | Path) -> Iterator[Path]:
    """Yield a unique temporary path next to `path`, moved onto it only if the block succeeds."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_dir = Path(tempfile.mkdtemp(prefix=f".{path.name}.", dir=path.parent))
    try:
        yield tmp_dir / path.name
        (tmp_dir / path.name).replace(path)
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
    logger.info("wrote %s", path)


def srs(definition: str):
    """osr.SpatialReference from "EPSG:4326" or any user input, in x/y (lon/lat) order."""
    from osgeo import osr

    with osr.ExceptionMgr(useExceptions=True):
        ref = osr.SpatialReference()
        ref.SetFromUserInput(definition)
        ref.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    return ref


def mem_dataset(bands: list, geotransform: tuple[float, ...], crs: str = "EPSG:4326"):
    """In-memory float32 GDAL dataset, one band per array, NaN nodata."""
    from osgeo import gdal

    rows, cols = bands[0].shape
    dataset = gdal.GetDriverByName("MEM").Create("", cols, rows, len(bands), gdal.GDT_Float32)
    dataset.SetGeoTransform(geotransform)
    dataset.SetSpatialRef(srs(crs))
    for index, values in enumerate(bands, start=1):
        band = dataset.GetRasterBand(index)
        band.SetNoDataValue(float("nan"))
        band.WriteArray(values)
    return dataset


def to_geotiff(forecast: Forecast, path: str | Path) -> Path:
    """COG, one band; a wind variable adds band 2 `direction`, degrees clockwise from north it blows from.

    Overviews average the values; with a direction band they take the nearest cell (angles never average).
    """
    from osgeo import gdal

    from wxforecast.wind import DIRECTION_CONVENTION

    bands = [(forecast.variable, forecast.unit, forecast.values)]
    metadata = {
        "model": forecast.model,
        "variable": forecast.variable,
        "unit": forecast.unit,
        "init_time": forecast.init_time.isoformat(),
        "valid_time": forecast.valid_time.isoformat(),
    }
    if forecast.direction is not None:
        bands.append(("direction", "degree", forecast.direction))
        metadata["direction"] = DIRECTION_CONVENTION
    with gdal.ExceptionMgr(useExceptions=True), atomic(path) as tmp:
        source = mem_dataset([values for _, _, values in bands], forecast.geotransform, forecast.crs)
        source.SetMetadata(metadata)
        for index, (description, unit, _) in enumerate(bands, start=1):
            source.GetRasterBand(index).SetDescription(description)
            source.GetRasterBand(index).SetUnitType(unit)
        resampling = "NEAREST" if forecast.direction is not None else "AVERAGE"
        options = [*COG_OPTIONS, f"RESAMPLING={resampling}"]
        gdal.Translate(str(tmp), source, format="COG", creationOptions=options)
    return Path(path)
