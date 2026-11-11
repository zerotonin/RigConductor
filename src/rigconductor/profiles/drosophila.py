# ╔══════════════════════════════════════════════════════════════════╗
# ║  Rig Conductor — profiles.drosophila                             ║
# ║  « triad arenas via ThermoKourt »                                ║
# ╠══════════════════════════════════════════════════════════════════╣
# ║  Drosophila heat-aggression triads; stages delegate to           ║
# ║  ThermoKourt's session runner.                                   ║
# ╚══════════════════════════════════════════════════════════════════╝
"""Drosophila triad arenas: everything delegates to ThermoKourt's session runner,
so a session started here can also be resumed with ``thermokourt-trial``."""

from __future__ import annotations

import csv
import json
import sqlite3
import subprocess
import sys
from pathlib import Path

from rigconductor import ConductorError
from rigconductor.config import ConductorConfig
from rigconductor.constants import N_ARENAS, TK_CROP_PADDING
from rigconductor.models import Model
from rigconductor.profiles.base import Profile, SessionContext

ROLES = ("male_1", "male_2", "female_decap")
STIMULI = {"triad": 3, "two_males": 2, "single_male": 1}
EXPERIMENTS = ("PILOT", "E1", "E2", "E3", "E4", "E5", "E6", "E7")


def experiment_label(exp_id: str) -> str:
    """Speaking name for pickers, e.g. ``E1 – Thermal performance curve``."""
    try:
        from thermokourt.db.seed import experiment_label as tk_label

        return tk_label(exp_id)
    except ImportError:
        return exp_id


def _tk():
    try:
        from thermokourt.trial import config as tkconfig
        from thermokourt.trial import runner as tkrunner
    except ImportError as e:
        raise ConductorError(
            "ThermoKourt is not installed in this environment "
            "(pip install -e ~/PyProjects/thermokourt)"
        ) from e
    return tkconfig, tkrunner


class DrosophilaTriad(Profile):
    name = "drosophila_triad"
    organism = "drosophila"
    title = "Drosophila triad arenas (ThermoKourt)"
    n_units = N_ARENAS
    unit_noun = "arena"

    # ── config bridges ─────────────────────────────────────────────────
    def tk_config(self, cfg: ConductorConfig):
        tkconfig, _ = _tk()
        p = self.config(cfg).get("thermokourt_rig")
        return tkconfig.RigConfig.load(p)

    def ddb_snapshot(self, cfg: ConductorConfig) -> Path | None:
        try:
            return self.tk_config(cfg).ddb_snapshot
        except Exception:
            return None

    def active_vials(self, cfg: ConductorConfig) -> dict[str, str]:
        """print code -> genotype name from the synced DrosoDB snapshot."""
        snap = self.ddb_snapshot(cfg)
        if not snap or not Path(snap).is_file():
            return {}
        from thermokourt.db import ddb as ddbmod

        con = ddbmod.open_snapshot(snap)
        rows = con.execute(
            "SELECT v.print_code, g.name FROM vial v JOIN genotype g ON g.id=v.genotype_id "
            "WHERE v.is_active = 1 ORDER BY v.print_code"
        ).fetchall()
        con.close()
        return {r[0]: r[1] for r in rows}

    def vial_catalogue(self, cfg: ConductorConfig) -> list[dict]:
        """Active vials with what the shelf shows: code, genotype, location, generation,
        owner, created. Empty list when no snapshot is available."""
        snap = self.ddb_snapshot(cfg)
        if not snap or not Path(snap).is_file():
            return []
        con = sqlite3.connect(f"file:{snap}?mode=ro", uri=True)
        con.row_factory = sqlite3.Row
        rows = con.execute(
            """SELECT v.print_code AS code, g.name AS genotype, g.is_wildtype AS wildtype,
                      COALESCE(o.name, '') AS location, v.generation AS generation,
                      COALESCE(u.full_name, u.username, '') AS owner,
                      substr(v.created_at, 1, 10) AS created
               FROM vial v JOIN genotype g ON g.id = v.genotype_id
               LEFT JOIN org_unit o ON o.id = v.org_unit_id
               LEFT JOIN user u ON u.id = v.owner_id
               WHERE v.is_active = 1
               ORDER BY g.name, v.print_code"""
        ).fetchall()
        con.close()
        return [dict(r) for r in rows]

    # ── metadata ───────────────────────────────────────────────────────
    def validate_metadata(self, meta: dict, cfg: ConductorConfig) -> list[str]:
        errs = []
        if meta.get("experiment_id") not in EXPERIMENTS:
            errs.append(f"experiment must be one of {', '.join(EXPERIMENTS)}")
        if not meta.get("group_code"):
            errs.append("group code is required")
        if meta.get("test_temp_c") is None:
            errs.append("test temperature is required")
        n_roles = STIMULI.get(meta.get("stimulus", "triad"))
        if n_roles is None:
            errs.append(f"stimulus must be one of {', '.join(STIMULI)}")
        arenas = meta.get("arenas") or {}
        if not arenas:
            errs.append("tick at least one arena")
        vials = self.active_vials(cfg)
        for idx, flies in sorted(arenas.items(), key=lambda kv: int(kv[0])):
            if n_roles and len(flies) != n_roles:
                errs.append(f"arena A{int(idx):02d}: expected {n_roles} animals, got {len(flies)}")
            for f in flies:
                code = f.get("print_code")
                if not code:
                    errs.append(f"arena A{int(idx):02d}: print code for {f.get('role')} missing")
                elif vials and code not in vials:
                    errs.append(f"arena A{int(idx):02d}: {code} is not an active vial in DrosoDB")
        return errs

    def motif_metadata(self, meta: dict) -> dict:
        return {
            "conductor_profile": self.name,
            "experiment_id": meta.get("experiment_id"),
            "group_code": meta.get("group_code"),
            "test_temp_c": meta.get("test_temp_c"),
            "stimulus": meta.get("stimulus", "triad"),
            "operator": meta.get("operator"),
        }

    # ── ThermoKourt session ────────────────────────────────────────────
    def _session(self, ctx: SessionContext, model: Model | None = None):
        _, tkrunner = _tk()
        tkcfg = self.tk_config(ctx.cfg)
        if model is not None:
            tkcfg.cluster.sleap_run = model.run
        m = ctx.metadata
        spec = tkrunner.SessionSpec(
            recording_dir=ctx.recording_dir,
            experiment_id=m["experiment_id"],
            group_code=m["group_code"],
            test_temp_c=float(m["test_temp_c"]),
            stimulus=m.get("stimulus", "triad"),
            ramp_c_per_h=m.get("ramp_c_per_h"),
            rearing_temp_c=m.get("rearing_temp_c"),
            treatment=m.get("treatment", "none"),
            operator=m.get("operator"),
            notes=m.get("notes"),
            cross_label=m.get("cross_label"),
            temp_log=Path(m["temp_log"]) if m.get("temp_log") else None,
            arenas={int(k): v for k, v in (m.get("arenas") or {}).items()},
        )
        return tkrunner.Session(tkcfg, spec, log=ctx.log)

    def inverts_for(self, ctx: SessionContext, model: Model) -> bool | None:
        return bool(self.tk_config(ctx.cfg).cluster.invert)

    def cut(self, ctx: SessionContext) -> list[Path]:
        s = self._session(ctx)
        s.extract()
        return sorted(s.clips_dir.glob(f"{s.name}_arena_*.mp4"))

    def preview_image(self, ctx: SessionContext) -> Path | None:
        p = ctx.clips_dir / f"{ctx.recording_dir.name}_arenas.png"
        return p if p.is_file() else None

    def cluster_queue(self, ctx: SessionContext) -> tuple[str, str, list[int]] | None:
        name = ctx.recording_dir.name
        project = self.tk_config(ctx.cfg).cluster.project
        idxs = sorted(int(i) for i in (ctx.state.get("trials") or {}))
        return (f"{project}/pose/{name}", name, idxs) if idxs else None

    def unit_label(self, index: int) -> str:
        return f"A{index:02d}"

    def arena_layout_path(self, ctx: SessionContext) -> Path | None:
        here = ctx.clips_dir / f"{ctx.recording_dir.name}_arenas.json"
        if here.is_file():
            return here
        rig = Path(self.tk_config(ctx.cfg).arenas_json)
        return rig if rig.is_file() else None

    def save_arena_layout(self, ctx: SessionContext, layout) -> list[Path]:
        """Save for this recording and as the rig default (the next cut reads that)."""
        from rigconductor.arenas import save_layout

        layout.meta.setdefault("recording", ctx.recording_dir.name)
        here = ctx.clips_dir / f"{ctx.recording_dir.name}_arenas.json"
        return [
            save_layout(here, layout),
            save_layout(Path(self.tk_config(ctx.cfg).arenas_json), layout),
        ]

    @staticmethod
    def _forget_tk_stages(ctx: SessionContext, stages: tuple[str, ...]) -> None:
        """ThermoKourt keeps its own resume marks next to the recording."""
        tk = ctx.recording_dir / "thermokourt_session.json"
        if not tk.is_file():
            return
        st = json.loads(tk.read_text(encoding="utf-8"))
        for k in stages:
            st.get("done", {}).pop(k, None)
        st.pop("job_id", None)
        tk.write_text(json.dumps(st, indent=2), encoding="utf-8")

    def archive_analysis(self, ctx: SessionContext, label: str) -> str | None:
        """Poses (local and on Aoraki) and clips drawn from them go to
        ``archive/<model>_<time>/`` next to the recording and
        ``pose/_archive/<trial>.<model>_<time>`` on the cluster."""
        import shlex
        from datetime import datetime

        tag = f"{label}_{datetime.now():%Y%m%d_%H%M%S}"
        dest = ctx.recording_dir / "archive" / tag
        moved = []
        pose = ctx.recording_dir / "pose"
        drawn = sorted(ctx.results_dir.glob("clips_*")) if ctx.results_dir.is_dir() else []
        for src in ([pose] if pose.is_dir() else []) + [d for d in drawn if d.name != "clips"]:
            dest.mkdir(parents=True, exist_ok=True)
            src.rename(dest / src.name)
            moved.append(src.name)
        project = self.tk_config(ctx.cfg).cluster.project
        remote = f"{project}/pose/{ctx.recording_dir.name}"
        arch = f"{project}/pose/_archive/{ctx.recording_dir.name}.{tag}"
        ctx.ssh(
            f"if [ -d {shlex.quote(remote)} ]; then mkdir -p {shlex.quote(project)}/pose/_archive "
            f"&& mv {shlex.quote(remote)} {shlex.quote(arch)}; fi"
        )
        where = f"{dest} ({', '.join(moved)})" if moved else "nothing local"
        return f"{where}; on Aoraki {arch}"

    def reset_analysis(self, ctx: SessionContext) -> None:
        super().reset_analysis(ctx)
        self._forget_tk_stages(ctx, ("upload", "submit", "poll", "fetch", "score"))

    def reset_cut(self, ctx: SessionContext) -> Path | None:
        old = super().reset_cut(ctx)
        self._forget_tk_stages(
            ctx, ("extract", "register", "upload", "submit", "poll", "fetch", "score")
        )
        if old is not None:  # keep the layout so a cropped recording can be re-cut
            lay = old / f"{ctx.recording_dir.name}_arenas.json"
            if lay.is_file():
                ctx.clips_dir.mkdir(exist_ok=True)
                (ctx.clips_dir / lay.name).write_text(
                    lay.read_text(encoding="utf-8"), encoding="utf-8"
                )
        return old

    def arena_circles(self, ctx: SessionContext) -> list[tuple[float, float, float]] | None:
        """Arenas grown to what the clips will cover (ThermoKourt padding and its
        minimum clip size), so cropping the recording never cuts into a clip."""
        p = ctx.clips_dir / f"{ctx.recording_dir.name}_arenas.json"
        if not p.is_file():
            return None
        d = json.loads(p.read_text(encoding="utf-8"))
        ex = self.tk_config(ctx.cfg).extract
        pad = float(d.get("padding", TK_CROP_PADDING))
        half = getattr(ex, "min_size", 0) / 2
        return [
            (float(a["cx"]), float(a["cy"]), max(float(a["r"]) * (1 + pad), half))
            for a in d.get("arenas", [])
        ]

    def register(self, ctx: SessionContext) -> None:
        s = self._session(ctx)
        s.register()
        ctx.state["trials"] = s.state["trials"]

    def upload(self, ctx: SessionContext) -> None:
        self._session(ctx).upload()

    def submit(self, ctx: SessionContext, model: Model) -> str:
        s = self._session(ctx, model)
        s.submit()
        return str(s.state["job_id"])

    def poll(self, ctx: SessionContext, interval_s: float) -> bool:
        return self._session(ctx).poll(interval_s=interval_s) == "done"

    def fetch(self, ctx: SessionContext) -> list[Path]:
        s = self._session(ctx)
        s.fetch()
        return sorted(s.pose_dir.glob("*.h5"))

    def score(self, ctx: SessionContext, model: Model) -> None:
        s = self._session(ctx, model)
        s.score()
        # record which pose model produced the tracks
        from thermokourt.db.core import connect

        con = connect(s.cfg.db)
        con.execute(
            "INSERT OR IGNORE INTO annotators (kind, name, version) VALUES ('model', ?, ?)",
            (model.id, model.trained or ""),
        )
        con.commit()
        con.close()

    # ── offline relay ──────────────────────────────────────────────────
    TK_STATE = "thermokourt_session.json"

    def relay_files(self, ctx: SessionContext) -> dict[str, Path]:
        files = {}
        rec = ctx.recording_dir
        for name in ("metadata.yaml", self.TK_STATE):
            if (rec / name).is_file():
                files[name] = rec / name
        if self.TK_STATE not in files:
            raise ConductorError("run 'cut' and 'register' before packing for the relay")
        for c in sorted(ctx.clips_dir.glob(f"{rec.name}_arena_*.mp4")):
            files[f"clips/{c.name}"] = c
        return files

    def result_files(self, ctx: SessionContext) -> dict[str, Path]:
        rec = ctx.recording_dir
        files = {self.TK_STATE: rec / self.TK_STATE}
        pose = rec / "pose"
        for p in sorted(pose.glob("*.h5")) if pose.is_dir() else []:
            files[f"pose/{p.name}"] = p
        return files

    def adopt_relay_state(self, ctx: SessionContext, relay_profile_state: dict) -> None:
        """The relay's ThermoKourt state knows the job and the finished cluster stages;
        keep the rig's own spec and trials, take the rest."""
        import json

        p = ctx.recording_dir / self.TK_STATE
        st = json.loads(p.read_text(encoding="utf-8"))
        st["spec"]["recording_dir"] = str(ctx.recording_dir)
        p.write_text(json.dumps(st, indent=2), encoding="utf-8")
        super().adopt_relay_state(ctx, relay_profile_state)

    # ── export ─────────────────────────────────────────────────────────
    def export_files(self, ctx: SessionContext, blind: bool, clips: str) -> dict[str, Path]:
        tkcfg = self.tk_config(ctx.cfg)
        out = ctx.results_dir
        out.mkdir(exist_ok=True)
        trials = [tid for _, tid in sorted(ctx.state.get("trials", {}).items())]
        if not trials:
            raise ConductorError("nothing registered for this session; run 'register' first")
        db_out = out / ("db_export_blind.sqlite3" if blind else "db_export.sqlite3")
        _export_db(tkcfg.db, db_out, blind)
        files = {db_out.name: db_out}
        con = sqlite3.connect(tkcfg.db)
        con.row_factory = sqlite3.Row
        q = ",".join("?" * len(trials))
        files["events.csv"] = _csv(
            out / "events.csv",
            con.execute(
                f"""SELECT e.trial_id, a.name AS annotator, b.name AS behaviour,
                           fa.role AS actor, ft.role AS target, e.start_s, e.end_s, e.confidence
                    FROM behaviour_events e
                    JOIN annotators a ON a.annotator_id=e.annotator_id
                    JOIN behaviours b ON b.behaviour_id=e.behaviour_id
                    LEFT JOIN flies fa ON fa.fly_id=e.actor_fly_id
                    LEFT JOIN flies ft ON ft.fly_id=e.target_fly_id
                    WHERE e.trial_id IN ({q}) ORDER BY e.trial_id, e.start_s""",
                trials,
            ),
        )
        files["window_summary.csv"] = _csv(
            out / "window_summary.csv",
            con.execute(
                f"SELECT * FROM window_summary WHERE trial_id IN ({q}) "
                "ORDER BY trial_id, window_start_s",
                trials,
            ),
        )
        con.close()
        prev = self.preview_image(ctx)
        if prev:
            files["arenas.png"] = prev
        if clips == "plain":
            for c in sorted(ctx.clips_dir.glob("*_arena_*.mp4")):
                files[f"clips/{c.name}"] = c
        elif clips in ("skeleton", "events", "both"):
            for out in self.render_clips(ctx, clips):
                files[f"clips/{out.name}"] = out
        return files

    def render_clips(self, ctx: SessionContext, mode: str) -> list[Path]:
        """Annotated arena clips (``thermokourt-overlay``), drawn in parallel in child
        processes (OpenCV and Qt cannot share the GUI process). Reuses clips already
        drawn in the same mode."""
        from concurrent.futures import ThreadPoolExecutor

        from rigconductor.constants import RENDER_JOBS

        s = self._session(ctx)
        db = self.tk_config(ctx.cfg).db
        trials = {int(i): tid for i, tid in ctx.state.get("trials", {}).items()}
        con = sqlite3.connect(db)
        marks = ",".join("?" * len(trials))
        rows = con.execute(
            f"SELECT trial_id, fps FROM trials WHERE trial_id IN ({marks})", list(trials.values())
        )
        fps = {tid: float(f or 25.0) for tid, f in rows}
        con.close()
        out_dir = ctx.results_dir / f"clips_{mode}"
        jobs = []
        for idx, tid in sorted(trials.items()):
            clip = ctx.clips_dir / f"{ctx.recording_dir.name}_arena_{idx:02d}.mp4"
            out = out_dir / f"{clip.stem}_{mode}.mp4"
            if clip.is_file():
                jobs.append((idx, tid, clip, out))
        done: dict[int, float] = {i: (1.0 if o.is_file() else 0.0) for i, _, _, o in jobs}
        last = [0.0]

        def report():
            import time

            if time.monotonic() - last[0] >= 1.0 or all(v >= 1 for v in done.values()):
                frac = sum(done.values()) / max(len(done), 1)
                n_done = sum(v >= 1 for v in done.values())
                ctx.log(f"@progress render {frac:.4f} {n_done}/{len(done)} clips drawn")
                last[0] = time.monotonic()

        def draw(job):
            idx, tid, clip, out = job
            if out.is_file():
                return out
            h5 = s.pose_dir / f"{clip.stem}.h5"
            cmd = [
                sys.executable,
                "-m",
                "thermokourt.overlay.cli",
                str(clip),
                "-o",
                str(out),
                "--db",
                str(db),
                "--trial",
                tid,
                "--fps",
                f"{fps.get(tid, 25.0):g}",
                "--tag",
                f"{idx:02d}",
                *(["--h5", str(h5)] if h5.is_file() else []),
                *(["--skeleton"] if mode in ("skeleton", "both") else []),
                *(["--events"] if mode in ("events", "both") else []),
            ]
            if mode == "skeleton" and not h5.is_file():
                return clip  # no animals tracked: nothing to draw
            proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
            )
            tail = []
            for line in proc.stdout:
                if line.startswith("@progress "):
                    f = dict(kv.split("=", 1) for kv in line.split()[1:] if "=" in kv)
                    done[idx] = float(f.get("frac", 0))
                    report()
                else:
                    tail = (tail + [line.rstrip()])[-10:]
            if proc.wait() != 0:
                raise ConductorError(f"drawing arena {idx:02d} failed:\n" + "\n".join(tail))
            done[idx] = 1.0
            report()
            return out

        ctx.log(f"render: {len(jobs)} clips ({mode}) → {out_dir}")
        with ThreadPoolExecutor(RENDER_JOBS) as pool:
            return list(pool.map(draw, jobs))


def _export_db(src_path: Path, dst_path: Path, blind: bool) -> None:
    from thermokourt.db.cli import BLIND_DROP_TABLES
    from thermokourt.db.core import connect

    if dst_path.exists():
        dst_path.unlink()
    src = connect(src_path)
    dst = sqlite3.connect(dst_path)
    src.backup(dst)
    if blind:
        for t in BLIND_DROP_TABLES:
            dst.execute(f"DROP TABLE IF EXISTS {t}")
        dst.execute("UPDATE coded_groups SET ddb_genotype_id = NULL")
        dst.execute("UPDATE flies SET ddb_vial_id = NULL, ddb_print_code = NULL")
        dst.execute("INSERT OR REPLACE INTO schema_info VALUES ('blinded', '1')")
        dst.commit()
        dst.execute("VACUUM")
    dst.close()
    src.close()


def _csv(path: Path, cursor) -> Path:
    rows = cursor.fetchall()
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow([d[0] for d in cursor.description])
        w.writerows([tuple(r) for r in rows])
    return path
