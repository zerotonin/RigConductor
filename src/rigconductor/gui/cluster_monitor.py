# ╔══════════════════════════════════════════════════════════════════╗
# ║  Rig Conductor — gui.cluster_monitor                             ║
# ║  « live tiles: one per cluster task »                            ║
# ╠══════════════════════════════════════════════════════════════════╣
# ║  Counts of pending / running / done / failed tasks, one coloured tile ║
# ║  per task with its node and run time, refreshed every few seconds. ║
# ╚══════════════════════════════════════════════════════════════════╝
"""Graphical SLURM job monitor."""

from __future__ import annotations

import threading
from collections.abc import Callable
from datetime import datetime

from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtWidgets import QFrame, QGridLayout, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from rigconductor.cluster_status import (
    STALE,
    JobStatus,
    TaskState,
    WorkerState,
    fetch_queue_status,
    fetch_tasks,
    summarise,
)
from rigconductor.constants import JOB_STATE_COLOURS, MONITOR_REFRESH_S

CATEGORY_TEXT = {"pending": "waiting", "running": "running", "done": "done", "failed": "failed"}
TILE_COLUMNS = 5


def _css(rgb: tuple[int, int, int]) -> str:
    return "rgb({}, {}, {})".format(*rgb)


class MonitorThread(QThread):
    """Polls ``fetch`` every ``interval_s`` and emits the task list (or an error)."""

    tasks = pyqtSignal(object)
    problem = pyqtSignal(str)

    def __init__(self, fetch: Callable[[], list[TaskState]], interval_s: float = MONITOR_REFRESH_S):
        super().__init__()
        self.fetch = fetch
        self.interval_s = interval_s
        self._stop = threading.Event()

    def run(self):
        while not self._stop.is_set():
            try:
                self.tasks.emit(self.fetch())
            except Exception as e:  # noqa: BLE001 - shown in the widget, polling continues
                self.problem.emit(str(e))
            self._stop.wait(self.interval_s)

    def stop(self):
        self._stop.set()
        self.wait(3000)


class TaskTile(QFrame):
    clicked = pyqtSignal(int)

    def __init__(self, label: str, task: int = 0):
        super().__init__()
        self.task = task
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("Click for this arena's log on the cluster")
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setMinimumSize(110, 84)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 4, 6, 4)
        self.title = QLabel(f"<b>{label}</b>")
        self.state = QLabel("…")
        self.where = QLabel("")
        self.elapsed = QLabel("")
        for w in (self.title, self.state, self.where, self.elapsed):
            w.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lay.addWidget(w)

    def mousePressEvent(self, e):  # noqa: N802
        self.clicked.emit(self.task)
        super().mousePressEvent(e)

    def show_task(self, t: TaskState):
        cat = t.category
        stale = t.exit_code.startswith(STALE)
        fg = "white" if cat in ("running", "failed", "done") or stale else "black"
        colour = JOB_STATE_COLOURS["stale" if stale else cat]
        self.setStyleSheet(
            f"TaskTile {{ background: {_css(colour)}; border-radius: 6px; }}"
            f"QLabel {{ color: {fg}; }}"
        )
        if stale:
            text = "no heartbeat"
        elif cat == "done" and t.exit_code == "no animals":
            text = "no animals"
        elif cat == "failed":
            text = "failed" if t.state == "FAILED" else t.state.lower()
        elif cat == "pending" and t.exit_code == "retrying":
            text = "retrying"
        else:
            text = CATEGORY_TEXT[cat]
        self.state.setText(text)
        self.where.setText(t.node if cat != "failed" or not t.exit_code else t.exit_code)
        self.elapsed.setText(t.elapsed if cat in ("running", "done") else "")
        detail = t.exit_code or t.state.lower()
        self.setToolTip(f"{detail}. Click for this arena's log on the cluster.")


class ClusterMonitor(QWidget):
    arena_clicked = pyqtSignal(int)

    """One tile per arena plus a line per GPU worker. ``start(job_ids)`` begins
    polling; ``stop()`` ends it.

    With a ``queue`` lookup (pose dir, trial, arena indices) the arenas come from
    the pull-queue files and the jobs are workers; without one, ``job_ids`` is a
    single array job whose task index is the arena."""

    def __init__(
        self,
        ssh_host: str,
        ssh_cmd: str = "ssh -o BatchMode=yes",
        label: Callable[[int], str] = str,
        fetch=None,
        queue: Callable[[], tuple[str, str, list[int]] | None] | None = None,
    ):
        super().__init__()
        self.ssh_host, self.ssh_cmd, self.label = ssh_host, ssh_cmd, label
        self._fetch = fetch  # test hook: (job_ids) -> JobStatus | list[TaskState]
        self.queue = queue
        self.thread: MonitorThread | None = None
        self.job_id: str | None = None
        self.tiles: dict[int, TaskTile] = {}
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.head = QLabel("No job sent yet.")
        self.head.setTextFormat(Qt.TextFormat.RichText)
        lay.addWidget(self.head)
        self.counts = QHBoxLayout()
        self.count_labels = {}
        for cat in ("running", "pending", "done", "failed"):
            lab = QLabel()
            lab.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lab.setStyleSheet(
                f"background: {_css(JOB_STATE_COLOURS[cat])}; border-radius: 6px; padding: 6px;"
                f"color: {'black' if cat == 'pending' else 'white'}; font-weight: bold;"
            )
            self.count_labels[cat] = lab
            self.counts.addWidget(lab)
        lay.addLayout(self.counts)
        self.grid_w = QWidget()
        self.grid = QGridLayout(self.grid_w)
        self.grid.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.grid_w)
        self.nodes = QLabel("")
        self.nodes.setWordWrap(True)
        lay.addWidget(self.nodes)
        self.workers = QLabel("")
        self.workers.setWordWrap(True)
        self.workers.setTextFormat(Qt.TextFormat.RichText)
        lay.addWidget(self.workers)
        self.update_counts(
            {"running": 0, "pending": 0, "done": 0, "failed": 0, "total": 0, "nodes": {}}
        )

    def fetch(self) -> JobStatus:
        if self._fetch is not None:
            got = self._fetch(self.job_id)
        else:
            q = self.queue() if self.queue else None
            if q:
                got = fetch_queue_status(self.ssh_host, self.job_id, *q, ssh_cmd=self.ssh_cmd)
            else:
                got = fetch_tasks(self.ssh_host, self.job_id, self.ssh_cmd)
        return got if isinstance(got, JobStatus) else JobStatus([], got)

    def start(self, job_id: str):
        if self.thread and self.job_id == str(job_id):
            return
        self.stop()
        self.job_id = str(job_id)
        self.head.setText(f"Cluster job <b>{job_id}</b>: asking Aoraki…")
        self.thread = MonitorThread(self.fetch)
        self.thread.tasks.connect(self.show_status)
        self.thread.problem.connect(lambda m: self.head.setText(f"Cluster job {job_id}: {m}"))
        self.thread.start()

    def stop(self):
        if self.thread:
            self.thread.stop()
            self.thread = None

    def update_counts(self, s: dict):
        for cat, lab in self.count_labels.items():
            lab.setText(f"{s[cat]}\n{CATEGORY_TEXT[cat]}")
        nodes = s["nodes"]
        self.nodes.setText(
            "Running on: " + ", ".join(f"{n} ({k})" for n, k in nodes.items()) if nodes else ""
        )

    def show_tasks(self, tasks: list[TaskState]):
        self.show_status(JobStatus([], tasks))

    def show_workers(self, workers: list[WorkerState]):
        if not workers:
            self.workers.setText("")
            return
        bits = []
        for w in workers:
            unused = w.state == "CANCELLED" and not w.node  # never got a GPU, no work left
            colour = _css(JOB_STATE_COLOURS["pending" if unused else w.category])
            waiting = "waiting for a GPU" + (f" ({w.reason})" if w.reason else "")
            where = w.node if w.node else ("not needed" if unused else waiting)
            text = {"running": "running", "done": "finished", "failed": w.state.lower()}.get(
                w.category, ""
            )
            text = "" if unused else text
            bits.append(
                f"<span style='color:{colour}'>●</span> {w.card} · {where}"
                + (f" ({text})" if text else "")
            )
        self.workers.setText("GPU workers: " + " &nbsp; ".join(bits))

    def show_status(self, status: JobStatus):
        tasks = status.arenas
        s = summarise(tasks)
        self.update_counts(s)
        self.show_workers(status.workers)
        busy = any(w.category in ("running", "pending") for w in status.workers)
        if s["total"] and s["done"] + s["failed"] == s["total"]:
            verdict = "finished" if not s["failed"] else f"finished, {s['failed']} failed"
            if not busy:
                self.stop()
        else:
            verdict = f"updated {datetime.now():%H:%M:%S}, every {MONITOR_REFRESH_S} s"
        self.head.setText(
            f"<b>{s['total']} arenas</b> on {len(status.workers)} GPU workers · {verdict}"
            if status.workers
            else f"Cluster job <b>{self.job_id}</b>: {s['total']} tasks · {verdict}"
        )
        for t in tasks:
            if t.task not in self.tiles:
                tile = TaskTile(self.label(t.task), t.task)
                tile.clicked.connect(self.arena_clicked)
                n = len(self.tiles)
                self.grid.addWidget(tile, n // TILE_COLUMNS, n % TILE_COLUMNS)
                self.tiles[t.task] = tile
            self.tiles[t.task].show_task(t)
