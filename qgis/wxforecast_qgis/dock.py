from processing.gui.Postprocessing import handleAlgorithmResults
from qgis.core import (
    Qgis,
    QgsApplication,
    QgsMessageLog,
    QgsProcessing,
    QgsProcessingAlgRunnerTask,
    QgsProcessingContext,
    QgsProcessingFeedback,
    QgsProcessingOutputLayerDefinition,
    QgsProject,
    QgsReferencedRectangle,
    QgsTask,
)
from qgis.gui import QgsDockWidget, QgsExtentWidget, QgsFileWidget
from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import (
    QComboBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSpinBox,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

import wxforecast

from . import indicators
from .icons import kind_icon
from .style import save_styles_path, styles_path, variable_kind

TAG = "Weather Forecast"
ALGORITHM = "wxforecast:fetchforecast"
MAX_LEAD_HOURS = 840
MAX_DAYS = 16
DEFAULT_MODEL = "noaa-gfs-forecast"


def _load_models(task: QgsTask) -> dict:
    return {model_id: wxforecast.model(model_id) for model_id in wxforecast.list_models()}


class ForecastDock(QgsDockWidget):
    def __init__(self, iface):
        super().__init__("Weather forecast", iface.mainWindow())
        self.setObjectName("WxForecastDock")
        self.iface = iface
        self.models = {}
        self.catalog_task = None
        self.job = None

        self.model_combo = QComboBox()
        self.variable_combo = QComboBox()
        self.lead_spin = QSpinBox()
        self.lead_spin.setRange(-1, MAX_LEAD_HOURS)
        self.lead_spin.setSpecialValueText("now")
        self.lead_spin.setSuffix(" h")
        self.lead_spin.setValue(-1)
        self.lead_spin.setToolTip("Hours after the latest run; 'now' picks the valid time nearest now")
        self.days_spin = QSpinBox()
        self.days_spin.setRange(0, MAX_DAYS)
        self.days_spin.setSpecialValueText("one step")
        self.days_spin.setSuffix(" d")
        self.days_spin.setToolTip("Every step up to now + days, for the Temporal Controller; lead ignored")
        self.every_spin = QSpinBox()
        self.every_spin.setRange(0, 240)
        self.every_spin.setSpecialValueText("every step")
        self.every_spin.setSuffix(" h")
        self.every_spin.setToolTip("Keep a step once this many hours have passed since the last kept one")
        self.extent_widget = QgsExtentWidget(None, QgsExtentWidget.WidgetStyle.CondensedStyle)
        self.extent_widget.setMapCanvas(iface.mapCanvas())
        self.extent_widget.setNullValueAllowed(True, "Whole world")
        canvas = iface.mapCanvas()
        if QgsProject.instance().mapLayers():
            self.extent_widget.setOutputExtentFromUser(canvas.extent(), canvas.mapSettings().destinationCrs())
        else:
            self.extent_widget.clear()
        self.fetch_button = QPushButton("Fetch")
        self.reload_button = QPushButton("Reload catalog")
        self.status = QLabel()
        self.status.setWordWrap(True)
        self.styles_widget = QgsFileWidget()
        self.styles_widget.setFilter("Styles (*.toml)")
        self.styles_widget.setFilePath(styles_path() or "")
        self.styles_widget.setToolTip("Style override file (TOML); empty: the library defaults")
        self.styles_button = QToolButton()
        self.styles_button.setIcon(QgsApplication.getThemeIcon("mActionRefresh.svg"))
        self.styles_button.setToolTip("Reload styles and restyle the forecast layers")
        styles_row = QHBoxLayout()
        styles_row.addWidget(self.styles_widget)
        styles_row.addWidget(self.styles_button)

        form = QFormLayout()
        form.addRow("Model", self.model_combo)
        form.addRow("Variable", self.variable_combo)
        form.addRow("Lead", self.lead_spin)
        form.addRow("Days ahead", self.days_spin)
        form.addRow("Every", self.every_spin)
        form.addRow("Extent", self.extent_widget)
        form.addRow("Styles", styles_row)
        buttons = QHBoxLayout()
        buttons.addWidget(self.reload_button)
        buttons.addWidget(self.fetch_button)

        body = QWidget()
        layout = QVBoxLayout(body)
        layout.addLayout(form)
        layout.addLayout(buttons)
        layout.addWidget(self.status)
        layout.addStretch()
        self.setWidget(body)

        self.model_combo.currentIndexChanged.connect(self._fill_variables)
        self.fetch_button.clicked.connect(self._fetch)
        self.reload_button.clicked.connect(self._reload_catalog)
        self.styles_widget.fileChanged.connect(self.reload_styles)
        self.styles_button.clicked.connect(self.reload_styles)

        self._load_catalog()

    def _report(self, message: str, details: str = "", level=Qgis.MessageLevel.Critical) -> None:
        self.iface.messageBar().pushMessage(TAG, message, level=level, duration=10)
        QgsMessageLog.logMessage(f"{message}\n{details}".strip(), TAG, level)
        self.status.setText(message)

    def _set_ready(self, ready: bool) -> None:
        widgets = (self.model_combo, self.variable_combo, self.lead_spin, self.days_spin, self.every_spin)
        for widget in (*widgets, self.extent_widget):
            widget.setEnabled(ready)
        self.fetch_button.setEnabled(ready and self.job is None and self.variable_combo.count() > 0)

    def unload(self) -> None:
        """Cancel running tasks; their completion slots die with this widget."""
        for task in (self.catalog_task, self.job and self.job[0]):
            if task is not None:
                task.cancel()

    def reload_styles(self) -> None:
        """Validate the styles file, then restyle tagged layers, indicators and the variable icons."""
        path = self.styles_widget.filePath().strip()
        try:
            wxforecast.colors.load_styles(path or None)
        except (OSError, ValueError) as exc:
            self._report(f"Styles not applied: {exc}")
            return
        save_styles_path(path)
        count = indicators.restyle(QgsProject.instance())
        indicators.rebuild()
        if self.models:
            self._fill_variables()
        self.status.setText(f"Styles reloaded from {path or 'the defaults'}: {count} layer(s) restyled")

    def _reload_catalog(self) -> None:
        wxforecast.catalog.cache_clear()
        self._load_catalog()

    def _load_catalog(self) -> None:
        self._set_ready(False)
        self.reload_button.setEnabled(False)
        self.status.setText("Loading catalog...")
        self.catalog_task = QgsTask.fromFunction("Load weather forecast catalog", _load_models)
        self.catalog_task.taskCompleted.connect(self._catalog_loaded)
        self.catalog_task.taskTerminated.connect(self._catalog_loaded)
        QgsApplication.taskManager().addTask(self.catalog_task)

    def _catalog_loaded(self) -> None:
        task, self.catalog_task = self.catalog_task, None
        self.reload_button.setEnabled(True)
        if task.exception is not None or task.returned_values is None:
            error = task.exception or "cancelled"
            self._report(f"Cannot load the catalog: {error}", repr(error))
            return
        self.models = task.returned_values
        self.model_combo.blockSignals(True)
        self.model_combo.clear()
        for model_id, model in self.models.items():
            self.model_combo.addItem(model.title or model_id, model_id)
        self.model_combo.setCurrentIndex(max(self.model_combo.findData(DEFAULT_MODEL), 0))
        self.model_combo.blockSignals(False)
        self._fill_variables()
        self._set_ready(bool(self.models))
        self.status.setText(f"{len(self.models)} models" if self.models else "No forecast model found")

    def _fill_variables(self) -> None:
        self.variable_combo.clear()
        model = self.models.get(self.model_combo.currentData())
        if model is None:
            return
        entries = menu_variables(model)
        try:
            kinds = [variable_kind(variable.name, variable.unit) for variable in entries]
        except (OSError, ValueError) as exc:
            self._report(f"Styles file unusable, fix it or clear the Styles field: {exc}")
            kinds = [None] * len(entries)
        for variable, kind in zip(entries, kinds, strict=True):
            label = variable.long_name or variable.name
            text = f"{label} ({variable.unit})" if variable.unit else label
            item_icon = kind_icon(kind, bool(variable.components)) if kind else QIcon()
            self.variable_combo.addItem(item_icon, text, variable.name)
            self.variable_combo.setItemData(
                self.variable_combo.count() - 1,
                indicators.describe(variable.name, variable.components),
                Qt.ItemDataRole.ToolTipRole,
            )
        self.fetch_button.setEnabled(self.job is None and self.variable_combo.count() > 0)

    def _fetch(self) -> None:
        variable = self.variable_combo.currentData()
        project = QgsProject.instance()
        parameters = {
            "MODEL": self.model_combo.currentData(),
            "VARIABLE": variable,
            "LEAD_HOURS": None if self.lead_spin.value() < 0 else self.lead_spin.value(),
            "DAYS": self.days_spin.value(),
            "EVERY_HOURS": self.every_spin.value() or None,
            "EXTENT": None,
            "OUTPUT": QgsProcessingOutputLayerDefinition(QgsProcessing.TEMPORARY_OUTPUT, project),
        }
        if self.extent_widget.isValid() and not self.extent_widget.outputExtent().isNull():
            extent = self.extent_widget.outputExtent()
            parameters["EXTENT"] = QgsReferencedRectangle(extent, self.extent_widget.outputCrs())
        algorithm = QgsApplication.processingRegistry().createAlgorithmById(ALGORITHM)
        context = QgsProcessingContext()
        context.setProject(project)
        feedback = QgsProcessingFeedback()
        task = QgsProcessingAlgRunnerTask(algorithm, parameters, context, feedback)
        self.job = (task, algorithm, context, feedback, variable)
        task.executed.connect(self._fetched)
        self.fetch_button.setEnabled(False)
        self.status.setText(f"Fetching {variable}...")
        QgsApplication.taskManager().addTask(task)

    def _fetched(self, ok: bool, results: dict) -> None:
        (_, algorithm, context, feedback, variable), self.job = self.job, None
        self.fetch_button.setEnabled(self.variable_combo.count() > 0)
        if feedback.isCanceled():
            self.status.setText(f"Fetch of {variable} cancelled")
        elif not ok:
            log = feedback.textLog().strip()
            error = log.splitlines()[-1] if log else "unknown error"
            self._report(f"Fetch of {variable} failed: {error}", log)
        else:
            handleAlgorithmResults(algorithm, context, feedback, results)
            self.status.setText(f"Fetched {variable}")


def menu_variables(model) -> list:
    """Variables in name order; each calculated one is followed by the raw components it is read from."""
    entries = []
    for _, variable in sorted(model.display_variables.items()):
        entries.append(variable)
        entries.extend(model.variables[name] for name in variable.components)
    return entries
