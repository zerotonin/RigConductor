import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PyQt6.QtWidgets")
_APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def test_tiles_and_counts_from_tasks():
    from rigconductor.cluster_status import parse_sacct
    from rigconductor.gui.cluster_monitor import ClusterMonitor
    from tests.test_cluster_status import SACCT

    m = ClusterMonitor("aoraki", label=lambda i: f"A{i:02d}")
    m.job_id = "6411849"
    m.show_tasks(parse_sacct(SACCT, "6411849"))
    assert len(m.tiles) == 10
    assert m.count_labels["failed"].text().startswith("3")
    assert "aoraki-g01 (2)" in m.nodes.text()
    assert m.tiles[3].state.text() == "failed" and m.tiles[0].where.text() == "aoraki-g01"
    assert m.tiles[0].elapsed.text() == "00:00:41" and m.tiles[6].elapsed.text() == ""


def test_monitor_stops_when_everything_finished():
    from rigconductor.cluster_status import TaskState
    from rigconductor.gui.cluster_monitor import ClusterMonitor

    m = ClusterMonitor("aoraki", fetch=lambda job: [TaskState(0, "COMPLETED")])
    m.job_id = "1"
    m.show_status(m.fetch())
    assert "finished" in m.head.text() and m.thread is None


def test_queue_mode_shows_workers_and_no_animals():
    from rigconductor.cluster_status import JobStatus, TaskState, WorkerState
    from rigconductor.gui.cluster_monitor import ClusterMonitor

    m = ClusterMonitor("aoraki", label=lambda i: f"A{i:02d}")
    m.job_id = "11,12"
    st = JobStatus(
        [
            WorkerState("11", "RUNNING", "aoraki_gpu_L4_24GB", "aoraki29", "00:01:00"),
            WorkerState("12", "PENDING", "aoraki_gpu_H100"),
        ],
        [TaskState(0, "COMPLETED", exit_code="no animals"), TaskState(1, "RUNNING", "aoraki29")],
    )
    m.show_status(st)
    assert m.tiles[0].state.text() == "no animals"
    assert "L4 · aoraki29" in m.workers.text() and "H100 · waiting for a GPU" in m.workers.text()
    assert "2 arenas</b> on 2 GPU workers" in m.head.text()
    m.show_workers([WorkerState("13", "CANCELLED", "aoraki_gpu_H100")])
    assert "H100 · not needed" in m.workers.text()


def test_stale_tile_and_click_signal():
    from rigconductor.cluster_status import JobStatus, TaskState, WorkerState
    from rigconductor.gui.cluster_monitor import ClusterMonitor

    m = ClusterMonitor("aoraki", label=lambda i: f"A{i:02d}")
    m.job_id = "11"
    m.show_status(
        JobStatus(
            [WorkerState("11", "PENDING", "aoraki_gpu_H100", reason="Resources")],
            [TaskState(0, "RUNNING", "n1", "00:09:00", "stale: no heartbeat for 7 min")],
        )
    )
    assert m.tiles[0].state.text() == "no heartbeat"
    assert "waiting for a GPU (Resources)" in m.workers.text()
    got = []
    m.arena_clicked.connect(got.append)
    m.tiles[0].clicked.emit(0)
    assert got == [0]
