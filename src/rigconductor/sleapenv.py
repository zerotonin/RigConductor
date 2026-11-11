# ╔══════════════════════════════════════════════════════════════════╗
# ║  Rig Conductor — sleapenv                                        ║
# ║  « find, install and use the SLEAP 1.6 environment »             ║
# ╠══════════════════════════════════════════════════════════════════╣
# ║  SLEAP lives in its own uv tool env (PyTorch); the Conductor     ║
# ║  only ever talks to it through child processes.                  ║
# ╚══════════════════════════════════════════════════════════════════╝
"""The SLEAP 1.6 environment used for labelling and pre-filling.

Installed per user with ``uv tool install "sleap[nn]"`` (CPU wheels, no sudo,
no GPU). Everything that needs sleap-io runs as
``<env>/bin/python -I labels/worker.py <task> <args.json>``.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from importlib import resources
from pathlib import Path

from rigconductor import ConductorError
from rigconductor.constants import (
    SLEAP_CPU_INDEX,
    SLEAP_PYTHON,
    SLEAP_TASK_TIMEOUT_S,
    SLEAP_TOOL,
    SLEAP_VERSION,
)

WORKER = Path(__file__).resolve().parent / "labels" / "worker.py"
ENV_VAR = "RIGCONDUCTOR_SLEAP_PYTHON"
DEFAULT_TOOL_DIR = Path("~/.local/share/uv/tools").expanduser()
Log = Callable[[str], None]
Progress = Callable[[float, str], None]


@dataclass
class SleapEnv:
    python: Path
    version: str = ""

    @property
    def bin_dir(self) -> Path:
        return self.python.parent

    def exe(self, name: str) -> Path:
        return self.bin_dir / name


# ── finding ───────────────────────────────────────────────────────
def uv_binary() -> Path | None:
    found = shutil.which("uv")
    if found:
        return Path(found)
    local = Path("~/.local/bin/uv").expanduser()
    return local if local.is_file() else None


def candidates() -> list[Path]:
    out = []
    if os.environ.get(ENV_VAR):
        out.append(Path(os.environ[ENV_VAR]).expanduser())
    uv = uv_binary()
    if uv:
        r = subprocess.run([str(uv), "tool", "dir"], capture_output=True, text=True, timeout=30)
        if r.returncode == 0 and r.stdout.strip():
            out.append(Path(r.stdout.strip()) / SLEAP_TOOL / "bin" / "python")
    out.append(DEFAULT_TOOL_DIR / SLEAP_TOOL / "bin" / "python")
    return out


def probe(python: Path, run=subprocess.run) -> str | None:
    """SLEAP version of an env, or None when it is not a working SLEAP 1.6 env."""
    if not python.is_file():
        return None
    code = "import sleap, sleap_io, sleap_nn; print(sleap.__version__)"
    try:
        r = run([str(python), "-I", "-c", code], capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.TimeoutExpired):
        return None
    lines = r.stdout.strip().splitlines()
    return lines[-1].strip() if r.returncode == 0 and lines else None


def find(run=subprocess.run) -> SleapEnv | None:
    for py in candidates():
        v = probe(py, run=run)
        if v:
            return SleapEnv(py, v)
    return None


def require() -> SleapEnv:
    env = find()
    if env is None:
        raise ConductorError(
            "SLEAP is not installed for this user. Use 'Set up SLEAP' in the labelling "
            "wizard (or run install_sleap.sh from a labelling package)."
        )
    return env


# ── running tasks ─────────────────────────────────────────────────
def parse_line(line: str) -> tuple[str, object]:
    """Classify one worker output line: ('progress', (frac, text)), ('result', dict), ('log', s)."""
    if line.startswith("@progress label "):
        parts = line.split(" ", 3)
        try:
            return "progress", (float(parts[2]), parts[3] if len(parts) > 3 else "")
        except (IndexError, ValueError):
            return "log", line
    if line.startswith("@result "):
        return "result", json.loads(line[len("@result ") :])
    return "log", line


def run_task(
    env: SleapEnv,
    task: str,
    args: dict,
    log: Log | None = None,
    progress: Progress | None = None,
    timeout_s: float = SLEAP_TASK_TIMEOUT_S,
) -> dict:
    """Run one worker task in the SLEAP env and return its result."""
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as f:
        json.dump(args, f)
        arg_file = f.name
    cmd = [str(env.python), "-I", str(WORKER), task, arg_file]
    result: dict | None = None
    tail: list[str] = []
    try:
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8"
        )
        for raw in proc.stdout:
            kind, val = parse_line(raw.rstrip("\n"))
            if kind == "progress" and progress:
                progress(*val)
            elif kind == "result":
                result = val
            elif kind == "log":
                tail = (tail + [val])[-40:]
                if log:
                    log(val)
        proc.wait(timeout=timeout_s)
    finally:
        Path(arg_file).unlink(missing_ok=True)
    if proc.returncode != 0 or result is None:
        raise ConductorError(f"SLEAP task '{task}' failed:\n" + "\n".join(tail[-15:]))
    return result


def open_labelling(env: SleapEnv, slp: Path) -> subprocess.Popen:
    """Start the SLEAP labelling GUI on ``slp`` (does not wait)."""
    exe = env.exe("sleap-label")
    if not exe.is_file():
        raise ConductorError(f"sleap-label missing in {env.bin_dir}")
    return subprocess.Popen([str(exe), str(slp)], start_new_session=True)


# ── installing ────────────────────────────────────────────────────
def installer_text(name: str) -> str:
    """An installer file with this Conductor's pinned versions filled in."""
    text = resources.files("rigconductor.installer").joinpath(name).read_text(encoding="utf-8")
    return (
        text.replace("@SLEAP_VERSION@", SLEAP_VERSION)
        .replace("@SLEAP_PYTHON@", SLEAP_PYTHON)
        .replace("@SLEAP_CPU_INDEX@", SLEAP_CPU_INDEX)
        .replace("@SLEAP_TOOL@", SLEAP_TOOL)
    )


def write_installer(dest_dir: Path) -> list[Path]:
    """Copy the installer and the launcher script into ``dest_dir``."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    out = []
    for name in ("install_sleap.sh", "label.sh"):
        p = dest_dir / name
        p.write_text(installer_text(name), encoding="utf-8")
        p.chmod(0o755)
        out.append(p)
    return out


def install(log: Log | None = None, reinstall: bool = False) -> SleapEnv:
    """Install SLEAP (CPU) for this user with the bundled script; returns the env."""
    tmp = Path(tempfile.mkdtemp(prefix="rc_sleap_"))
    script = write_installer(tmp)[0]
    env = dict(os.environ, SLEAP_REINSTALL="1" if reinstall else "0")
    proc = subprocess.Popen(
        ["bash", str(script)],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        env=env,
    )
    tail: list[str] = []
    for raw in proc.stdout:
        line = raw.rstrip("\n")
        tail = (tail + [line])[-30:]
        if log:
            log(line)
    proc.wait()
    shutil.rmtree(tmp, ignore_errors=True)
    if proc.returncode != 0:
        raise ConductorError("SLEAP installation failed:\n" + "\n".join(tail[-12:]))
    found = find()
    if found is None:
        raise ConductorError("SLEAP was installed but cannot be found; see the log above")
    return found
