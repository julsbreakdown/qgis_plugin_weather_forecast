from functools import cache
from pathlib import Path

from qgis.PyQt.QtCore import QRectF, Qt
from qgis.PyQt.QtGui import QIcon, QPainter, QPixmap
from qgis.PyQt.QtSvg import QSvgRenderer

from .style import styles

ICONS = Path(__file__).parent / "icons"
BADGE_SIZES = (16, 24, 32, 48, 96)
BADGE_SCALE = 0.58


def svg_path(name: str) -> str:
    return str(ICONS / f"{name}.svg")


def icon(name: str) -> QIcon:
    return QIcon(svg_path(name))


def kind_svg(kind: str) -> str:
    """The SVG named by the kind's `icon` in the styles; generic if the kind or the file is unknown."""
    style = styles().get(kind)
    path = svg_path(style.icon if style else "generic")
    return path if Path(path).is_file() else svg_path("generic")


@cache
def kind_icon(kind: str, calculated: bool = False) -> QIcon:
    """The kind's icon; a calculated variable gets a gear badge in the bottom right corner."""
    if not calculated:
        return QIcon(kind_svg(kind))
    result = QIcon()
    for size in BADGE_SIZES:
        result.addPixmap(badged_pixmap(kind, size))
    return result


def badged_pixmap(kind: str, size: int) -> QPixmap:
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    QSvgRenderer(kind_svg(kind)).render(painter, QRectF(0, 0, size, size))
    badge = size * BADGE_SCALE
    QSvgRenderer(svg_path("gear")).render(painter, QRectF(size - badge, size - badge, badge, badge))
    painter.end()
    return pixmap
