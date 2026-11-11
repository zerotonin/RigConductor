# ╔══════════════════════════════════════════════════════════════════╗
# ║  Rig Conductor — labels.worker                                   ║
# ║  « the half that runs inside the SLEAP 1.6 environment »         ║
# ╠══════════════════════════════════════════════════════════════════╣
# ║  Started as `<sleap python> -I worker.py <task> <args.json>`;    ║
# ║  prints @progress lines and one @result JSON line at the end.    ║
# ╚══════════════════════════════════════════════════════════════════╝
"""SLEAP-side tasks of the labelling wizard.

The Conductor never imports sleap-io itself (it lives in its own uv env with
PyTorch). It writes the arguments to a JSON file and runs this script with the
SLEAP env's Python; see :mod:`rigconductor.sleapenv`.

Tasks: ``inspect``, ``seed``, ``select``, ``build``, ``check``, ``harvest``,
``merge``, ``summary``, ``split``. Every frame in a label set is identified by a hash of its pixels,
so provenance survives SLEAP re-saving, merging and splitting.
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
import tempfile
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # rigconductor (pure modules)

import numpy as np  # noqa: E402

from rigconductor.labels import rules, select  # noqa: E402

PROV_KEY = "rigconductor"
CLIP_RE = re.compile(r"(?P<rec>.+)_arena_(?P<arena>\d+)")


# ── helpers ───────────────────────────────────────────────────────
def emit_progress(frac: float, detail: str = "") -> None:
    print(f"@progress label {frac:.3f} {detail}", flush=True)


def emit_result(obj: dict) -> None:
    print("@result " + json.dumps(obj), flush=True)


def as_gray(img: np.ndarray) -> np.ndarray:
    img = np.asarray(img)
    if img.ndim == 3:
        img = img[..., 0]
    return np.ascontiguousarray(img.astype(np.uint8))


def frame_hash(img: np.ndarray) -> str:
    return hashlib.sha1(as_gray(img).tobytes()).hexdigest()[:16]


def clip_scale(video) -> str:
    """Camera scale of an arena clip from its width (arena + ThermoKourt padding)."""
    from rigconductor.constants import TK_CROP_PADDING
    from rigconductor.labels.store import scale_name

    return scale_name(video.shape[2] / (1 + 2 * TK_CROP_PADDING))


def provenance(labels) -> dict:
    return dict((labels.provenance or {}).get(PROV_KEY, {}).get("frames", {}))


def set_provenance(labels, frames: dict, **extra) -> None:
    labels.provenance[PROV_KEY] = {**extra, "frames": frames}


def user_frames(labels) -> list:
    return [lf for lf in labels.labeled_frames if lf.user_instances]


def hashes_of(labels, frames: list) -> list[str]:
    return [frame_hash(lf.video[lf.frame_idx]) for lf in frames]


def stats(labels, prov: dict) -> dict:
    """Counts for the registry: frames, flies, per recording, scale, labeller."""
    lfs = user_frames(labels)
    hs = hashes_of(labels, lfs)
    info = [prov.get(h, {}) for h in hs]
    return {
        "frames": len(lfs),
        "instances": sum(len(lf.user_instances) for lf in lfs),
        "recordings": dict(Counter(i.get("recording", "?") for i in info)),
        "scales": dict(Counter(i.get("scale", "?") for i in info)),
        "labellers": dict(Counter(i.get("labeller", "?") for i in info)),
        "frames_index": [
            [i.get("recording", "?"), int(i.get("arena", -1)), int(i.get("source_frame", -1))]
            for i in info
        ],
    }


def keep_user_only(labels, frames: list) -> None:
    """Reduce ``labels`` to ``frames`` with only hand-placed instances."""
    for lf in frames:
        lf.instances = list(lf.user_instances)
    labels.labeled_frames = frames
    labels.suggestions = []
    labels.clean(frames=True, skeletons=True, tracks=True, videos=True)


def save_embedded(labels, out: str, embed: str = "user") -> None:
    import sleap_io as sio

    Path(out).parent.mkdir(parents=True, exist_ok=True)
    sio.save_slp(labels, out, embed=embed, verbose=False)


# ── tasks ─────────────────────────────────────────────────────────
def frame_states(labels) -> dict:
    """Frames to label (suggestions and labelled frames) by state.

    ``labelled``: hand-placed instances; ``predicted``: only model predictions;
    ``empty``: nothing yet.
    """
    vid = {id(v): i for i, v in enumerate(labels.videos)}
    keys = {(vid[id(s.video)], s.frame_idx) for s in labels.suggestions}
    by = {(vid[id(lf.video)], lf.frame_idx): lf for lf in labels.labeled_frames}
    keys |= set(by)
    user = sum(1 for k in keys if k in by and by[k].user_instances)
    pred = sum(
        1 for k in keys if k in by and not by[k].user_instances and by[k].predicted_instances
    )
    return {
        "total": len(keys),
        "labelled": user,
        "predicted": pred,
        "empty": len(keys) - user - pred,
    }


def task_inspect(a: dict) -> dict:
    import sleap_io as sio

    labels = sio.load_slp(a["slp"])
    lfs = labels.labeled_frames
    return {
        "states": frame_states(labels),
        "frames": len(lfs),
        "user_frames": len(user_frames(labels)),
        "user_instances": sum(len(lf.user_instances) for lf in lfs),
        "predicted_instances": sum(len(lf.predicted_instances) for lf in lfs),
        "videos": len(labels.videos),
        "nodes": labels.skeletons[0].node_names if labels.skeletons else [],
        "provenance_frames": len(provenance(labels)),
    }


def clip_info(lf, scale: str | None, labeller: str, source: str) -> dict:
    """Provenance of a frame labelled straight on an arena clip (not a package)."""
    stem = Path(lf.video.filename).stem
    m = CLIP_RE.match(stem)
    return {
        "recording": m["rec"] if m else stem,
        "arena": int(m["arena"]) if m else -1,
        "source_frame": int(lf.frame_idx),
        "scale": scale or clip_scale(lf.video),
        "labeller": labeller,
        "package": source,
        "reason": source,
    }


def task_seed(a: dict) -> dict:
    """First version of a set from an existing SLEAP project (hand labels only)."""
    import sleap_io as sio

    labels = sio.load_slp(a["src"])
    lfs = user_frames(labels)
    prov = {}
    for k, lf in enumerate(lfs):
        prov[frame_hash(lf.video[lf.frame_idx])] = clip_info(
            lf, a.get("scale"), a.get("labeller", "seed"), "seed"
        )
        emit_progress((k + 1) / max(len(lfs), 1), "reading frames")
    keep_user_only(labels, lfs)
    labels.skeletons = labels.skeletons[:1] if labels.skeletons else labels.skeletons
    sk = labels.skeletons[0]
    for lf in lfs:
        for inst in lf.instances:
            inst.skeleton = sk
    set_provenance(labels, prov, set=a.get("set", ""), version=1)
    save_embedded(labels, a["out"])
    return stats(sio.load_slp(a["out"]), prov)


def _pose_arrays(path: str):
    import h5py

    with h5py.File(path, "r") as h:
        nodes = [n.decode() if isinstance(n, bytes) else str(n) for n in h["node_names"][:]]
        return h["tracks"][:], h["point_scores"][:], h["track_occupancy"][:], nodes


def task_select(a: dict) -> dict:
    """Pick frames per arena clip."""
    import sleap_io as sio

    exclude = {(r, int(ar), int(f)) for r, ar, f in a.get("exclude", [])}
    out = []
    clips = a["clips"]
    for k, c in enumerate(clips):
        fps = float(c.get("fps") or 25.0)
        gap = max(1, int(round(a.get("min_gap_s", 2.0) * fps)))
        banned = {f for r, ar, f in exclude if r == c["recording"] and ar == int(c["arena"])}
        unsure = contact = None
        n_frames = None
        if c.get("pose") and Path(c["pose"]).is_file():
            tracks, scores, occ, nodes = _pose_arrays(c["pose"])
            n_frames = tracks.shape[-1]
            unsure = select.unsure_scores(scores, occ, int(a.get("n_expected", 3)))
            contact = select.contact_scores(tracks, nodes)
        if n_frames is None:
            n_frames = int(sio.load_video(c["clip"]).shape[0])
        picks = select.pick_frames(
            n_frames,
            int(a["n"]),
            unsure,
            contact,
            a.get("mix"),
            gap,
            banned,
            seed=int(a.get("seed", 0)) + k,
        )
        for p in picks:
            out.append(
                {
                    **{k2: c[k2] for k2 in ("recording", "arena", "clip", "scale")},
                    "frame": p.frame,
                    "reason": p.reason,
                    "score": round(p.score, 3),
                }
            )
        emit_progress((k + 1) / len(clips), f"{c['recording']} arena {int(c['arena']):02d}")
    return {"frames": out}


def _write_images(group: list[dict], invert: bool, tmp: Path, tag: str):
    """Grab the frames of one clip, return PNG paths and their hashes."""
    import imageio.v3 as iio
    import sleap_io as sio

    video = sio.load_video(group[0]["clip"])
    paths, hashes = [], []
    for k, f in enumerate(group):
        img = as_gray(video[int(f["frame"])])
        if invert:
            img = 255 - img
        p = tmp / f"{tag}_{k:04d}.png"
        iio.imwrite(p, img)
        paths.append(str(p))
        hashes.append(frame_hash(img))
    video.close()
    return paths, hashes


def _prefill(raw: str, models: list[str], n_animals: int, tmp: Path):
    import sleap_io as sio

    nn = Path(sys.executable).with_name("sleap-nn")
    pred = tmp / "pred.slp"
    cmd = [
        str(nn),
        "track",
        "-i",
        raw,
        "--only_suggested_frames",
        "-n",
        str(n_animals),
        "-o",
        str(pred),
    ]
    for m in models:
        cmd += ["-m", m]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0 or not pred.is_file():
        raise RuntimeError(f"pre-fill failed: {(r.stderr or r.stdout)[-800:]}")
    return sio.load_slp(str(pred))


def task_build(a: dict) -> dict:
    """Write a labelling package: chosen frames embedded, optional pre-fill."""
    import sleap_io as sio

    skel = sio.load_slp(a["skeleton_from"]).skeletons[0]
    invert = a.get("polarity", "inverted") == "inverted"
    groups: dict[tuple, list[dict]] = {}
    for f in a["frames"]:
        groups.setdefault((f["recording"], int(f["arena"]), f["clip"]), []).append(f)
    tmp = Path(tempfile.mkdtemp(prefix="rc_pkg_"))
    videos, suggestions, prov = [], [], {}
    for k, (key, group) in enumerate(sorted(groups.items())):
        group = sorted(group, key=lambda f: int(f["frame"]))
        paths, hashes = _write_images(group, invert, tmp, f"v{k:03d}")
        v = sio.Video.from_filename(paths)
        videos.append(v)
        for i, (f, h) in enumerate(zip(group, hashes, strict=True)):
            suggestions.append(sio.SuggestionFrame(video=v, frame_idx=i))
            prov[h] = {
                "recording": f["recording"],
                "arena": int(f["arena"]),
                "source_frame": int(f["frame"]),
                "scale": f.get("scale", "?"),
                "reason": f.get("reason", ""),
                "package": a["package"],
            }
        emit_progress(0.6 * (k + 1) / len(groups), f"frames of {key[0]} arena {key[1]:02d}")
    labels = sio.Labels(skeletons=[skel], videos=videos)
    labels.suggestions = suggestions
    set_provenance(labels, prov, set=a.get("set", ""), package=a["package"])
    n_pred = 0
    if a.get("prefill"):
        raw = str(tmp / "raw.pkg.slp")
        save_embedded(labels, raw, embed="suggestions")
        emit_progress(0.7, "pre-filling with the model")
        pred = _prefill(raw, a["prefill"], int(a.get("n_animals", 3)), tmp)
        for lf in pred.labeled_frames:
            vi = pred.videos.index(lf.video)
            for inst in lf.instances:
                inst.skeleton = skel
            labels.append(
                sio.LabeledFrame(
                    video=videos[vi], frame_idx=lf.frame_idx, instances=list(lf.instances)
                )
            )
            n_pred += len(lf.instances)
    emit_progress(0.9, "writing the package")
    save_embedded(labels, a["out"], embed="suggestions")
    return {"frames": len(suggestions), "videos": len(videos), "predicted": n_pred, "hashes": prov}


def _returned_frames(labels, pkg_frames: dict):
    """Labelled frames of a returned package, each with its hash and package info."""
    out = []
    for lf in labels.labeled_frames:
        h = frame_hash(lf.video[lf.frame_idx])
        out.append((lf, h, pkg_frames.get(h)))
    return out


def task_check(a: dict) -> dict:
    """Judge every frame of a returned package; nothing is merged here."""
    import sleap_io as sio

    labels = sio.load_slp(a["returned"])
    nodes = labels.skeletons[0].node_names if labels.skeletons else []
    problem = rules.skeleton_problem(nodes, a["nodes"])
    if problem:
        return {"skeleton": problem, "frames": [], "summary": {}}
    return _judge(_returned_frames(labels, a["package_frames"]), nodes, a)


def _judge(items: list, nodes: list[str], a: dict) -> dict:
    """Rule check of (frame, hash, info) items; the report the Back page shows."""
    rows = []
    for k, (lf, h, info) in enumerate(items):
        if info is None:
            v = rules.Verdict("rejected", ["frame is not part of this package"])
        else:
            img = lf.video[lf.frame_idx]
            insts = [
                rules.Inst(i.numpy(), user=type(i).__name__ == "Instance") for i in lf.instances
            ]
            v = rules.check_frame(
                insts,
                nodes,
                img.shape[:2],
                tuple(a.get("allowed_counts", [3])),
                a.get("n_females", 1),
            )
        rows.append(
            {
                "hash": h,
                **(info or {}),
                "status": v.status,
                "reasons": v.reasons,
                "warnings": v.warnings,
                "flies": len(lf.user_instances),
            }
        )
        emit_progress((k + 1) / max(len(items), 1), "checking frames")
    summary = {s: sum(1 for r in rows if r["status"] == s) for s in ("accepted", "rejected")}
    summary["warnings"] = sum(1 for r in rows if r["warnings"] and r["status"] == "accepted")
    summary["unlabelled"] = len(a["package_frames"]) - summary["accepted"] - summary["rejected"]
    return {"skeleton": None, "frames": rows, "summary": summary}


def task_harvest(a: dict) -> dict:
    """Frames hand-labelled in a SLEAP project since they were last taken into the set.

    Frames already in the set (by pixel hash) are skipped; the rest get
    provenance from their clip names and the usual rule check.
    """
    import sleap_io as sio

    labels = sio.load_slp(a["slp"])
    nodes = labels.skeletons[0].node_names if labels.skeletons else []
    problem = rules.skeleton_problem(nodes, a["nodes"])
    if problem:
        return {"skeleton": problem, "frames": [], "summary": {}, "package_frames": {}}
    known = set(provenance(sio.load_slp(a["base"]))) if a.get("base") else set()
    items, frames = [], {}
    lfs = user_frames(labels)
    for k, lf in enumerate(lfs):
        h = frame_hash(lf.video[lf.frame_idx])
        if h not in known:
            frames[h] = clip_info(lf, a.get("scale"), "?", a.get("source", "project"))
            items.append((lf, h, frames[h]))
        emit_progress(0.5 * (k + 1) / max(len(lfs), 1), "finding new frames")
    rep = _judge(items, nodes, {**a, "package_frames": frames})
    rep["summary"]["unlabelled"] = 0
    rep["summary"]["already_in_set"] = len(lfs) - len(items)
    return {**rep, "package_frames": frames}


def task_merge(a: dict) -> dict:
    """New version = base set + accepted frames of a returned package."""
    import sleap_io as sio

    ret = sio.load_slp(a["returned"])
    accepted = set(a["accepted"])
    items = [
        (lf, h, info)
        for lf, h, info in _returned_frames(ret, a["package_frames"])
        if h in accepted and info is not None
    ]
    new_prov = {
        h: {**info, "labeller": a.get("labeller", "?"), "labelled": a.get("date", "")}
        for _, h, info in items
    }
    keep_user_only(ret, [lf for lf, _, _ in items])
    if a.get("base"):
        base = sio.load_slp(a["base"])
        prov = provenance(base)
        keep_user_only(base, user_frames(base))
        for lf in ret.labeled_frames:  # appended, not merged: SLEAP would match videos
            for inst in lf.instances:  # by source clip and mix up frame numbers
                inst.skeleton = base.skeletons[0]
                inst.track = None
            base.append(lf)
        labels = base
    else:
        labels, prov = ret, {}
    labels.skeletons = labels.skeletons[:1]
    prov.update(new_prov)
    set_provenance(labels, prov, set=a.get("set", ""), version=a.get("version"))
    save_embedded(labels, a["out"])
    out = sio.load_slp(a["out"])
    return {**stats(out, prov), "added": len(items)}


def _example_image(img: np.ndarray, instances: list) -> np.ndarray:
    """Frame as RGB with every labelled point as a coloured dot (one colour per fly)."""
    from rigconductor.constants import WONG

    colours = [WONG[k] for k in ("orange", "sky_blue", "bluish_green", "yellow", "vermillion")]
    rgb = np.repeat(img[..., None], 3, axis=2)
    h, w = img.shape
    for k, pts in enumerate(instances):
        for x, y in pts:
            if not (np.isfinite(x) and np.isfinite(y)):
                continue
            xi, yi = int(round(x)), int(round(y))
            rgb[max(0, yi - 3) : min(h, yi + 4), max(0, xi - 3) : min(w, xi + 4)] = colours[
                k % len(colours)
            ]
    return rgb


def _new_summary(name: str) -> dict:
    return {
        "recording": name,
        "frames": 0,
        "flies": Counter(),
        "sizes": Counter(),
        "scales": Counter(),
        "arenas": set(),
        "labellers": Counter(),
    }


def task_summary(a: dict) -> dict:
    """Per recording: frames, flies per frame, image sizes and one example frame."""
    import imageio.v3 as iio
    import sleap_io as sio

    labels = sio.load_slp(a["slp"])
    prov = provenance(labels)
    lfs = user_frames(labels)
    out_dir = Path(a["out_dir"])
    out_dir.mkdir(parents=True, exist_ok=True)
    recs: dict[str, dict] = {}
    best: dict[str, tuple] = {}
    for k, lf in enumerate(lfs):
        img = as_gray(lf.video[lf.frame_idx])
        info = prov.get(frame_hash(img), {})
        name = info.get("recording", "?")
        r = recs.setdefault(name, _new_summary(name))
        n = len(lf.user_instances)
        r["frames"] += 1
        r["flies"][n] += 1
        r["sizes"][f"{img.shape[1]}×{img.shape[0]}"] += 1
        r["scales"][info.get("scale", "?")] += 1
        r["arenas"].add(int(info.get("arena", -1)))
        r["labellers"][info.get("labeller", "?")] += 1
        if name not in best or n > best[name][0]:
            best[name] = (n, img, [i.numpy() for i in lf.user_instances], info)
        emit_progress((k + 1) / max(len(lfs), 1), "reading frames")
    rows = []
    for i, (name, r) in enumerate(sorted(recs.items())):
        _, img, pts, info = best[name]
        example = out_dir / f"example_{i:03d}.png"
        iio.imwrite(example, _example_image(img, pts))
        rows.append(
            {
                "recording": name,
                "frames": r["frames"],
                "instances": sum(n * c for n, c in r["flies"].items()),
                "flies": {str(n): c for n, c in sorted(r["flies"].items())},
                "sizes": dict(r["sizes"]),
                "scale": r["scales"].most_common(1)[0][0],
                "arenas": sorted(x for x in r["arenas"] if x >= 0),
                "labellers": dict(r["labellers"]),
                "example": str(example),
                "example_from": [int(info.get("arena", -1)), int(info.get("source_frame", -1))],
            }
        )
    return {"recordings": rows}


def hash_share(h: str) -> float:
    """A frame's fixed place in [0, 1) from its pixel hash (same in every version)."""
    return int(h[:8], 16) / 16**8


def task_split(a: dict) -> dict:
    """Train file + one validation (test) file per scale.

    Validation recordings are held out whole. Recordings in ``shared``
    (recording → scale) are split by frame: a ``test_share`` of their frames,
    chosen by pixel hash so a frame stays on its side in later versions, joins
    the validation file of their scale; the rest is trained on.
    """
    import sleap_io as sio

    src = a["slp"]
    labels = sio.load_slp(src)
    prov = provenance(labels)
    lfs = user_frames(labels)
    hs = hashes_of(labels, lfs)
    rec_of = [prov.get(h, {}).get("recording", "?") for h in hs]
    val_sets = {name: set(recs) for name, recs in (a.get("val") or {}).items()}
    held = set().union(*val_sets.values()) if val_sets else set()
    left_out = set(a.get("exclude") or []) - held
    shared = {r: s for r, s in (a.get("shared") or {}).items() if r not in held | left_out}
    share = float(a.get("test_share", 0.2))
    tested = {
        i: shared[r] for i, r in enumerate(rec_of) if r in shared and hash_share(hs[i]) < share
    }
    parts = {
        "train": [i for i, r in enumerate(rec_of) if r not in held | left_out and i not in tested]
    }
    for name in sorted(set(val_sets) | set(tested.values())):
        recs = val_sets.get(name, set())
        parts[f"val_{name}"] = [
            i for i, r in enumerate(rec_of) if r in recs or tested.get(i) == name
        ]
    counts = {}
    for part, idx in parts.items():
        counts[part] = len(idx)
        if not idx:
            continue
        sub = sio.load_slp(src)
        sub_lfs = user_frames(sub)
        keep_user_only(sub, [sub_lfs[i] for i in idx])
        save_embedded(sub, str(Path(a["out_dir"]) / f"{part}.pkg.slp"))
    return {
        "counts": counts,
        "left_out": sum(r in left_out for r in rec_of),
        "shared_test": len(tested),
    }


TASKS = {
    "inspect": task_inspect,
    "seed": task_seed,
    "select": task_select,
    "build": task_build,
    "check": task_check,
    "harvest": task_harvest,
    "merge": task_merge,
    "summary": task_summary,
    "split": task_split,
}


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 2 or argv[0] not in TASKS:
        print(f"usage: worker.py {{{','.join(TASKS)}}} ARGS.json", file=sys.stderr)
        return 2
    args = json.loads(Path(argv[1]).read_text(encoding="utf-8"))
    emit_result(TASKS[argv[0]](args))
    return 0


if __name__ == "__main__":
    sys.exit(main())
