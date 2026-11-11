# ╔══════════════════════════════════════════════════════════════════╗
# ║  Rig Conductor — labels.sync                                     ║
# ║  « copy the label sets to Aoraki, on demand or from cron »       ║
# ╠══════════════════════════════════════════════════════════════════╣
# ║  rsync without --delete: the cluster copy only ever grows.       ║
# ╚══════════════════════════════════════════════════════════════════╝
"""Send a label set (versions, registry, packages) to Aoraki.

Lands in ``<project>/sleap_labels/sets/<name>/``. The ``train/`` folder is
left out (it is rebuilt before every training run). Run from cron, the ssh
key comes from the desktop keyring's agent, so the PC must stay logged in.
"""

from __future__ import annotations

import fcntl
import os
import shlex
import shutil
import subprocess
from datetime import datetime
from pathlib import Path

from rigconductor import ConductorError
from rigconductor.config import ConductorConfig
from rigconductor.constants import LABEL_PROFILE, LABEL_SYNC_HOURS
from rigconductor.labels import train as train_mod
from rigconductor.labels.store import LabelSet, default_set

CRON_TAG = "# rigconductor label-sync"
SKIP = ("train/",)
LOCK = Path("~/.cache/rigconductor/label-sync.lock")
LOG = Path("~/.local/state/rigconductor/label-sync.log")


def remote_dir(cfg: ConductorConfig, name: str, profile: str = LABEL_PROFILE) -> str:
    return f"{train_mod.project_root(cfg, profile)}/sleap_labels/sets/{name}"


def ssh_env() -> dict:
    """Environment for ssh/rsync; under cron, borrow the desktop keyring's agent."""
    env = dict(os.environ)
    if not env.get("SSH_AUTH_SOCK"):
        sock = Path(f"/run/user/{os.getuid()}/keyring/ssh")
        if sock.exists():
            env["SSH_AUTH_SOCK"] = str(sock)
    return env


def rsync_command(cfg: ConductorConfig, lset: LabelSet, dest: str) -> list[str]:
    return [
        "rsync",
        "-a",
        "--itemize-changes",
        "-e",
        "ssh -o BatchMode=yes -o ConnectTimeout=30",
        *[f"--exclude={x}" for x in SKIP],
        f"{lset.path}/",
        f"{cfg.cluster.ssh_host}:{dest}/",
    ]


def sync(cfg: ConductorConfig, name: str | None = None, run=subprocess.run) -> str:
    """Copy one label set (default: the default set) to Aoraki; returns a log line."""
    name = name or default_set()
    if not name:
        raise ConductorError("no default label set; give one with --set or make one default")
    lset = LabelSet.load(name)
    dest = remote_dir(cfg, name)
    env = ssh_env()
    ssh = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=30", cfg.cluster.ssh_host]
    r = run([*ssh, f"mkdir -p {shlex.quote(dest)}"], capture_output=True, text=True, env=env)
    if r.returncode == 0:
        r = run(rsync_command(cfg, lset, dest), capture_output=True, text=True, env=env)
    if r.returncode != 0:
        raise ConductorError(f"label sync of '{name}' failed: {(r.stderr or r.stdout)[-400:]}")
    sent = [ln.split(" ", 1)[1] for ln in r.stdout.splitlines() if ln.startswith("<f")]
    v = (lset.latest() or {}).get("version")
    what = f"{len(sent)} file(s): {', '.join(sent[:5])}" if sent else "nothing new"
    return f"{datetime.now():%Y-%m-%d %H:%M} {name} v{v} → {dest}: {what}"


def sync_locked(cfg: ConductorConfig, name: str | None = None) -> str:
    """``sync`` unless another copy of it is still running."""
    lock = LOCK.expanduser()
    lock.parent.mkdir(parents=True, exist_ok=True)
    with lock.open("w") as fh:
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return f"{datetime.now():%Y-%m-%d %H:%M} skipped: a sync is still running"
        return sync(cfg, name)


# ── cron ──────────────────────────────────────────────────────────
def cron_line(exe: str, hours: int = LABEL_SYNC_HOURS, config: str | None = None) -> str:
    log = LOG.expanduser()
    cfg = f" --config {shlex.quote(config)}" if config else ""
    return (
        f"0 */{int(hours)} * * * {shlex.quote(exe)}{cfg} label-sync "
        f">> {shlex.quote(str(log))} 2>&1 {CRON_TAG}"
    )


def with_cron_line(current: str, line: str | None) -> str:
    """Crontab text with our line replaced (or removed when ``line`` is None)."""
    keep = [ln for ln in current.splitlines() if not ln.rstrip().endswith(CRON_TAG)]
    return "\n".join([*keep, *([line] if line else [])]) + "\n"


def install_cron(exe: str, config: str | None = None, remove: bool = False, run=subprocess.run):
    """Add (or remove) the label-sync line in this user's crontab; returns the line."""
    if not shutil.which("crontab"):
        raise ConductorError("crontab is not installed")
    cur = run(["crontab", "-l"], capture_output=True, text=True)
    current = cur.stdout if cur.returncode == 0 else ""
    line = None if remove else cron_line(exe, config=config)
    LOG.expanduser().parent.mkdir(parents=True, exist_ok=True)
    r = run(["crontab", "-"], input=with_cron_line(current, line), capture_output=True, text=True)
    if r.returncode != 0:
        raise ConductorError(f"crontab: {r.stderr.strip()}")
    return line
