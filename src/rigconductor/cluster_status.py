# ╔══════════════════════════════════════════════════════════════════╗
# ║  Rig Conductor — cluster_status                                  ║
# ║  « what every task of a SLURM array job is doing »               ║
# ╠══════════════════════════════════════════════════════════════════╣
# ║  One sacct call per refresh. Qt-free, so the GUI monitor and tests ║
# ║  share the parsing.                                              ║
# ╚══════════════════════════════════════════════════════════════════╝
"""SLURM array job status from ``sacct``."""

from __future__ import annotations

import re
import shlex
import subprocess
from collections import Counter
from dataclasses import dataclass

from rigconductor.constants import SSH_TIMEOUT_S, STALE_AFTER_S

SACCT_FIELDS = "JobID,State,NodeList,Elapsed,ExitCode"
FAILED_STATES = {
    "FAILED",
    "TIMEOUT",
    "OUT_OF_MEMORY",
    "NODE_FAIL",
    "CANCELLED",
    "BOOT_FAIL",
    "DEADLINE",
    "PREEMPTED",
    "REVOKED",
}
_TASK_RE = re.compile(r"^(\d+)_(\d+|\[[^\]]+\])$")


@dataclass
class TaskState:
    """One array task.

    Attributes:
        task: Array index (= arena / unit index for Conductor jobs).
        state: SLURM state, e.g. ``RUNNING``, ``CANCELLED by 123`` → ``CANCELLED``.
        node: Node it runs or ran on ('' while pending).
        elapsed: ``HH:MM:SS`` as sacct prints it.
        exit_code: ``0:0`` etc.
    """

    task: int
    state: str
    node: str = ""
    elapsed: str = ""
    exit_code: str = ""

    @property
    def category(self) -> str:
        """pending · running · done · failed."""
        if self.state in ("PENDING", "REQUEUED", "SUSPENDED", "CONFIGURING"):
            return "pending"
        if self.state in ("RUNNING", "COMPLETING"):
            return "running"
        if self.state == "COMPLETED":
            return "done"
        return "failed" if self.state in FAILED_STATES else "pending"


def expand_array_spec(spec: str) -> list[int]:
    """``[3-5,8%2]`` → ``[3, 4, 5, 8]``."""
    out: list[int] = []
    for part in spec.strip("[]").split("%")[0].split(","):
        if "-" in part:
            a, b = part.split("-")
            out.extend(range(int(a), int(b) + 1))
        elif part:
            out.append(int(part))
    return out


def parse_sacct(text: str, job_id: str) -> list[TaskState]:
    """Parse ``sacct -X -n -P -o SACCT_FIELDS`` output for one array job."""
    tasks: dict[int, TaskState] = {}
    for line in text.splitlines():
        cols = line.split("|")
        if len(cols) < 5:
            continue
        jid, state, node, elapsed, code = cols[:5]
        m = _TASK_RE.match(jid)
        if not m or m.group(1) != str(job_id):
            continue
        state = state.split()[0]
        node = "" if node in ("None assigned", "(null)") else node
        idx = m.group(2)
        for t in expand_array_spec(idx) if idx.startswith("[") else [int(idx)]:
            tasks[t] = TaskState(t, state, node, elapsed, code)
    return [tasks[k] for k in sorted(tasks)]


def summarise(tasks: list[TaskState]) -> dict:
    """Counts per category and running tasks per node."""
    counts = Counter(t.category for t in tasks)
    nodes = Counter(t.node for t in tasks if t.category == "running" and t.node)
    return {
        "pending": counts["pending"],
        "running": counts["running"],
        "done": counts["done"],
        "failed": counts["failed"],
        "total": len(tasks),
        "nodes": dict(sorted(nodes.items())),
    }


def fetch_tasks(
    ssh_host: str, job_id: str, ssh_cmd: str = "ssh -o BatchMode=yes", run=subprocess.run
) -> list[TaskState]:
    """Ask the cluster for every task of ``job_id`` (one ssh round trip)."""
    remote = f"sacct -j {shlex.quote(str(job_id))} -X -n -P -o {SACCT_FIELDS}"
    r = run(
        [*shlex.split(ssh_cmd), ssh_host, remote],
        capture_output=True,
        text=True,
        timeout=SSH_TIMEOUT_S,
    )
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip()[-200:] or f"ssh exit {r.returncode}")
    return parse_sacct(r.stdout, str(job_id))


# ── pull-queue jobs (sleap_submit.sh / sleap_worker.sh) ─────────────────

QUEUE_MARK = "@@QUEUE"
REASON_MARK = "@@REASON"
STALE = "stale"  # TaskState.exit_code prefix for a claim without heartbeat
WORKER_FIELDS = "JobID,State,NodeList,Elapsed,ExitCode,Partition"


@dataclass
class WorkerState:
    """One GPU worker job.

    Attributes:
        job_id: SLURM job id.
        state: SLURM state.
        partition: GPU partition it was sent to.
        node: Node it runs or ran on.
        elapsed: ``HH:MM:SS``.
        reason: Why a pending job waits (squeue ``%r``, e.g. ``Resources``).
    """

    job_id: str
    state: str
    partition: str = ""
    node: str = ""
    elapsed: str = ""
    reason: str = ""

    @property
    def category(self) -> str:
        return TaskState(0, self.state).category

    @property
    def card(self) -> str:
        """``aoraki_gpu_L4_24GB`` → ``L4``."""
        return self.partition.removeprefix("aoraki_gpu_").split("_")[0] or self.partition


def queue_listing_command(pose_dir: str) -> str:
    """Shell snippet that lists a trial's outputs and queue state in one go."""
    d = shlex.quote(pose_dir)
    return (
        f"cd {d} 2>/dev/null && {{ ls -1; ls -1 _queue 2>/dev/null | sed 's|^|_queue/|'; "
        'for c in _queue/claim_*; do [ -e "$c/owner" ] && '
        'echo "OWNER $c $(cat "$c/owner") $(stat -c %Y "$c")"; done; '
        'echo "NOW $(date +%s)"; }'
    )


def parse_workers(text: str, job_ids: list[str]) -> list[WorkerState]:
    out = []
    for line in text.splitlines():
        cols = line.split("|")
        if len(cols) >= 6 and cols[0] in job_ids:
            node = "" if cols[2] in ("None assigned", "(null)") else cols[2]
            out.append(WorkerState(cols[0], cols[1].split()[0], cols[5], node, cols[3]))
    return sorted(out, key=lambda w: job_ids.index(w.job_id))


def _hms(seconds: int) -> str:
    h, rest = divmod(max(0, seconds), 3600)
    return f"{h:02d}:{rest // 60:02d}:{rest % 60:02d}"


def parse_queue(
    text: str,
    trial: str,
    indices: list[int],
    workers_done: bool,
    stale_after_s: float = STALE_AFTER_S,
) -> list[TaskState]:
    """Per-arena state from :func:`queue_listing_command` output.

    ``workers_done``: no worker is pending or running any more, so a clip that is
    neither finished nor parked will not be picked up again (shown as failed).
    A claim whose heartbeat is older than ``stale_after_s`` is reported with
    ``exit_code`` "stale …": its worker died or hangs; another worker (or a rerun)
    takes it over.
    """
    names, owners, now = set(), {}, None
    for line in text.splitlines():
        if line.startswith("OWNER "):
            parts = line.split()
            if len(parts) >= 5:
                beat = int(parts[5]) if len(parts) >= 6 else int(parts[4])
                owners[parts[1].split("_")[-1]] = (parts[3], int(parts[4]), beat)
        elif line.startswith("NOW "):
            now = int(line.split()[1])
        elif line:
            names.add(line.strip())
    out = []
    for i in indices:
        nn = f"{i:02d}"
        stem = f"{trial}_arena_{nn}"
        if f"{stem}.h5" in names:
            out.append(TaskState(i, "COMPLETED"))
        elif f"{stem}.empty" in names:
            out.append(TaskState(i, "COMPLETED", exit_code="no animals"))
        elif f"_queue/parked_{nn}" in names:
            out.append(TaskState(i, "FAILED", exit_code="gave up after 3 tries"))
        elif f"_queue/claim_{nn}" in names:
            node, start, beat = owners.get(nn, ("", now or 0, now or 0))
            quiet = (now or beat) - beat
            note = f"{STALE}: no heartbeat for {quiet // 60} min" if quiet > stale_after_s else ""
            out.append(TaskState(i, "RUNNING", node, _hms((now or start) - start), note))
        elif workers_done:
            out.append(TaskState(i, "FAILED", exit_code="not tracked"))
        else:
            retry = f"_queue/strikes_{nn}" in names
            out.append(TaskState(i, "PENDING", exit_code="retrying" if retry else ""))
    return out


@dataclass
class JobStatus:
    workers: list[WorkerState]
    arenas: list[TaskState]


def fetch_queue_status(
    ssh_host: str,
    job_ids: str,
    pose_dir: str,
    trial: str,
    indices: list[int],
    ssh_cmd: str = "ssh -o BatchMode=yes",
    run=subprocess.run,
) -> JobStatus:
    """Workers (sacct) and arenas (queue files) in one ssh round trip."""
    ids = [j for j in str(job_ids).split(",") if j]
    jobs = shlex.quote(",".join(ids))
    remote = (
        f"sacct -j {jobs} -X -n -P -o {WORKER_FIELDS}; "
        f"echo {REASON_MARK}; squeue -j {jobs} -h -t PENDING -o '%i|%r' 2>/dev/null; "
        f"echo {QUEUE_MARK}; {queue_listing_command(pose_dir)}"
    )
    r = run(
        [*shlex.split(ssh_cmd), ssh_host, remote],
        capture_output=True,
        text=True,
        timeout=SSH_TIMEOUT_S,
    )
    if r.returncode != 0 and QUEUE_MARK not in r.stdout:
        raise RuntimeError(r.stderr.strip()[-200:] or f"ssh exit {r.returncode}")
    head, _, queue_part = r.stdout.partition(QUEUE_MARK)
    sacct_part, _, reason_part = head.partition(REASON_MARK)
    workers = parse_workers(sacct_part, ids)
    reasons = dict(x.split("|", 1) for x in reason_part.split() if "|" in x)
    for w in workers:
        w.reason = reasons.get(w.job_id, "")
    done = bool(workers) and all(w.category in ("done", "failed") for w in workers)
    return JobStatus(workers, parse_queue(queue_part, trial, indices, done))


# ── actions ───────────────────────────────────────────────────────────


def _ssh(ssh_host: str, ssh_cmd: str, remote: str, run=subprocess.run) -> str:
    r = run(
        [*shlex.split(ssh_cmd), ssh_host, remote],
        capture_output=True,
        text=True,
        timeout=SSH_TIMEOUT_S,
    )
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip()[-300:] or f"ssh exit {r.returncode}")
    return r.stdout


def stop_run(
    ssh_host: str,
    job_ids: str,
    pose_dir: str | None,
    ssh_cmd: str = "ssh -o BatchMode=yes",
    run=subprocess.run,
) -> str:
    """Cancel every job of a run and release its claimed arenas, so a rerun can
    take them at once. Finished arenas (.h5 / .empty) are kept."""
    ids = shlex.quote(",".join(j for j in str(job_ids).split(",") if j))
    # scancel exits 1 on job ids that already finished, so cancel the live ones only
    remote = (
        f"alive=$(squeue -j {ids} -h -o %i 2>/dev/null | paste -sd,); "
        '[ -z "$alive" ] || scancel "$alive" 2>/dev/null'
    )
    if pose_dir:
        remote += f"; rm -rf {shlex.quote(pose_dir)}/_queue/claim_*"
    remote += f"; squeue -j {ids} -h -o '%i %T' 2>/dev/null; true"
    return _ssh(ssh_host, ssh_cmd, remote, run)


def arena_log(
    ssh_host: str,
    pose_dir: str,
    index: int,
    lines: int = 80,
    ssh_cmd: str = "ssh -o BatchMode=yes",
    run=subprocess.run,
) -> str:
    """Last lines of one arena's SLEAP log on the cluster."""
    log = f"{pose_dir}/_queue/arena_{index:02d}.log"
    remote = (
        f"tail -n {int(lines)} {shlex.quote(log)} 2>/dev/null || echo 'no log yet for this arena'"
    )
    return _ssh(ssh_host, ssh_cmd, remote, run)
