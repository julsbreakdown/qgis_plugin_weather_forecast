from qgis.core import Qgis, QgsMessageLog

from .i18n import tr

TAG = "Weather Forecast"


class MissingPackagesPlugin:
    """Stands in for the plugin when its Python packages are not installed: says which and how."""

    def __init__(self, iface, missing: list[str], help_url: str):
        self.iface = iface
        self.message = tr(
            "Weather Forecast needs extra Python packages: {packages}. Install them with the qpip plugin "
            'or with pip, see <a href="{url}">the installation guide</a>.'
        ).format(packages=", ".join(missing), url=help_url)

    def initGui(self) -> None:
        QgsMessageLog.logMessage(self.message, TAG, Qgis.MessageLevel.Warning)
        self.iface.messageBar().pushMessage(TAG, self.message, level=Qgis.MessageLevel.Warning, duration=0)

    def unload(self) -> None:
        pass
