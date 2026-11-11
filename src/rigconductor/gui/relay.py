# ╔══════════════════════════════════════════════════════════════════╗
# ║  Rig Conductor — gui.relay                                       ║
# ║  « the Rig Relay window »                                        ║
# ╠══════════════════════════════════════════════════════════════════╣
# ║  Runs the cluster part of an offline rig's session.              ║
# ╚══════════════════════════════════════════════════════════════════╝
"""Rig Relay: a small window for a networked PC that runs the cluster part of a
session packed by an offline rig.

Load the .job.zip from the stick → Send to Aoraki and wait → Write results to
the stick. Everything goes through :mod:`rigconductor.relay`.
"""

from __future__ import annotations

import sys
from pathlib import Path

from PyQt6.QtWidgets import (
    QApplication,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QMainWindow,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from rigconductor import ConductorError
from rigconductor import relay as relay_mod
from rigconductor.config import ConductorConfig
from rigconductor.gui import dialogs
from rigconductor.gui.wizard import Worker
from rigconductor.session import ConductorSession, ConductorSpec


class RelayWindow(QMainWindow):
    def __init__(self, cfg: ConductorConfig):
        super().__init__()
        self.cfg = cfg
        self.worker = None
        self.setWindowTitle("Rig Relay")
        self.resize(820, 600)
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.addWidget(
            QLabel(
                "1. Load the job package from the rig's USB stick.  2. Send it to Aoraki and "
                "wait.  3. Write the results back to the stick and take it to the rig."
            )
        )
        row = QHBoxLayout()
        for text, fn in (
            ("Load job package…", self.load),
            ("Send to Aoraki and wait", self.send),
            ("Write results to stick…", self.write_results),
            ("Refresh", self.refresh),
        ):
            b = QPushButton(text)
            b.clicked.connect(fn)
            row.addWidget(b)
        lay.addLayout(row)
        self.list = QListWidget()
        lay.addWidget(self.list, 1)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        lay.addWidget(self.log, 2)
        self.setCentralWidget(w)
        self.refresh()

    def _sessions(self) -> list[ConductorSession]:
        import json

        out = []
        for p in sorted(self.cfg.sessions_dir.glob("*.json")):
            st = json.loads(p.read_text(encoding="utf-8"))
            if (st.get("relay") or {}).get("role") == "relay":
                out.append(ConductorSession(self.cfg, ConductorSpec.from_json(st["spec"]), None))
        return out

    def refresh(self):
        self.list.clear()
        for s in self._sessions():
            done = [k for k in relay_mod.CLUSTER_STAGES if s.is_done(k)]
            state = "results ready" if len(done) == 4 else (f"at {done[-1]}" if done else "new")
            self.list.addItem(f"{s.spec.session_id}   [{state}]   model {s.spec.model_id}")

    def _selected(self) -> ConductorSession | None:
        i = self.list.currentRow()
        ss = self._sessions()
        if i < 0 or i >= len(ss):
            dialogs.information(self, "Rig Relay", "Select a session in the list first")
            return None
        return ss[i]

    def load(self):
        p, _ = QFileDialog.getOpenFileName(
            self, "Job package", "/media", "Conductor job (*.job.zip)"
        )
        if not p:
            return
        try:
            relay_mod.import_job(self.cfg, Path(p), log=self.log.appendPlainText)
        except ConductorError as e:
            dialogs.warning(self, "Rig Relay", str(e))
        self.refresh()

    def send(self):
        s = self._selected()
        if s is None:
            return
        if self.worker and self.worker.isRunning():
            dialogs.information(self, "Rig Relay", "A session is already running")
            return

        def job(log):
            s.log = log
            relay_mod.run_cluster_stages(s)

        self.worker = Worker(job)
        self.worker.line.connect(self.log.appendPlainText)
        self.worker.finished_ok.connect(self._done)
        self.worker.start()

    def _done(self, ok: bool, msg: str):
        self.refresh()
        if ok:
            dialogs.information(self, "Rig Relay", "Cluster part finished. Write the results.")
        else:
            dialogs.critical(self, "Rig Relay", msg)

    def write_results(self):
        s = self._selected()
        if s is None:
            return
        d = QFileDialog.getExistingDirectory(self, "USB stick", "/media")
        if not d:
            return
        try:
            out = relay_mod.pack_results(s, Path(d), log=self.log.appendPlainText)
        except ConductorError as e:
            dialogs.warning(self, "Rig Relay", str(e))
            return
        dialogs.information(self, "Rig Relay", f"Wrote {out.name}. Eject the stick.")


def main(config_path: str | None = None):
    app = QApplication(sys.argv)
    try:
        cfg = ConductorConfig.load(config_path)
    except FileNotFoundError as e:
        dialogs.critical(None, "Rig Relay", str(e))
        return 1
    w = RelayWindow(cfg)
    w.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
