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

FIELD = "valid_time"


def step_ends(starts: list[datetime]) -> list[datetime]:
    """Each step lasts until the next one; the last lasts as long as the one before it (1 h if alone)."""
    last = starts[-1] - starts[-2] if len(starts) > 1 else timedelta(hours=1)
    return [*starts[1:], starts[-1] + last]


def qdatetime(value: datetime) -> QDateTime:
    return QDateTime.fromSecsSinceEpoch(int(value.timestamp()), QTimeZone(b"UTC"))


def time_range(start: datetime, end: datetime) -> QgsDateTimeRange:
    return QgsDateTimeRange(qdatetime(start), qdatetime(end), True, False)


def set_raster_step(layer: QgsRasterLayer, start: datetime, end: datetime) -> None:
    properties = layer.temporalProperties()
    properties.setMode(Qgis.RasterTemporalMode.FixedTemporalRange)
    properties.setFixedTemporalRange(time_range(start, end))
    properties.setIsActive(True)


def set_flow_steps(layer: QgsVectorLayer, starts: list[datetime], ends: list[datetime]) -> None:
    """Each line shows from its `valid_time` until the next step, so a step change (1 h to 3 h) holds."""
    hours = [(end - start).total_seconds() / 3600 for start, end in zip(starts, ends, strict=True)]
    cases = "".join(
        f" WHEN \"{FIELD}\" < to_datetime('{starts[k]:%Y-%m-%dT%H:%M:%S}Z') THEN {hours[k - 1]:g}"
        for k in range(1, len(hours))
        if hours[k] != hours[k - 1]
    )
    duration = f"CASE{cases} ELSE {hours[-1]:g} END" if cases else f"{hours[-1]:g}"
    properties = layer.temporalProperties()
    properties.setMode(Qgis.VectorTemporalMode.FeatureDateTimeStartAndEndFromExpressions)
    properties.setStartExpression(f'"{FIELD}"')
    properties.setEndExpression(f'"{FIELD}" + make_interval(hours:={duration})')
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
