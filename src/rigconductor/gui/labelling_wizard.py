# ╔══════════════════════════════════════════════════════════════════╗
# ║  Rig Conductor — gui.labelling_wizard                            ║
# ║  « more SLEAP training data from the recordings we make »        ║
# ╠══════════════════════════════════════════════════════════════════╣
# ║  Start → Recordings → Frames → Package     (new frames)          ║
# ║  Start → Continue                           (picked frames)      ║
# ║  Start → Bring back                         (labelled frames)    ║
# ║  Start → Training data → Train              (Aoraki, promote)    ║
# ╚══════════════════════════════════════════════════════════════════╝
"""The training-data wizard (second wizard of Rig Conductor).

All SLEAP work runs in the SLEAP 1.6 env as child processes
(:mod:`rigconductor.sleapenv`); this window only shows progress and choices.
"""

from __future__ import annotations

import getpass
import sys
from pathlib import Path

from PyQt6.QtCore import QSize, Qt, QTimer
from PyQt6.QtGui import QColor, QPainter, QPixmap
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QRadioButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
    QWizard,
    QWizardPage,
)

from rigconductor import ConductorError, sleapenv
from rigconductor import models as models_mod
from rigconductor.config import ConductorConfig
from rigconductor.constants import (
    LABEL_FRAMES_PER_ARENA,
    LABEL_MIN_GAP_S,
    LABEL_MIX,
    LABEL_PROFILE,
    LABEL_TEST_SHARE,
    LABEL_THUMB_PX,
    TRAIN_EPOCHS,
    TRAIN_POLL_S,
    TRAIN_TABLE_MAX_PX,
    WONG,
)
from rigconductor.export import removable_disks
from rigconductor.gui import dialogs
from rigconductor.gui.help_window import add_help_button
from rigconductor.gui.progress import ProgressPanel, parse_progress
from rigconductor.gui.train_progress import TrainingProgress
from rigconductor.gui.wizard import Worker
from rigconductor.labels import package as pkg_mod
from rigconductor.labels import train as train_mod
from rigconductor.labels.recordings import Recording, find_recordings
from rigconductor.labels.store import LabelSet, default_set, list_sets, set_default

(
    PAGE_START,
    PAGE_RECS,
    PAGE_FRAMES,
    PAGE_PACKAGE,
    PAGE_CONTINUE,
    PAGE_BACK,
    PAGE_DATA,
    PAGE_TRAIN,
) = range(8)
MODES = (
    ("new", "Pick new frames to label", PAGE_RECS),
    ("continue", "Continue labelling frames picked earlier", PAGE_CONTINUE),
    ("back", "Bring labelled frames back", PAGE_BACK),
    ("train", "Choose training data and train a model on Aoraki", PAGE_DATA),
)
OPEN_PACKAGE_STATES = ("out", "returned")  # packages not merged yet


def _item(text, checkable: bool = False, checked: bool = False) -> QTableWidgetItem:
    it = QTableWidgetItem(str(text))
    flags = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
    if checkable:
        flags |= Qt.ItemFlag.ItemIsUserCheckable
        it.setCheckState(Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked)
    it.setFlags(flags)
    return it


def _table(headers: list[str]) -> QTableWidget:
    t = QTableWidget(0, len(headers))
    t.setHorizontalHeaderLabels(headers)
    t.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
    t.horizontalHeader().setStretchLastSection(True)
    t.verticalHeader().setVisible(False)
    return t


class TaskPage(QWizardPage):
    """A page with a log, a progress bar and one background task at a time."""

    def __init__(self, wiz: LabellingWizard, title: str, subtitle: str):
        super().__init__()
        self.wiz = wiz
        self.setTitle(title)
        self.setSubTitle(subtitle)
        self.lay = QVBoxLayout(self)
        self.progress = ProgressPanel()
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(2000)

    def add_log_widgets(self, stretch: int = 1):
        self.lay.addWidget(self.progress)
        self.lay.addWidget(self.log, stretch)

    def add_line(self, line: str):
        p = parse_progress(line)
        if p is not None:
            self.progress.show_line(p)
        else:
            self.log.appendPlainText(line)

    def run(self, fn, done):
        """Run ``fn(emit)`` in the worker; ``done(ok, msg)`` afterwards."""

        def finished(ok, msg):
            self.progress.finish(ok)
            if not ok:
                dialogs.critical(self, "Failed", msg)
            done(ok, msg)

        self.wiz.run_in_worker(fn, finished, self.add_line)


def progress_to(emit):
    return lambda frac, text: emit(f"@progress label {frac:.3f} {text}")


# ── start ─────────────────────────────────────────────────────────
class StartPage(TaskPage):
    manual_section = "Start"

    def __init__(self, wiz):
        super().__init__(wiz, "Training data", "Grow a label set, bring labels back, or train.")
        form = QFormLayout()
        self.set_combo = QComboBox()
        self.set_combo.currentIndexChanged.connect(self._set_changed)
        row = QHBoxLayout()
        row.addWidget(self.set_combo, 1)
        new_btn = QPushButton("New set from a SLEAP project…")
        new_btn.clicked.connect(self._new_set)
        row.addWidget(new_btn)
        self.default_btn = QPushButton("Make default")
        self.default_btn.setToolTip("Pre-selected here and copied to Aoraki by label-sync")
        self.default_btn.clicked.connect(self._make_default)
        row.addWidget(self.default_btn)
        form.addRow("Label set", row)
        self.summary = QLabel("")
        self.summary.setWordWrap(True)
        form.addRow("", self.summary)
        self.lay.addLayout(form)
        self.mode = QButtonGroup(self)
        for i, (key, text, page) in enumerate(MODES):
            b = QRadioButton(text)
            b.setProperty("key", key)
            b.setProperty("page", page)
            self.mode.addButton(b, i)
            self.lay.addWidget(b)
        self.mode.button(0).setChecked(True)
        sleap_row = QHBoxLayout()
        self.sleap_status = QLabel("SLEAP: checking…")
        sleap_row.addWidget(self.sleap_status, 1)
        self.setup_btn = QPushButton("Set up SLEAP")
        self.setup_btn.setToolTip("Install SLEAP (CPU build) for this user; no GPU or sudo needed")
        self.setup_btn.clicked.connect(self._setup_sleap)
        sleap_row.addWidget(self.setup_btn)
        self.lay.addLayout(sleap_row)
        self.add_log_widgets()

    def initializePage(self):  # noqa: N802
        self.fill_sets()
        QTimer.singleShot(0, self.check_sleap)

    def fill_sets(self, select: str | None = None):
        self.set_combo.blockSignals(True)
        self.set_combo.clear()
        for name in list_sets():
            self.set_combo.addItem(name)
        select = select or default_set()
        if select:
            self.set_combo.setCurrentText(select)
        self.set_combo.blockSignals(False)
        self._set_changed()

    def _set_changed(self, *_):
        name = self.set_combo.currentText()
        self.wiz.lset = LabelSet.load(name) if name else None
        is_default = bool(name) and name == default_set()
        self.default_btn.setEnabled(bool(name) and not is_default)
        self.default_btn.setText("Default set" if is_default else "Make default")
        self.summary.setText(describe_set(self.wiz.lset))
        self.completeChanged.emit()

    def _make_default(self):
        set_default(self.set_combo.currentText())
        self._set_changed()

    def check_sleap(self):
        self.wiz.env = sleapenv.find()
        if self.wiz.env:
            self.sleap_status.setText(
                f"SLEAP {self.wiz.env.version} ready ({self.wiz.env.bin_dir})"
            )
            self.setup_btn.setText("Reinstall SLEAP")
        else:
            self.sleap_status.setText("SLEAP is not installed for this user yet.")
        self.completeChanged.emit()

    def _setup_sleap(self):
        reinstall = self.wiz.env is not None
        self.setup_btn.setEnabled(False)

        def job(emit):
            sleapenv.install(log=emit, reinstall=reinstall)

        def done(ok, _msg):
            self.setup_btn.setEnabled(True)
            self.check_sleap()

        self.run(job, done)

    def _new_set(self):
        if not self.wiz.env:
            dialogs.information(self, "SLEAP", "Set up SLEAP first.")
            return
        src, _ = QFileDialog.getOpenFileName(
            self, "SLEAP project with hand labels", str(Path.home() / "data"), "SLEAP (*.slp)"
        )
        if not src:
            return
        name = self.set_combo.currentText() or LABEL_PROFILE
        name, ok = _ask_text(self, "Label set name", "Name of the new label set:", name)
        if not ok or not name:
            return
        env = self.wiz.env

        def job(emit):
            info = sleapenv.run_task(env, "inspect", {"slp": src}, log=emit)
            lset = LabelSet.create(name, info["nodes"])
            res = sleapenv.run_task(
                env,
                "seed",
                {"src": src, "out": str(lset.path / "v1.pkg.slp"), "set": name},
                log=emit,
                progress=progress_to(emit),
            )
            lset.add_version(res, note=f"seed from {Path(src).name}")
            lset.add_project(src, note="seed of v1; keep labelling it and bring it back")
            emit(f"label set '{name}' v1: {res['frames']} frames, {res['instances']} flies")

        self.run(job, lambda ok, _m: ok and self.fill_sets(name))

    def isComplete(self):  # noqa: N802
        return self.wiz.lset is not None and self.wiz.env is not None

    def nextId(self):  # noqa: N802
        return self.mode.checkedButton().property("page")


def describe_set(lset: LabelSet | None) -> str:
    if lset is None:
        return "No label set yet: create one from an existing SLEAP project."
    v = lset.latest()
    if not v:
        return f"{lset.name}: no versions yet"
    recs = ", ".join(f"{r} ({n})" for r, n in v.get("recordings", {}).items())
    scales = ", ".join(f"{s}: {n}" for s, n in v.get("scales", {}).items())
    out_pk = sum(1 for p in lset.data["packages"] if p.get("status") in OPEN_PACKAGE_STATES)
    val = "; ".join(f"{s}: {', '.join(r)}" for s, r in lset.data["validation"].items()) or "none"
    return (
        f"<b>v{v['version']}</b>: {v['frames']} frames, {v['instances']} flies · scales {scales}"
        f"<br>recordings: {recs}<br>validation recordings: {val}"
        f"<br>packages out for labelling: {out_pk}"
    )


def _ask_text(parent, title, label, default=""):
    return dialogs.get_text(parent, title, label, default)


# ── recordings ────────────────────────────────────────────────────
class RecordingsPage(TaskPage):
    manual_section = "Pick new frames: Recordings"

    COLS = ["Use", "Validation only", "Recording", "Date", "Scale", "Arenas", "Poses", "In set"]

    def __init__(self, wiz):
        super().__init__(
            wiz,
            "Recordings",
            "Tick the recordings to take frames from. Validation recordings are never "
            "used for training; they test every new model (one per camera scale).",
        )
        filt = QHBoxLayout()
        self.only_new = QCheckBox("Only recordings not in the label set yet")
        self.only_new.setChecked(True)
        self.only_new.toggled.connect(self.fill)
        self.only_pose = QCheckBox("Only analysed recordings (with poses)")
        self.only_pose.toggled.connect(self.fill)
        filt.addWidget(self.only_new)
        filt.addWidget(self.only_pose)
        filt.addStretch(1)
        self.lay.addLayout(filt)
        self.table = _table(self.COLS)
        self.table.itemChanged.connect(lambda *_: self.completeChanged.emit())
        self.lay.addWidget(self.table, 1)
        self.recs: list[Recording] = []

    def initializePage(self):  # noqa: N802
        self.all_recs = find_recordings(self.wiz.cfg.data_root)
        self.fill()

    def fill(self, *_):
        lset = self.wiz.lset
        in_set = set((lset.latest() or {}).get("recordings", {})) if lset else set()
        val = lset.validation_recordings() if lset else set()
        self.recs = [
            r
            for r in self.all_recs
            if (not self.only_new.isChecked() or r.name not in in_set or r.name in val)
            and (not self.only_pose.isChecked() or r.poses)
        ]
        self.table.blockSignals(True)
        self.table.setRowCount(len(self.recs))
        for i, r in enumerate(self.recs):
            cells = [
                _item("", True, False),
                _item("", True, r.name in val),
                _item(r.name),
                _item(r.date),
                _item(r.scale),
                _item(len(r.clips)),
                _item(f"{len(r.poses)}/{len(r.clips)}"),
                _item("yes" if r.name in in_set else ""),
            ]
            for c, it in enumerate(cells):
                self.table.setItem(i, c, it)
        self.table.blockSignals(False)
        self.completeChanged.emit()

    def _checked(self, col: int) -> list[Recording]:
        return [
            r
            for i, r in enumerate(self.recs)
            if self.table.item(i, col).checkState() == Qt.CheckState.Checked
        ]

    def chosen(self) -> list[Recording]:
        return self._checked(0)

    def isComplete(self):  # noqa: N802
        return bool(self.chosen())

    def validatePage(self):  # noqa: N802
        val = self._checked(1)
        if val:
            by_scale: dict[str, list[str]] = {}
            for r in val:
                by_scale.setdefault(r.scale, []).append(r.name)
            for scale, names in by_scale.items():
                old = set(self.wiz.lset.data["validation"].get(scale, []))
                self.wiz.lset.set_validation(scale, sorted(old | set(names)))
        self.wiz.recordings = self.chosen()
        return True


# ── frames ────────────────────────────────────────────────────────
class FramesPage(TaskPage):
    manual_section = "Pick new frames: Frames"

    def __init__(self, wiz):
        super().__init__(
            wiz,
            "Frames",
            "How many frames per arena, and how to choose them. Unsure = the model doubted "
            "itself; contact = flies touch; spread = evenly over the clip.",
        )
        form = QFormLayout()
        self.n = QSpinBox()
        self.n.setRange(1, 200)
        self.n.setValue(LABEL_FRAMES_PER_ARENA)
        form.addRow("Frames per arena", self.n)
        mix_row = QHBoxLayout()
        self.mix = {}
        for key, text in (("unsure", "unsure"), ("contact", "contact"), ("uniform", "spread")):
            s = QSpinBox()
            s.setRange(0, 100)
            s.setSuffix(" %")
            s.setValue(round(LABEL_MIX[key] * 100))
            self.mix[key] = s
            mix_row.addWidget(QLabel(text))
            mix_row.addWidget(s)
        mix_row.addStretch(1)
        form.addRow("Mix", mix_row)
        self.prefill = QComboBox()
        form.addRow("Pre-fill with model", self.prefill)
        self.lay.addLayout(form)
        btns = QHBoxLayout()
        self.pick_btn = QPushButton("Choose frames")
        self.pick_btn.clicked.connect(self.pick)
        btns.addWidget(self.pick_btn)
        self.picked_label = QLabel("")
        btns.addWidget(self.picked_label, 1)
        self.lay.addLayout(btns)
        self.table = _table(["Recording", "Arena", "Frame", "Why", "Score"])
        self.lay.addWidget(self.table, 2)
        self.add_log_widgets(1)
        self.frames: list[dict] = []

    def initializePage(self):  # noqa: N802
        self.prefill.clear()
        self.prefill.addItem("none (empty frames)", None)
        for run_id in train_mod.local_runs():
            self.prefill.addItem(run_id, run_id)
        want = self.wiz.default_model()
        i = self.prefill.findData(want) if want else -1
        self.prefill.setCurrentIndex(i if i >= 0 else min(1, self.prefill.count() - 1))
        self.frames = []
        self.table.setRowCount(0)
        self.picked_label.setText("")
        self.completeChanged.emit()

    def pick(self):
        env, lset = self.wiz.env, self.wiz.lset
        clips = [c for r in self.wiz.recordings for c in r.clip_entries()]
        val = lset.validation_recordings()
        args = {
            "clips": clips,
            "n": self.n.value(),
            "mix": {k: s.value() / 100 for k, s in self.mix.items()},
            "min_gap_s": LABEL_MIN_GAP_S,
            "n_expected": 3,
            "exclude": [list(x) for x in lset.labelled_frames()],
        }
        self.pick_btn.setEnabled(False)
        result = {}

        def job(emit):
            result.update(
                sleapenv.run_task(env, "select", args, log=emit, progress=progress_to(emit))
            )

        def done(ok, _m):
            self.pick_btn.setEnabled(True)
            if ok:
                self.show_frames(result["frames"], val)

        self.run(job, done)

    def show_frames(self, frames: list[dict], val: set[str]):
        self.frames = frames
        self.table.setRowCount(len(frames))
        for i, f in enumerate(frames):
            why = f["reason"] + (" · validation" if f["recording"] in val else "")
            for c, v in enumerate(
                [f["recording"], f"{int(f['arena']):02d}", f["frame"], why, f.get("score", "")]
            ):
                self.table.setItem(i, c, _item(v))
        n_rec = len({f["recording"] for f in frames})
        self.picked_label.setText(f"{len(frames)} frames from {n_rec} recordings")
        self.completeChanged.emit()

    def isComplete(self):  # noqa: N802
        return bool(self.frames)

    def validatePage(self):  # noqa: N802
        self.wiz.frames = self.frames
        self.wiz.prefill_run = self.prefill.currentData()
        return True


# ── package ───────────────────────────────────────────────────────
class PackagePage(TaskPage):
    manual_section = "Pick new frames: Where to label"

    def __init__(self, wiz):
        super().__init__(
            wiz,
            "Where to label",
            "Label on this PC, or write a package to a USB stick for another Linux PC "
            "(it carries the frames, the rules and a SLEAP installer).",
        )
        self.where = QComboBox()
        self.lay.addWidget(self.where)
        row = QHBoxLayout()
        self.go = QPushButton("Make the package")
        self.go.clicked.connect(self.make)
        row.addWidget(self.go)
        self.result = QLabel("")
        self.result.setWordWrap(True)
        row.addWidget(self.result, 1)
        self.lay.addLayout(row)
        self.add_log_widgets()
        self.made = False

    def initializePage(self):  # noqa: N802
        self.where.clear()
        self.where.addItem("Label here on this PC (opens SLEAP)", "here")
        for d in removable_disks():
            if d.mountpoint:
                self.where.addItem(f"USB stick: {d.text}", d.mountpoint)
        self.where.addItem("A folder…", "__dir__")
        self.made = False
        self.completeChanged.emit()

    def make(self):
        where = self.where.currentData()
        dest = None
        if where == "__dir__":
            d = QFileDialog.getExistingDirectory(self, "Package folder", str(Path.home()))
            if not d:
                return
            dest = Path(d)
        elif where != "here":
            dest = Path(where)
        lset, env, frames = self.wiz.lset, self.wiz.env, self.wiz.frames
        prefill = train_mod.model_dirs(self.wiz.prefill_run) if self.wiz.prefill_run else None
        out = {}
        self.go.setEnabled(False)

        def job(emit):
            out["path"] = pkg_mod.build_package(
                lset, frames, env, prefill=prefill, dest=dest, log=emit, progress=progress_to(emit)
            )

        def done(ok, _m):
            self.go.setEnabled(not ok)
            if not ok:
                return
            self.made = True
            path = out["path"]
            if where == "here":
                sleapenv.open_labelling(env, path / pkg_mod.LABELS_FILE)
                self.result.setText(
                    f"SLEAP opened <b>{path / pkg_mod.LABELS_FILE}</b>. When you are done, "
                    "save and start this wizard again with 'Bring labelled frames back'."
                )
            else:
                self.result.setText(
                    f"Package written to <b>{path}</b>. On the labelling PC run "
                    "<code>label.sh</code> in that folder (it installs SLEAP the first time)."
                )
            self.completeChanged.emit()

        self.run(job, done)

    def isComplete(self):  # noqa: N802
        return self.made

    def nextId(self):  # noqa: N802
        return -1


# ── packages out for labelling ────────────────────────────────────
def open_packages(lset: LabelSet, extra_roots: list[Path] = ()) -> list[tuple[Path, str, dict]]:
    """Unmerged packages of ``lset``: (folder, where, info), sticks before this PC.

    A package written to a stick has a copy on this PC too; the stick copy is
    the one that gets labelled, so it wins when both are there.
    """
    roots = [(Path(d.mountpoint), "stick") for d in removable_disks() if d.mountpoint]
    roots += [(Path(r), "folder") for r in extra_roots]
    found = [(p, where) for root, where in roots for p in pkg_mod.find_packages(root)]
    found += [(p, "this PC") for p in sorted((lset.path / "packages").glob("*"))]
    out, seen = [], set()
    for folder, where in found:
        try:
            info = pkg_mod.read_info(folder)
            entry = lset.package(info["id"])
        except (ConductorError, KeyError):
            continue
        if info.get("set") != lset.name or info["id"] in seen:
            continue
        if entry.get("status") not in OPEN_PACKAGE_STATES:
            continue
        seen.add(info["id"])
        out.append((folder, where, {**info, **entry}))  # entry: frame count, status
    return out


STATE_COLOURS = {  # labelled / only predicted / nothing yet
    "labelled": WONG["bluish_green"],
    "predicted": WONG["yellow"],
    "empty": WONG["vermillion"],
}


class LabelBar(QWidget):
    """Green / yellow / red bar of a project's frames."""

    def __init__(self, states: dict | None = None):
        super().__init__()
        self.setMinimumSize(160, 18)
        self.set_states(states)

    def set_states(self, states: dict | None):
        self.states = states or {}
        if states:
            self.setToolTip(
                f"{states['labelled']} labelled · {states['predicted']} predicted only · "
                f"{states['empty']} not labelled yet (of {states['total']})"
            )
        self.update()

    def paintEvent(self, _ev):  # noqa: N802
        p = QPainter(self)
        r = self.rect().adjusted(2, 4, -2, -4)
        p.fillRect(r, QColor(200, 200, 200))
        total = self.states.get("total") or 0
        x = r.left()
        for key, colour in STATE_COLOURS.items():
            if not total:
                break
            w = round(r.width() * self.states.get(key, 0) / total)
            p.fillRect(x, r.top(), w, r.height(), QColor(*colour))
            x += w
        p.end()


def labelling_items(lset: LabelSet, extra_roots: list[Path] = ()) -> list[dict]:
    """Everything that can be labelled further: wizard packages and SLEAP projects."""
    items = [
        {
            "kind": "package",
            "name": e["id"],
            "where": where if where == "this PC" else f"{where}: {folder}",
            "slp": folder / pkg_mod.LABELS_FILE,
            "folder": folder,
            "detail": ", ".join(e.get("recordings", [])),
        }
        for folder, where, e in open_packages(lset, extra_roots)
    ]
    for pr in lset.projects():
        path = Path(pr["path"])
        merged = f"; last taken in: v{pr['merged_into']}" if pr.get("merged_into") else ""
        items.append(
            {
                "kind": "project",
                "name": path.name,
                "where": str(path.parent),
                "slp": path,
                "missing": not path.is_file(),
                "detail": (pr.get("note") or "") + merged,
            }
        )
    return items


class ContinuePage(TaskPage):
    manual_section = "Continue labelling"

    COLS = ["Name", "Kind", "Progress", "Labelled", "Predicted", "Not yet", "Where", "About"]

    def __init__(self, wiz):
        super().__init__(
            wiz,
            "Continue labelling",
            "Packages and SLEAP projects that still have frames to label. Green = labelled, "
            "yellow = only the model's prediction, red = nothing yet. Open one in SLEAP, "
            "label, save; bring the new frames in with 'Bring labelled frames back'.",
        )
        self.table = _table(self.COLS)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.cellDoubleClicked.connect(lambda *_: self.open_selected())
        self.lay.addWidget(self.table, 2)
        row = QHBoxLayout()
        self.open_btn = QPushButton("Open in SLEAP")
        self.open_btn.clicked.connect(self.open_selected)
        row.addWidget(self.open_btn)
        add = QPushButton("Add a SLEAP project…")
        add.setToolTip("A .slp labelled outside the wizard; its new hand labels can be taken in")
        add.clicked.connect(self._add_project)
        row.addWidget(add)
        more = QPushButton("Look on a folder…")
        more.clicked.connect(self._pick_folder)
        row.addWidget(more)
        self.copy_btn = QPushButton("Copy to USB…")
        self.copy_btn.setToolTip(
            "Copy the selected package, with what is labelled so far, to a stick or folder"
        )
        self.copy_btn.clicked.connect(self.copy_selected)
        row.addWidget(self.copy_btn)
        self.delete_btn = QPushButton("Delete…")
        self.delete_btn.setToolTip(
            "Delete a package (all its copies), or take a project off this list"
        )
        self.delete_btn.clicked.connect(self.delete_selected)
        row.addWidget(self.delete_btn)
        self.result = QLabel("")
        self.result.setWordWrap(True)
        row.addWidget(self.result, 1)
        self.lay.addLayout(row)
        self.add_log_widgets(1)
        self.states: dict[int, dict] = {}
        self.items: list[dict] = []
        self.bars: list[LabelBar] = []
        self.extra: list[Path] = []
        self.opened = False

    def initializePage(self):  # noqa: N802
        self.opened = False
        self.result.setText("")
        self.fill()

    def fill(self):
        self.items = labelling_items(self.wiz.lset, self.extra)
        self.states = {}
        self.bars = []
        self.table.setRowCount(len(self.items))
        for i, it in enumerate(self.items):
            vals = [it["name"], it["kind"], "", "…", "…", "…", it["where"], it["detail"]]
            if it.get("missing"):
                vals[3:6] = ["file missing", "", ""]
            for c, v in enumerate(vals):
                self.table.setItem(i, c, _item(v))
            bar = LabelBar()
            self.table.setCellWidget(i, 2, bar)
            self.bars.append(bar)
        self.table.setColumnWidth(2, 180)
        if self.items:
            self.table.selectRow(0)
            self.count_states()
        else:
            self.result.setText("Nothing to continue. Pick new frames first.")
        self.open_btn.setEnabled(bool(self.items))
        self.delete_btn.setEnabled(bool(self.items))
        self.copy_btn.setEnabled(any(it["kind"] == "package" for it in self.items))

    def count_states(self):
        """Labelled / predicted / empty frames of every item (read in the SLEAP env)."""
        env, items, out = self.wiz.env, list(self.items), {}

        def job(emit):
            for k, it in enumerate(items):
                if it["slp"].is_file():
                    out[k] = sleapenv.run_task(env, "inspect", {"slp": str(it["slp"])})["states"]
            emit(f"read {len(out)} of {len(items)}")

        def done(ok, _m):
            if ok and items == self.items:
                self.show_states(out)

        self.run(job, done)

    def show_states(self, states: dict[int, dict]):
        self.states = dict(states)
        for k, st in states.items():
            self.bars[k].set_states(st)
            for c, key in ((3, "labelled"), (4, "predicted"), (5, "empty")):
                self.table.setItem(k, c, _item(st[key]))

    def _pick_folder(self):
        d = QFileDialog.getExistingDirectory(self, "Folder with labelling packages")
        if d:
            self.extra.append(Path(d))
            self.fill()

    def _add_project(self):
        src, _ = QFileDialog.getOpenFileName(
            self, "SLEAP project", str(Path.home() / "data"), "SLEAP (*.slp)"
        )
        if src:
            self.wiz.lset.add_project(src)
            self.fill()

    def selected(self) -> tuple[int, dict] | None:
        rows = self.table.selectionModel().selectedRows()
        return (rows[0].row(), self.items[rows[0].row()]) if rows and self.items else None

    def copy_selected(self):
        sel = self.selected()
        if not sel:
            return
        _, it = sel
        if it["kind"] != "package":
            dialogs.information(
                self,
                "Copy to USB",
                "A SLEAP project needs its videos and cannot be carried on its own. "
                "Pick new frames from its recordings to make a package instead.",
            )
            return
        dest = self._ask_destination()
        if dest is None:
            return
        lset, src = self.wiz.lset, it["folder"]
        target = pkg_mod.copy_target(lset, it["name"], dest)
        replace = False
        if target.exists():
            ask = dialogs.warning(
                self,
                "Copy to USB",
                f"<b>{target}</b> exists already.<br><br>Replace it with the copy from "
                f"{it['where']}? <b>Anything labelled in the copy on the stick will be "
                "lost.</b> To keep those labels, bring them back first.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            )
            if ask != QMessageBox.StandardButton.Yes:
                return
            replace = True
        out = {}

        def job(emit):
            out["path"] = pkg_mod.copy_package(lset, src, dest, replace=replace)
            emit(f"copied to {out['path']}")

        def done(ok, _m):
            if ok:
                self.result.setText(
                    f"Copied to <b>{out['path']}</b>. On the labelling PC run "
                    "<code>label.sh</code> in that folder. Label only one copy of a package."
                )

        self.run(job, done)

    def _ask_destination(self) -> Path | None:
        """A plugged-in stick, or a folder."""
        sticks = [d for d in removable_disks() if d.mountpoint]
        choices = [f"USB stick: {d.text}" for d in sticks] + ["A folder…"]
        pick, ok = dialogs.get_item(self, "Copy to USB", "Copy the package to:", choices)
        if not ok:
            return None
        if pick == "A folder…":
            d = QFileDialog.getExistingDirectory(self, "Copy the package into", str(Path.home()))
            return Path(d) if d else None
        return Path(sticks[choices.index(pick)].mountpoint)

    def delete_selected(self):
        sel = self.selected()
        if not sel:
            return
        k, it = sel
        if it["kind"] == "project":
            self._forget_project(it)
        else:
            self._delete_package(k, it)

    def _forget_project(self, it: dict):
        ask = dialogs.question(
            self,
            "Take project off the list",
            f"Take <b>{it['name']}</b> off this list?<br><br>The file itself is not deleted "
            f"({it['slp']}), and frames already brought in stay in the label set.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if ask == QMessageBox.StandardButton.Yes:
            self.wiz.lset.remove_project(it["slp"])
            self.fill()

    def _delete_package(self, k: int, it: dict):
        lset, pkg_id = self.wiz.lset, it["name"]
        copies = pkg_mod.package_copies(lset, pkg_id, [it["folder"]])
        labelled = (self.states.get(k) or {}).get("labelled")
        lost = (
            f"<b>{labelled} labelled frame(s) in it have not been brought back and will be "
            "lost.</b>"
            if labelled
            else "No labelled frames found in it."
            if labelled == 0
            else "<b>It could not be read, so labelled frames in it may be lost.</b>"
        )
        where = "<br>".join(f"• {c}" for c in copies) or "• (no copies found)"
        ask = dialogs.warning(
            self,
            "Delete package",
            f"Delete package <b>{pkg_id}</b> for good?<br><br>{lost}<br><br>These folders "
            f"will be deleted:<br>{where}<br><br>Its frames can be picked again later. "
            "Copies on sticks that are not plugged in are not touched. This cannot be undone.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if ask != QMessageBox.StandardButton.Yes:
            return
        if labelled != 0:
            typed, ok = _ask_text(
                self, "Delete package", f"Type the package name ({pkg_id}) to delete it:"
            )
            if not ok or typed.strip() != pkg_id:
                self.result.setText("Not deleted.")
                return
        try:
            gone = pkg_mod.delete_package(lset, pkg_id, [it["folder"]])
        except (ConductorError, OSError) as err:
            dialogs.critical(self, "Delete package", str(err))
            return
        self.fill()
        self.result.setText(f"Package {pkg_id} deleted ({len(gone)} folder(s)).")

    def open_selected(self):
        rows = self.table.selectionModel().selectedRows()
        if not rows or not self.items:
            return
        it = self.items[rows[0].row()]
        try:
            sleapenv.open_labelling(self.wiz.env, it["slp"])
        except ConductorError as err:
            dialogs.critical(self, "SLEAP", str(err))
            return
        note = ""
        if it["kind"] == "package" and it["where"] == "this PC":
            note = (
                " This is the copy on this PC; if the package also went out on a stick, "
                "label only one of the two."
            )
        self.result.setText(
            f"SLEAP opened <b>{it['name']}</b>. Save in SLEAP (Ctrl+S) when you stop; "
            f"take the new frames in with 'Bring labelled frames back'.{note}"
        )
        self.opened = True
        self.completeChanged.emit()

    def isComplete(self):  # noqa: N802
        return self.opened

    def nextId(self):  # noqa: N802
        return -1


# ── bring back ────────────────────────────────────────────────────
class BackPage(TaskPage):
    manual_section = "Bring labelled frames back"

    def __init__(self, wiz):
        super().__init__(
            wiz,
            "Bring labelled frames back",
            "Pick the package, check every frame, then merge the good ones into a new "
            "version of the label set.",
        )
        row = QHBoxLayout()
        self.pkgs = QComboBox()
        row.addWidget(self.pkgs, 1)
        b = QPushButton("Look on a folder…")
        b.clicked.connect(self._pick_folder)
        row.addWidget(b)
        self.lay.addLayout(row)
        row2 = QHBoxLayout()
        self.check_btn = QPushButton("Check frames")
        self.check_btn.clicked.connect(self.check)
        row2.addWidget(self.check_btn)
        row2.addWidget(QLabel("Labeller initials"))
        self.labeller = QLineEdit()
        self.labeller.setMaximumWidth(80)
        row2.addWidget(self.labeller)
        self.with_warnings = QCheckBox("Also merge frames with warnings")
        self.with_warnings.setChecked(True)
        row2.addWidget(self.with_warnings)
        self.merge_btn = QPushButton("Merge accepted frames")
        self.merge_btn.clicked.connect(self.merge)
        self.merge_btn.setEnabled(False)
        row2.addWidget(self.merge_btn)
        self.lay.addLayout(row2)
        self.summary = QLabel("")
        self.lay.addWidget(self.summary)
        self.table = _table(["Status", "Recording", "Arena", "Frame", "Flies", "Why"])
        self.lay.addWidget(self.table, 2)
        self.add_log_widgets(1)
        self.report = None
        self.project: Path | None = None
        self.merged = False

    def initializePage(self):  # noqa: N802
        self.fill_packages()
        self.labeller.setText(self.labeller.text() or getpass.getuser()[:3].upper())
        self.merged = False

    def fill_packages(self, extra: list[Path] = ()):
        self.pkgs.clear()
        for it in labelling_items(self.wiz.lset, extra):
            if it.get("missing"):
                continue
            self.pkgs.addItem(f"{it['kind']}: {it['name']} · {it['where']}", it)

    def _pick_folder(self):
        d = QFileDialog.getExistingDirectory(self, "Folder with labelling packages")
        if d:
            self.fill_packages([Path(d)])
            i = self.pkgs.findText(d, Qt.MatchFlag.MatchContains)
            self.pkgs.setCurrentIndex(i if i >= 0 else self.pkgs.count() - 1)

    def check(self):
        it = self.pkgs.currentData()
        if not it:
            return
        lset, env = self.wiz.lset, self.wiz.env
        self.project = it["slp"] if it["kind"] == "project" else None
        out = {}

        def job(emit):
            if self.project:
                out.update(info=None, returned=self.project)
                out["report"] = pkg_mod.check_project(
                    lset, self.project, env, log=emit, progress=progress_to(emit)
                )
                return
            folder = it["folder"]
            for problem in pkg_mod.manifest_problems(Path(folder)):
                emit(f"note: {problem}")
            info, returned = pkg_mod.take_back(lset, Path(folder))
            out.update(info=info, returned=returned)
            out["report"] = pkg_mod.check_returned(
                lset, info, returned, env, log=emit, progress=progress_to(emit)
            )

        def done(ok, _m):
            if ok:
                self.info, self.returned, self.report = out["info"], out["returned"], out["report"]
                self.show_report()

        self.run(job, done)

    def show_report(self):
        rep = self.report
        if rep.get("skeleton"):
            self.summary.setText(f"<b>Not merged:</b> {rep['skeleton']}")
            self.merge_btn.setEnabled(False)
            return
        s = rep["summary"]
        tail = (
            f"{s['already_in_set']} already in the set"
            if "already_in_set" in s
            else f"{s.get('unlabelled', 0)} not labelled"
        )
        self.summary.setText(
            f"{s.get('accepted', 0)} accepted ({s.get('warnings', 0)} with warnings), "
            f"{s.get('rejected', 0)} rejected, {tail}"
        )
        rows = [r for r in rep["frames"] if r["status"] != "unlabelled"]
        self.table.setRowCount(len(rows))
        for i, r in enumerate(rows):
            why = "; ".join(r["reasons"] + [f"warning: {w}" for w in r["warnings"]])
            vals = [
                r["status"],
                r.get("recording", "?"),
                r.get("arena", "?"),
                r.get("source_frame", "?"),
                r["flies"],
                why,
            ]
            for c, v in enumerate(vals):
                self.table.setItem(i, c, _item(v))
        self.merge_btn.setEnabled(s.get("accepted", 0) > 0)

    def accepted(self) -> list[str]:
        return [
            r["hash"]
            for r in self.report["frames"]
            if r["status"] == "accepted" and (self.with_warnings.isChecked() or not r["warnings"])
        ]

    def merge(self):
        who = self.labeller.text().strip()
        if not who:
            dialogs.information(self, "Labeller", "Enter the labeller's initials.")
            return
        lset, env, acc = self.wiz.lset, self.wiz.env, self.accepted()
        out = {}

        project, report = self.project, self.report

        def job(emit):
            if project:
                out["entry"] = pkg_mod.merge_project(lset, project, report, acc, who, env, log=emit)
                return
            out["entry"] = pkg_mod.merge_returned(
                lset,
                self.info,
                self.returned,
                acc,
                who,
                env,
                report=self.report,
                log=emit,
                progress=progress_to(emit),
            )

        def done(ok, _m):
            if ok:
                e = out["entry"]
                self.merged = True
                self.merge_btn.setEnabled(False)
                self.summary.setText(
                    f"Merged: label set <b>v{e['version']}</b> now has {e['frames']} frames "
                    f"({e['instances']} flies). Train it from the start page."
                )
                self.completeChanged.emit()

        self.run(job, done)

    def isComplete(self):  # noqa: N802
        return self.merged

    def nextId(self):  # noqa: N802
        return -1


# ── training data ─────────────────────────────────────────────────
ROLES = (
    ("train", "Training"),
    ("all", "All (split automatically)"),
    ("val", "Validation"),
    ("out", "Leave out"),
)


def flies_text(flies: dict) -> str:
    """``{"3": 28, "2": 3}`` → ``3 flies: 28 · 2 flies: 3`` (most first)."""
    parts = sorted(flies.items(), key=lambda kv: -int(kv[0]))
    return " · ".join(f"{n} {'fly' if n == '1' else 'flies'}: {c}" for n, c in parts)


class ExampleDialog(QDialog):
    def __init__(self, parent, path: str, title: str):
        super().__init__(parent)
        self.setWindowTitle(title)
        lay = QVBoxLayout(self)
        img = QLabel()
        side = dialogs.screen_side(parent)
        img.setPixmap(
            QPixmap(path).scaled(
                side,
                side,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        )
        lay.addWidget(img)


class DataPage(TaskPage):
    manual_section = "Training data"

    COLS = [
        "Example",
        "Use as",
        "Recording",
        "Date",
        "Scale",
        "Frames",
        "Flies",
        "Flies per frame",
        "Image size",
        "Arenas",
        "Labelled by",
    ]

    def __init__(self, wiz):
        super().__init__(
            wiz,
            "Training data",
            "Which version of the label set, and what each recording is for. Validation "
            "recordings are never trained on; they test the new and the current model "
            "(pick ones the current model has not seen). <i>All</i> uses a recording for "
            f"both: {LABEL_TEST_SHARE:.0%} of its frames, picked by their pixels so they stay "
            "the same in later versions, test the models and the rest is trained on. "
            "Double-click an example to enlarge.",
        )
        form = QFormLayout()
        self.version = QComboBox()
        self.version.currentIndexChanged.connect(self.load)
        form.addRow("Label set version", self.version)
        self.lay.addLayout(form)
        self.table = _table(self.COLS)
        self.table.setIconSize(QSize(LABEL_THUMB_PX, LABEL_THUMB_PX))
        self.table.verticalHeader().setDefaultSectionSize(LABEL_THUMB_PX + 6)
        self.table.cellDoubleClicked.connect(self._enlarge)
        self.lay.addWidget(self.table, 4)
        self.totals = QLabel("")
        self.totals.setWordWrap(True)
        self.lay.addWidget(self.totals)
        self.add_log_widgets(1)
        self.rows: list[dict] = []
        self.roles: list[QComboBox] = []

    def initializePage(self):  # noqa: N802
        lset = self.wiz.lset
        self.version.blockSignals(True)
        self.version.clear()
        for v in reversed(lset.versions):
            self.version.addItem(
                f"v{v['version']} · {v['frames']} frames · {v['note']}", v["version"]
            )
        self.version.blockSignals(False)
        self.load()

    def load(self, *_):
        v = self.version.currentData()
        self.rows, self.roles = [], []
        self.table.setRowCount(0)
        self.update_totals()
        if v is None:
            return
        lset, env, out = self.wiz.lset, self.wiz.env, {}

        def job(emit):
            out["rows"] = train_mod.summarise(lset, v, env, log=emit, progress=progress_to(emit))

        def done(ok, _m):
            if ok and self.version.currentData() == v:
                self.show_rows(out["rows"])

        self.run(job, done)

    def role_of(self, rec: str) -> str:
        lset = self.wiz.lset
        if rec in lset.validation_recordings():
            return "val"
        if rec in lset.shared():
            return "all"
        return "out" if rec in lset.left_out() else "train"

    def show_rows(self, rows: list[dict]):
        self.rows = rows
        self.roles = []
        self.table.setRowCount(len(rows))
        for i, r in enumerate(rows):
            thumb = QTableWidgetItem()
            thumb.setFlags(Qt.ItemFlag.ItemIsEnabled)
            thumb.setData(
                Qt.ItemDataRole.DecorationRole,
                QPixmap(r["example"]).scaled(
                    LABEL_THUMB_PX,
                    LABEL_THUMB_PX,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                ),
            )
            arena, frame = r.get("example_from", [-1, -1])
            thumb.setToolTip(f"arena {arena:02d}, frame {frame}" if frame >= 0 else "")
            self.table.setItem(i, 0, thumb)
            combo = QComboBox()
            for key, text in ROLES:
                combo.addItem(text, key)
            combo.setCurrentIndex(combo.findData(self.role_of(r["recording"])))
            combo.currentIndexChanged.connect(self.update_totals)
            self.table.setCellWidget(i, 1, combo)
            self.roles.append(combo)
            date = Recording(r["recording"], Path()).date
            vals = [
                r["recording"],
                date,
                r["scale"],
                r["frames"],
                r["instances"],
                flies_text(r["flies"]),
                ", ".join(r["sizes"]),
                len(r["arenas"]),
                ", ".join(f"{k} ({n})" for k, n in r["labellers"].items()),
            ]
            for c, val in enumerate(vals, start=2):
                self.table.setItem(i, c, _item(val))
        self.update_totals()

    def chosen(self) -> dict[str, str]:
        return {r["recording"]: c.currentData() for r, c in zip(self.rows, self.roles, strict=True)}

    def counts(self) -> dict[str, int]:
        """Frames per use; for *All* recordings the test share is an estimate."""
        roles = self.chosen()
        out = {"train": 0, "val": 0, "out": 0}
        for r in self.rows:
            role, n = roles[r["recording"]], r["frames"]
            if role == "all":
                test = round(n * LABEL_TEST_SHARE)
                out["val"] += test
                out["train"] += n - test
            else:
                out[role] += n
        return out

    def update_totals(self, *_):
        if not self.rows:
            self.totals.setText("")
            self.completeChanged.emit()
            return
        n = self.counts()
        roles = self.chosen()
        val_scales = sorted(
            {r["scale"] for r in self.rows if roles[r["recording"]] in ("val", "all")}
        )
        approx = "≈ " if "all" in roles.values() else ""
        text = (
            f"{approx}<b>{n['train']}</b> frames for training, "
            f"{approx}<b>{n['val']}</b> for validation"
            + (f" ({', '.join(val_scales)})" if val_scales else "")
            + f", {n['out']} left out."
        )
        if not n["val"]:
            text += (
                "<br><b>No validation frames yet:</b> set one recording per camera scale "
                "to Validation (ideally a recording made for it), or set recordings to "
                "<i>All</i> to split their frames automatically."
            )
        if not n["train"]:
            text += "<br><b>Nothing left to train on.</b>"
        self.totals.setText(text)
        self.completeChanged.emit()

    def _enlarge(self, row: int, _col: int):
        if 0 <= row < len(self.rows):
            r = self.rows[row]
            ExampleDialog(self, r["example"], r["recording"]).exec()

    def isComplete(self):  # noqa: N802
        n = self.counts() if self.rows else {}
        return bool(n.get("train")) and bool(n.get("val"))

    def validatePage(self):  # noqa: N802
        roles = self.chosen()
        self.wiz.lset.set_training_roles({r["recording"]: r["scale"] for r in self.rows}, roles)
        self.wiz.train_version = self.version.currentData()
        self.wiz.train_rows = self.rows
        self.wiz.excluded = sorted(k for k, v in roles.items() if v == "out")
        self.wiz.shared = sorted(k for k, v in roles.items() if v == "all")
        return True


# ── train ─────────────────────────────────────────────────────────
class TrainPage(TaskPage):
    manual_section = "Train on Aoraki"

    def __init__(self, wiz):
        super().__init__(
            wiz,
            "Train on Aoraki",
            "Fine-tune from an existing model, then compare with the current model on "
            "every validation recording. A new model becomes the default only if it is "
            "not worse anywhere and better somewhere, and only with your OK.",
        )
        form = QFormLayout()
        self.data_label = QLabel("")
        self.data_label.setWordWrap(True)
        form.addRow("Training data", self.data_label)
        self.base = QComboBox()
        form.addRow("Start from model", self.base)
        self.compare = QComboBox()
        form.addRow("Compare with", self.compare)
        self.epochs = QSpinBox()
        self.epochs.setRange(1, 2000)
        self.epochs.setValue(TRAIN_EPOCHS)
        form.addRow("Epochs (at most)", self.epochs)
        self.val_label = QLabel("")
        self.val_label.setWordWrap(True)
        form.addRow("Validation", self.val_label)
        self.lay.addLayout(form)
        row = QHBoxLayout()
        self.start_btn = QPushButton("Start training")
        self.start_btn.clicked.connect(self.start)
        row.addWidget(self.start_btn)
        self.state = QLabel("")
        row.addWidget(self.state, 1)
        self.lay.addLayout(row)
        self.training = TrainingProgress()
        self.lay.addWidget(self.training)
        self.table = _table(["Validation set", "Frames", "New mAP", "Current mAP"])
        self.table.setMaximumHeight(TRAIN_TABLE_MAX_PX)  # a few validation sets
        self.lay.addWidget(self.table)
        prow = QHBoxLayout()
        self.promote_btn = QPushButton("Make it the default model")
        self.promote_btn.clicked.connect(lambda: self.register(True))
        self.keep_btn = QPushButton("Register without making it the default")
        self.keep_btn.clicked.connect(lambda: self.register(False))
        for b in (self.promote_btn, self.keep_btn):
            b.setEnabled(False)
            prow.addWidget(b)
        self.lay.addLayout(prow)
        self.add_log_widgets(1)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.poll)
        self.plan: train_mod.TrainPlan | None = None
        self.verdict: dict | None = None

    def initializePage(self):  # noqa: N802
        lset = self.wiz.lset
        out, shared = self.wiz.excluded, self.wiz.shared
        self.data_label.setText(
            f"v{self.wiz.train_version}"
            + (f"; left out: {', '.join(out)}" if out else "; all recordings")
            + (f"; split by frame: {', '.join(shared)}" if shared else "")
        )
        models = self.wiz.registry_models()
        for combo in (self.base, self.compare):
            combo.clear()
            for m in models:
                combo.addItem(m.label, m.id)
        want = self.wiz.default_model()
        for combo in (self.base, self.compare):
            i = combo.findData(want) if want else -1
            if i >= 0:
                combo.setCurrentIndex(i)
        val = lset.data["validation"]
        parts = [f"{s}: {', '.join(r)}" for s, r in val.items()]
        if lset.shared():
            parts.append(f"{LABEL_TEST_SHARE:.0%} of the frames of {', '.join(lset.shared())}")
        self.val_label.setText(
            "; ".join(parts) or "<b>none yet</b>: go back and set a recording to Validation or All"
        )
        self.start_btn.setEnabled(lset.has_validation() and bool(models))
        if not models:
            self.state.setText("No models in the registry cache; open the main wizard once.")
        self.resume()

    def resume(self):
        """Pick up a training run that is still going (wizard reopened)."""
        for m in reversed(self.wiz.lset.data["models"]):
            if m.get("status") in ("submitted", "trained"):
                self.plan = train_mod.TrainPlan.from_entry(m)
                self.training.show_status({}, m.get("epochs", TRAIN_EPOCHS))
                self.state.setText(f"run {self.plan.run}, job {self.plan.job_id}: checking…")
                self.start_btn.setEnabled(False)
                self.timer.start(TRAIN_POLL_S * 1000)
                QTimer.singleShot(0, self.poll)
                return

    def start(self):
        lset, env, cfg = self.wiz.lset, self.wiz.env, self.wiz.cfg
        version, excluded = self.wiz.train_version, list(self.wiz.excluded)
        shared = list(self.wiz.shared)
        plan = train_mod.TrainPlan(
            lset.name,
            version,
            train_mod.run_name(lset, version),
            self.base.currentData(),
            self.compare.currentData(),
            excluded=excluded,
            shared=shared,
        )
        epochs = self.epochs.value()
        scripts = self.wiz.thermokourt_scripts()
        self.start_btn.setEnabled(False)

        def job(emit):
            emit("splitting into training and validation frames")
            files = train_mod.prepare(
                lset, version, env, exclude=excluded, log=emit, progress=progress_to(emit)
            )
            emit(f"uploading {', '.join(p.name for p in files.values())} to Aoraki")
            train_mod.upload(cfg, LABEL_PROFILE, lset, plan, files, scripts)
            train_mod.submit(cfg, LABEL_PROFILE, plan, epochs)
            lset.add_model({**plan.__dict__, "status": "submitted", "epochs": epochs})
            emit(f"training job {plan.job_id} submitted (run {plan.run})")

        def done(ok, _m):
            if ok:
                self.plan = plan
                self.training.show_status({}, epochs)
                self.timer.start(TRAIN_POLL_S * 1000)
                self.poll()
            else:
                self.start_btn.setEnabled(True)

        self.run(job, done)

    def poll(self):
        if self.plan is None or (self.wiz.worker and self.wiz.worker.isRunning()):
            return
        plan, cfg = self.plan, self.wiz.cfg
        out = {}

        def job(emit):
            out["st"] = train_mod.status(cfg, LABEL_PROFILE, plan)
            if out["st"]["done"]:
                out["rows"] = train_mod.fetch_eval(cfg, LABEL_PROFILE, plan.run)

        def done(ok, _m):
            if not ok:
                return
            st = out["st"]
            self.state.setText(f"job {plan.job_id}: {(st['state'] or 'queued').lower()}")
            self.training.show_status(st)
            if st["failed"]:
                self.timer.stop()
                self.wiz.lset.update_model(plan.run, status="failed", error=st["failed"])
                self.log.appendPlainText("\n".join(st["tail"]))
                self.state.setText(f"training failed: {st['failed']}")
                self.start_btn.setEnabled(True)
            elif st["done"]:
                self.timer.stop()
                self.show_verdict(out["rows"])

        self.wiz.run_in_worker(job, done, self.add_line)

    def show_verdict(self, rows: list[dict]):
        plan = self.plan
        self.verdict = train_mod.compare(rows, plan.run, plan.compare_run)
        self.wiz.lset.update_model(plan.run, status="trained", eval=rows)
        t = self.verdict["table"]
        self.table.setRowCount(len(t))
        for i, r in enumerate(t):
            vals = [r["set"], r["frames"], _fmt(r["new"]), _fmt(r["old"])]
            for c, v in enumerate(vals):
                self.table.setItem(i, c, _item(v))
        ok = self.verdict["promotable"]
        self.state.setText(
            f"trained: {plan.run} — "
            + (
                "better than the current model, can become the default"
                if ok
                else "not better everywhere; keep the current default"
            )
        )
        self.promote_btn.setEnabled(ok)
        self.keep_btn.setEnabled(True)

    def register(self, promote: bool):
        if (
            promote
            and dialogs.question(
                self,
                "Make default",
                f"Make {self.plan.run} the default pose model for new recordings?",
            )
            != QMessageBox.StandardButton.Yes
        ):
            return
        base = next((m for m in self.wiz.registry_models() if m.id == self.plan.base_run), None)
        entry = train_mod.registry_entry(
            self.plan.run,
            base.__dict__ if base else {},
            self.wiz.lset,
            self.plan.version,
            self.verdict,
            promote,
            excluded=self.plan.excluded,
            shared=self.plan.shared,
        )
        cfg, plan = self.wiz.cfg, self.plan

        def job(emit):
            train_mod.append_registry(cfg, LABEL_PROFILE, entry)
            models_mod.refresh(
                cfg.cluster.ssh_host,
                cfg.registry_remote(LABEL_PROFILE),
                cfg.registry_cache(LABEL_PROFILE),
            )
            emit(f"registered {plan.run}" + (" as the default model" if promote else ""))

        def done(ok, _m):
            if ok:
                self.wiz.lset.update_model(plan.run, status="registered", default=promote)
                self.promote_btn.setEnabled(False)
                self.keep_btn.setEnabled(False)

        self.run(job, done)

    def nextId(self):  # noqa: N802
        return -1


def _fmt(x) -> str:
    return "" if x is None else f"{x:.3f}"


# ── the wizard ────────────────────────────────────────────────────
class LabellingWizard(QWizard):
    def __init__(self, cfg: ConductorConfig):
        super().__init__()
        self.cfg = cfg
        self.setWindowTitle("Rig Conductor — training data")
        self.setWizardStyle(QWizard.WizardStyle.ModernStyle)
        self.resize(1000, 760)
        self.worker: Worker | None = None
        self.lset: LabelSet | None = None
        self.env: sleapenv.SleapEnv | None = None
        self.recordings: list[Recording] = []
        self.frames: list[dict] = []
        self.prefill_run: str | None = None
        self.train_version: int | None = None
        self.train_rows: list[dict] = []
        self.excluded: list[str] = []
        self.shared: list[str] = []
        for pid, cls in (
            (PAGE_START, StartPage),
            (PAGE_RECS, RecordingsPage),
            (PAGE_FRAMES, FramesPage),
            (PAGE_PACKAGE, PackagePage),
            (PAGE_CONTINUE, ContinuePage),
            (PAGE_BACK, BackPage),
            (PAGE_DATA, DataPage),
            (PAGE_TRAIN, TrainPage),
        ):
            self.setPage(pid, cls(self))
        add_help_button(self)

    def run_in_worker(self, fn, on_done, sink):
        if self.worker and self.worker.isRunning():
            dialogs.information(self, "Busy", "Something is still running")
            return
        self.worker = Worker(fn)
        self.worker.line.connect(sink)
        self.worker.finished_ok.connect(on_done)
        self.worker.start()

    def registry_models(self) -> list[models_mod.Model]:
        ms = models_mod.load_cached(self.cfg.registry_cache(LABEL_PROFILE))
        return models_mod.models_for(ms, "drosophila", include_deprecated=False)

    def default_model(self) -> str | None:
        from rigconductor.presets import by_key

        ms = self.registry_models()
        return models_mod.default_for(ms, "drosophila") or by_key(LABEL_PROFILE, "heat").model

    @staticmethod
    def thermokourt_scripts() -> Path:
        try:
            import thermokourt
        except ImportError as e:
            raise ConductorError(
                "ThermoKourt is not installed; it carries the training scripts"
            ) from e
        return Path(thermokourt.__file__).resolve().parents[1] / "scripts"


def main(config_path: str | None = None) -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    try:
        cfg = ConductorConfig.load(config_path)
    except FileNotFoundError as e:
        dialogs.critical(None, "Rig Conductor", str(e))
        return 1
    w = LabellingWizard(cfg)
    w.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
