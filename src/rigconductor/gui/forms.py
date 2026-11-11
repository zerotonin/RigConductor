# ╔══════════════════════════════════════════════════════════════════╗
# ║  Rig Conductor — gui.forms                                       ║
# ║  « what is in the arenas or wells »                              ║
# ╠══════════════════════════════════════════════════════════════════╣
# ║  Per-profile metadata forms for the Animals page.                ║
# ╚══════════════════════════════════════════════════════════════════╝
"""Per-profile metadata forms for the wizard's Animals page."""

from __future__ import annotations

import csv
from pathlib import Path

from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from rigconductor.config import ConductorConfig
from rigconductor.constants import N_ARENAS
from rigconductor.gui.vialboard import VialBoard
from rigconductor.profiles.drosophila import (
    EXPERIMENTS,
    STIMULI,
    DrosophilaTriad,
    experiment_label,
)
from rigconductor.profiles.tadpole import WELL_FIELDS, WELLS, parse_plate_csv


class MetadataForm(QWidget):
    """Base: ``values()`` returns the profile metadata dict; ``set_values`` restores it."""

    def values(self) -> dict:  # pragma: no cover - abstract
        raise NotImplementedError

    def set_values(self, d: dict) -> None:  # pragma: no cover - abstract
        raise NotImplementedError


class DrosophilaForm(MetadataForm):
    def __init__(self, cfg: ConductorConfig, parent=None):
        super().__init__(parent)
        self.cfg = cfg
        self.profile = DrosophilaTriad()
        self.vials = self.profile.active_vials(cfg)
        lay = QVBoxLayout(self)

        self.exp = QComboBox()
        for e in EXPERIMENTS:
            self.exp.addItem(experiment_label(e), e)
        self.group = QLineEdit()
        self.group.setPlaceholderText("coded group, e.g. G01")
        self.cross = QLineEdit()
        self.cross.setPlaceholderText("cross label from the crossing guide (optional)")
        self.temp = QDoubleSpinBox()
        self.temp.setRange(10, 45)
        self.temp.setValue(25)
        self.temp.setSuffix(" °C")
        self.stimulus = QComboBox()
        self.stimulus.addItems(list(STIMULI))
        self.ramp = QDoubleSpinBox()
        self.ramp.setRange(0, 20)
        self.ramp.setSuffix(" °C/h (0 = step)")
        self.rearing = QDoubleSpinBox()
        self.rearing.setRange(0, 40)
        self.rearing.setSuffix(" °C (0 = n/a)")
        self.treatment = QComboBox()
        self.treatment.addItems(["none", "ethanol", "drug", "other"])
        self.notes = QLineEdit()
        # two compact columns so the vial board gets the height
        grid = QGridLayout()
        fields = [
            ("Experiment", self.exp),
            ("Group code", self.group),
            ("Cross label", self.cross),
            ("Test temperature", self.temp),
            ("Stimulus", self.stimulus),
            ("Ramp", self.ramp),
            ("Rearing temperature", self.rearing),
            ("Treatment", self.treatment),
            ("Notes", self.notes),
        ]
        for k, (lab, w) in enumerate(fields):
            r, c = k // 2, (k % 2) * 2
            grid.addWidget(QLabel(lab), r, c)
            grid.addWidget(w, r, c + 1)
        grid.setColumnStretch(1, 1)
        grid.setColumnStretch(3, 1)
        lay.addLayout(grid)

        self.board = VialBoard(self.profile.vial_catalogue(cfg), N_ARENAS)
        if not self.board.shelf.catalogue:
            lay.addWidget(
                QLabel(
                    "<span style='color:#b00'>DrosoDB snapshot not found — the shelf is empty. "
                    "Double-click a slot to type a print code.</span>"
                )
            )
        lay.addWidget(self.board, 1)

    def values(self) -> dict:
        arenas = self.board.values(STIMULI[self.stimulus.currentText()])
        return {
            "experiment_id": self.exp.currentData(),
            "group_code": self.group.text().strip(),
            "cross_label": self.cross.text().strip() or None,
            "test_temp_c": self.temp.value(),
            "stimulus": self.stimulus.currentText(),
            "ramp_c_per_h": self.ramp.value() or None,
            "rearing_temp_c": self.rearing.value() or None,
            "treatment": self.treatment.currentText(),
            "notes": self.notes.text().strip() or None,
            "arenas": arenas,
        }

    def set_values(self, d: dict) -> None:
        if d.get("experiment_id") in EXPERIMENTS:
            self.exp.setCurrentIndex(EXPERIMENTS.index(d["experiment_id"]))
        self.group.setText(d.get("group_code") or "")
        self.cross.setText(d.get("cross_label") or "")
        if d.get("test_temp_c") is not None:
            self.temp.setValue(float(d["test_temp_c"]))
        if d.get("stimulus") in STIMULI:
            self.stimulus.setCurrentText(d["stimulus"])
        self.ramp.setValue(float(d.get("ramp_c_per_h") or 0))
        self.rearing.setValue(float(d.get("rearing_temp_c") or 0))
        if d.get("treatment"):
            self.treatment.setCurrentText(d["treatment"])
        self.notes.setText(d.get("notes") or "")
        self.board.set_values(d.get("arenas") or {})


class TadpoleForm(MetadataForm):
    COLS = ("use", *WELL_FIELDS[1:])

    def __init__(self, cfg: ConductorConfig, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        form = QFormLayout()
        self.experiment = QLineEdit()
        self.experiment.setPlaceholderText("series name, e.g. PTZ_dose_2026-12")
        self.notes = QLineEdit()
        form.addRow("Experiment / series", self.experiment)
        form.addRow("Notes", self.notes)
        lay.addLayout(form)
        box = QGroupBox("Plate map (24 wells)")
        v = QVBoxLayout(box)
        self.table = QTableWidget(len(WELLS), len(self.COLS))
        self.table.setHorizontalHeaderLabels(list(self.COLS))
        self.table.setVerticalHeaderLabels(list(WELLS))
        for r in range(len(WELLS)):
            cb = QCheckBox()
            cb.setChecked(True)
            self.table.setCellWidget(r, 0, cb)
            for c in range(1, len(self.COLS)):
                self.table.setItem(r, c, QTableWidgetItem(""))
        self.table.resizeColumnsToContents()
        v.addWidget(self.table)
        row = QHBoxLayout()
        b = QPushButton("Load plate CSV…")
        b.clicked.connect(self._load_csv)
        row.addWidget(b)
        b2 = QPushButton("Fill down from A1")
        b2.clicked.connect(self._fill_down)
        row.addWidget(b2)
        row.addStretch(1)
        v.addLayout(row)
        lay.addWidget(box, 1)

    def _fill_down(self):
        for c in range(1, len(self.COLS)):
            t = self.table.item(0, c).text()
            for r in range(1, len(WELLS)):
                self.table.item(r, c).setText(t)

    def _load_csv(self):
        p, _ = QFileDialog.getOpenFileName(self, "Plate map CSV", str(Path.home()), "CSV (*.csv)")
        if not p:
            return
        rows = parse_plate_csv(Path(p).read_text(encoding="utf-8"))
        by_well = {r["well"]: r for r in rows}
        for r, well in enumerate(WELLS):
            d = by_well.get(well)
            self.table.cellWidget(r, 0).setChecked(d is not None)
            for c, k in enumerate(self.COLS[1:], start=1):
                self.table.item(r, c).setText((d or {}).get(k, ""))

    def values(self) -> dict:
        wells = []
        for r, well in enumerate(WELLS):
            if not self.table.cellWidget(r, 0).isChecked():
                continue
            d = {"well": well}
            for c, k in enumerate(self.COLS[1:], start=1):
                d[k] = self.table.item(r, c).text().strip()
            wells.append(d)
        return {
            "experiment": self.experiment.text().strip(),
            "notes": self.notes.text().strip() or None,
            "wells": wells,
        }

    def set_values(self, d: dict) -> None:
        self.experiment.setText(d.get("experiment") or "")
        self.notes.setText(d.get("notes") or "")
        by_well = {w["well"]: w for w in d.get("wells") or []}
        for r, well in enumerate(WELLS):
            w = by_well.get(well)
            self.table.cellWidget(r, 0).setChecked(w is not None)
            for c, k in enumerate(self.COLS[1:], start=1):
                self.table.item(r, c).setText((w or {}).get(k, ""))

    def export_csv(self, path: Path):
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=WELL_FIELDS)
            w.writeheader()
            w.writerows(self.values()["wells"])


def make_form(profile_name: str, cfg: ConductorConfig) -> MetadataForm:
    return {"drosophila_triad": DrosophilaForm, "tadpole_24well": TadpoleForm}[profile_name](cfg)
