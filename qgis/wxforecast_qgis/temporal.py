"""Time ranges of forecast steps on layers and the Temporal Controller."""

from datetime import datetime, timedelta

from qgis.core import (
    Qgis,
    QgsDateTimeRange,
    QgsInterval,
    QgsRasterLayer,
    QgsTemporalNavigationObject,
    QgsVectorLayer,
)
from qgis.PyQt.QtCore import QDateTime, QTimeZone
from qgis.PyQt.QtWidgets import QDockWidget

import wxforecast

step_ends = wxforecast.step_ends


def qdatetime(value: datetime) -> QDateTime:
    return QDateTime.fromSecsSinceEpoch(int(value.timestamp()), QTimeZone(b"UTC"))


def time_range(start: datetime, end: datetime) -> QgsDateTimeRange:
    return QgsDateTimeRange(qdatetime(start), qdatetime(end), True, False)


def set_raster_steps(layer: QgsRasterLayer, starts: list[datetime], ends: list[datetime]) -> None:
    """Band k + 1 shows over the k-th step's range."""
    properties = layer.temporalProperties()
    properties.setMode(Qgis.RasterTemporalMode.FixedRangePerBand)
    properties.setFixedRangePerBand(
        {band: time_range(start, end) for band, (start, end) in enumerate(zip(starts, ends, strict=True), 1)}
    )
    properties.setIsActive(True)


def set_flow_steps(layer: QgsVectorLayer) -> None:
    """Each line shows over its step's span, stored and indexed by the library so the filter runs in SQL."""
    properties = layer.temporalProperties()
    properties.setMode(Qgis.VectorTemporalMode.FeatureDateTimeStartAndEndFromFields)
    properties.setStartField("show_from")
    properties.setEndField("show_until")
    properties.setIsActive(True)


def show_in_controller(start: datetime, end: datetime, frame: timedelta) -> None:
    """Animate the Temporal Controller over [start, end) by `frame` and show its panel, in the QGIS app."""
    from qgis.utils import iface

    if iface is None:
        return
    controller = iface.mapCanvas().temporalController()
    if isinstance(controller, QgsTemporalNavigationObject):
        controller.setNavigationMode(Qgis.TemporalNavigationMode.Animated)
        controller.setTemporalExtents(time_range(start, end))
        controller.setFrameDuration(QgsInterval(frame.total_seconds()))
    panel = iface.mainWindow().findChild(QDockWidget, "Temporal Controller")
    if panel is not None:
        panel.show()
