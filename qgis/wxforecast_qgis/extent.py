from qgis.core import (
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsCsException,
    QgsPointXY,
    QgsProject,
    QgsRectangle,
)

WGS84 = QgsCoordinateReferenceSystem("EPSG:4326")
EDGE_SAMPLES = 64


def _wrap(lon: float) -> float:
    return (lon + 180) % 360 - 180


def to_bbox(
    extent: QgsRectangle, crs: QgsCoordinateReferenceSystem
) -> tuple[float, float, float, float] | None:
    """(west, south, east, north) in EPSG:4326, west > east across the antimeridian; None: whole world.

    Longitudes follow the extent's outline, unwrapped, so an extent across 180 (or beyond the CRS's
    world, as a zoomed-out Web Mercator canvas) keeps its side instead of becoming its complement.
    """
    if crs == WGS84:
        lons, box = [extent.xMinimum(), extent.xMaximum()], extent
    else:
        transform = QgsCoordinateTransform(crs, WGS84, QgsProject.instance())
        box = transform.transformBoundingBox(extent)
        lons = _outline_lons(extent, transform)
    south, north = max(box.yMinimum(), -90.0), min(box.yMaximum(), 90.0)
    if lons is None or max(lons) - min(lons) >= 360:
        return None if south <= -90 and north >= 90 else (-180.0, south, 180.0, north)
    return (_wrap(min(lons)), south, -_wrap(-max(lons)), north)


def _outline_lons(extent: QgsRectangle, transform: QgsCoordinateTransform) -> list[float] | None:
    """Unwrapped longitudes around the extent's outline; None if the outline circles a pole."""
    x0, y0, x1, y1 = extent.xMinimum(), extent.yMinimum(), extent.xMaximum(), extent.yMaximum()
    ts = [i / EDGE_SAMPLES for i in range(EDGE_SAMPLES)]
    ring = (
        [(x0 + t * (x1 - x0), y0) for t in ts]
        + [(x1, y0 + t * (y1 - y0)) for t in ts]
        + [(x1 - t * (x1 - x0), y1) for t in ts]
        + [(x0, y1 - t * (y1 - y0)) for t in ts]
    )
    lons = []
    for x, y in ring:
        try:
            lon = transform.transform(QgsPointXY(x, y)).x()
        except QgsCsException:
            continue
        lons.append(lon if not lons else lons[-1] + _wrap(lon - lons[-1]))
    if not lons or abs(_wrap(lons[0] - lons[-1]) + lons[-1] - lons[0]) > 180:
        return None
    return lons
