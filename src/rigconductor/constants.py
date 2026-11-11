# ╔══════════════════════════════════════════════════════════════════╗
# ║  Rig Conductor — constants                                       ║
# ║  « one source of truth for numbers and colours »                 ║
# ╠══════════════════════════════════════════════════════════════════╣
# ║  Shared defaults, limits and colours. rig.yaml overrides the     ║
# ║  operator-facing ones (padding, crf, poll interval, …).          ║
# ╚══════════════════════════════════════════════════════════════════╝
"""Shared constants for Rig Conductor."""

from __future__ import annotations

# ┌────────────────────────────────────────────────────────────┐
# │ Rig layout  « what the triad plate holds »                 │
# └────────────────────────────────────────────────────────────┘
N_ARENAS = 10
N_FLY_ROLES = 3  # male 1, male 2, decapitated female
TK_CROP_PADDING = 0.10  # ThermoKourt's clip padding (fraction of r) if the layout lacks one
ARENA_RADIUS_TOL = 0.10  # radius this far from the median gets flagged in the editor
ARENA_RESIZE_STEP = 0.03  # +/-, wheel: fractional radius change per step
ARENA_MIN_RADIUS_PX = 20.0
EDITOR_HANDLE_PX = 12  # screen px for grabbing a centre or rim

# ┌────────────────────────────────────────────────────────────┐
# │ Reduce  « crop the recording to the arenas »               │
# └────────────────────────────────────────────────────────────┘
REDUCE_PADDING_PX = 40  # camera px on each side of the arena bounding box
REDUCE_PADDING_MAX_PX = 500
REDUCE_CRF = 18  # x264 quality of the cropped video (lower = better, bigger)
REDUCE_ALIGN_PX = 16  # crop sizes are multiples of this (encoder-friendly, even)
FPS_SNAP_TOL = 0.01  # measured frame rate this close to the set rate → use the set rate

# ┌────────────────────────────────────────────────────────────┐
# │ Motif and cluster  « timeouts and polling »                │
# └────────────────────────────────────────────────────────────┘
MOTIF_PORT = 6083
MOTIF_CODEC = "mq-fast"
# Camera settings applied before every recording (Basler a2A2600-64umPRO, 2026-11-11)
CAMERA_SETTINGS = {
    "ExposureAuto": "Off",
    "ExposureTime": 8850.0,  # µs
    "GainAuto": "Off",
    "Gain": 0.0,  # dB
    "AcquisitionFrameRateEnable": True,
    "AcquisitionFrameRate": 69.0,  # fps
}
CAMERA_SETTING_TOL = 0.01  # read-back may round (e.g. 8850 → 8849.96)
HTTP_TIMEOUT_S = 10.0
RECORD_GRACE_S = 90.0  # extra wait after the requested duration before giving up
RECORDING_QUIET_S = 5.0  # no file change for this long = recording closed
POLL_INTERVAL_S = 60
DEFAULT_DURATION_S = 600
DURATION_MIN_S = 5
DURATION_MAX_S = 6 * 3600 - 1
ARENA_DETECT_TIMEOUT_S = 120
MONITOR_REFRESH_S = 5  # cluster monitor refresh
PROGRESS_EVERY_S = 1.0  # progress lines from long local steps (crop)
SSH_TIMEOUT_S = 30
GIT_TIMEOUT_S = 60
UPDATE_PACKAGES = ("rigconductor", "thermokourt")  # checked by 'Check for updates'
STALE_AFTER_S = 300  # claim without heartbeat this long = worker dead (sleap_worker STALE_MIN)

# ┌────────────────────────────────────────────────────────────┐
# │ Doctor  « readiness thresholds »                           │
# └────────────────────────────────────────────────────────────┘
MIN_FREE_DISK_GB = 50.0
MAX_SNAPSHOT_AGE_H = 48.0

# ┌────────────────────────────────────────────────────────────┐
# │ Wong (2011) palette  « colourblind-safe base colours »     │
# └────────────────────────────────────────────────────────────┘
WONG = {
    "black": (0, 0, 0),
    "orange": (230, 159, 0),
    "sky_blue": (86, 180, 233),
    "bluish_green": (0, 158, 115),
    "yellow": (240, 228, 66),
    "blue": (0, 114, 178),
    "vermillion": (213, 94, 0),
    "reddish_purple": (204, 121, 167),
}

# ┌────────────────────────────────────────────────────────────┐
# │ Preview overlay  « arena circles, numbers, genotypes »     │
# └────────────────────────────────────────────────────────────┘
COLOUR_RIG_LAYOUT = WONG["sky_blue"]  # saved rig layout, not yet checked on this image
COLOUR_ARENAS_OK = WONG["bluish_green"]  # all arenas found
COLOUR_ARENAS_PARTIAL = WONG["orange"]  # some arenas missing
COLOUR_ARENA_SELECTED = WONG["yellow"]
COLOUR_ARENA_ODD_SIZE = WONG["vermillion"]  # radius off the median by > ARENA_RADIUS_TOL
LABEL_BOX_RGBA = (0, 0, 0, 170)
OVERLAY_PEN_PX = 3
OVERLAY_FONT_PX = 18
BLACK_FRAME_MEAN = 8  # mean gray below this: backlight off or lens capped

# ┌────────────────────────────────────────────────────────────┐
# │ Cluster monitor  « one colour per job state »              │
# └────────────────────────────────────────────────────────────┘
JOB_STATE_COLOURS = {
    "pending": (170, 170, 170),
    "running": WONG["blue"],
    "done": WONG["bluish_green"],
    "failed": WONG["vermillion"],
    "stale": WONG["orange"],
}

# ┌────────────────────────────────────────────────────────────┐
# │ Delivery  « what goes onto the USB disk »                  │
# └────────────────────────────────────────────────────────────┘
CLIP_MODES = {
    "none": "no clips",
    "plain": "arena clips as recorded",
    "skeleton": "clips with the tracked skeletons",
    "events": "clips with the scored events",
    "both": "clips with skeletons and events",
}
DEFAULT_CLIP_MODE = "plain"
RENDER_JOBS = 4  # annotated clips drawn in parallel
