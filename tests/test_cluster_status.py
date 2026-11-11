from types import SimpleNamespace

from rigconductor.cluster_status import (
    TaskState,
    expand_array_spec,
    fetch_tasks,
    parse_sacct,
    summarise,
)

SACCT = """6411849_[6-9%4]|PENDING|None assigned|00:00:00|0:0
6411849_0|RUNNING|aoraki-g01|00:00:41|0:0
6411849_1|RUNNING|aoraki-g01|00:00:40|0:0
6411849_2|COMPLETED|aoraki29|00:00:55|0:0
6411849_3|FAILED|aoraki-g02|00:00:55|1:0
6411849_4|CANCELLED by 5012|aoraki-g02|00:00:10|0:15
6411849_5|OUT_OF_MEMORY|aoraki29|00:01:02|0:125
99_0|RUNNING|x|00:00:01|0:0
"""


def test_expand_array_spec():
    assert expand_array_spec("[6-9%4]") == [6, 7, 8, 9]
    assert expand_array_spec("[0,2,5-6]") == [0, 2, 5, 6]


def test_parse_and_summarise():
    tasks = parse_sacct(SACCT, "6411849")
    assert [t.task for t in tasks] == list(range(10))
    assert tasks[6].category == "pending" and tasks[6].node == ""
    assert tasks[4].state == "CANCELLED" and tasks[4].category == "failed"
    s = summarise(tasks)
    assert (s["running"], s["pending"], s["done"], s["failed"]) == (2, 4, 1, 3)
    assert s["nodes"] == {"aoraki-g01": 2}


def test_fetch_uses_one_ssh_call():
    calls = []

    def run(cmd, **kw):
        calls.append(cmd)
        return SimpleNamespace(returncode=0, stdout=SACCT, stderr="")

    tasks = fetch_tasks("aoraki", "6411849", run=run)
    assert len(calls) == 1 and calls[0][:3] == ["ssh", "-o", "BatchMode=yes"]
    assert "sacct -j 6411849" in calls[0][-1] and len(tasks) == 10
    assert TaskState(0, "COMPLETED").category == "done"


QUEUE = """gdgs_arena_00.empty
gdgs_arena_01.h5
gdgs_arena_01.slp
_queue/clips.txt
_queue/claim_02
_queue/strikes_03
_queue/parked_04
OWNER _queue/claim_02 6411954 aoraki29 1000
NOW 1065
"""


def test_parse_queue_states():
    from rigconductor.cluster_status import parse_queue

    a = parse_queue(QUEUE, "gdgs", [0, 1, 2, 3, 4, 5], workers_done=False)
    assert [t.category for t in a] == ["done", "done", "running", "pending", "failed", "pending"]
    assert a[0].exit_code == "no animals" and a[2].node == "aoraki29"
    assert a[2].elapsed == "00:01:05" and a[3].exit_code == "retrying"
    finished = parse_queue(QUEUE, "gdgs", [5], workers_done=True)
    assert finished[0].category == "failed" and finished[0].exit_code == "not tracked"


def test_fetch_queue_status_one_ssh_call():
    from rigconductor.cluster_status import QUEUE_MARK, fetch_queue_status

    sacct = (
        "11|RUNNING|aoraki29|00:01:00|0:0|aoraki_gpu_L4_24GB\n"
        "12|PENDING|None assigned|00:00:00|0:0|aoraki_gpu_H100\n"
    )
    calls = []

    def run(cmd, **kw):
        calls.append(cmd)
        return SimpleNamespace(returncode=0, stdout=sacct + QUEUE_MARK + "\n" + QUEUE, stderr="")

    st = fetch_queue_status("aoraki", "11,12", "/p/pose/gdgs", "gdgs", [0, 1, 2], run=run)
    assert len(calls) == 1
    assert [(w.card, w.category) for w in st.workers] == [("L4", "running"), ("H100", "pending")]
    assert [t.category for t in st.arenas] == ["done", "done", "running"]


def test_stale_claim_is_flagged():
    from rigconductor.cluster_status import STALE, parse_queue

    text = (
        "_queue/claim_00\n_queue/claim_01\nOWNER _queue/claim_00 7 n1 1000 1590\n"
        "OWNER _queue/claim_01 7 n1 1000 1200\nNOW 1600\n"
    )
    a = parse_queue(text, "t", [0, 1], workers_done=False)
    assert a[0].exit_code == "" and a[1].exit_code.startswith(STALE)
    assert "6 min" in a[1].exit_code


def test_wait_reason_and_actions():
    from rigconductor.cluster_status import (
        QUEUE_MARK,
        REASON_MARK,
        arena_log,
        fetch_queue_status,
        stop_run,
    )

    out = (
        "12|PENDING|None assigned|00:00:00|0:0|aoraki_gpu_H100\n"
        f"{REASON_MARK}\n12|QOSMaxJobsPerUserLimit\n{QUEUE_MARK}\nNOW 1\n"
    )
    calls = []

    def run(cmd, **kw):
        calls.append(cmd[-1])
        return SimpleNamespace(returncode=0, stdout=out if "sacct" in cmd[-1] else "", stderr="")

    st = fetch_queue_status("aoraki", "12", "/p/pose/t", "t", [], run=run)
    assert st.workers[0].reason == "QOSMaxJobsPerUserLimit"
    stop_run("aoraki", "11,12", "/p/pose/t", run=run)
    assert "squeue -j 11,12 -h -o %i" in calls[-1] and 'scancel "$alive"' in calls[-1]
    assert "rm -rf /p/pose/t/_queue/claim_*" in calls[-1]
    arena_log("aoraki", "/p/pose/t", 3, run=run)
    assert "tail -n 80 /p/pose/t/_queue/arena_03.log" in calls[-1]
