"""Check the built plugin zip in a QGIS without the plugin's packages ("missing"), then with them
("installed"): python3 qgis/zip_check.py dist/wxforecast_qgis-<version>.zip missing|installed
"""

import sys
import tempfile
import zipfile
from unittest.mock import MagicMock

from qgis.core import QgsApplication

app = QgsApplication([], False)
app.initQgis()
plugins = tempfile.mkdtemp()
zipfile.ZipFile(sys.argv[1]).extractall(plugins)
sys.path.insert(0, plugins)
import wxforecast_qgis  # noqa: E402

plugin = wxforecast_qgis.classFactory(MagicMock())
print(type(plugin).__name__, wxforecast_qgis.missing_packages())
if sys.argv[2] == "missing":
    assert type(plugin).__name__ == "MissingPackagesPlugin"
    plugin.initGui()
    print(plugin.message)
else:
    assert type(plugin).__name__ == "WxForecastPlugin"
    import wxforecast

    assert "/libs/wxforecast/" in wxforecast.__file__, wxforecast.__file__
    from wxforecast_qgis import processing_provider  # noqa: F401

    print("bundled library:", wxforecast.__file__)
print("ZIPCHECK OK")
