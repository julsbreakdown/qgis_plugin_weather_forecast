from qgis.core import QgsApplication
from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QAction

from . import icons
from .indicators import Indicators

MENU = "&Weather Forecast"


class WxForecastPlugin:
    def __init__(self, iface):
        self.iface = iface
        self.action = None
        self.dock = None
        self.provider = None
        self.indicators = None

    def initProcessing(self) -> None:
        from .processing_provider import WxForecastProvider

        self.provider = WxForecastProvider()
        QgsApplication.processingRegistry().addProvider(self.provider)

    def initGui(self) -> None:
        self.initProcessing()
        icon = icons.icon("forecast")
        self.action = QAction(icon, "Weather forecast", self.iface.mainWindow())
        self.action.setCheckable(True)
        self.action.toggled.connect(self._toggle)
        self.iface.addToolBarIcon(self.action)
        self.iface.addPluginToMenu(MENU, self.action)
        self.indicators = Indicators(self.iface.layerTreeView())

    def _toggle(self, checked: bool) -> None:
        if self.dock is None:
            if not checked:
                return
            from .dock import ForecastDock

            self.dock = ForecastDock(self.iface)
            self.iface.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.dock)
            self.dock.visibilityChanged.connect(self.action.setChecked)
        self.dock.setVisible(checked)

    def unload(self) -> None:
        if self.provider is not None:
            QgsApplication.processingRegistry().removeProvider(self.provider)
            self.provider = None
        if self.indicators is not None:
            self.indicators.unload()
            self.indicators = None
        self.iface.removePluginMenu(MENU, self.action)
        self.iface.removeToolBarIcon(self.action)
        if self.dock is not None:
            self.dock.unload()
            self.iface.removeDockWidget(self.dock)
            self.dock.deleteLater()
            self.dock = None
        self.action.deleteLater()
