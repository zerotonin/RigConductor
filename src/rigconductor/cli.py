# ╔══════════════════════════════════════════════════════════════════╗
# ║  Rig Conductor — cli                                             ║
# ║  « the command-line twin of the wizard »                         ║
# ╠══════════════════════════════════════════════════════════════════╣
# ║  Every wizard stage, plus relay, DrosoDB import and redo.        ║
# ╚══════════════════════════════════════════════════════════════════╝
"""``rigconductor`` — the command line twin of the wizard.

rigconductor init-config
rigconductor doctor [--profile P] [--no-ssh]
rigconductor motif status | motif record TITLE SECONDS [--codec C]
rigconductor models [--profile P] [--refresh]
rigconductor disks
rigconductor run SESSION.json [--from STAGE] [--to STAGE] [--disk /dev/sdX1]
rigconductor status SESSION_ID
rigconductor gui
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from rigconductor import ConductorError, __version__
from rigconductor.config import ConductorConfig, write_example
from rigconductor.session import STAGES, ConductorSession, ConductorSpec


def _cfg(a) -> ConductorConfig:
    return ConductorConfig.load(a.config)


def _motif(cfg: ConductorConfig):
    from rigconductor.motif import MotifClient

    m = cfg.motif
    return MotifClient(m.host, m.port, m.serial, api_key_cmd=m.api_key_cmd)


def cmd_init_config(a):
    try:
        p = write_example(a.path, overwrite=a.force)
    except FileExistsError as e:
        raise SystemExit(f"{e} exists; use --force to overwrite") from None
    print(f"wrote {p}; edit the paths before the first run")


def cmd_doctor(a):
    from rigconductor import doctor
    from rigconductor.profiles import make_profile

    cfg = _cfg(a)
    prof = make_profile(a.profile)
    checks = doctor.run_all(
        cfg, a.profile, lambda: _motif(cfg), prof.ddb_snapshot(cfg), with_ssh=not a.no_ssh
    )
    for c in checks:
        print(c.line)
    sys.exit(0 if all(c.ok for c in checks) else 1)


def cmd_motif(a):
    cfg = _cfg(a)
    m = _motif(cfg)
    if a.motif_cmd == "status":
        print(json.dumps(m.version()))
        st = m.camera()
        rec = st.filename or ""
        print(f"camera {st.serial}: status={st.status} recording={st.is_recording} {rec}")
    elif a.motif_cmd == "record":
        import time

        from rigconductor.motif import find_new_recording, wait_for_stop

        t0 = time.time()
        m.start_recording(a.title, a.seconds, a.codec or cfg.motif.codec, {"conductor": "test"})
        print(f"started '{a.title}' for {a.seconds} s …")
        wait_for_stop(m, timeout_s=a.seconds + 90, log=print)
        rec = find_new_recording(cfg.data_root, t0, a.title)
        print("recording folder:", rec)
    elif a.motif_cmd == "stop":
        m.stop_recording()
        print("stopped")


def cmd_models(a):
    from rigconductor import models as models_mod
    from rigconductor.profiles import make_profile

    cfg = _cfg(a)
    prof = make_profile(a.profile)
    cache = cfg.registry_cache(a.profile)
    if a.refresh:
        remote = cfg.registry_remote(a.profile)
        if not remote:
            raise SystemExit(f"no registry configured for {a.profile}")
        ms = models_mod.refresh(cfg.cluster.ssh_host, remote, cache)
        print(f"refreshed from {cfg.cluster.ssh_host}:{remote}")
    else:
        ms = models_mod.load_cached(cache)
    for m in models_mod.models_for(ms, prof.organism, include_deprecated=True):
        print(f"{m.id:28s} {m.kind:16s} run={m.run:24s} {m.label}")
    if not ms:
        print("(no models cached; use --refresh)")


def cmd_disks(a):
    from rigconductor.export import removable_disks

    for d in removable_disks():
        print(f"{d.device:12s} {d.text}")


def cmd_run(a):
    from rigconductor.export import removable_disks

    cfg = _cfg(a)
    spec = ConductorSpec.from_json(json.loads(Path(a.session).read_text(encoding="utf-8")))
    i0 = STAGES.index(a.from_stage) if a.from_stage else 0
    i1 = STAGES.index(a.to_stage) + 1 if a.to_stage else len(STAGES)
    stages = STAGES[i0:i1]
    recorder = _motif(cfg) if "record" in stages and not spec.recording_dir else None
    disk = None
    if a.disk:
        disk = next((d for d in removable_disks() if d.device == a.disk), None)
        if disk is None:
            raise SystemExit(f"{a.disk} is not a removable partition (see `rigconductor disks`)")
    ConductorSession(cfg, spec, recorder).run(stages, disk=disk, poll_interval_s=a.poll)


def cmd_status(a):
    cfg = _cfg(a)
    p = cfg.sessions_dir / f"{a.session_id}.json"
    if not p.is_file():
        raise SystemExit(f"no session {a.session_id} in {cfg.sessions_dir}")
    st = json.loads(p.read_text(encoding="utf-8"))
    spec = ConductorSpec.from_json(st["spec"])
    s = ConductorSession(cfg, spec)
    print("\n".join(s.status_lines()))


def cmd_redo(a):
    cfg = _cfg(a)
    p = cfg.sessions_dir / f"{a.session_id}.json"
    if not p.is_file():
        raise SystemExit(f"no session {a.session_id} in {cfg.sessions_dir}")
    s = ConductorSession(
        cfg, ConductorSpec.from_json(json.loads(p.read_text(encoding="utf-8"))["spec"])
    )
    s.redo_cut()
    if a.run:
        s.run(("cut", "reduce", "register"))
    print("\n".join(s.status_lines()))


def cmd_rerun(a):
    cfg = _cfg(a)
    p = cfg.sessions_dir / f"{a.session_id}.json"
    if not p.is_file():
        raise SystemExit(f"no session {a.session_id} in {cfg.sessions_dir}")
    spec = ConductorSpec.from_json(json.loads(p.read_text(encoding="utf-8"))["spec"])
    s = ConductorSession(cfg, spec)
    s.rerun_with_model(a.model)
    if a.run:
        s.run(("upload", "submit", "poll", "fetch", "score"))
    print("\n".join(s.status_lines()))


def cmd_sessions(a):
    cfg = _cfg(a)
    for p in sorted(cfg.sessions_dir.glob("*.json")):
        st = json.loads(p.read_text(encoding="utf-8"))
        done = [s for s in STAGES if s in st.get("done", {})]
        print(f"{p.stem:40s} {st['spec'].get('profile', ''):18s} {done[-1] if done else '-'}")


def _load(cfg: ConductorConfig, sid: str) -> ConductorSession:
    p = cfg.sessions_dir / f"{sid}.json"
    if not p.is_file():
        raise SystemExit(f"no session {sid} in {cfg.sessions_dir}")
    return ConductorSession(
        cfg, ConductorSpec.from_json(json.loads(p.read_text(encoding="utf-8"))["spec"])
    )


def _dest(a) -> Path:
    """--out DIR, or --disk DEV (mounted if needed)."""
    from rigconductor.export import mount, removable_disks

    if a.disk:
        d = next((d for d in removable_disks() if d.device == a.disk), None)
        if d is None:
            raise SystemExit(f"{a.disk} is not a removable partition (see `rigconductor disks`)")
        return mount(d)
    if a.out:
        return Path(a.out)
    raise SystemExit("give --out DIR or --disk /dev/sdX1")


def cmd_ddb(a):
    from rigconductor import ddbsync
    from rigconductor.export import removable_disks
    from rigconductor.profiles import make_profile

    cfg = _cfg(a)
    target = make_profile("drosophila_triad").ddb_snapshot(cfg)
    if not target:
        raise SystemExit("no ddb_snapshot configured in the ThermoKourt rig.yaml")
    roots = (
        [Path(a.source)]
        if a.source
        else [Path(d.mountpoint) for d in removable_disks() if d.mountpoint]
    )
    found = [i for i in (ddbsync.find_on(r) for r in roots) if i]
    if not found:
        raise SystemExit("no drosodb/ddb.latest.sqlite3 on any mounted USB stick")
    newest = max(found, key=lambda i: i.created)
    ddbsync.import_snapshot(newest, Path(target), force=a.force)


def cmd_pack(a):
    from rigconductor.relay import pack_job

    cfg = _cfg(a)
    pack_job(_load(cfg, a.session_id), _dest(a))


def cmd_import_results(a):
    from rigconductor.relay import import_results, read_manifest

    cfg = _cfg(a)
    m = read_manifest(Path(a.package))
    import_results(_load(cfg, m["session_id"]), Path(a.package))


def cmd_relay(a):
    from rigconductor import relay

    cfg = _cfg(a)
    if a.relay_cmd == "import":
        s = relay.import_job(cfg, Path(a.package))
        if a.run:
            relay.run_cluster_stages(s, a.poll)
            relay.pack_results(s, Path(a.out) if a.out else Path(a.package).parent)
    elif a.relay_cmd == "run":
        relay.run_cluster_stages(_load(cfg, a.session_id), a.poll)
    elif a.relay_cmd == "pack-results":
        relay.pack_results(_load(cfg, a.session_id), _dest(a))
    elif a.relay_cmd == "gui":
        from rigconductor.gui.relay import main as relay_gui

        relay_gui(a.config)
    elif a.relay_cmd == "find":
        for p in relay.find_packages(Path(a.root)):
            m = relay.read_manifest(p)
            print(f"{p.name:50s} {m['kind']:18s} {m['session_id']}")


def cmd_gui(a):
    from rigconductor.gui.wizard import main as gui_main

    gui_main(a.config)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="rigconductor", description=__doc__.split("\n\n")[0])
    ap.add_argument("--config", default=None, help="rig.yaml (default ~/.config/rigconductor)")
    ap.add_argument("--version", action="version", version=__version__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("init-config")
    p.add_argument("path", nargs="?")
    p.add_argument("--force", action="store_true")
    p.set_defaults(fn=cmd_init_config)

    p = sub.add_parser("doctor")
    p.add_argument("--profile", default="drosophila_triad")
    p.add_argument("--no-ssh", action="store_true")
    p.set_defaults(fn=cmd_doctor)

    p = sub.add_parser("motif")
    ms = p.add_subparsers(dest="motif_cmd", required=True)
    ms.add_parser("status")
    ms.add_parser("stop")
    r = ms.add_parser("record")
    r.add_argument("title")
    r.add_argument("seconds", type=float)
    r.add_argument("--codec")
    p.set_defaults(fn=cmd_motif)

    p = sub.add_parser("models")
    p.add_argument("--profile", default="drosophila_triad")
    p.add_argument("--refresh", action="store_true")
    p.set_defaults(fn=cmd_models)

    sub.add_parser("disks").set_defaults(fn=cmd_disks)

    p = sub.add_parser("run")
    p.add_argument("session")
    p.add_argument("--from", dest="from_stage", choices=STAGES)
    p.add_argument("--to", dest="to_stage", choices=STAGES)
    p.add_argument("--disk", help="/dev/sdX1 to export to")
    p.add_argument("--poll", type=float, default=None)
    p.set_defaults(fn=cmd_run)

    p = sub.add_parser("status")
    p.add_argument("session_id")
    p.set_defaults(fn=cmd_status)

    p = sub.add_parser("redo-cut", help="cut a session's recording again (new clip settings)")
    p.add_argument("session_id")
    p.add_argument("--run", action="store_true", help="cut, crop and register straight away")
    p.set_defaults(fn=cmd_redo)

    p = sub.add_parser("rerun", help="analyse a session again with another pose model")
    p.add_argument("session_id")
    p.add_argument("--model", required=True, help="model id from 'rigconductor models'")
    p.add_argument("--run", action="store_true", help="send it to the cluster straight away")
    p.set_defaults(fn=cmd_rerun)

    sub.add_parser("sessions").set_defaults(fn=cmd_sessions)

    p = sub.add_parser("ddb", help="DrosoDB snapshot by USB stick")
    ds = p.add_subparsers(dest="ddb_cmd", required=True)
    di = ds.add_parser("import")
    di.add_argument("--from", dest="source", help="folder holding drosodb/ (default: USB sticks)")
    di.add_argument("--force", action="store_true", help="import even if not newer")
    p.set_defaults(fn=cmd_ddb)

    p = sub.add_parser("pack", help="offline: pack a session for a relay")
    p.add_argument("session_id")
    p.add_argument("--out")
    p.add_argument("--disk")
    p.set_defaults(fn=cmd_pack)

    p = sub.add_parser("import-results", help="offline: bring a relay's results back")
    p.add_argument("package")
    p.set_defaults(fn=cmd_import_results)

    p = sub.add_parser("relay", help="run on a networked machine for an offline rig")
    rs = p.add_subparsers(dest="relay_cmd", required=True)
    ri = rs.add_parser("import")
    ri.add_argument("package")
    ri.add_argument(
        "--run", action="store_true", help="also run the cluster stages and pack results"
    )
    ri.add_argument("--out", help="where the results package goes (default: next to the job)")
    ri.add_argument("--poll", type=float, default=None)
    rr = rs.add_parser("run")
    rr.add_argument("session_id")
    rr.add_argument("--poll", type=float, default=None)
    rp = rs.add_parser("pack-results")
    rp.add_argument("session_id")
    rp.add_argument("--out")
    rp.add_argument("--disk")
    rs.add_parser("gui")
    rf = rs.add_parser("find")
    rf.add_argument("root")
    p.set_defaults(fn=cmd_relay)
    sub.add_parser("gui").set_defaults(fn=cmd_gui)

    a = ap.parse_args(argv)
    try:
        a.fn(a)
    except ConductorError as e:
        raise SystemExit(f"error: {e}") from None


if __name__ == "__main__":
    main()
