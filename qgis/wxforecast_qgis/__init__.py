import os
import sys
from importlib.util import find_spec
from pathlib import Path

REQUIRED = ("icechunk", "zarr", "pmtiles")
HELP_URL = "https://github.com/julsbreakdown/qgis_plugin_weather_forecast/blob/master/qgis/README.md#install"


def _library_paths() -> list[str]:
    """$WXFORECAST_SITE_PACKAGES, the library bundled in the plugin zip, and a development checkout's venv
    and src when the plugin is linked from one."""
    paths = [p for p in os.environ.get("WXFORECAST_SITE_PACKAGES", "").split(os.pathsep) if p]
    paths.append(str(Path(__file__).parent / "libs"))
    root = Path(__file__).resolve().parents[2]
    version = f"python{sys.version_info.major}.{sys.version_info.minor}"
    paths += [str(root / ".venv" / "lib" / version / "site-packages"), str(root / "src")]
    return [p for p in paths if Path(p).is_dir()]


# Appended, not prepended: QGIS's own numpy and GDAL must win over the venv copies.
sys.path.extend([path for path in _library_paths() if path not in sys.path])


def missing_packages() -> list[str]:
    return [name for name in REQUIRED if find_spec(name) is None]


def classFactory(iface):
    if missing_packages():
        from .missing import MissingPackagesPlugin

        return MissingPackagesPlugin(iface, missing_packages(), HELP_URL)
    from .plugin import WxForecastPlugin

    return WxForecastPlugin(iface)
