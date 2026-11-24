"""Build the installable plugin zip, with the wxforecast library bundled in wxforecast_qgis/libs:
python3 qgis/build_zip.py [dist] [version]  ->  dist/wxforecast_qgis-<version>.zip
A version (e.g. from a release tag) replaces the one in metadata.txt, inside the zip only.
"""

import configparser
import re
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "qgis" / "wxforecast_qgis"
LIBRARY = ROOT / "src" / "wxforecast"


def _files(folder: Path):
    return sorted(p for p in folder.rglob("*") if p.is_file() and "__pycache__" not in p.parts)


def build(dist: Path, version: str | None = None) -> Path:
    text = (PLUGIN / "metadata.txt").read_text()
    if version:
        text = re.sub(r"(?m)^version=.*$", f"version={version}", text)
    metadata = configparser.ConfigParser()
    metadata.read_string(text)
    path = dist / f"wxforecast_qgis-{metadata['general']['version']}.zip"
    dist.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        for file in _files(PLUGIN):
            name = Path("wxforecast_qgis") / file.relative_to(PLUGIN)
            if file.name == "metadata.txt":
                archive.writestr(str(name), text)
            else:
                archive.write(file, name)
        for file in _files(LIBRARY):
            archive.write(file, Path("wxforecast_qgis/libs/wxforecast") / file.relative_to(LIBRARY))
    return path


if __name__ == "__main__":
    args = sys.argv[1:]
    print(build(Path(args[0]) if args else ROOT / "dist", args[1] if len(args) > 1 else None))
