import ast
import importlib.util
import string
from pathlib import Path

PLUGIN = Path(__file__).parents[1] / "qgis" / "wxforecast_qgis"
spec = importlib.util.spec_from_file_location("i18n", PLUGIN / "i18n.py")
i18n = importlib.util.module_from_spec(spec)
spec.loader.exec_module(i18n)


def tr_literals() -> set[str]:
    found = set()
    for path in PLUGIN.glob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "tr":
                argument = node.args[0]
                assert isinstance(argument, ast.Constant), f"{path.name}:{node.lineno}: tr() needs a literal"
                found.add(argument.value)
    return found


def test_every_tr_literal_has_a_french_entry_and_no_dead_keys():
    literals = tr_literals()
    assert sorted(literals - i18n.FRENCH.keys()) == []
    assert sorted(i18n.FRENCH.keys() - literals) == []


def test_french_keeps_the_placeholders_and_has_no_em_dash():
    for english, french in i18n.FRENCH.items():
        assert "\u2014" not in french
        assert _fields(english) == _fields(french), english


def _fields(text: str) -> list[str]:
    return sorted(name for _, name, _, _ in string.Formatter().parse(text) if name is not None)


def test_translate_by_locale():
    assert i18n.translate("Weather forecast", "fr_FR") == "Prévisions météo"
    assert i18n.translate("Weather forecast", "fr") == "Prévisions météo"
    assert i18n.translate("Weather forecast", "en_US") == "Weather forecast"
    assert i18n.translate("Not in the dictionary", "fr") == "Not in the dictionary"
