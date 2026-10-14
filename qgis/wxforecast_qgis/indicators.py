"""Kind and raw/calculated origin stored on layers and groups, shown as layer tree indicators."""

from qgis.core import QgsLayerTree, QgsLayerTreeNode, QgsProject, QgsRasterLayer, QgsVectorLayer
from qgis.gui import QgsLayerTreeViewIndicator
from qgis.PyQt import sip

from .icons import kind_icon
from .style import apply_flow_style, apply_style

PREFIX = "wxforecast/"
VARIABLE, KIND, ORIGIN, COMPONENTS, UNIT = (
    f"{PREFIX}{key}" for key in ("variable", "kind", "origin", "components", "unit")
)
RAW, CALCULATED = "raw", "calculated"

_active: "Indicators | None" = None


def tag(target, variable: str, kind: str, components: tuple[str, ...] = (), unit: str = "") -> None:
    """Store the variable, kind, origin and unit on a layer or a layer tree group (saved in the project)."""
    target.setCustomProperty(VARIABLE, variable)
    target.setCustomProperty(KIND, kind)
    target.setCustomProperty(ORIGIN, CALCULATED if components else RAW)
    target.setCustomProperty(COMPONENTS, ",".join(components))
    target.setCustomProperty(UNIT, unit)


def describe(variable: str, components: tuple[str, ...] | list[str]) -> str:
    if components:
        return f"{variable}: calculated from {', '.join(components)}"
    return f"{variable}: raw, read as stored"


def read_tag(node: QgsLayerTreeNode) -> dict | None:
    """The tag of a group node or of a layer node's layer, None if untagged."""
    return read_layer_tag(node.layer() if QgsLayerTree.isLayer(node) else node)


def read_layer_tag(target) -> dict | None:
    if target is None or not target.customProperty(ORIGIN):
        return None
    return {
        "variable": str(target.customProperty(VARIABLE, "")),
        "kind": str(target.customProperty(KIND, "other")),
        "calculated": target.customProperty(ORIGIN) == CALCULATED,
        "components": tuple(c for c in str(target.customProperty(COMPONENTS, "")).split(",") if c),
        "unit": str(target.customProperty(UNIT, "") or ""),
    }


def restyle(project: QgsProject) -> int:
    """Re-apply the current styles to every tagged raster and flow layer; returns how many."""
    count = 0
    for layer in project.mapLayers().values():
        info = read_layer_tag(layer)
        if info is None:
            continue
        if isinstance(layer, QgsRasterLayer):
            kind = apply_style(layer, info["variable"], info["unit"])
            tag(layer, info["variable"], kind, info["components"], info["unit"])
        elif isinstance(layer, QgsVectorLayer) and layer.fields().indexOf("speed") >= 0:
            apply_flow_style(layer, info["unit"])
        else:
            continue
        count += 1
    return count


def refresh() -> None:
    if _active is not None:
        _active.refresh()


def rebuild() -> None:
    """Recreate every indicator, e.g. after a styles change gave a kind another icon."""
    kind_icon.cache_clear()
    if _active is not None:
        _active.clear()
        _active.refresh()


class Indicators:
    """One indicator per tagged node of the layer tree view: the kind icon, with a gear if calculated."""

    def __init__(self, view, project: QgsProject | None = None):
        global _active
        self.view = view
        self.project = project or QgsProject.instance()
        self.shown: list[tuple[QgsLayerTreeNode, QgsLayerTreeViewIndicator]] = []
        root = self.project.layerTreeRoot()
        root.addedChildren.connect(self._on_added)
        root.willRemoveChildren.connect(self._on_removing)
        self.project.readProject.connect(self._on_read)
        _active = self
        self.refresh()

    def _on_added(self, *_) -> None:
        self.refresh()

    def _on_read(self, *_) -> None:
        self.refresh()

    def _on_removing(self, parent, start: int, end: int) -> None:
        # The view keys indicators by node pointer and keeps them: a new node at that address inherits them.
        doomed = []
        for child in parent.children()[start : end + 1]:
            doomed += [child, *_walk(child)] if QgsLayerTree.isGroup(child) else [child]
        kept = []
        for node, indicator in self.shown:
            if any(node is gone for gone in doomed):
                self.view.removeIndicator(node, indicator)
            else:
                kept.append((node, indicator))
        self.shown = kept

    def refresh(self) -> None:
        self.shown = [(n, i) for n, i in self.shown if not sip.isdeleted(n) and not sip.isdeleted(i)]
        for node in _walk(self.project.layerTreeRoot()):
            info = read_tag(node)
            if info is None or any(shown is node for shown, _ in self.shown):
                continue
            indicator = QgsLayerTreeViewIndicator(self.view)
            indicator.setIcon(kind_icon(info["kind"], info["calculated"]))
            indicator.setToolTip(describe(info["variable"], info["components"]))
            self.view.addIndicator(node, indicator)
            self.shown.append((node, indicator))

    def clear(self) -> None:
        for node, indicator in self.shown:
            if not sip.isdeleted(node) and not sip.isdeleted(indicator):
                self.view.removeIndicator(node, indicator)
        self.shown = []

    def unload(self) -> None:
        global _active
        root = self.project.layerTreeRoot()
        root.addedChildren.disconnect(self._on_added)
        root.willRemoveChildren.disconnect(self._on_removing)
        self.project.readProject.disconnect(self._on_read)
        self.clear()
        if _active is self:
            _active = None


def _walk(group):
    for child in group.children():
        yield child
        if QgsLayerTree.isGroup(child):
            yield from _walk(child)
