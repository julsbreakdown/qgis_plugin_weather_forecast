import os
import sys
from pathlib import Path


def _library_paths() -> list[str]:
    """$WXFORECAST_SITE_PACKAGES, else the venv and src of the checkout this plugin is linked from."""
    if os.environ.get("WXFORECAST_SITE_PACKAGES"):
        return os.environ["WXFORECAST_SITE_PACKAGES"].split(os.pathsep)
    root = Path(__file__).resolve().parents[2]
    version = f"python{sys.version_info.major}.{sys.version_info.minor}"
    return [str(root / ".venv" / "lib" / version / "site-packages"), str(root / "src")]


# Appended, not prepended: QGIS's own numpy and GDAL must win over the venv copies.
sys.path.extend([path for path in _library_paths() if path and path not in sys.path])


def classFactory(iface):
    from .plugin import WxForecastPlugin

    return WxForecastPlugin(iface)
