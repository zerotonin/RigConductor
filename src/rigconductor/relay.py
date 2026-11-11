# ╔══════════════════════════════════════════════════════════════════╗
# ║  Rig Conductor — relay                                           ║
# ║  « offline rig, networked relay »                                ║
# ╠══════════════════════════════════════════════════════════════════╣
# ║  Pack a session, run its cluster stages elsewhere, bring the     ║
# ║  results back; every file checked by SHA-256.                    ║
# ╚══════════════════════════════════════════════════════════════════╝
"""Offline hand-over: a rig without network packs a session, a *relay* (any
Conductor install that can reach the cluster) runs the cluster stages, and the
results travel back on the same USB stick.

    rig    : pack_job(session, dest)            → <session>.job.zip
    relay  : import_job(cfg, zip) → session ; run upload → fetch
             pack_results(session, dest)        → <session>.results.zip
    rig    : import_results(session, zip)       ; then score → export

Packages are plain ZIP files (videos stored, not re-compressed) with a
``manifest.json`` holding the SHA-256 of every member; both ends verify.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import socket
import zipfile
from pathlib import Path

from rigconductor import ConductorError
from rigconductor.config import ConductorConfig
from rigconductor.session import ConductorSession, ConductorSpec, now_iso

FORMAT = 1
JOB = "conductor-job"
RESULTS = "conductor-results"
CLUSTER_STAGES = ("upload", "submit", "poll", "fetch")
_STORED = {".mp4", ".h5", ".zip", ".png", ".npz", ".sqlite3"}


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _write_zip(out: Path, manifest: dict, files: dict[str, Path], log) -> Path:
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(out.suffix + ".part")
    manifest["files"] = {}
    with zipfile.ZipFile(tmp, "w", allowZip64=True) as z:
        for rel, src in sorted(files.items()):
            src = Path(src)
            if not src.is_file():
                raise ConductorError(f"pack: missing {src}")
            comp = zipfile.ZIP_STORED if src.suffix in _STORED else zipfile.ZIP_DEFLATED
            z.write(src, f"payload/{rel}", compress_type=comp)
            manifest["files"][rel] = {"sha256": _sha256(src), "bytes": src.stat().st_size}
            log(f"pack: {rel} ({src.stat().st_size / 1e6:.1f} MB)")
        z.writestr("manifest.json", json.dumps(manifest, indent=2))
    tmp.replace(out)
    return out


def read_manifest(path: Path) -> dict:
    with zipfile.ZipFile(path) as z:
        try:
            m = json.loads(z.read("manifest.json"))
        except KeyError:
            raise ConductorError(f"{path.name} is not a Conductor package (no manifest)") from None
    if m.get("format") != FORMAT or m.get("kind") not in (JOB, RESULTS):
        raise ConductorError(
            f"{path.name}: unknown package format {m.get('format')}/{m.get('kind')}"
        )
    return m


def _extract_verified(path: Path, dest: Path, manifest: dict, log) -> None:
    dest = Path(dest).resolve()
    with zipfile.ZipFile(path) as z:
        for rel, info in manifest["files"].items():
            target = (dest / rel).resolve()
            if dest not in target.parents:
                raise ConductorError(f"package member escapes its folder: {rel}")
            target.parent.mkdir(parents=True, exist_ok=True)
            with z.open(f"payload/{rel}") as src, open(target, "wb") as out:
                shutil.copyfileobj(src, out, 1 << 20)
            if _sha256(target) != info["sha256"]:
                raise ConductorError(f"{rel} is corrupt in {path.name}; copy the package again")
            log(f"unpack: {rel}")


# ── rig side ─────────────────────────────────────────────────────────────


def pack_job(sess: ConductorSession, dest_dir: Path, log=print) -> Path:
    for st in ("record", "cut", "register"):
        if not sess.is_done(st):
            raise ConductorError(f"'{st}' is not done yet; finish it before packing")
    if not sess.spec.model_id:
        raise ConductorError("choose the model before packing (the relay submits with it)")
    ctx = sess._ctx()
    files = sess.profile.relay_files(ctx)
    manifest = {
        "format": FORMAT,
        "kind": JOB,
        "session_id": sess.spec.session_id,
        "profile": sess.spec.profile,
        "recording_name": ctx.recording_dir.name,
        "model_id": sess.spec.model_id,
        "force_model": sess.spec.force_model,
        "from_host": socket.gethostname(),
        "created": now_iso(),
        "conductor_state": sess.state,
    }
    out = _write_zip(Path(dest_dir) / f"{sess.spec.session_id}.job.zip", manifest, files, log)
    sess.state["relay"] = {"packed": now_iso(), "package": str(out)}
    sess._save()
    log(f"pack: wrote {out}")
    return out


def import_results(sess: ConductorSession, path: Path, log=print) -> None:
    m = read_manifest(path)
    if m["kind"] != RESULTS:
        raise ConductorError(f"{Path(path).name} is a job package, not results")
    if m["session_id"] != sess.spec.session_id:
        raise ConductorError(
            f"these results belong to {m['session_id']}, not {sess.spec.session_id}"
        )
    ctx = sess._ctx()
    _extract_verified(path, ctx.recording_dir, m, log)
    sess.profile.adopt_relay_state(ctx, m.get("profile_state", {}))
    for st in CLUSTER_STAGES:
        info = dict(m["stages"].get(st) or {})
        info.pop("at", None)
        sess._mark(st, via_relay=m.get("relay_host"), **info)
    sess.state["relay"] = {**sess.state.get("relay", {}), "results_imported": now_iso()}
    sess._save()
    log(f"import: results from relay {m.get('relay_host')}; continue with 'score'")


# ── relay side ───────────────────────────────────────────────────────────


def relay_root(cfg: ConductorConfig) -> Path:
    return cfg.sessions_dir.parent / "relay"


def import_job(cfg: ConductorConfig, path: Path, log=print) -> ConductorSession:
    m = read_manifest(path)
    if m["kind"] != JOB:
        raise ConductorError(f"{Path(path).name} is a results package, not a job")
    sid = m["session_id"]
    rec = relay_root(cfg) / sid / m["recording_name"]
    if (cfg.sessions_dir / f"{sid}.json").exists():
        raise ConductorError(f"session {sid} already exists on this machine")
    _extract_verified(path, rec, m, log)
    st = m["conductor_state"]
    spec = ConductorSpec.from_json(st["spec"])
    spec.recording_dir = str(rec)
    spec.export_device = spec.export_dir = None
    spec.model_id = m["model_id"]
    spec.force_model = bool(m.get("force_model"))
    sess = ConductorSession(cfg, spec, None, log=log)
    sess.state["done"] = {k: v for k, v in st["done"].items() if k in ("record", "cut", "register")}
    sess.state["profile_state"] = st.get("profile_state", {})
    sess.state["relay"] = {"role": "relay", "from_host": m.get("from_host"), "imported": now_iso()}
    sess._save()
    # ThermoKourt keeps its own state in the recording folder; point it here
    tk = rec / "thermokourt_session.json"
    if tk.is_file():
        d = json.loads(tk.read_text(encoding="utf-8"))
        d["spec"]["recording_dir"] = str(rec)
        tk.write_text(json.dumps(d, indent=2), encoding="utf-8")
    log(f"relay: imported {sid} from {m.get('from_host')} → {rec}")
    return sess


def run_cluster_stages(sess: ConductorSession, poll_interval_s: float | None = None) -> None:
    sess.run(CLUSTER_STAGES, poll_interval_s=poll_interval_s)


def pack_results(sess: ConductorSession, dest_dir: Path, log=print) -> Path:
    missing = [s for s in CLUSTER_STAGES if not sess.is_done(s)]
    if missing:
        raise ConductorError(f"cluster stages not finished: {', '.join(missing)}")
    ctx = sess._ctx()
    files = sess.profile.result_files(ctx)
    manifest = {
        "format": FORMAT,
        "kind": RESULTS,
        "session_id": sess.spec.session_id,
        "profile": sess.spec.profile,
        "relay_host": socket.gethostname(),
        "created": now_iso(),
        "stages": {s: sess.state["done"][s] for s in CLUSTER_STAGES},
        "profile_state": sess.state.get("profile_state", {}),
    }
    out = _write_zip(Path(dest_dir) / f"{sess.spec.session_id}.results.zip", manifest, files, log)
    log(f"relay: wrote {out}; take it back to the rig and import it there")
    return out


def find_packages(root: Path) -> list[Path]:
    """Conductor packages on a mounted stick (top level and one folder down)."""
    root = Path(root)
    pats = ("*.job.zip", "*.results.zip")
    out = [p for pat in pats for p in root.glob(pat)]
    out += [p for pat in pats for p in root.glob(f"*/{pat}")]
    return sorted(set(out))
