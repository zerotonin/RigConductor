# ╔══════════════════════════════════════════════════════════════════╗
# ║  Rig Conductor — labels.train                                    ║
# ║  « from a label-set version to a registered model on Aoraki »    ║
# ╠══════════════════════════════════════════════════════════════════╣
# ║  split here → upload → sleap_nn_train.sh → eval.json → registry. ║
# ╚══════════════════════════════════════════════════════════════════╝
"""Fine-tune a pose model on a label-set version and decide whether it replaces
the current one.

Promotion rule (Decisions 2026-11-11): the new model is offered as the default
only if it is not worse than the current model on any validation set and better
on at least one; Bart confirms it in the wizard.
"""

from __future__ import annotations

import json
import re
import shlex
import subprocess
from dataclasses import dataclass, field, fields
from datetime import date
from pathlib import Path, PurePosixPath

import yaml

from rigconductor import ConductorError, sleapenv
from rigconductor.config import ConductorConfig
from rigconductor.constants import LABEL_TEST_SHARE, NN_ENV_REMOTE, SSH_TIMEOUT_S, TRAIN_EPOCHS
from rigconductor.labels.store import LabelSet

TRAIN_SCRIPTS = (  # thermokourt repo → $PROJ/scripts
    "slurm/sleap_nn_train.sh",
    "slurm/sleap_track.sh",
    "sleap/nn_config.py",
    "sleap/nn_eval.py",
)
EPOCH_RE = re.compile(r"Epoch (\d+)")
METRIC = "mAP"
HEADS = ("centroid", "centered_instance")  # trained one after the other
# training_log.csv → "epoch,train loss,val loss,seconds" (column names differ by version)
CURVE_AWK = (
    "NR==1{for(i=1;i<=NF;i++){"
    'if($i=="epoch")e=i;'
    'if($i=="train/loss"||$i=="train_loss")t=i;'
    'if($i=="val/loss"||$i=="val_loss")v=i;'
    'if($i=="train/time")s=i'
    "};next}"
    '{print $e","(t?$t:"")","(v?$v:"")","(s?$s:"")}'
)
# the early-stopping patience (the scheduler has a "patience" too, earlier in the file)
PATIENCE_AWK = "/early_stopping:/{f=1} f&&/patience:/{print $2; exit}"


@dataclass
class TrainPlan:
    set_name: str
    version: int
    run: str
    base_run: str
    compare_run: str
    remote_dir: str = ""
    val: dict[str, str] = field(default_factory=dict)  # scale → remote file
    job_id: str = ""
    excluded: list[str] = field(default_factory=list)  # recordings left out of training
    shared: list[str] = field(default_factory=list)  # recordings split by frame

    @classmethod
    def from_entry(cls, entry: dict) -> TrainPlan:
        """Rebuild a plan from its record in the label set's ``models`` list."""
        return cls(**{f.name: entry[f.name] for f in fields(cls) if f.name in entry})


def project_root(cfg: ConductorConfig, profile: str) -> str:
    """Aoraki project folder (the registry lives in ``<project>/models/``)."""
    explicit = cfg.profile(profile).get("project_root")
    if explicit:
        return explicit
    reg = cfg.registry_remote(profile)
    if not reg:
        raise ConductorError(f"profile '{profile}' has no model registry on the cluster")
    return str(PurePosixPath(reg).parents[1])


def run_name(lset: LabelSet, version: int, today: date | None = None) -> str:
    """``<set>_v<N>_<date>``, with b, c, … added when that run exists already."""
    name = f"{lset.name}_v{version}_{(today or date.today()):%Y%m%d}"
    taken = {m.get("run") for m in lset.data["models"]}
    suffixes = iter("bcdefghijklmnopqrstuvwxyz")
    out = name
    while out in taken:
        out = name + next(suffixes)
    return out


def _ssh(cfg: ConductorConfig, remote: str, run=subprocess.run, timeout=SSH_TIMEOUT_S) -> str:
    r = run(
        ["ssh", "-o", "BatchMode=yes", cfg.cluster.ssh_host, remote],
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if r.returncode != 0:
        raise ConductorError(f"Aoraki: {(r.stderr or r.stdout).strip()[-400:]}")
    return r.stdout


def _rsync(cfg: ConductorConfig, files: list[Path], remote_dir: str, run=subprocess.run) -> None:
    _ssh(cfg, f"mkdir -p {shlex.quote(remote_dir)}", run=run)
    r = run(
        ["rsync", "-a", *[str(f) for f in files], f"{cfg.cluster.ssh_host}:{remote_dir}/"],
        capture_output=True,
        text=True,
    )
    if r.returncode != 0:
        raise ConductorError(f"upload to Aoraki failed: {r.stderr.strip()[-400:]}")


# ── steps ─────────────────────────────────────────────────────────
def summarise(lset: LabelSet, version: int, env, log=None, progress=None) -> list[dict]:
    """Per-recording facts of a version (cached: version files never change)."""
    out_dir = lset.path / "train" / f"v{version}"
    cache = out_dir / "summary.json"
    if cache.is_file():
        rows = json.loads(cache.read_text(encoding="utf-8"))
        if all(Path(r["example"]).is_file() for r in rows):
            return rows
    res = sleapenv.run_task(
        env,
        "summary",
        {"slp": str(lset.version_file(version)), "out_dir": str(out_dir / "examples")},
        log=log,
        progress=progress,
    )
    cache.write_text(json.dumps(res["recordings"], indent=1), encoding="utf-8")
    return res["recordings"]


def prepare(
    lset: LabelSet, version: int, env, exclude=(), log=None, progress=None
) -> dict[str, Path]:
    """Split a version into train / validation files (by recording, or by
    frame for the recordings set to *All*).

    Args:
        exclude: Recordings left out of training altogether.
    """
    out_dir = lset.path / "train" / f"v{version}"
    out_dir.mkdir(parents=True, exist_ok=True)
    for old in out_dir.glob("*.pkg.slp"):
        old.unlink()
    res = sleapenv.run_task(
        env,
        "split",
        {
            "slp": str(lset.version_file(version)),
            "val": lset.data["validation"],
            "exclude": list(exclude),
            "shared": lset.shared(),
            "test_share": LABEL_TEST_SHARE,
            "out_dir": str(out_dir),
        },
        log=log,
        progress=progress,
    )
    files = {part: out_dir / f"{part}.pkg.slp" for part, n in res["counts"].items() if n}
    if "train" not in files:
        raise ConductorError("no training frames left after holding out the validation recordings")
    return files


def upload(
    cfg: ConductorConfig,
    profile: str,
    lset: LabelSet,
    plan: TrainPlan,
    files: dict[str, Path],
    scripts_root: Path,
    run=subprocess.run,
) -> TrainPlan:
    """Send the split and the training scripts to Aoraki; fills in remote paths."""
    proj = project_root(cfg, profile)
    plan.remote_dir = f"{proj}/sleap_labels/{lset.name}/v{plan.version}"
    _rsync(cfg, list(files.values()), plan.remote_dir, run=run)
    plan.val = {
        part[len("val_") :]: f"{plan.remote_dir}/{p.name}"
        for part, p in files.items()
        if part.startswith("val_")
    }
    for sub in ("slurm", "sleap"):
        mine = [scripts_root / s for s in TRAIN_SCRIPTS if s.startswith(sub + "/")]
        _rsync(cfg, mine, f"{proj}/scripts/{sub}", run=run)
    return plan


def submit_command(proj: str, plan: TrainPlan, epochs: int = TRAIN_EPOCHS) -> str:
    vals = " ".join(shlex.quote(f"{k}={v}") for k, v in sorted(plan.val.items()))
    return (
        f"cd {shlex.quote(proj)} && mkdir -p logs && EPOCHS={int(epochs)} "
        f"NN_ENV={shlex.quote(proj + '/' + NN_ENV_REMOTE)} sbatch --parsable "
        f"scripts/slurm/sleap_nn_train.sh {shlex.quote(plan.remote_dir + '/train.pkg.slp')} "
        f"{shlex.quote(plan.run)} {shlex.quote(plan.base_run)} {shlex.quote(plan.compare_run)} "
        f"{vals}"
    ).rstrip()


def submit(
    cfg: ConductorConfig, profile: str, plan: TrainPlan, epochs=TRAIN_EPOCHS, run=subprocess.run
):
    out = _ssh(cfg, submit_command(project_root(cfg, profile), plan, epochs), run=run)
    m = re.search(r"^(\d+)", out.strip().splitlines()[-1] if out.strip() else "")
    if not m:
        raise ConductorError(f"sbatch gave no job id: {out[-300:]!r}")
    plan.job_id = m.group(1)
    return plan


def status_command(proj: str, plan: TrainPlan) -> str:
    q = shlex.quote
    models = f"{proj}/models/{plan.run}"
    log = f"{proj}/logs/sleap-nn-train_{plan.job_id}.log"
    curves = "".join(
        f"echo CURVE={head}; awk -F, {q(CURVE_AWK)} {q(f'{models}.{head}/training_log.csv')}"
        f" 2>/dev/null; echo PATIENCE=$(awk {q(PATIENCE_AWK)} "
        f"{q(f'{models}.{head}/training_config.yaml')} 2>/dev/null); "
        "echo CURVE_END; "
        for head in HEADS
    )
    return (
        f"echo STATE=$(sacct -j {q(plan.job_id)} -n -X -o State 2>/dev/null | head -n1 | xargs); "
        f"test -f {q(models + '.done')} && echo DONE; "
        f"test -f {q(models + '.failed')} && echo FAILED=$(cat {q(models + '.failed')}); "
        f"echo TRAINED=$(grep -c ' trained in ' {q(log)} 2>/dev/null); "
        + curves
        + f"tail -c 4000 {q(log)} 2>/dev/null | tr '\\r' '\\n' | tail -n 20"
    )


def _num(text: str) -> float | None:
    try:
        return float(text)
    except ValueError:
        return None


def _curve_row(line: str) -> tuple | None:
    """``epoch,train,val,seconds`` → (epoch, train, val, seconds); missing values None."""
    parts = (line.split(",") + ["", "", "", ""])[:4]
    epoch = _num(parts[0])
    if epoch is None:
        return None
    return (int(epoch), *(_num(x) for x in parts[1:]))


def parse_status(text: str) -> dict:
    """State, done/failed flags, loss curves per head, last epoch seen and a log tail.

    ``curves`` maps a head to rows (epoch, train loss, val loss, seconds);
    ``patience`` a head to its early-stopping patience (epochs without a better
    validation loss); ``trained`` is how many heads have finished training.
    """
    st = {
        "state": "",
        "done": False,
        "failed": "",
        "epoch": None,
        "trained": 0,
        "curves": {},
        "patience": {},
        "tail": [],
    }
    head = None
    for line in text.splitlines():
        if head is not None:
            if line == "CURVE_END":
                head = None
            elif line.startswith("PATIENCE="):
                n = _num(line[len("PATIENCE=") :].strip())
                if n is not None:
                    st["patience"][head] = int(n)
            elif (row := _curve_row(line)) is not None:
                st["curves"].setdefault(head, []).append(row)
        elif line.startswith("CURVE="):
            head = line[len("CURVE=") :].strip()
        elif line.startswith("TRAINED="):
            st["trained"] = int(_num(line[len("TRAINED=") :].strip() or "0") or 0)
        elif line.startswith("STATE="):
            st["state"] = line[len("STATE=") :].strip()
        elif line == "DONE":
            st["done"] = True
        elif line.startswith("FAILED="):
            st["failed"] = line[len("FAILED=") :].strip() or "failed"
        else:
            st["tail"].append(line)
            for m in EPOCH_RE.finditer(line):
                st["epoch"] = int(m.group(1))
    if st["state"].startswith(("FAILED", "CANCELLED", "TIMEOUT", "OUT_OF_ME", "NODE_FAIL")):
        st["failed"] = st["failed"] or st["state"]
    return st


def status(cfg: ConductorConfig, profile: str, plan: TrainPlan, run=subprocess.run) -> dict:
    return parse_status(_ssh(cfg, status_command(project_root(cfg, profile), plan), run=run))


def fetch_eval(cfg: ConductorConfig, profile: str, run_id: str, run=subprocess.run) -> list[dict]:
    path = f"{project_root(cfg, profile)}/models/{run_id}.eval.json"
    return json.loads(_ssh(cfg, f"cat {shlex.quote(path)}", run=run))


# ── judging and registering ───────────────────────────────────────
def compare(rows: list[dict], new: str, old: str, metric: str = METRIC) -> dict:
    """Side-by-side numbers per validation set and the promotion verdict."""
    by = {(r["model"], r["set"]): r for r in rows}
    sets = sorted({r["set"] for r in rows})
    table, better, worse = [], 0, 0
    for s in sets:
        a = (by.get((new, s)) or {}).get(metric)
        b = (by.get((old, s)) or {}).get(metric)
        table.append(
            {"set": s, "new": a, "old": b, "frames": (by.get((new, s)) or {}).get("frames", 0)}
        )
        if a is None or b is None:
            continue
        better += a > b
        worse += a < b
    ok = bool(sets) and better > 0 and worse == 0 and new != old
    return {"metric": metric, "table": table, "promotable": ok}


def registry_entry(
    run_id: str,
    base: dict,
    lset: LabelSet,
    version: int,
    verdict: dict,
    promote: bool,
    today: date | None = None,
    excluded: list[str] | None = None,
    shared: list[str] | None = None,
) -> dict:
    """Registry entry for a trained model (expects inherited from its base)."""
    frames = sum(r["frames"] or 0 for r in verdict["table"])
    sets = {r["set"]: r["new"] for r in verdict["table"] if r["new"] is not None}
    entry = {
        "id": run_id,
        "organism": base.get("organism", "drosophila"),
        "kind": base.get("kind", "sleap-topdown"),
        "run": run_id,
        "env": "sleap-nn",
        "trained": str(today or date.today()),
        "validation": {
            "frames": frames,
            METRIC: max(sets.values()) if sets else None,
            "sets": sets,
        },
        "grayscale_input": base.get("grayscale_input", True),
        "expects": dict(base.get("expects") or {}),
        "label_set": {"name": lset.name, "version": version},
        "notes": f"sleap-nn fine-tune of {base.get('id')} on {lset.name} v{version}",
    }
    if excluded:
        entry["label_set"]["left_out"] = sorted(excluded)
    if shared:
        entry["label_set"]["split_by_frame"] = sorted(shared)
        entry["label_set"]["test_share"] = LABEL_TEST_SHARE
    entry["expects"].pop("fps", None)  # trained on several recordings
    if promote:
        entry["default"] = True
    return entry


def append_registry(cfg: ConductorConfig, profile: str, entry: dict, run=subprocess.run) -> None:
    """Append ``entry`` to the registry on Aoraki (backup kept next to it)."""
    reg = cfg.registry_remote(profile)
    text = "\n" + yaml.safe_dump([entry], sort_keys=False)
    q = shlex.quote(reg)
    remote = (
        f"cp {q} {q}.bak_$(date +%Y%m%d%H%M%S) && cat >> {q} <<'RIGCONDUCTOR_EOF'\n"
        f"{text}RIGCONDUCTOR_EOF\n"
    )
    _ssh(cfg, remote, run=run)


# ── models on this PC (for pre-filling) ───────────────────────────
def local_models_dir() -> Path:
    from rigconductor.constants import LOCAL_MODELS_DIR

    return Path(LOCAL_MODELS_DIR).expanduser()


def local_runs(root: Path | None = None) -> list[str]:
    """Model runs with both halves (centroid + centred instance) on this PC."""
    root = root or local_models_dir()
    if not root.is_dir():
        return []
    cen = {p.name[: -len(".centroid")] for p in root.glob("*.centroid") if p.is_dir()}
    ci = {p.name[: -len(".centered_instance")] for p in root.glob("*.centered_instance")}
    return sorted(cen & ci)


def model_dirs(run_id: str, root: Path | None = None) -> list[str]:
    root = root or local_models_dir()
    return [str(root / f"{run_id}.centroid"), str(root / f"{run_id}.centered_instance")]


def fetch_model(cfg: ConductorConfig, profile: str, run_id: str, run=subprocess.run) -> None:
    """Copy a model pair from Aoraki to this PC (only the files inference needs)."""
    proj = project_root(cfg, profile)
    dest = local_models_dir()
    dest.mkdir(parents=True, exist_ok=True)
    for half in ("centroid", "centered_instance"):
        r = run(
            [
                "rsync",
                "-a",
                "--exclude",
                "*.slp",
                "--exclude",
                "viz",
                f"{cfg.cluster.ssh_host}:{proj}/models/{run_id}.{half}",
                f"{dest}/",
            ],
            capture_output=True,
            text=True,
        )
        if r.returncode != 0:
            raise ConductorError(f"could not fetch {run_id}.{half}: {r.stderr.strip()[-300:]}")
