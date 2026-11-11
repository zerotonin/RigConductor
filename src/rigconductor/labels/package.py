# ╔══════════════════════════════════════════════════════════════════╗
# ║  Rig Conductor — labels.package                                  ║
# ║  « labelling packages out to a labeller and back in »            ║
# ╠══════════════════════════════════════════════════════════════════╣
# ║  A package is a folder: labels.pkg.slp (frames embedded),        ║
# ║  package.json, RULES.md, install_sleap.sh, label.sh, MANIFEST.   ║
# ╚══════════════════════════════════════════════════════════════════╝
"""Build labelling packages and take labelled ones back.

The package folder is self-contained: a labeller needs neither the videos nor
the Conductor, only a Linux PC (``label.sh`` installs SLEAP when needed).
``package.json`` carries the pixel hash of every frame, so frames that come
back are matched without trusting file names.
"""

from __future__ import annotations

import json
import shutil
from datetime import date, datetime
from pathlib import Path

from rigconductor import ConductorError, sleapenv
from rigconductor.constants import SLEAP_VERSION
from rigconductor.export import sha256_of
from rigconductor.labels.store import LabelSet

LABELS_FILE = "labels.pkg.slp"
INFO_FILE = "package.json"
MANIFEST = "MANIFEST.sha256"
RETURNED_FILE = "returned.pkg.slp"


def write_manifest(folder: Path) -> Path:
    lines = [
        f"{sha256_of(p)}  {p.name}"
        for p in sorted(folder.iterdir())
        if p.is_file() and p.name != MANIFEST
    ]
    out = folder / MANIFEST
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out


def manifest_problems(folder: Path) -> list[str]:
    """Files changed since packing; the labels file is expected to change."""
    m = folder / MANIFEST
    if not m.is_file():
        return [f"{MANIFEST} missing"]
    problems = []
    for line in m.read_text(encoding="utf-8").splitlines():
        digest, _, name = line.partition("  ")
        if not name or name == LABELS_FILE:
            continue
        p = folder / name
        if not p.is_file():
            problems.append(f"{name} missing")
        elif sha256_of(p) != digest:
            problems.append(f"{name} changed")
    return problems


def build_package(
    lset: LabelSet,
    frames: list[dict],
    env: sleapenv.SleapEnv,
    prefill: list[str] | None = None,
    dest: Path | None = None,
    allowed_counts: tuple[int, ...] = (3,),
    log=None,
    progress=None,
) -> Path:
    """Write a package for ``frames`` and register it with the set.

    Args:
        lset: Label set the frames will be merged into.
        frames: Picks from the ``select`` task (recording, arena, clip, frame, …).
        env: SLEAP environment.
        prefill: Model dirs (centroid, centred instance) to pre-fill with, or None.
        dest: Copy the finished folder here as well (a USB stick), or None.
        allowed_counts: Valid fly counts per frame.

    Returns:
        The package folder inside the set (or the copy on ``dest``).
    """
    if not frames:
        raise ConductorError("no frames chosen")
    pkg_id = lset.new_package_id()
    work = lset.package_dir(pkg_id)
    work.mkdir(parents=True)
    res = sleapenv.run_task(
        env,
        "build",
        {
            "frames": frames,
            "skeleton_from": str(lset.version_file()),
            "out": str(work / LABELS_FILE),
            "polarity": lset.polarity,
            "prefill": prefill,
            "n_animals": max(allowed_counts),
            "set": lset.name,
            "package": pkg_id,
        },
        log=log,
        progress=progress,
    )
    info = {
        "id": pkg_id,
        "set": lset.name,
        "parent_version": (lset.latest() or {}).get("version"),
        "created": datetime.now().isoformat(timespec="seconds"),
        "polarity": lset.polarity,
        "nodes": lset.skeleton,
        "allowed_counts": list(allowed_counts),
        "prefill": prefill,
        "sleap_version": SLEAP_VERSION,
        "frames": res["hashes"],
    }
    (work / INFO_FILE).write_text(json.dumps(info, indent=2), encoding="utf-8")
    sleapenv.write_installer(work)
    (work / "RULES.md").write_text(sleapenv.installer_text("RULES.md"), encoding="utf-8")
    write_manifest(work)
    recordings = sorted({f["recording"] for f in frames})
    lset.add_package(
        {
            "id": pkg_id,
            "created": info["created"],
            "parent_version": info["parent_version"],
            "frames": len(res["hashes"]),
            "predicted": res.get("predicted", 0),
            "recordings": recordings,
            "frames_index": [[f["recording"], int(f["arena"]), int(f["frame"])] for f in frames],
        }
    )
    if dest is None:
        return work
    target = Path(dest) / f"{lset.name}_{pkg_id}"
    shutil.copytree(work, target)
    return target


def find_packages(root: Path) -> list[Path]:
    """Package folders under ``root`` (a stick or a folder), newest first."""
    root = Path(root)
    hits = [p.parent for p in root.glob(f"*/{INFO_FILE}")] + (
        [root] if (root / INFO_FILE).is_file() else []
    )
    return sorted(set(hits), key=lambda p: p.stat().st_mtime, reverse=True)


def read_info(folder: Path) -> dict:
    p = Path(folder) / INFO_FILE
    if not p.is_file():
        raise ConductorError(f"{folder} is not a labelling package ({INFO_FILE} missing)")
    return json.loads(p.read_text(encoding="utf-8"))


def take_back(lset: LabelSet, folder: Path) -> tuple[dict, Path]:
    """Copy a returned labels file into the set's package folder.

    Returns:
        The package info and the path of the stored copy.
    """
    info = read_info(folder)
    if info.get("set") != lset.name:
        raise ConductorError(f"package {info.get('id')} belongs to label set '{info.get('set')}'")
    lset.package(info["id"])  # raises if unknown
    src = Path(folder) / LABELS_FILE
    if not src.is_file():
        raise ConductorError(f"{LABELS_FILE} missing in {folder}")
    dst = lset.package_dir(info["id"]) / RETURNED_FILE
    dst.parent.mkdir(parents=True, exist_ok=True)
    if src.resolve() != dst.resolve():
        shutil.copy2(src, dst)
    lset.update_package(info["id"], status="returned", returned=str(date.today()))
    return info, dst


def check_returned(lset: LabelSet, info: dict, returned: Path, env, log=None, progress=None):
    """Run the frame checks; returns the worker's report (nothing is merged)."""
    return sleapenv.run_task(
        env,
        "check",
        {
            "returned": str(returned),
            "package_frames": info["frames"],
            "nodes": lset.skeleton,
            "allowed_counts": info.get("allowed_counts", [3]),
            "n_females": 1,
        },
        log=log,
        progress=progress,
    )


def merge_returned(
    lset: LabelSet,
    info: dict,
    returned: Path,
    accepted: list[str],
    labeller: str,
    env,
    report: dict | None = None,
    log=None,
    progress=None,
) -> dict:
    """Merge accepted frames into a new version of the set; returns its registry entry."""
    if not accepted:
        raise ConductorError("no accepted frames to merge")
    n = lset.next_version()
    res = sleapenv.run_task(
        env,
        "merge",
        {
            "base": str(lset.version_file()) if lset.latest() else None,
            "returned": str(returned),
            "accepted": accepted,
            "package_frames": info["frames"],
            "labeller": labeller,
            "date": str(date.today()),
            "set": lset.name,
            "version": n,
            "out": str(lset.path / f"v{n}.pkg.slp"),
        },
        log=log,
        progress=progress,
    )
    added = res.pop("added", 0)
    entry = lset.add_version(res, note=f"package {info['id']} by {labeller}: +{added} frames")
    summary = (report or {}).get("summary", {})
    lset.update_package(
        info["id"],
        status="merged",
        merged_into=entry["version"],
        labeller=labeller,
        accepted=added,
        rejected=summary.get("rejected", 0),
    )
    return entry


def copy_target(lset: LabelSet, pkg_id: str, dest_root: Path) -> Path:
    return Path(dest_root) / f"{lset.name}_{pkg_id}"


def copy_package(lset: LabelSet, source: Path, dest_root: Path, replace: bool = False) -> Path:
    """Copy a package folder (with what is labelled so far) to a stick or folder.

    Args:
        source: The package folder to copy (the copy being labelled).
        dest_root: Stick mount point or folder; the copy goes into ``<set>_<id>``.
        replace: Overwrite an existing copy there (its labels are lost).

    Returns:
        The new copy.
    """
    info = read_info(source)
    pkg_id = info["id"]
    if lset.package(pkg_id).get("status") not in ("out", "returned"):
        raise ConductorError(f"package {pkg_id} is {lset.package(pkg_id)['status']}")
    target = copy_target(lset, pkg_id, dest_root)
    if target.resolve() == Path(source).resolve():
        raise ConductorError("the package is already there")
    if target.exists():
        if not replace:
            raise ConductorError(f"{target} exists already")
        shutil.rmtree(target)
    tmp = target.with_name(target.name + ".partial")
    shutil.rmtree(tmp, ignore_errors=True)
    shutil.copytree(source, tmp)
    tmp.rename(target)
    copies = sorted(set(lset.package(pkg_id).get("copies", [])) | {str(target)})
    lset.update_package(pkg_id, copies=copies)
    return target


def package_copies(lset: LabelSet, pkg_id: str, also: list[Path] = ()) -> list[Path]:
    """Folders holding package ``pkg_id``: this PC's copy, copies made with
    ``copy_package`` that are reachable now, and any of ``also``."""
    recorded = [Path(c) for c in lset.package(pkg_id).get("copies", [])]
    out = []
    for folder in [lset.package_dir(pkg_id), *recorded, *also]:
        try:
            same = read_info(folder).get("id") == pkg_id
        except ConductorError:
            same = False
        if same and Path(folder).resolve() not in [o.resolve() for o in out]:
            out.append(Path(folder))
    return out


def delete_package(lset: LabelSet, pkg_id: str, also: list[Path] = ()) -> list[Path]:
    """Delete every copy of a package that was not merged; its frames can be picked again.

    Only folders whose ``package.json`` names this package are removed. The
    registry keeps the entry (status ``deleted``).

    Returns:
        The folders removed.
    """
    if lset.package(pkg_id).get("status") == "merged":
        raise ConductorError(f"package {pkg_id} is merged into the set; it cannot be deleted")
    gone = package_copies(lset, pkg_id, also)
    for folder in gone:
        shutil.rmtree(folder)
    lset.update_package(pkg_id, status="deleted", deleted=str(date.today()))
    return gone


# ── SLEAP projects (labelled outside a package) ───────────────────
def check_project(lset: LabelSet, slp: Path, env, log=None, progress=None) -> dict:
    """New hand-labelled frames of a project, checked; the report carries their hashes."""
    return sleapenv.run_task(
        env,
        "harvest",
        {
            "slp": str(slp),
            "base": str(lset.version_file()) if lset.latest() else None,
            "nodes": lset.skeleton,
            "allowed_counts": [3],
            "n_females": 1,
            "source": f"project:{Path(slp).name}",
        },
        log=log,
        progress=progress,
    )


def merge_project(
    lset: LabelSet, slp: Path, report: dict, accepted: list[str], labeller: str, env, log=None
) -> dict:
    """Accepted new frames of a project → next version of the set; returns its entry."""
    if not accepted:
        raise ConductorError("no accepted frames to merge")
    n = lset.next_version()
    res = sleapenv.run_task(
        env,
        "merge",
        {
            "base": str(lset.version_file()) if lset.latest() else None,
            "returned": str(slp),
            "accepted": accepted,
            "package_frames": report["package_frames"],
            "labeller": labeller,
            "date": str(date.today()),
            "set": lset.name,
            "version": n,
            "out": str(lset.path / f"v{n}.pkg.slp"),
        },
        log=log,
    )
    added = res.pop("added", 0)
    entry = lset.add_version(res, note=f"{Path(slp).name} by {labeller}: +{added} frames")
    lset.update_project(slp, merged_into=entry["version"], merged=str(date.today()))
    return entry
