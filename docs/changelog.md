# Changelog

## Unreleased

- **Training-data wizard** (`rigconductor label`, "Training data…" button, desktop
  icon *Rig Conductor — training data*): label sets with versions
  (`~/data/sleap_labels/sets/<set>/`), frame choice from analysed recordings
  (unsure / flies touching / spread, at least 2 s apart, never frames already
  labelled), packages with the frames embedded and pre-filled by a model, to label
  here or on another Linux PC from a USB stick (`label.sh` installs SLEAP),
  checks on return (half-corrected frames, wrong fly count, missing thorax,
  points off the image, duplicates; female head rule as a warning), merge into a
  new version, training on Aoraki and the promotion rule. Frames are recognised by
  a hash of their pixels.
- Training-data wizard, first screen: **Continue labelling frames picked earlier**
  lists the packages not brought back yet (on a stick or this PC) with how many
  frames are labelled so far, and opens one in SLEAP.
- **Training data** page before training: per recording of the chosen version an
  example frame with the labels drawn on, frames, flies, flies per frame, image
  size, arenas and labellers; each recording is set to Training, Validation or
  Leave out. Left-out recordings are kept out of the split and named in the
  registry entry; repeated runs on one day get unique names.
- **Continue labelling** lists wizard packages *and* SLEAP projects labelled outside
  the wizard (a set's seed project is added automatically; *Add a SLEAP project…*
  for others), each with a bar: green labelled, yellow only predicted, red nothing
  yet. *Bring labelled frames back* takes a project's frames labelled since the
  last time (frames already in the set are skipped by pixel hash), checks them and
  merges them as a new version.
- Fix: merging appends the new frames with their own videos; SLEAP's merge matched
  videos by source clip and mixed up frame numbers when new frames came from a
  clip already in the set.
- *Copy to USB…* on the Continue page: copies a package (with what is labelled so
  far) to a plugged-in stick or a folder; an existing copy there is only replaced
  after a warning that its labels would be lost. Copies are recorded so *Delete…*
  finds them.
- *Delete…* on the Continue page: deletes a package (this PC's copy and copies on
  plugged-in sticks) after a warning that lists the folders and the labelled
  frames that would be lost; with labelled frames the package name must be typed.
  Merged packages cannot be deleted; deleted packages' frames can be picked again.
  For a SLEAP project it only takes it off the list (the file stays).
- Frame check: "same fly" now means every point of one fly lies on the other (a
  fly labelled twice); flies overlapping in courtship or copulation pass.
- Message boxes in all three windows (`gui/dialogs.py`) are as wide as their
  longest line (up to 80 % of the screen), their text can be selected and copied,
  and messages over 24 lines show the start with the rest under *Show Details*.
  The same goes for the hand-built boxes (model vs camera), the text and choice
  inputs (label set name, vial code, *Copy to USB* destination) and the Aoraki
  arena log, which now opens in a log window sized to its lines; the example
  frame is capped at the screen.
- Training data page: a recording can be set to **All (split automatically)**.
  `LABEL_TEST_SHARE` (20 %) of its frames, chosen by pixel hash so a frame stays
  on its side in later versions, join the validation set of its scale; the rest
  is trained on. Validation recordings are no longer required when a recording
  is set to All; the registry entry names the recordings split by frame.
- Re-saving an arena layout drops the clip boxes and video crop of the previous
  cut (ThermoKourt writes them again on the next cut).
- **Default label set** (`rigconductor label-default [NAME]`, *Make default* on the
  first screen) and **`rigconductor label-sync`**: copies it to
  `<project>/sleap_labels/sets/<name>/` on Aoraki (never deletes there; `train/`
  left out). `label-sync --install-cron` adds a crontab line every
  `LABEL_SYNC_HOURS` (2) hours, log in `~/.local/state/rigconductor/label-sync.log`;
  under cron the ssh key comes from the desktop keyring, so the PC must stay
  logged in.
- **SLEAP 1.6 for labelling** (`sleapenv.py`, `rigconductor setup-sleap`): CPU
  build installed per user with uv, no sudo; all SLEAP work runs in that env as a
  child process.
- Promoted models: registry entries with `default: true` are pre-selected for new
  recordings (the last one per organism).
- `rigconductor install-desktop` writes both desktop icons.

- Standard camera settings set and read back before every recording
  (`CAMERA_SETTINGS`, rig.yaml `motif.camera`): exposure 8850 µs, gain 0 dB,
  69 fps, auto exposure/gain off; codec default `mq-fast`. A camera that does not
  take them stops the recording with the difference.
- **Check for updates / Update** (bottom left of the wizard): `git fetch` for the
  Conductor and ThermoKourt, lists new commits; Update fast-forwards
  (`git pull --ff-only`) only when no tracked file was changed locally and no
  local commit exists, and reinstalls when `pyproject.toml` changed.
- **Rerun with this model** (Analyse page, `rigconductor rerun SID --model ID`):
  earlier poses and drawn clips go to `archive/<model>_<time>/` (and
  `pose/_archive/` on Aoraki), the cluster stages are cleared, the clips are sent
  again with the new model; scores are replaced.
- Unmounting the USB disk after export is optional (`export.unmount`, Deliver
  page checkbox), off by default.
- Clips go onto the USB disk by default. `export.clips` / Deliver page chooser:
  none, plain, skeleton, events, both. Annotated clips are drawn with
  `thermokourt-overlay` (4 in parallel, progress bar "Drawing the clips") into
  `results/clips_<mode>/` and reused on the next export. Replaces `include_clips`.
- Stop cancels only jobs still in the queue (`scancel` exits 1 on finished ids).
- Analyse page: **Stop** (cancels every GPU worker of the run, releases claimed
  arenas, keeps finished ones) and **Rerun unfinished arenas** (sends only arenas
  without poses). Arena tiles turn orange with "no heartbeat" when a claim has had
  no heartbeat for 5 min (`STALE_AFTER_S`), and open that arena's cluster log
  when clicked. Waiting workers show SLURM's reason (Resources, QOS limit, …).
- Progress bar on the stage pages (`gui.progress`): percent, what is being worked
  on, time so far and time left, from `@progress` lines of the cutter and the crop.
- Choosing an existing recording resumes its session: Setup and Animals pages
  refilled from `conductor_session.json`, stages already done stay done.
- Cluster monitor follows pull-queue runs: arena tiles from the queue files
  (waiting / running on node / done / no animals / retrying / failed) and a
  line per GPU worker (card, node, state), still one ssh call per refresh
  (`cluster_status.fetch_queue_status`, profile hook `cluster_queue`).
- Fix: the recording length could silently stay at the default (1800 s). The
  seconds box ignored keystrokes that made an out-of-range number (clicking into
  "1800" and typing "30" gives 301800). New `gui.duration.DurationEdit`: clicking
  selects the whole number so typing replaces it, out-of-range values are clamped,
  the length is shown in words. The same field is on the Record page (kept in sync
  with Setup), with "will record for …" above the Start button.
- **Fix arenas…** (Cut page): Qt arena editor with ThermoKourt's controls (drag
  centre / rim, wheel, right-click add/remove, +/−, U, Ctrl+Z); arenas whose radius
  is > 10 % off the median are flagged. Saves the layout for the recording and as
  the rig default, then offers to cut again.
- **Cluster monitor** (Analyse page): one `sacct` call every 5 s; counts of
  running / waiting / done / failed and one coloured tile per arena with node and
  run time. **Send again** clears the cluster stages after a failed job.
- Readable log: commands sent to the cluster are hidden unless "Show technical
  details" is ticked.
- Rules pass: `src/` layout, module banners, `encoding="utf-8"` on all text I/O,
  ruff `N, C4, SIM` added, `environment.yml`, CI workflows (tests, lint).
- `rigconductor/constants.py`: shared defaults, limits, timeouts and the Wong
  palette; overlay colours switched to Wong (sky blue / bluish green / orange).
- Default padding around the arenas (preview Reduce view and the crop) 20 → 40 px.
- **Cut again** (Cut page button, `rigconductor redo-cut SID [--run]`): moves the
  clips to `clips.old-<time>`, clears cut and the stages after it (record and the
  crop stay), and cuts again.
- Crop box covers what the clips need (ThermoKourt padding and minimum clip size).
- Model check: frame-rate hint for pose models no longer says to re-record.
- **Reduce stage** (`rigconductor/reduce.py`, new stage between cut and register):
  crops every Motif chunk to the bounding box of all arenas + padding, frame for
  frame, verifies the frame count, then replaces the full-frame chunk (or moves
  it to `full_frame/` with `reduce.keep_full`). Updates `metadata.yaml` imgshape
  (original kept as `metadata.full.yaml`), writes `reduced.json` with the offset.
  The crop is re-timed to the real camera rate from the Motif timestamps.
  Test on the 11 Nov recording: 2600×2160 → 944×976, 504 → 59 MB (88 % saved), 4 s.
- **Presets** (`rigconductor/presets.py`): built-in `heat` preset for the triad
  (grayscale, whatever frame rate the camera runs at, crop to arenas, padding
  20, model lab_oct2025_v2); a preset may pin a frame rate (`fps:`); rig.yaml
  `profiles.<name>.presets` overrides or adds presets.
- **Model on page 1**: preset and pose model are chosen on the setup page and
  checked against the live camera (`compat.check_model_vs_camera`) before
  recording. Colour camera + grayscale-only model offers "Convert to grayscale" or
  "Pick another model"; camera frame rate ≠ preset offers "Set camera to N fps".
- Shared padding: the preview's Reduce-view padding (now camera px) is the
  padding used for the crop; the Cut page shows both the crop switch and padding.
- `MotifClient.read(name)` for single camera settings.
- Fix: changes made on the setup page after going back (duration, title, codec,
  existing recording) are now used; before, the first values stuck until restart.
- Preview: **Reduce view** button runs arena detection, then shows only the
  bounding box of all arenas plus an editable padding (default 20 px, in
  preview-image pixels). Display only; the camera keeps recording the full image.
- Preview overlays drawn in screen pixels (3 px lines, 18 px bold labels on dark
  boxes) and individually toggleable: arenas, numbers, genotypes.
- Genotype overlay: each arena shows its vials' genotypes from the vial board, in
  the board's genotype colours (`VialBoard.genotypes_by_arena`).

## 0.1.0 — 2026-11-10

First working version (Rig Conductor dev plan, days 1–2 and most of day 4).

- `motif.py`: REST client for the loopbio recnode (start/stop/status/configure,
  self-signed TLS, key via `recnode-apikey`); recording-folder discovery and
  close detection; `FakeMotif` for tests.
- `doctor.py`: readiness checks (Motif, camera, data disk, SSH to the cluster,
  ThermoKourt import, DrosoDB snapshot age, cached model registry) with hints
  written for a non-technical operator.
- `profiles/`: `Profile` interface; `drosophila_triad` delegates every stage to
  ThermoKourt's session runner and adds the CSV/blind-DB export;
  `tadpole_24well` has the plate-map metadata (form + CSV import), upload,
  submit, poll, fetch, per-well prototype proportions and export; its `cut`
  waits for the tadpose decision (dev plan §4). SLURM scaffold
  `scripts/slurm/tadpose_track_assign.sh`.
- `models.py`: per-organism YAML registry on the cluster, cached locally,
  dropdown labels with date and headline validation number, deprecation flag.
- `export.py`: removable-disk detection (`lsblk -J`), `udisksctl` mount/unmount,
  bundle with SHA-256 manifest and read-back verification.
- `session.py`: `ConductorSession` with on-disk state and resumable stages;
  `cli.py`: `rigconductor` command; `gui/`: PyQt6 six-page wizard.
- Verified on the rig PC: `doctor` against the live Motif 6.1.10 and camera,
  registry refresh from the cluster, end-to-end Drosophila session on the
  October 2025 recording through to a verified blind export bundle.
