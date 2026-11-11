# rigconductor

One wizard from "press record" to "results on the USB disk", for a loopbio Motif
rig. Two organism profiles:

| Profile | Animals | Backend |
|---|---|---|
| `drosophila_triad` | 10 circular arenas, 2 males + decapitated female | [ThermoKourt](https://github.com/zerotonin/thermokourt) (arena finder, SLEAP on the cluster, rules/SimBA scoring, SQLite + DrosoDB copy) |
| `tadpole_24well` | 24-well plate of *Xenopus* tadpoles | [tadpose](https://github.com/zerotonin/tadpose) (well split, SLEAP/DLC tracking, assignment to the canonical prototypes) |

Stages, each idempotent and resumable: `record → cut → register → upload → submit →
poll → fetch → score → export`.

## Install (rig PC)

```bash
conda activate heatagg                      # python ≥3.10, PyQt6, thermokourt installed
pip install -e ".[gui,dev]"
rigconductor init-config                    # writes ~/.config/rigconductor/rig.yaml — edit it
rigconductor doctor                         # Motif, camera, disk, cluster, DrosoDB, registry
rigconductor models --refresh               # pull the model registry from the cluster
rigconductor gui
```

Motif is reached through its REST API on the rig PC (`recnode-apikey` supplies the
key). The cluster is reached by key-based SSH (`ssh <alias>` must work without a
password); off campus that needs the VPN first.

## Command line

```
rigconductor doctor [--profile P] [--no-ssh]
rigconductor motif status | record TITLE SECONDS | stop
rigconductor models [--profile P] [--refresh]
rigconductor disks
rigconductor run SESSION.json [--from STAGE] [--to STAGE] [--disk /dev/sdX1]
rigconductor status SESSION_ID
rigconductor sessions
```

`SESSION.json` is what the wizard writes (see `rigconductor.session.ConductorSpec`).
Give `recording_dir` to analyse an existing recording instead of recording.

## Model registry

One YAML per organism on the cluster (path in `rig.yaml`), cached locally:

```yaml
- id: flies13_lab_v1
  organism: drosophila
  kind: sleap-topdown
  run: flies13_lab_v1        # models/<run>.centroid + .centered_instance
  env: sleap
  trained: 2026-11-20
  validation: {mAP: 0.82, frames: 150}
```

The chosen model id is stamped into the results (ThermoKourt `annotators`,
tadpose run metadata).

## Export bundle

`<disk>/<profile>/<session_id>/` with the database export (optionally blinded:
no genotypes, no vial codes), `events.csv`, `window_summary.csv`, the arena
overlay, `session.json` and `MANIFEST.sha256`; every file is re-read and checked
before the disk is unmounted.

## Development

```bash
ruff check . && ruff format --check . && pytest -q
```

Tests run without Motif, a cluster or a GUI (`FakeMotif`, a fake profile).
