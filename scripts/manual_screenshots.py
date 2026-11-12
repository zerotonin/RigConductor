# ╔══════════════════════════════════════════════════════════════════╗
# ║  Rig Conductor — manual_screenshots                              ║
# ║  « every window of the Conductor, staged and numbered »          ║
# ╠══════════════════════════════════════════════════════════════════╣
# ║  Writes the PNGs of docs/manual/. Run it again whenever a window ║
# ║  changes; then rebuild the PDF with build_manual_pdf.py.         ║
# ╚══════════════════════════════════════════════════════════════════╝
"""Screenshots for the user manual (docs/manual/img/).

Opens the windows offscreen with this rig's config, fills them with example
content (nothing is recorded, sent or saved), draws a numbered marker on every
control the manual explains, and blanks home folders and user names.

Usage::

    python scripts/manual_screenshots.py --frame FRAME.png --cut-preview ARENAS.png \\
        --layout ARENAS.json [--out docs/manual/img]

``FRAME.png`` is one full camera frame, ``ARENAS.png`` / ``ARENAS.json`` the arena
preview and layout ThermoKourt wrote next to that recording's clips.
"""

from __future__ import annotations

import argparse
import getpass
import json
import os
import sys
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QBuffer, QIODevice, QPoint, QRect, Qt  # noqa: E402
from PyQt6.QtGui import QColor, QFont, QImage, QPainter, QPen, QPixmap  # noqa: E402
from PyQt6.QtWidgets import (  # noqa: E402
    QApplication,
    QComboBox,
    QLabel,
    QLineEdit,
    QListWidget,
    QPlainTextEdit,
    QTableWidget,
    QWidget,
)

from rigconductor.constants import WONG  # noqa: E402

MARK = QColor(*WONG["vermillion"])
BADGE_PX = 24
SETTLE_S = 60  # longest wait for a background task (SLEAP inspect, summaries)
EXAMPLE_GROUP = "G01"
EXAMPLE_LABELLER = "JC"
WINDOW_W, WINDOW_H = 1280, 820  # wide enough for the Analyse toolbar


# ── helpers ───────────────────────────────────────────────────────
def pump(app: QApplication, seconds: float = 0.3) -> None:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.01)


def wait_worker(app: QApplication, wiz) -> None:
    """Let a wizard's background task finish (and its result arrive)."""
    end = time.monotonic() + SETTLE_S
    while wiz.worker is not None and wiz.worker.isRunning() and time.monotonic() < end:
        pump(app, 0.2)
    pump(app, 0.5)


def feed(panel, frame: bytes, layout: str) -> None:
    """Show ``frame`` in a preview panel with the arena layout that belongs to it."""
    panel.arenas_json = layout
    panel._load_overlay()
    panel.view.overlay = []
    panel._on_jpeg(frame)


def jpeg(path: Path) -> bytes:
    buf = QBuffer()
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    QImage(str(path)).save(buf, "JPG", 90)
    return bytes(buf.data())


def show_page(app: QApplication, wiz, page_id: int) -> QWidget:
    wiz.setStartId(page_id)
    wiz.restart()
    pump(app)
    return wiz.currentPage()


def rect_of(win: QWidget, target) -> QRect:
    """Window rectangle of a widget, or of ``(table, row, col)`` / ``(list, row)``."""
    if isinstance(target, tuple):
        view = target[0]
        if isinstance(view, QTableWidget):
            r = view.visualRect(view.model().index(target[1], target[2]))
        else:
            r = view.visualItemRect(view.item(target[1]))
        top_left = view.viewport().mapTo(win, r.topLeft())
        return QRect(top_left, r.size())
    return QRect(target.mapTo(win, QPoint(0, 0)), target.size())


def cluster_user(host: str) -> str:
    """The login name ssh uses for the cluster (from ~/.ssh/config), to hide it."""
    import subprocess

    try:
        r = subprocess.run(["ssh", "-G", host], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    for line in r.stdout.splitlines():
        if line.startswith("user "):
            return line.split(None, 1)[1].strip()
    return ""


def scrub_text(text: str, secrets: list[str]) -> str:
    for s, repl in secrets:
        text = text.replace(s, repl)
    return text


def scrub(win: QWidget, secrets) -> None:
    """Home folders and user names out of everything the window shows."""
    for w in win.findChildren(QLabel):
        if w.text():
            w.setText(scrub_text(w.text(), secrets))
    for w in win.findChildren(QLineEdit):
        w.setText(scrub_text(w.text(), secrets))
    for w in win.findChildren(QPlainTextEdit):
        w.setPlainText(scrub_text(w.toPlainText(), secrets))
    for w in win.findChildren(QComboBox):
        for i in range(w.count()):
            w.setItemText(i, scrub_text(w.itemText(i), secrets))
    for w in win.findChildren(QTableWidget):
        for r in range(w.rowCount()):
            for c in range(w.columnCount()):
                it = w.item(r, c)
                if it is not None and it.text():
                    it.setText(scrub_text(it.text(), secrets))
    for w in win.findChildren(QListWidget):
        for i in range(w.count()):
            w.item(i).setText(scrub_text(w.item(i).text(), secrets))


def shoot(app, win: QWidget, name: str, marks: list, out: Path, legend: dict, secrets) -> None:
    """Grab ``win``, draw numbered markers on ``marks`` ((label, target) pairs)."""
    scrub(win, secrets)
    pump(app, 0.2)
    img = win.grab().toImage().convertToFormat(QImage.Format.Format_RGB32)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    font = QFont()
    font.setBold(True)
    font.setPixelSize(13)
    p.setFont(font)
    for n, (_label, target) in enumerate(marks, start=1):
        r = rect_of(win, target).adjusted(-3, -3, 3, 3)
        p.setPen(QPen(MARK, 2.5))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(r, 4, 4)
        x = min(max(r.left() - BADGE_PX // 2, 0), img.width() - BADGE_PX)
        y = min(max(r.top() - BADGE_PX // 2, 0), img.height() - BADGE_PX)
        badge = QRect(x, y, BADGE_PX, BADGE_PX)
        p.setPen(QPen(QColor("white"), 1.5))
        p.setBrush(MARK)
        p.drawEllipse(badge)
        p.drawText(badge, Qt.AlignmentFlag.AlignCenter, str(n))
    p.end()
    img.save(str(out / f"{name}.png"))
    legend[name] = [label for label, _ in marks]
    print(f"{name}.png  {len(marks)} markers")


# ── the main wizard ───────────────────────────────────────────────
def main_wizard(app, cfg, a, out, legend, secrets):
    from rigconductor.cluster_status import JobStatus, TaskState, WorkerState
    from rigconductor.gui import wizard as wz
    from rigconductor.gui.progress import parse_progress

    wz.Wizard.preview_url = lambda self: None  # no camera stream: frames are fed below
    w = wz.Wizard(cfg)
    w.resize(WINDOW_W, WINDOW_H)
    w.show()
    pump(app)
    frame = jpeg(Path(a.frame))

    setup = show_page(app, w, 0)
    setup.rec_title.setText("Droso_28deg")
    setup.operator.setText(EXAMPLE_LABELLER)
    setup.check()
    feed(setup.preview, frame, a.layout)
    pv = setup.preview
    shoot(
        app,
        w,
        "main_1_setup",
        [
            ("Profile", setup.profile),
            ("Preset", setup.preset),
            ("Pose model", setup.model),
            ("Refresh (model list)", _button(setup, "Refresh")),
            ("Recording title", setup.rec_title),
            ("Duration", setup.duration),
            ("Operator", setup.operator),
            ("Existing recording / Choose…", setup.existing),
            ("Check rig readiness", _button(setup, "Check rig readiness")),
            ("Import DrosoDB from USB stick", _button(setup, "Import DrosoDB from USB stick")),
            ("Start preview", pv.toggle),
            ("Find arenas in this image", pv.find),
            ("Reduce view + padding", pv.reduce),
            ("Overlays", pv.chk_circles),
            ("Check for updates / Update", w.button(w.WizardButton.CustomButton1)),
            ("Training data…", w.button(w.WizardButton.CustomButton3)),
            ("Next", w.button(w.WizardButton.NextButton)),
        ],
        out,
        legend,
        secrets,
    )

    animals = show_page(app, w, 1)
    form = animals.form
    form.group.setText(EXAMPLE_GROUP)
    board = form.board
    codes = [v["code"] for v in board.shelf.catalogue][:8]
    for i, code in enumerate(codes[:4]):
        board.cards[i].drop_codes([code])
    pump(app)
    shoot(
        app,
        w,
        "main_2_animals",
        [
            ("Experiment", form.exp),
            ("Group code", form.group),
            ("Test temperature", form.temp),
            ("Vial shelf search", board.shelf.search),
            ("Vial shelf", board.shelf.tree),
            ("Arena card (tick = in use)", board.cards[0]),
            ("Copy A00 to all", _button(board, "Copy A00 to all")),
            ("Fill from selection", _button(board, "Fill from selection")),
            ("Clear all", _button(board, "Clear all")),
        ],
        out,
        legend,
        secrets,
    )

    record = show_page(app, w, 2)
    feed(record.preview, frame, a.layout)
    shoot(
        app,
        w,
        "main_3_record",
        [
            ("Record for", record.duration),
            ("Start recording", record.btn),
            ("Abort", record.abort),
            ("Live preview", record.preview.view),
            ("Show technical details", record.details),
        ],
        out,
        legend,
        secrets,
    )

    cut = show_page(app, w, 3)
    pix = QPixmap(a.cut_preview)
    cut.preview.setPixmap(
        pix.scaled(
            900, 330, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation
        )
    )
    for line in (
        "cut: 10 arenas found (rig layout), 2600×2160, 68.99 fps",
        "@progress cut 1.0 10/10 arenas",
        "reduce: 2600×2160 → 1376×1392 at (648, 282), padding 40 px",
        "register: 10 trials in the ThermoKourt database",
    ):
        p = parse_progress(line)
        cut.progress.show_line(p) if p else cut.log.appendPlainText(line)
    cut.status.setText("done")
    shoot(
        app,
        w,
        "main_4_cut",
        [
            ("Cut into arenas / wells", cut.btn),
            ("Then crop the full recording", cut.crop),
            ("padding", cut.padding),
            ("Fix arenas…", cut.fix),
            ("Cut again", cut.again),
            ("Arena overlay", cut.preview),
        ],
        out,
        legend,
        secrets,
    )

    an = show_page(app, w, 4)
    states = ["COMPLETED"] * 7 + ["RUNNING", "RUNNING", "PENDING"]
    nodes = ["aoraki18", "aoraki29", "aoraki-g01", "aoraki12"]
    tasks = [
        TaskState(i, s, *((nodes[i % 4], "00:01:12") if s != "PENDING" else ("", "")))
        for i, s in enumerate(states)
    ]
    workers = [
        WorkerState("6418456", "COMPLETED", "aoraki_gpu_L40", "aoraki18", "00:01:17"),
        WorkerState("6418457", "RUNNING", "aoraki_gpu_L4_24GB", "aoraki29", "00:01:20"),
        WorkerState("6418458", "RUNNING", "aoraki_gpu_RTX3090", "aoraki-g01", "00:01:19"),
        WorkerState("6418460", "PENDING", "aoraki_gpu_A100_80GB", "", "", "Resources"),
    ]
    an.monitor.show_status(JobStatus(workers, tasks))
    an.add_line("upload: 10 clips → Aoraki")
    an.add_line("submit: 4 GPU workers (L40, L4, RTX3090, A100_80GB)")
    an.add_line("@progress poll 0.7 7/10 arenas")
    tile = an.monitor.tiles[0]
    shoot(
        app,
        w,
        "main_5_analyse",
        [
            ("Model", an.model),
            ("Refresh models", _button(an, "Refresh models")),
            ("Check model vs videos", _button(an, "Check model vs videos")),
            ("use anyway", an.force),
            ("Rerun with this model", _button(an, "Rerun with this model")),
            ("Rerun unfinished arenas", _button(an, "Rerun unfinished arenas")),
            ("Stop", _button(an, "Stop")),
            ("Send to Aoraki and wait", an.btn),
            ("Pack for Rig Relay…", _button(an, "Pack for Rig Relay…")),
            ("Import relay results…", _button(an, "Import relay results…")),
            ("Arena tile (click for its log)", tile),
            ("GPU workers", an.monitor.workers),
        ],
        out,
        legend,
        secrets,
    )

    de = show_page(app, w, 5)
    de.disk.insertItem(0, "LAB-EXPORT (sdb1, 1.8 TB, exfat)", "/dev/sdb1")
    de.disk.setCurrentIndex(0)
    shoot(
        app,
        w,
        "main_6_deliver",
        [
            ("Disk", de.disk),
            ("Rescan", _button(de, "Rescan")),
            ("blind export", de.blind),
            ("Clips", de.clips),
            ("unmount the disk when done", de.unmount),
            ("Export", de.btn),
        ],
        out,
        legend,
        secrets,
    )
    w.close()


def _button(parent: QWidget, text: str):
    from PyQt6.QtWidgets import QPushButton

    for b in parent.findChildren(QPushButton):
        if b.text() == text:
            return b
    raise LookupError(f"no button '{text}'")


# ── dialogs and the relay ─────────────────────────────────────────
def extra_windows(app, cfg, a, out, legend, secrets):
    from rigconductor.arenas import load_layout
    from rigconductor.gui import dialogs
    from rigconductor.gui.arena_editor import ArenaEditorDialog
    from rigconductor.gui.relay import RelayWindow

    img = QImage(a.frame)
    layout = load_layout(Path(a.layout))
    dlg = ArenaEditorDialog(img, layout.circles, (0, 0), 10, detect=lambda: layout.circles)
    dlg.resize(1000, 860)
    dlg.show()
    dlg.canvas.selected = 4
    pump(app)
    shoot(
        app,
        dlg,
        "arena_editor",
        [
            ("Same size for all", _button(dlg, "Same size for all")),
            ("All bigger / All smaller", _button(dlg, "All bigger")),
            ("Undo / Reset", _button(dlg, "Undo")),
            ("Find arenas again", _button(dlg, "Find arenas again")),
            ("Arena circles (drag, wheel, right-click)", dlg.canvas),
            ("Save", _button(dlg, "Save")),
        ],
        out,
        legend,
        secrets,
    )
    dlg.close()

    log = "\n".join(
        [
            "=== SLEAP track ===",
            "Model : lab_oct2025_v2  (backend tf)",
            "GPU   : NVIDIA L40",
            "Predicting... 100% 345/345 frames, 96 FPS",
            "inference+tracking: 41 s",
            "instances found: 1035",
            "Out   : pose/<trial>/<trial>_arena_05.slp  .h5",
        ]
    )
    tdlg = dialogs.TextDialog(None, "Arena 05: log on Aoraki", "Last lines of the arena log", log)
    tdlg.show()
    pump(app)
    shoot(app, tdlg, "arena_log", [], out, legend, secrets)
    tdlg.close()

    rw = RelayWindow(cfg)
    if not rw.list.count():
        rw.list.addItem("drosophila_triad_20261112_074228   [new]   model lab_oct2025_v2")
    rw.list.setCurrentRow(0)
    rw.show()
    pump(app)
    shoot(
        app,
        rw,
        "relay",
        [
            ("Load job package…", _button(rw, "Load job package…")),
            ("Send to Aoraki and wait", _button(rw, "Send to Aoraki and wait")),
            ("Write results to stick…", _button(rw, "Write results to stick…")),
            ("Refresh", _button(rw, "Refresh")),
            ("Sessions", rw.list),
        ],
        out,
        legend,
        secrets,
    )
    rw.close()


# ── the training-data wizard ──────────────────────────────────────
def training_wizard(app, cfg, a, out, legend, secrets):
    from rigconductor.gui import labelling_wizard as lw
    from rigconductor.labels.store import LabelSet, default_set

    wiz = lw.LabellingWizard(cfg)
    wiz.resize(WINDOW_W, WINDOW_H)
    wiz.show()
    start = show_page(app, wiz, lw.PAGE_START)
    pump(app, 1.0)
    radios = start.mode.buttons()
    shoot(
        app,
        wiz,
        "train_1_start",
        [
            ("Label set", start.set_combo),
            ("New set from a SLEAP project…", _button(start, "New set from a SLEAP project…")),
            ("Make default", start.default_btn),
            ("Pick new frames to label", radios[0]),
            ("Continue labelling", radios[1]),
            ("Bring labelled frames back", radios[2]),
            ("Choose training data and train", radios[3]),
            ("Set up SLEAP", start.setup_btn),
            ("Next", wiz.button(wiz.WizardButton.NextButton)),
        ],
        out,
        legend,
        secrets,
    )
    wiz.lset = LabelSet.load(a.label_set or default_set())

    recs = show_page(app, wiz, lw.PAGE_RECS)
    recs.only_new.setChecked(False)
    pump(app)
    for r in range(min(2, recs.table.rowCount())):
        recs.table.item(r, 0).setCheckState(Qt.CheckState.Checked)
    marks = [("Filters", recs.only_new), ("Use", (recs.table, 0, 0))]
    if recs.table.rowCount():
        marks.append(("Validation only", (recs.table, 0, 1)))
    shoot(app, wiz, "train_2_recordings", marks, out, legend, secrets)

    fr = show_page(app, wiz, lw.PAGE_FRAMES)
    rows = [
        ("drosophila_triad_20261111_104847", 3, 1802, "unsure", "0.41"),
        ("drosophila_triad_20261111_104847", 3, 2933, "contact", "1.2 BL"),
        ("drosophila_triad_20261111_104847", 7, 518, "spread", ""),
        ("jess_test_01_20261112_074228", 0, 211, "unsure", "0.37"),
        ("jess_test_01_20261112_074228", 5, 87, "contact", "0.9 BL"),
    ]
    fr.table.setRowCount(len(rows))
    for i, row in enumerate(rows):
        for c, v in enumerate(row):
            fr.table.setItem(i, c, lw._item(v))
    fr.picked_label.setText("200 frames from 2 recordings (example)")
    shoot(
        app,
        wiz,
        "train_3_frames",
        [
            ("Frames per arena", fr.n),
            ("Mix", fr.mix["unsure"]),
            ("Pre-fill with model", fr.prefill),
            ("Choose frames", fr.pick_btn),
        ],
        out,
        legend,
        secrets,
    )

    pk = show_page(app, wiz, lw.PAGE_PACKAGE)
    shoot(
        app,
        wiz,
        "train_4_package",
        [("Where to label", pk.where), ("Make the package", pk.go)],
        out,
        legend,
        secrets,
    )

    wiz.env = __import__("rigconductor.sleapenv", fromlist=["find"]).find()
    co = show_page(app, wiz, lw.PAGE_CONTINUE)
    wait_worker(app, wiz)
    marks = [
        ("Package / project list", (co.table, 0, 0) if co.table.rowCount() else co.table),
        ("Open in SLEAP", co.open_btn),
        ("Add a SLEAP project…", _button(co, "Add a SLEAP project…")),
        ("Look on a folder…", _button(co, "Look on a folder…")),
        ("Copy to USB…", co.copy_btn),
        ("Delete…", co.delete_btn),
    ]
    if co.table.rowCount():
        marks.insert(1, ("Progress bar", (co.table, 0, 2)))
    shoot(app, wiz, "train_5_continue", marks, out, legend, secrets)

    bk = show_page(app, wiz, lw.PAGE_BACK)
    bk.labeller.setText(EXAMPLE_LABELLER)
    shoot(
        app,
        wiz,
        "train_6_back",
        [
            ("Package or project", bk.pkgs),
            ("Look on a folder…", _button(bk, "Look on a folder…")),
            ("Labeller", bk.labeller),
            ("Check frames", bk.check_btn),
            ("Also merge frames with warnings", bk.with_warnings),
            ("Merge accepted frames", bk.merge_btn),
        ],
        out,
        legend,
        secrets,
    )

    da = show_page(app, wiz, lw.PAGE_DATA)
    wait_worker(app, wiz)
    marks = [("Label set version", da.version)]
    if da.roles:  # shown, never saved: validatePage is not called
        da.roles[0].setCurrentIndex(da.roles[0].findData("all"))
    if da.table.rowCount():
        marks += [("Example (double-click)", (da.table, 0, 0)), ("Use as", da.roles[0])]
    marks.append(("Totals", da.totals))
    shoot(app, wiz, "train_7_data", marks, out, legend, secrets)

    wiz.train_version = da.version.currentData() or 1
    tr = show_page(app, wiz, lw.PAGE_TRAIN)
    shoot(
        app,
        wiz,
        "train_8_train",
        [
            ("Start from model", tr.base),
            ("Compare with", tr.compare),
            ("Epochs (at most)", tr.epochs),
            ("Start training", tr.start_btn),
            ("Make it the default model", tr.promote_btn),
            ("Register without making it the default", tr.keep_btn),
        ],
        out,
        legend,
        secrets,
    )
    wiz.close()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--frame", required=True, help="one full camera frame (PNG)")
    ap.add_argument("--cut-preview", required=True, help="ThermoKourt's *_arenas.png")
    ap.add_argument("--layout", required=True, help="ThermoKourt's *_arenas.json")
    ap.add_argument("--label-set", default="", help="label set to show (default set if empty)")
    ap.add_argument("--config", default=None)
    ap.add_argument("--out", default=str(Path(__file__).resolve().parents[1] / "docs/manual/img"))
    a = ap.parse_args(argv)
    app = QApplication.instance() or QApplication(sys.argv[:1])
    from rigconductor.config import ConductorConfig

    cfg = ConductorConfig.load(a.config)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    user = getpass.getuser()
    secrets = [(str(Path.home()), "~"), (user, "user")]
    remote = cluster_user(cfg.cluster.ssh_host)
    if remote:
        secrets.append((remote, "user"))
    legend: dict[str, list[str]] = {}
    main_wizard(app, cfg, a, out, legend, secrets)
    extra_windows(app, cfg, a, out, legend, secrets)
    training_wizard(app, cfg, a, out, legend, secrets)
    (out / "markers.json").write_text(json.dumps(legend, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
