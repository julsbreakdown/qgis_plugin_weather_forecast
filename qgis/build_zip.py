"""Build the installable plugin zip, with the wxforecast library bundled in wxforecast_qgis/libs:
python3 qgis/build_zip.py  ->  dist/wxforecast_qgis-<version>.zip
"""

import configparser
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "qgis" / "wxforecast_qgis"
LIBRARY = ROOT / "src" / "wxforecast"


def _files(folder: Path):
    return sorted(p for p in folder.rglob("*") if p.is_file() and "__pycache__" not in p.parts)


def build(dist: Path) -> Path:
    metadata = configparser.ConfigParser()
    metadata.read(PLUGIN / "metadata.txt")
    path = dist / f"wxforecast_qgis-{metadata['general']['version']}.zip"
    dist.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        for file in _files(PLUGIN):
            archive.write(file, Path("wxforecast_qgis") / file.relative_to(PLUGIN))
        for file in _files(LIBRARY):
            archive.write(file, Path("wxforecast_qgis/libs/wxforecast") / file.relative_to(LIBRARY))
    return path


if __name__ == "__main__":
    print(build(Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "dist"))
