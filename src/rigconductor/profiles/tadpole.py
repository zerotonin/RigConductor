# ╔══════════════════════════════════════════════════════════════════╗
# ║  Rig Conductor — profiles.tadpole                                ║
# ║  « 24-well plates via TadPose »                                  ║
# ╠══════════════════════════════════════════════════════════════════╣
# ║  Xenopus tadpoles in a 24-well plate.                            ║
# ╚══════════════════════════════════════════════════════════════════╝
"""Xenopus tadpoles in a 24-well plate (TadPose backend).

Metadata: one row per occupied well — ``well`` (A1…D6), ``clutch``, ``genotype``,
``drug``, ``dose``, ``stage``, ``notes`` — matching TadPose's ``tadpole_group`` /
``well_type`` vocabulary. The cluster side runs ``scripts/slurm/tadpose_track_assign.sh``
(track → ingest → features → assign to the canonical k = 36 prototypes).

Status: the metadata, upload, submit, poll, fetch and export paths are complete;
``cut`` (plate → 24 well crops) requires ``tadpose`` installed on this PC and is
pending the recording-rig decision (Rig Conductor dev plan §4).
"""

from __future__ import annotations

import csv
import json
import shlex
from collections import Counter
from pathlib import Path

from rigconductor import ConductorError
from rigconductor.config import ConductorConfig
from rigconductor.models import Model
from rigconductor.profiles.base import Profile, SessionContext

WELLS = tuple(f"{r}{c}" for r in "ABCD" for c in range(1, 7))
WELL_FIELDS = ("well", "clutch", "genotype", "drug", "dose", "stage", "notes")


def parse_plate_csv(text: str) -> list[dict]:
    """Plate map CSV with a header containing at least ``well``; unknown columns are kept
    in ``notes``."""
    rows = list(csv.DictReader(text.splitlines()))
    if not rows or "well" not in rows[0]:
        raise ConductorError("plate CSV needs a header with a 'well' column (A1…D6)")
    out = []
    for r in rows:
        well = (r.get("well") or "").strip().upper()
        if not well:
            continue
        d = {k: (r.get(k) or "").strip() for k in WELL_FIELDS}
        d["well"] = well
        extra = {k: v for k, v in r.items() if k not in WELL_FIELDS and v}
        if extra:
            d["notes"] = (d["notes"] + " " + json.dumps(extra)).strip()
        out.append(d)
    return out


def validate_wells(wells: list[dict]) -> list[str]:
    errs = []
    if not wells:
        errs.append("enter at least one occupied well")
    seen = Counter(w.get("well", "") for w in wells)
    for w, n in seen.items():
        if w not in WELLS:
            errs.append(f"'{w}' is not a well of a 24-well plate (A1…D6)")
        if n > 1:
            errs.append(f"well {w} listed {n} times")
    for w in wells:
        if not (w.get("genotype") or w.get("drug")):
            errs.append(
                f"well {w.get('well')}: give a genotype or a drug (what is being compared?)"
            )
    return errs


class Tadpole24Well(Profile):
    name = "tadpole_24well"
    organism = "xenopus"
    title = "Xenopus tadpoles, 24-well plate (TadPose)"
    n_units = 24
    unit_noun = "well"

    def _project(self, cfg: ConductorConfig) -> str:
        p = self.config(cfg).get("project", "tadpole_project")
        return p if p.startswith("/") else f"{cfg.cluster.lab_root}/{p}"

    def validate_metadata(self, meta: dict, cfg: ConductorConfig) -> list[str]:
        errs = validate_wells(meta.get("wells") or [])
        if not meta.get("experiment"):
            errs.append("experiment / series name is required")
        return errs

    def motif_metadata(self, meta: dict) -> dict:
        return {
            "conductor_profile": self.name,
            "experiment": meta.get("experiment"),
            "operator": meta.get("operator"),
            "n_wells": len(meta.get("wells") or []),
        }

    # ── stages ─────────────────────────────────────────────────────────
    def cut(self, ctx: SessionContext) -> list[Path]:
        try:
            import tadpose  # noqa: F401
        except ImportError as e:
            raise ConductorError(
                "Cutting a plate video into wells needs the `tadpose` package on this PC, "
                "which is not installed yet (decision pending: is the plate recorded on this "
                "rig? See 'Rig Conductor — Dev Plan' §4). Analysis of an already-split "
                "recording can continue from 'upload'."
            ) from e
        raise ConductorError("tadpose well split not wired yet")  # pragma: no cover

    def register(self, ctx: SessionContext) -> None:
        """Write the plate map next to the recording; TadPose builds its DB on the cluster."""
        ctx.results_dir.mkdir(exist_ok=True)
        plate = ctx.results_dir / "plate_map.json"
        plate.write_text(json.dumps(ctx.metadata, indent=2), encoding="utf-8")
        ctx.state["plate_map"] = str(plate)
        ctx.log(f"register: {len(ctx.metadata.get('wells', []))} wells → {plate.name}")

    def _remote(self, ctx: SessionContext, sub: str) -> str:
        return f"{self._project(ctx.cfg)}/conductor/{sub}/{ctx.session_id}"

    def upload(self, ctx: SessionContext) -> None:
        src = ctx.clips_dir if ctx.clips_dir.is_dir() else ctx.recording_dir
        rin, rout = self._remote(ctx, "in"), self._remote(ctx, "out")
        ctx.ssh(f"mkdir -p {shlex.quote(rin)} {shlex.quote(rout)}")
        ctx.run(
            [
                "rsync",
                "-a",
                "--partial",
                "--include=*.mp4",
                "--include=*.yaml",
                "--include=*.json",
                "--exclude=*",
                f"{src}/",
                f"{ctx.cfg.cluster.ssh_host}:{rin}/",
            ]
        )
        ctx.run(
            [
                "rsync",
                "-a",
                str(ctx.results_dir / "plate_map.json"),
                f"{ctx.cfg.cluster.ssh_host}:{rin}/",
            ]
        )
        n = int(ctx.ssh(f"ls {shlex.quote(rin)} | wc -l"))
        ctx.log(f"upload: {n} files on the cluster")

    def submit(self, ctx: SessionContext, model: Model) -> str:
        proj = self._project(ctx.cfg)
        cmd = (
            f"cd {shlex.quote(proj)} && sbatch --parsable "
            f"scripts/slurm/tadpose_track_assign.sh {shlex.quote(ctx.session_id)} "
            f"{shlex.quote(model.run)} {shlex.quote(model.env)}"
        )
        out = ctx.ssh(cmd)
        job = out.split(";")[0].strip()
        if not job.isdigit():
            raise ConductorError(f"sbatch did not return a job id: {out!r}")
        ctx.state["job_id"] = job
        return job

    def poll(self, ctx: SessionContext, interval_s: float) -> bool:
        import time

        from thermokourt.trial.runner import job_states_ok  # same parser as the fly side

        job = ctx.state.get("job_id")
        if not job:
            raise ConductorError("no job submitted")
        while True:
            q = ctx.ssh(f"squeue -j {job} -h -o '%T' | sort | uniq -c | tr '\\n' ' '")
            if not q:
                ok, counts = job_states_ok(ctx.ssh(f"sacct -j {job} -n -X -o State"))
                ctx.log(f"poll: job {job} finished: {counts}")
                return ok
            ctx.log(f"poll: job {job}: {q.strip()}")
            time.sleep(interval_s)

    def fetch(self, ctx: SessionContext) -> list[Path]:
        ctx.results_dir.mkdir(exist_ok=True)
        ctx.run(
            [
                "rsync",
                "-a",
                f"{ctx.cfg.cluster.ssh_host}:{self._remote(ctx, 'out')}/",
                f"{ctx.results_dir}/",
            ]
        )
        got = sorted(ctx.results_dir.glob("*_wells*.csv")) + sorted(
            ctx.results_dir.glob("*.sqlite3")
        )
        if not got:
            raise ConductorError("no results came back; check the SLURM log on the cluster")
        return got

    def score(self, ctx: SessionContext, model: Model) -> None:
        """Per-well prototype proportions from the assigned labels CSV(s)."""
        rows = []
        for p in sorted(ctx.results_dir.glob("*_labels.csv")):
            with open(p, encoding="utf-8") as f:
                for r in csv.DictReader(f):
                    rows.append(r)
        if not rows:
            ctx.log("score: no *_labels.csv in results; nothing summarised")
            return
        per_well: dict[str, Counter] = {}
        for r in rows:
            per_well.setdefault(r["well"], Counter())[r["label"]] += 1
        out = ctx.results_dir / "prototype_proportions.csv"
        labels = sorted({lab for c in per_well.values() for lab in c})
        with open(out, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["well", "n_frames", "model", *labels])
            for well, c in sorted(per_well.items()):
                n = sum(c.values())
                w.writerow([well, n, model.id, *[f"{c[lab] / n:.4f}" for lab in labels]])
        ctx.log(f"score: {len(per_well)} wells → {out.name}")

    def export_files(self, ctx: SessionContext, blind: bool, clips: str) -> dict[str, Path]:
        files = {p.name: p for p in ctx.results_dir.iterdir() if p.is_file()}
        if blind:
            files.pop("plate_map.json", None)
        if clips != "none" and ctx.clips_dir.is_dir():  # overlays: fly profile only
            for c in sorted(ctx.clips_dir.glob("*.mp4")):
                files[f"clips/{c.name}"] = c
        if not files:
            raise ConductorError("no results to export yet")
        return files
