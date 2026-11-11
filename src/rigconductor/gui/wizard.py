# ╔══════════════════════════════════════════════════════════════════╗
# ║  Rig Conductor — gui.wizard                                      ║
# ║  « Setup → Animals → Record → Cut → Analyse → Deliver »          ║
# ╠══════════════════════════════════════════════════════════════════╣
# ║  The operator wizard; organism specifics stay in profiles.       ║
# ╚══════════════════════════════════════════════════════════════════╝
"""The operator wizard: Setup → Animals → Record → Cut → Analyse → Deliver.

Every button goes through :class:`rigconductor.session.ConductorSession`, so a
session started here can be resumed from the command line and vice versa.
"""

from __future__ import annotations

import sys
import traceback
from pathlib import Path

from PyQt6.QtCore import Qt, QThread, QTimer, pyqtSignal
from PyQt6.QtGui import QImage, QPixmap
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
    QWizard,
    QWizardPage,
)

from rigconductor import ConductorError
from rigconductor import doctor as doctor_mod
from rigconductor import models as models_mod
from rigconductor.config import ConductorConfig
from rigconductor.constants import CLIP_MODES, DEFAULT_CLIP_MODE, REDUCE_PADDING_MAX_PX
from rigconductor.export import Disk, removable_disks
from rigconductor.gui import dialogs
from rigconductor.gui.duration import DurationEdit, format_duration
from rigconductor.gui.forms import make_form
from rigconductor.gui.preview import PreviewPanel
from rigconductor.gui.progress import ProgressPanel, parse_progress
from rigconductor.profiles import PROFILE_NAMES, make_profile
from rigconductor.session import STAGES, ConductorSession, ConductorSpec, new_session_id


class Worker(QThread):
    line = pyqtSignal(str)
    finished_ok = pyqtSignal(bool, str)

    def __init__(self, fn):
        super().__init__()
        self.fn = fn

    def run(self):
        try:
            self.fn(self.line.emit)
            self.finished_ok.emit(True, "")
        except ConductorError as e:
            self.line.emit(f"ERROR: {e}")
            self.finished_ok.emit(False, str(e))
        except Exception as e:  # noqa: BLE001
            self.line.emit(traceback.format_exc())
            self.finished_ok.emit(False, f"{type(e).__name__}: {e}")


class Wizard(QWizard):
    def __init__(self, cfg: ConductorConfig):
        super().__init__()
        self.cfg = cfg
        self.setWindowTitle("Rig Conductor")
        self.setWizardStyle(QWizard.WizardStyle.ModernStyle)
        self.setOption(QWizard.WizardOption.NoBackButtonOnLastPage, True)
        self.setOption(QWizard.WizardOption.HaveHelpButton, False)
        self.resize(1000, 760)
        self.session: ConductorSession | None = None
        self.worker: Worker | None = None
        self.recorder = None
        self.reduce_padding = cfg.reduce.padding_px  # camera px, shared by all previews
        self.previews: list[PreviewPanel] = []
        self.pages: dict[str, QWizardPage] = {}
        for key, cls in (
            ("setup", SetupPage),
            ("animals", AnimalsPage),
            ("record", RecordPage),
            ("cut", CutPage),
            ("analyse", AnalysePage),
            ("deliver", DeliverPage),
        ):
            self.pages[key] = cls(self)
            self.addPage(self.pages[key])
        self._add_update_buttons()

    def open_training_wizard(self):
        from rigconductor.gui.labelling_wizard import LabellingWizard

        self.training = LabellingWizard(self.cfg)
        self.training.show()

    # ── updates (bottom left of every page) ─────────────────────────────
    def _add_update_buttons(self):
        self.setButtonText(QWizard.WizardButton.CustomButton1, "Check for updates")
        self.setButtonText(QWizard.WizardButton.CustomButton2, "Update")
        self.setOption(QWizard.WizardOption.HaveCustomButton1, True)
        self.setOption(QWizard.WizardOption.HaveCustomButton2, True)
        self.setButtonText(QWizard.WizardButton.CustomButton3, "Training data…")
        self.setOption(QWizard.WizardOption.HaveCustomButton3, True)
        self.setButtonLayout(
            [
                QWizard.WizardButton.CustomButton1,
                QWizard.WizardButton.CustomButton2,
                QWizard.WizardButton.CustomButton3,
                QWizard.WizardButton.Stretch,
                QWizard.WizardButton.BackButton,
                QWizard.WizardButton.NextButton,
                QWizard.WizardButton.FinishButton,
                QWizard.WizardButton.CancelButton,
            ]
        )
        self.button(QWizard.WizardButton.CustomButton2).setToolTip(
            "Fast-forward to the newest version (git pull --ff-only). Refused when files "
            "were changed here or local commits exist."
        )
        self.customButtonClicked.connect(self._update_button)

    def _update_button(self, which: int):
        from rigconductor import updates

        if which == QWizard.WizardButton.CustomButton3.value:
            self.open_training_wizard()
            return

        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            states = updates.check_all()
        finally:
            QApplication.restoreOverrideCursor()
        lines = [s.summary() for s in states]
        for s in states:
            lines += [f"    {c}" for c in s.new_commits[:8]]
        if which == QWizard.WizardButton.CustomButton1.value:
            dialogs.information(self, "Check for updates", "\n".join(lines))
            return
        ready = [s for s in states if s.can_update]
        if not ready:
            dialogs.information(self, "Update", "Nothing to update.\n\n" + "\n".join(lines))
            return
        if self.worker and self.worker.isRunning():
            dialogs.information(self, "Update", "Wait until the running step has finished.")
            return
        names = ", ".join(s.name for s in ready)
        if (
            dialogs.question(self, "Update", f"Update {names}?\n\n" + "\n".join(lines))
            != QMessageBox.StandardButton.Yes
        ):
            return
        done = []
        for s in ready:
            try:
                done.append(updates.update_repo(s))
            except RuntimeError as e:
                done.append(f"{s.name}: update failed: {e}")
        dialogs.information(
            self, "Update", "\n".join(done) + "\n\nClose and start the Conductor again to use it."
        )

    # shared helpers
    def profile_name(self) -> str:
        setup = self.pages.get("setup")
        return setup.profile.currentText() if setup else PROFILE_NAMES[0]

    def make_recorder(self):
        from rigconductor.motif import MotifClient

        m = self.cfg.motif
        return MotifClient(m.host, m.port, m.serial, api_key_cmd=m.api_key_cmd)

    def preview_url(self) -> str | None:
        from rigconductor.preview import preview_url

        return preview_url(self.make_recorder().camera().raw)

    def arenas_json(self):
        if self.profile_name() != "drosophila_triad":
            return None
        try:
            return make_profile("drosophila_triad").tk_config(self.cfg).arenas_json
        except Exception:  # noqa: BLE001
            return None

    def arena_genotypes(self) -> dict[int, list[str]]:
        """Genotypes per arena from the vial board, if the animals page has one."""
        page = self.pages.get("animals")
        board = getattr(getattr(page, "form", None), "board", None)
        return board.genotypes_by_arena() if board is not None else {}

    def make_preview(self) -> PreviewPanel:
        n = make_profile(self.profile_name()).n_units
        p = PreviewPanel(
            self.preview_url,
            self.arenas_json(),
            n,
            self.arena_genotypes,
            padding=self.reduce_padding,
            on_padding=self.set_reduce_padding,
        )
        self.previews.append(p)
        return p

    def set_reduce_padding(self, v: int):
        """One padding for every preview and for cropping the recording."""
        self.reduce_padding = int(v)
        for p in self.previews:
            if p.padding.value() != v:
                p.set_padding(v)
        cut = self.pages.get("cut")
        if cut is not None and cut.padding.value() != v:
            cut.padding.blockSignals(True)
            cut.padding.setValue(v)
            cut.padding.blockSignals(False)

    def run_in_worker(self, fn, on_done, log_widget):
        if self.worker and self.worker.isRunning():
            dialogs.information(self, "Busy", "Something is still running")
            return
        self.worker = Worker(fn)
        sink = log_widget if callable(log_widget) else log_widget.appendPlainText
        self.worker.line.connect(sink)
        self.worker.finished_ok.connect(on_done)
        self.worker.start()

    def _apply_setup_choices(self, spec: ConductorSpec):
        setup = self.pages["setup"]
        spec.preset = setup.preset.currentData()
        if not (self.session and self.session.is_done("reduce")):
            p = setup.current_preset()
            spec.gray = p.gray if setup.gray_override is None else setup.gray_override
        if not (self.session and self.session.is_done("submit")) and setup.model.currentData():
            spec.model_id = setup.model.currentData()

    def ensure_session(self) -> ConductorSession:
        if self.session is None:
            setup = self.pages["setup"]
            spec = ConductorSpec(
                profile=self.profile_name(),
                session_id=new_session_id(self.profile_name()),
                title=setup.rec_title.text().strip() or self.profile_name(),
                duration_s=setup.duration.value(),
                codec=setup.codec.text().strip() or None,
                operator=setup.operator.text().strip() or None,
                metadata=self.pages["animals"].form.values(),
                blind=self.cfg.export.blind,
                clips=self.cfg.export.clips,
                reduce=self.pages["cut"].crop.isChecked() if "cut" in self.pages else None,
                reduce_padding_px=self.reduce_padding,
            )
            self._apply_setup_choices(spec)
            self.recorder = self.make_recorder() if not setup.existing.text().strip() else None
            if setup.existing.text().strip():
                spec.recording_dir = setup.existing.text().strip()
            self.session = ConductorSession(self.cfg, spec, self.recorder)
        else:
            setup = self.pages["setup"]
            spec = self.session.spec
            if not self.session.is_done("reduce") and "cut" in self.pages:
                spec.reduce = self.pages["cut"].crop.isChecked()
                spec.reduce_padding_px = self.reduce_padding
            spec.metadata = self.pages["animals"].form.values()
            spec.operator = setup.operator.text().strip() or None
            self._apply_setup_choices(spec)
            if not self.session.is_done("record"):
                # the user may have gone back and changed the setup page
                spec.title = setup.rec_title.text().strip() or self.profile_name()
                spec.duration_s = setup.duration.value()
                spec.codec = setup.codec.text().strip() or None
                existing = setup.existing.text().strip()
                if existing:
                    spec.recording_dir = existing
                    self.session.recorder = self.recorder = None
                elif self.session.recorder is None:
                    self.session.recorder = self.recorder = self.make_recorder()
        return self.session


class SetupPage(QWizardPage):
    def __init__(self, wiz: Wizard):
        super().__init__()
        self.wiz = wiz
        self.setTitle("1 · Setup")
        self.setSubTitle("Choose what you are recording and check the rig is ready.")
        outer = QHBoxLayout(self)
        left = QWidget()
        lay = QVBoxLayout(left)
        lay.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(left, 1)
        self.preview = None
        self._outer = outer
        form = QFormLayout()
        self.profile = QComboBox()
        for n in PROFILE_NAMES:
            self.profile.addItem(n)
        self.profile.currentTextChanged.connect(self._profile_changed)
        self.preset = QComboBox()
        self.preset.setToolTip("Fixes colour, frame rate and cropping for this kind of experiment")
        self.preset.currentIndexChanged.connect(self._preset_changed)
        self.model = QComboBox()
        self.model.setToolTip("Pose model used on Aoraki; checked against the camera right away")
        self.model.currentIndexChanged.connect(lambda _: self.check_camera(interactive=False))
        refresh = QPushButton("Refresh")
        refresh.clicked.connect(self._refresh_models)
        model_row = QHBoxLayout()
        model_row.addWidget(self.model, 1)
        model_row.addWidget(refresh)
        self.models: list = []
        self.gray_override: bool | None = None  # set by 'Convert to grayscale'
        self.rec_title = QLineEdit()
        self.rec_title.setPlaceholderText("recording title → folder prefix, e.g. Droso_28deg")
        self.duration = DurationEdit()
        self.codec = QLineEdit(wiz.cfg.motif.codec)
        self.operator = QLineEdit()
        self.operator.setPlaceholderText("initials")
        self.existing = QLineEdit()
        self.existing.setPlaceholderText("leave empty to record now")
        self.existing.editingFinished.connect(self._typed_existing)
        pick = QPushButton("Choose…")
        pick.clicked.connect(self._pick_existing)
        row = QHBoxLayout()
        row.addWidget(self.existing, 1)
        row.addWidget(pick)
        form.addRow("Profile", self.profile)
        form.addRow("Preset", self.preset)
        form.addRow("Pose model", model_row)
        form.addRow("Recording title", self.rec_title)
        form.addRow("Duration", self.duration)
        form.addRow("Motif codec", self.codec)
        form.addRow("Operator", self.operator)
        form.addRow("Existing recording", row)
        lay.addLayout(form)
        row2 = QHBoxLayout()
        btn = QPushButton("Check rig readiness")
        btn.clicked.connect(self.check)
        row2.addWidget(btn)
        ddb = QPushButton("Import DrosoDB from USB stick")
        ddb.clicked.connect(self._import_ddb)
        row2.addWidget(ddb)
        lay.addLayout(row2)
        self.report = QPlainTextEdit()
        self.report.setReadOnly(True)
        lay.addWidget(self.report, 1)
        self._profile_changed(self.profile.currentText())
        self.preview = wiz.make_preview()
        outer.addWidget(self.preview, 1)

    def cleanupPage(self):  # noqa: N802
        if self.preview:
            self.preview.stop()

    def validatePage(self) -> bool:  # noqa: N802
        if not self.check_camera(interactive=True):
            return False
        if self.preview:
            self.preview.stop()  # one stream at a time
        return True

    # ── preset + model, checked against the camera before recording ─────
    def current_preset(self):
        from rigconductor.presets import by_key

        return by_key(self.profile.currentText(), self.preset.currentData(), self.wiz.cfg)

    def current_model(self):
        mid = self.model.currentData()
        return next((m for m in self.models if m.id == mid), None)

    def _fill_presets(self, name):
        from rigconductor.presets import presets_for

        self.preset.blockSignals(True)
        self.preset.clear()
        for p in presets_for(name, self.wiz.cfg):
            self.preset.addItem(p.label, p.key)
        self.preset.blockSignals(False)

    def _fill_models(self, models=None):
        name = self.profile.currentText()
        cfg = self.wiz.cfg
        if models is None:
            models = models_mod.load_cached(cfg.registry_cache(name))
        self.models = models_mod.models_for(
            models, make_profile(name).organism, include_deprecated=True
        )
        self.model.blockSignals(True)
        self.model.clear()
        for m in self.models:
            self.model.addItem(m.label, m.id)
        if not self.models:
            self.model.addItem("(no models: press Refresh)", None)
        want = self.preferred_model()
        i = self.model.findData(want) if want else -1
        if i >= 0:
            self.model.setCurrentIndex(i)
        self.model.blockSignals(False)

    def preferred_model(self) -> str | None:
        """A model promoted in the registry beats the preset's built-in choice."""
        organism = make_profile(self.profile.currentText()).organism
        return models_mod.default_for(self.models, organism) or self.current_preset().model

    def _refresh_models(self):
        cfg = self.wiz.cfg
        name = self.profile.currentText()
        try:
            ms = models_mod.refresh(
                cfg.cluster.ssh_host, cfg.registry_remote(name), cfg.registry_cache(name)
            )
        except ConductorError as e:
            dialogs.warning(self, "Registry", str(e))
            return
        self._fill_models(ms)

    def _preset_changed(self, *_):
        p = self.current_preset()
        self.gray_override = None
        self.wiz.set_reduce_padding(p.padding_px)
        cut = self.wiz.pages.get("cut")
        if cut is not None:
            cut.crop.setChecked(p.reduce)
        want = self.preferred_model()
        if want and self.models:
            i = self.model.findData(want)
            if i >= 0:
                self.model.setCurrentIndex(i)

    def _ask(self, kind: str, check, preset) -> str:
        """Ask what to do about a failed camera check; returns an action key.
        Kept separate so tests can answer without a dialog."""
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle("Model vs camera")
        box.setText(check.detail)
        box.setInformativeText(check.hint)
        if kind == "channels":
            a = box.addButton("Convert to grayscale", QMessageBox.ButtonRole.AcceptRole)
            b = box.addButton("Pick another model", QMessageBox.ButtonRole.RejectRole)
            actions = {a: "gray", b: "model"}
        elif kind == "fps":
            a = box.addButton(
                f"Set camera to {preset.fps:g} fps", QMessageBox.ButtonRole.AcceptRole
            )
            b = box.addButton("Keep the camera as it is", QMessageBox.ButtonRole.DestructiveRole)
            c = box.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)
            actions = {a: "set_fps", b: "keep", c: "cancel"}
        else:
            a = box.addButton("Use anyway", QMessageBox.ButtonRole.DestructiveRole)
            b = box.addButton("Pick another model", QMessageBox.ButtonRole.RejectRole)
            actions = {a: "keep", b: "model"}
        dialogs.fit(box, self).exec()
        return actions.get(box.clickedButton(), "cancel")

    def check_camera(self, interactive: bool = True) -> bool:
        """Model + preset vs the live camera. Non-interactive: only report."""
        from dataclasses import replace

        from rigconductor import compat

        model = self.current_model()
        if model is None or self.existing.text().strip():
            return True  # existing recordings are checked against their clips later
        try:
            client = self.wiz.make_recorder()
            cam = compat.camera_info(client)
        except Exception as e:  # noqa: BLE001
            self.report.appendPlainText(f"camera check skipped: {e}")
            return True
        preset = self.current_preset()
        if self.gray_override is not None:
            preset = replace(preset, gray=self.gray_override)
        checks = compat.check_model_vs_camera(model, cam, preset)
        fps = f"{cam.fps:g} fps" if cam.fps else "fps unknown"
        self.report.appendPlainText(
            f"── {model.id} · preset '{preset.key}' · camera {cam.model_name or '?'} "
            f"({cam.pixel_format or '?'}, {fps})"
        )
        for c in checks:
            self.report.appendPlainText(c.line)
        if not interactive:
            return all(c.ok or not c.blocking for c in checks)
        for c in checks:
            if c.ok:
                continue
            kind = (
                "channels"
                if c.name.endswith(compat.CHANNELS)
                else "fps"
                if c.name.endswith(compat.CAMERA_FPS)
                else "other"
            )
            if kind == "other" and not c.blocking:
                continue
            act = self._ask(kind, c, preset)
            if act == "gray":
                self.gray_override = True
                self.report.appendPlainText("→ videos will be converted to grayscale")
            elif act == "set_fps":
                try:
                    client.configure(AcquisitionFrameRate=float(preset.fps))
                    now = client.read("AcquisitionFrameRate")
                    self.report.appendPlainText(f"→ camera set to {now} fps")
                except Exception as e:  # noqa: BLE001
                    dialogs.warning(self, "Camera", f"could not set the frame rate: {e}")
                    return False
            elif act == "keep":
                self.report.appendPlainText(f"→ kept despite: {c.detail}")
            else:  # 'model' or 'cancel'
                if act == "model":
                    self.model.setFocus()
                return False
        return True

    def _profile_changed(self, name):
        prof = make_profile(name)
        self.duration.setValue(prof.default_duration_s(self.wiz.cfg))
        self.wiz.session = None
        self._fill_presets(name)
        self._fill_models()
        self._preset_changed()
        if "animals" in self.wiz.pages:
            self.wiz.pages["animals"].rebuild(name)

    def _pick_existing(self):
        d = QFileDialog.getExistingDirectory(self, "Motif recording", str(self.wiz.cfg.data_root))
        if d:
            self.load_recording(Path(d))

    def _typed_existing(self):
        d = Path(self.existing.text().strip())
        if self.existing.text().strip() and d.is_dir():
            loaded = self.wiz.session and self.wiz.session.spec.recording_dir == str(d)
            if not loaded:
                self.load_recording(d)

    def load_recording(self, rec: Path) -> bool:
        """Choose an existing recording; if the Conductor made it, resume its
        session (animals, vials, model, stages already done). Returns True then."""
        from rigconductor.session import adopt_saved_state, saved_state_for

        state = saved_state_for(self.wiz.cfg, rec)
        if state is None:
            self.existing.setText(str(rec))
            self.report.appendPlainText(
                f"{rec.name}: not recorded with the Conductor, so there is no saved animal "
                "list. Fill in the Animals page."
            )
            return False
        spec = state["spec"]
        if spec.get("profile") in PROFILE_NAMES:
            self.profile.setCurrentText(spec["profile"])  # rebuilds the Animals page
        self.existing.setText(str(rec))
        self.rec_title.setText(spec.get("title") or "")
        self.duration.setValue(int(spec.get("duration_s") or 0))
        self.operator.setText(spec.get("operator") or "")
        if spec.get("codec"):
            self.codec.setText(spec["codec"])
        for combo, key in ((self.preset, "preset"), (self.model, "model_id")):
            i = combo.findData(spec.get(key)) if spec.get(key) else -1
            if i >= 0:
                combo.setCurrentIndex(i)
        self.gray_override = spec.get("gray")
        self.wiz.pages["animals"].form.set_values(spec.get("metadata") or {})
        session = adopt_saved_state(self.wiz.cfg, state)
        session.spec.recording_dir = str(rec)
        self.wiz.session = session
        self.wiz.recorder = None
        done = [s for s in STAGES if session.is_done(s)]
        n = len((spec.get("metadata") or {}).get("arenas") or {})
        self.report.appendPlainText(
            f"Resumed session {spec['session_id']}: {n} arenas with their vials, "
            f"model {spec.get('model_id') or '-'}; done: {', '.join(done) or 'nothing yet'}"
        )
        return True

    def _import_ddb(self):
        from rigconductor import ddbsync

        target = make_profile("drosophila_triad").ddb_snapshot(self.wiz.cfg)
        found = []
        try:
            for d in removable_disks():
                if d.mountpoint:
                    info = ddbsync.find_on(Path(d.mountpoint))
                    if info:
                        found.append(info)
        except ConductorError as e:
            dialogs.warning(self, "DrosoDB", str(e))
            return
        if not found or not target:
            dialogs.information(
                self,
                "DrosoDB",
                "No DrosoDB snapshot on any USB stick.\n"
                "On the tablet run 'DrosoDB → USB stick', then plug the stick in here.",
            )
            return
        info = max(found, key=lambda i: i.created)
        try:
            n = ddbsync.import_snapshot(info, Path(target), log=self.report.appendPlainText)
        except ConductorError as e:
            dialogs.warning(self, "DrosoDB", str(e))
            return
        dialogs.information(self, "DrosoDB", f"Imported {n} active vials ({info.created}).")
        if "animals" in self.wiz.pages:
            self.wiz.pages["animals"].rebuild(self.profile.currentText())

    def check(self):
        prof = make_profile(self.profile.currentText())
        self.report.setPlainText("checking…")
        QApplication.processEvents()
        checks = doctor_mod.run_all(
            self.wiz.cfg, prof.name, self.wiz.make_recorder, prof.ddb_snapshot(self.wiz.cfg)
        )
        self.report.setPlainText("\n".join(c.line for c in checks))
        self._ok = all(c.ok for c in checks)
        self.completeChanged.emit()

    def isComplete(self) -> bool:  # noqa: N802
        return True  # the operator may proceed with warnings; record() re-checks the camera


class AnimalsPage(QWizardPage):
    def __init__(self, wiz: Wizard):
        super().__init__()
        self.wiz = wiz
        self.setTitle("2 · Animals")
        self.setSubTitle("Who is in which arena or well. Codes are checked against DrosoDB.")
        self.lay = QVBoxLayout(self)
        self.form = None
        self.rebuild(wiz.profile_name())

    def rebuild(self, name):
        if self.form is not None:
            self.lay.removeWidget(self.form)
            self.form.deleteLater()
        self.form = make_form(name, self.wiz.cfg)
        self.lay.addWidget(self.form, 1)

    def validatePage(self) -> bool:  # noqa: N802
        prof = make_profile(self.wiz.profile_name())
        errs = prof.validate_metadata(self.form.values(), self.wiz.cfg)
        if errs:
            dialogs.warning(self, "Incomplete", "\n".join(errs))
            return False
        self.wiz.ensure_session()
        return True


class StagePage(QWizardPage):
    """A page that runs one or more stages in the worker and shows the log."""

    stages: tuple[str, ...] = ()
    button_text = "Run"

    def __init__(self, wiz: Wizard, title: str, subtitle: str):
        super().__init__()
        self.wiz = wiz
        self.setTitle(title)
        self.setSubTitle(subtitle)
        self.lay = QVBoxLayout(self)
        self.top = QHBoxLayout()
        self.lay.addLayout(self.top)
        self.btn = QPushButton(self.button_text)
        self.btn.clicked.connect(self.start)
        self.top.addWidget(self.btn)
        self.status = QLabel("")
        self.top.addWidget(self.status, 1)
        self.progress = ProgressPanel()
        self.lay.addWidget(self.progress)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.lay.addWidget(self.log, 1)
        self.details = QCheckBox("Show technical details (commands sent to the cluster)")
        self.details.toggled.connect(self._rerender_log)
        self.lay.addWidget(self.details)
        self.lines: list[str] = []
        self.done_ok = False

    def add_line(self, line: str):
        p = parse_progress(line)
        if p is not None:
            self.progress.show_line(p)
            return
        self.lines.append(line)
        if self._visible(line):
            self.log.appendPlainText(line)

    def _visible(self, line: str) -> bool:
        return self.details.isChecked() or not line.startswith("$ ")

    def _rerender_log(self, *_):
        self.log.setPlainText("\n".join(x for x in self.lines if self._visible(x)))

    def initializePage(self):  # noqa: N802
        s = self.wiz.ensure_session()
        self.done_ok = all(s.is_done(st) for st in self.stages)
        self.status.setText("already done" if self.done_ok else "")
        self.completeChanged.emit()

    def run_stages(self, log):
        s = self.wiz.ensure_session()
        s.log = log
        s.run(self.stages)

    def start(self):
        self.btn.setEnabled(False)
        self.wiz.run_in_worker(self.run_stages, self.finished, self.add_line)

    def finished(self, ok: bool, msg: str):
        self.progress.finish(ok)
        self.btn.setEnabled(True)
        self.done_ok = ok
        self.status.setText("done" if ok else "failed — fix and press again")
        if not ok:
            dialogs.critical(self, "Failed", msg)
        self.completeChanged.emit()

    def isComplete(self) -> bool:  # noqa: N802
        return self.done_ok


class RecordPage(StagePage):
    stages = ("record",)
    button_text = "Start recording"

    def __init__(self, wiz):
        super().__init__(wiz, "3 · Record", "Press Start. Motif records for the chosen duration.")
        self.abort = QPushButton("Abort")
        self.abort.clicked.connect(self._abort)
        self.top.insertWidget(1, self.abort)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)
        self.t = 0
        self.duration = DurationEdit()
        self.duration.seconds_changed.connect(self._duration_changed)
        self.top.insertWidget(0, QLabel("Record for"))
        self.top.insertWidget(1, self.duration)
        self.preview = wiz.make_preview()
        self.lay.insertWidget(1, self.preview, 3)

    def _setup_duration(self) -> DurationEdit:
        return self.wiz.pages["setup"].duration

    def _duration_changed(self, seconds: int):
        """The Record page's field and the Setup page's are one value."""
        if self._setup_duration().value() != seconds:
            self._setup_duration().setValue(seconds)
        self._show_plan()

    def _show_plan(self):
        if self.wiz.session and self.wiz.session.is_done("record"):
            return
        self.status.setText(f"will record for {format_duration(self.duration.value())}")

    def initializePage(self):  # noqa: N802
        super().initializePage()
        self.duration.setValue(self._setup_duration().value())
        recorded = bool(self.wiz.session and self.wiz.session.is_done("record"))
        self.duration.setEnabled(not recorded)
        self._show_plan()
        if not (self.preview.thread and self.preview.thread.isRunning()):
            self.preview._toggle()

    def cleanupPage(self):  # noqa: N802
        self.preview.stop()

    def validatePage(self) -> bool:  # noqa: N802
        self.preview.stop()
        return True

    def start(self):
        self._setup_duration().setValue(self.duration.value())
        self.wiz.ensure_session()  # picks up the duration shown here
        self.duration.setEnabled(False)
        self.t = 0
        self.timer.start(1000)
        super().start()

    def _tick(self):
        self.t += 1
        d = self.wiz.session.spec.duration_s if self.wiz.session else 0
        self.status.setText(f"recording {format_duration(self.t)} of {format_duration(d)}")

    def _abort(self):
        if self.wiz.session:
            self.wiz.session.abort_recording()

    def finished(self, ok, msg):
        self.timer.stop()
        self.duration.setEnabled(not ok)
        super().finished(ok, msg)


class CutPage(StagePage):
    stages = ("cut", "reduce", "register")
    button_text = "Cut into arenas / wells"

    def __init__(self, wiz):
        super().__init__(
            wiz, "4 · Cut & check", "Check the overlay: every arena found, none on a screw."
        )
        self.preview = QLabel("(preview appears here)")
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview.setMinimumHeight(300)
        self.lay.insertWidget(1, self.preview, 2)
        self.crop = QCheckBox("Then crop the full recording to the arenas (saves disk space)")
        self.crop.setChecked(wiz.cfg.reduce.enabled)
        self.crop.setToolTip(
            "Re-encodes the Motif video to the bounding box of all arenas plus the padding. "
            "The full-frame video is deleted"
            + (" (moved to full_frame/)" if wiz.cfg.reduce.keep_full else "")
            + " once the cropped copy is verified frame for frame."
        )
        self.padding = QSpinBox()
        self.padding.setRange(0, REDUCE_PADDING_MAX_PX)
        self.padding.setSuffix(" px")
        self.padding.setValue(wiz.reduce_padding)
        self.padding.valueChanged.connect(wiz.set_reduce_padding)
        self.again = QPushButton("Cut again")
        self.again.setToolTip(
            "Throw away these clips and cut the recording again (the old clips are kept "
            "in clips.old-…). Needed after changing clip settings."
        )
        self.again.clicked.connect(self._again)
        self.top.insertWidget(1, self.again)
        self.fix = QPushButton("Fix arenas…")
        self.fix.setToolTip("Move, resize, add or remove arena circles, then cut again")
        self.fix.clicked.connect(self._fix_arenas)
        self.top.insertWidget(1, self.fix)
        self.top.insertWidget(1, self.crop)
        self.top.insertWidget(2, QLabel("padding"))
        self.top.insertWidget(3, self.padding)

    def _fix_arenas(self):
        import tempfile

        from rigconductor.arenas import grab_frame, load_layout, video_offset
        from rigconductor.gui.arena_editor import ArenaEditorDialog
        from rigconductor.gui.preview import _detect

        s = self.wiz.ensure_session()
        if not s.spec.recording_dir:
            dialogs.information(self, "Fix arenas", "Record (or choose a recording) first.")
            return
        ctx = s._ctx()
        path = s.profile.arena_layout_path(ctx)
        if path is None:
            dialogs.information(self, "Fix arenas", "Press 'Cut' once first, then fix.")
            return
        layout = load_layout(path)
        offset = video_offset(ctx.recording_dir)
        with tempfile.TemporaryDirectory() as td:
            try:
                img = QImage(str(grab_frame(ctx.recording_dir, Path(td) / "frame.png")))
            except ConductorError as e:
                dialogs.warning(self, "Fix arenas", str(e))
                return

        def detect():
            ox, oy = offset
            return [(cx + ox, cy + oy, r) for cx, cy, r in _detect(img, s.profile.n_units)]

        dlg = ArenaEditorDialog(img, layout.circles, offset, s.profile.n_units, detect, self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        layout.circles = dlg.circles()
        saved = s.profile.save_arena_layout(ctx, layout)
        self.log.appendPlainText("arenas saved → " + ", ".join(str(p) for p in saved))
        if (
            dialogs.question(self, "Fix arenas", "Cut the recording again with these arenas?")
            == QMessageBox.StandardButton.Yes
        ):
            s.log = self.log.appendPlainText
            if s.is_done("cut"):
                s.redo_cut()
            self.done_ok = False
            self.completeChanged.emit()
            self.start()

    def _again(self):
        s = self.wiz.ensure_session()
        if not s.is_done("cut"):
            self.start()
            return
        if (
            dialogs.question(self, "Cut again", "Cut this recording again?")
            != QMessageBox.StandardButton.Yes
        ):
            return
        s.log = self.log.appendPlainText
        s.redo_cut()
        self.done_ok = False
        self.completeChanged.emit()
        self.start()

    def finished(self, ok, msg):
        super().finished(ok, msg)
        s = self.wiz.session
        if ok and s:
            p = s.profile.preview_image(s._ctx())
            if p:
                self.preview.setPixmap(
                    QPixmap(str(p)).scaled(
                        self.preview.size(),
                        Qt.AspectRatioMode.KeepAspectRatio,
                        Qt.TransformationMode.SmoothTransformation,
                    )
                )


class AnalysePage(StagePage):
    stages = ("upload", "submit", "poll", "fetch", "score")
    button_text = "Send to Aoraki and wait"

    def __init__(self, wiz):
        super().__init__(wiz, "5 · Analyse", "Pick the model, send the clips, wait for the scores.")
        self.model = QComboBox()
        refresh = QPushButton("Refresh models")
        refresh.clicked.connect(self._refresh)
        self.top.insertWidget(0, QLabel("Model:"))
        self.top.insertWidget(1, self.model, 1)
        self.top.insertWidget(2, refresh)
        check = QPushButton("Check model vs videos")
        check.clicked.connect(self._check)
        self.top.insertWidget(3, check)
        self.force = QCheckBox("use anyway (Bart's OK)")
        self.top.insertWidget(4, self.force)
        off = QHBoxLayout()
        off.addWidget(QLabel("No network here?"))
        pack = QPushButton("Pack for Rig Relay…")
        pack.clicked.connect(self._pack)
        off.addWidget(pack)
        imp = QPushButton("Import relay results…")
        imp.clicked.connect(self._import_results)
        off.addWidget(imp)
        off.addStretch(1)
        self.lay.insertLayout(1, off)
        again = QPushButton("Rerun unfinished arenas")
        again.setToolTip(
            "Send only the arenas without poses yet (failed, stopped or never run); "
            "finished arenas are kept"
        )
        again.clicked.connect(self._send_again)
        self.top.insertWidget(5, again)
        rerun = QPushButton("Rerun with this model")
        rerun.setToolTip(
            "Analyse this recording again with the model chosen on the left. The earlier "
            "poses and clips are kept in an archive folder; the scores are replaced."
        )
        rerun.clicked.connect(self._rerun_model)
        self.top.insertWidget(5, rerun)
        stop = QPushButton("Stop")
        stop.setToolTip("Cancel every GPU worker of this run on Aoraki; finished arenas are kept")
        stop.clicked.connect(self._stop)
        self.top.insertWidget(6, stop)
        from rigconductor.gui.cluster_monitor import ClusterMonitor

        self.monitor = ClusterMonitor(
            wiz.cfg.cluster.ssh_host,
            label=lambda i: make_profile(wiz.profile_name()).unit_label(i),
            queue=self._queue,
        )
        self.monitor.arena_clicked.connect(self._show_arena_log)
        self.lay.insertWidget(2, self.monitor)
        self.job_timer = QTimer(self)
        self.job_timer.timeout.connect(self._follow_job)
        self.models = []

    def _queue(self):
        """Pull-queue runs submit several worker jobs (comma-separated ids)."""
        s = self.wiz.session
        if s is None or "," not in str(self._job_id() or ""):
            return None
        return s.profile.cluster_queue(s._ctx())

    def _job_id(self) -> str | None:
        s = self.wiz.session
        return (s.state["done"].get("submit") or {}).get("job_id") if s else None

    def _follow_job(self):
        job = self._job_id()
        if job:
            self.monitor.start(job)

    def _rerun_model(self):
        s = self.wiz.ensure_session()
        new = self.model.currentData()
        if not new:
            dialogs.information(self, "Rerun", "Choose a model first.")
            return
        if self.wiz.worker and self.wiz.worker.isRunning():
            dialogs.information(self, "Rerun", "A run is still active: press Stop first.")
            return
        old = s.spec.model_id or "-"
        same = " (the same model again)" if new == old else ""
        if (
            dialogs.question(
                self,
                "Rerun with another model",
                f"Analyse this recording again with {new}{same}?\n\nNow: {old}. The earlier "
                "poses and drawn clips move to an archive folder; the scores are replaced.",
            )
            != QMessageBox.StandardButton.Yes
        ):
            return
        s.log = self.add_line
        try:
            s.rerun_with_model(new)
        except Exception as e:  # noqa: BLE001 - shown to the operator
            dialogs.warning(self, "Rerun", f"Could not archive the earlier run: {e}")
            return
        self.monitor.stop()
        self.done_ok = False
        self.completeChanged.emit()
        self.start()

    def _stop(self):
        from rigconductor.cluster_status import stop_run

        job = self._job_id()
        if not job:
            dialogs.information(self, "Stop", "Nothing has been sent to Aoraki yet.")
            return
        if (
            dialogs.question(
                self, "Stop", "Cancel every GPU worker of this run? Finished arenas are kept."
            )
            != QMessageBox.StandardButton.Yes
        ):
            return
        q = self._queue()
        try:
            left = stop_run(self.wiz.cfg.cluster.ssh_host, job, q[0] if q else None)
        except RuntimeError as e:
            dialogs.warning(self, "Stop", f"Could not reach Aoraki: {e}")
            return
        self.add_line(
            "stopped: workers cancelled"
            + ("" if not left.strip() else f" (still listed: {' '.join(left.split())})")
            + ". 'Rerun unfinished arenas' sends the rest again."
        )

    def _show_arena_log(self, index: int):
        from rigconductor.cluster_status import arena_log

        q = self._queue()
        if not q:
            dialogs.information(self, "Log", "Logs per arena exist for runs sent with workers.")
            return
        try:
            text = arena_log(self.wiz.cfg.cluster.ssh_host, q[0], index)
        except RuntimeError as e:
            text = f"could not read the log: {e}"
        dialogs.show_text(
            self,
            f"Arena {index:02d}: log on Aoraki",
            f"Last lines of {q[0]}/_queue/arena_{index:02d}.log",
            text,
        )

    def _send_again(self):
        s = self.wiz.ensure_session()
        s.log = self.add_line
        s.redo_analysis()
        self.monitor.stop()
        self.done_ok = False
        self.completeChanged.emit()
        self.start()

    def cleanupPage(self):  # noqa: N802
        self.job_timer.stop()
        self.monitor.stop()

    def initializePage(self):  # noqa: N802
        super().initializePage()
        self._fill(models_mod.load_cached(self.wiz.cfg.registry_cache(self.wiz.profile_name())))
        self.job_timer.start(2000)
        self._follow_job()

    def _fill(self, models):
        prof = make_profile(self.wiz.profile_name())
        # deprecated models stay visible (labelled) so the flow can be tested end to end;
        # the model-vs-data check still runs before submit
        self.models = models_mod.models_for(models, prof.organism, include_deprecated=True)
        self.model.clear()
        for m in self.models:
            self.model.addItem(m.label, m.id)
        if not self.models:
            self.model.addItem("(no models — press Refresh)", None)
        chosen = self.wiz.session.spec.model_id if self.wiz.session else None
        chosen = chosen or self.wiz.pages["setup"].model.currentData()
        i = self.model.findData(chosen) if chosen else -1
        if i >= 0:
            self.model.setCurrentIndex(i)

    def _refresh(self):
        cfg = self.wiz.cfg
        name = self.wiz.profile_name()
        try:
            ms = models_mod.refresh(
                cfg.cluster.ssh_host, cfg.registry_remote(name), cfg.registry_cache(name)
            )
        except ConductorError as e:
            dialogs.warning(self, "Registry", str(e))
            return
        self._fill(ms)

    def _check(self):
        s = self.wiz.ensure_session()
        mid = self.model.currentData()
        if not mid:
            dialogs.information(self, "Model", "Choose a model first")
            return
        s.spec.model_id = mid
        s.log = self.log.appendPlainText
        try:
            checks = s.check_model()
            dialogs.information(self, "Model check", "\n".join(c.line for c in checks))
        except ConductorError as e:
            dialogs.warning(self, "Model check", str(e))

    def _pick_dest(self) -> Path | None:
        from rigconductor.export import mount

        disks = list(removable_disks())
        if disks:
            d = disks[0] if len(disks) == 1 else None
            if d is not None:
                return mount(d)
        p = QFileDialog.getExistingDirectory(self, "USB stick or folder", "/media")
        return Path(p) if p else None

    def _pack(self):
        from rigconductor.relay import pack_job

        s = self.wiz.ensure_session()
        mid = self.model.currentData()
        if not mid:
            dialogs.information(self, "Relay", "Choose the model first")
            return
        s.spec.model_id = mid
        s.spec.force_model = self.force.isChecked()
        try:
            dest = self._pick_dest()
            if dest is None:
                return
            out = pack_job(s, dest, log=self.log.appendPlainText)
        except ConductorError as e:
            dialogs.warning(self, "Relay", str(e))
            return
        dialogs.information(
            self,
            "Relay",
            f"Wrote {out.name}.\nTake the stick to a Rig Relay with network, then bring "
            "the .results.zip back and press 'Import relay results'.",
        )

    def _import_results(self):
        from rigconductor.relay import import_results

        p, _ = QFileDialog.getOpenFileName(
            self, "Relay results", "/media", "Conductor results (*.results.zip)"
        )
        if not p:
            return
        try:
            import_results(self.wiz.ensure_session(), Path(p), log=self.log.appendPlainText)
        except ConductorError as e:
            dialogs.warning(self, "Relay", str(e))
            return
        dialogs.information(
            self, "Relay", "Results imported. Press 'Send to Aoraki and wait' to score them."
        )

    def run_stages(self, log):
        s = self.wiz.ensure_session()
        mid = self.model.currentData() or s.spec.model_id
        if not mid:
            raise ConductorError("choose a model first")
        s.spec.model_id = mid
        s.spec.force_model = self.force.isChecked()
        s.log = log
        s.run(self.stages)


class DeliverPage(StagePage):
    stages = ("export",)
    button_text = "Export"

    def __init__(self, wiz):
        super().__init__(wiz, "6 · Deliver", "Plug in the USB disk, pick it, press Export.")
        self.disk = QComboBox()
        rescan = QPushButton("Rescan")
        rescan.clicked.connect(self._scan)
        self.blind = QCheckBox("blind export (no genotypes / vials)")
        self.blind.setChecked(wiz.cfg.export.blind)
        self.clips = QComboBox()
        for key, text in CLIP_MODES.items():
            self.clips.addItem(text, key)
        i = self.clips.findData(wiz.cfg.export.clips)
        self.clips.setCurrentIndex(i if i >= 0 else self.clips.findData(DEFAULT_CLIP_MODE))
        self.clips.setToolTip(
            "Skeletons: male 1 blue, male 2 green, female orange (Wong palette). "
            "Events: the scored behaviour written into the frame while it happens."
        )
        self.top.insertWidget(0, QLabel("Disk:"))
        self.top.insertWidget(1, self.disk, 1)
        self.top.insertWidget(2, rescan)
        self.top.insertWidget(3, self.blind)
        self.top.insertWidget(4, QLabel("Clips:"))
        self.top.insertWidget(5, self.clips)
        self.unmount = QCheckBox("unmount the disk when done")
        self.unmount.setChecked(wiz.cfg.export.unmount)
        self.unmount.setToolTip(
            "Off: the disk stays mounted so you can look at the files; eject it from the "
            "file manager before unplugging. On: unmounted after the copy is verified."
        )
        self.top.insertWidget(6, self.unmount)
        self.disks: list[Disk] = []

    def initializePage(self):  # noqa: N802
        super().initializePage()
        self._scan()

    def _scan(self):
        try:
            self.disks = removable_disks()
        except ConductorError as e:
            self.log.appendPlainText(str(e))
            self.disks = []
        self.disk.clear()
        for d in self.disks:
            self.disk.addItem(d.text, d.device)
        self.disk.addItem("… a folder instead", "__dir__")

    def run_stages(self, log):
        s = self.wiz.ensure_session()
        s.spec.blind = self.blind.isChecked()
        s.spec.clips = self.clips.currentData()
        s.spec.unmount = self.unmount.isChecked()
        s.log = log
        dev = self.disk.currentData()
        if dev == "__dir__":
            if not s.spec.export_dir:
                raise ConductorError(
                    "choose an export folder (use the CLI --disk or set export_dir)"
                )
            s.export(None)
        else:
            disk = next(d for d in self.disks if d.device == dev)
            s.export(disk)

    def start(self):
        if self.disk.currentData() == "__dir__":
            d = QFileDialog.getExistingDirectory(self, "Export folder", str(Path.home()))
            if not d:
                return
            self.wiz.ensure_session().spec.export_dir = d
        super().start()


def main(config_path: str | None = None):
    app = QApplication(sys.argv)
    try:
        cfg = ConductorConfig.load(config_path)
    except FileNotFoundError as e:
        dialogs.critical(None, "Rig Conductor", str(e))
        return 1
    w = Wizard(cfg)
    w.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
