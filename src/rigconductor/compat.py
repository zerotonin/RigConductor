# ╔══════════════════════════════════════════════════════════════════╗
# ║  Rig Conductor — compat                                          ║
# ║  « does this AI model fit these videos? »                        ║
# ╠══════════════════════════════════════════════════════════════════╣
# ║  Model-vs-clips and model-vs-camera checks against the           ║
# ║  registry's 'expects' block, before recording and submit.        ║
# ╚══════════════════════════════════════════════════════════════════╝
"""Model-vs-data check: does the chosen AI model expect the kind of video we are sending?

Run before every cluster submission. Each registry entry may carry an ``expects`` block
describing the data the model was trained on::

    expects:
      channels: 1            # 1 = grayscale, 3 = colour
      polarity: bright       # animals bright on dark ("bright") or dark on light ("dark")
      invert: true           # the pipeline inverts our clips before inference
      min_side_px: 224       # clips smaller than this cannot hold one instance crop
      fps: [25, 30]          # frame rates the model was trained at (tolerance 20 %)
      input_size: 1024       # model resizes every frame to this (resize_and_pad_to_target);
                             # clips more than 10 % off change the animals' apparent size

Missing keys are not checked. Problems found on 2026-11-05/11 that motivated this:
flies13 was trained on bright flies on dark (ours are dark on white, so every
prediction failed), and fine-tuning crashed because our frames were 3-channel
and the base weights 1-channel. Then the fine-tune learned nothing because the
inherited config resized our 512 px clips to flies13's 1024 px, doubling the flies.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

from rigconductor.doctor import Check
from rigconductor.models import Model

# Pose models see single frames, so a frame-rate mismatch only warns for them;
# behaviour classifiers learn durations and speeds, so it blocks.
POSE_KINDS = {"sleap-topdown", "sleap-bottomup", "dlc"}

GRAY_FORMATS = {"gray", "gray8", "gray16le", "gray10le", "gray12le", "y8"}


@dataclass
class ClipInfo:
    path: Path
    width: int
    height: int
    fps: float
    pix_fmt: str
    mean_intensity: float | None  # 0..255 of the first decoded frame

    @property
    def channels_stored(self) -> int:
        return 1 if self.pix_fmt in GRAY_FORMATS else 3

    @property
    def polarity(self) -> str | None:
        """'dark' animals on a light background when the frame is mostly bright."""
        if self.mean_intensity is None:
            return None
        return "dark" if self.mean_intensity > 127 else "bright"


def probe_clip(path: Path, run=subprocess.run) -> ClipInfo:
    r = run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-of",
            "json",
            "-show_entries",
            "stream=width,height,r_frame_rate,pix_fmt",
            str(path),
        ],
        capture_output=True,
        text=True,
    )
    s = json.loads(r.stdout or "{}").get("streams", [{}])[0]
    num, _, den = str(s.get("r_frame_rate", "0/1")).partition("/")
    fps = float(num) / float(den or 1) if float(den or 1) else 0.0
    g = run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-i",
            str(path),
            "-frames:v",
            "1",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "gray",
            "-",
        ],
        capture_output=True,
    )
    data = g.stdout if isinstance(g.stdout, bytes | bytearray) else b""
    mean = sum(data) / len(data) if data else None
    return ClipInfo(
        Path(path),
        int(s.get("width", 0)),
        int(s.get("height", 0)),
        fps,
        str(s.get("pix_fmt", "")),
        mean,
    )


def check_model_vs_clips(
    model: Model, clips: list[ClipInfo], invert: bool | None = None
) -> list[Check]:
    """Compare the model's ``expects`` block with the probed clips.

    ``invert`` is what the pipeline will do to the clips before inference
    (``None`` = take the model's ``expects.invert``)."""
    exp = model.expects or {}
    out: list[Check] = []
    if not clips:
        return [Check("Model vs data", False, "no clips to check", "Run the cut step first")]
    if not exp:
        return [
            Check(
                "Model vs data",
                False,
                f"model {model.id} has no 'expects' block",
                "Add the training-data description to the registry entry",
            )
        ]
    name = f"{model.id} vs clips"

    pol = {c.polarity for c in clips if c.polarity}
    if "polarity" in exp and pol:
        inv = exp.get("invert", False) if invert is None else invert
        seen = {("bright" if p == "dark" else "dark") if inv else p for p in pol}
        ok = seen == {exp["polarity"]}
        out.append(
            Check(
                f"{name}: polarity",
                ok,
                f"model wants {exp['polarity']} animals, clips give {sorted(seen)}"
                f"{' after inversion' if inv else ''}",
                ""
                if ok
                else "Toggle inversion for this model or pick a model trained on this lighting",
            )
        )

    if "channels" in exp:
        chans = {c.channels_stored for c in clips}
        ok = chans == {exp["channels"]} or (exp["channels"] == 1 and model.grayscale_input)
        conv = " (model converts to gray)" if ok and chans != {1} else ""
        out.append(
            Check(
                f"{name}: channels",
                ok,
                f"model trained on {exp['channels']}-channel input, clips stored as "
                f"{sorted(chans)}-channel{conv}",
                ""
                if ok
                else "Set ensure_grayscale in the model's config, or re-encode the clips as gray",
            )
        )

    if "min_side_px" in exp:
        small = [c.path.name for c in clips if min(c.width, c.height) < exp["min_side_px"]]
        out.append(
            Check(
                f"{name}: frame size",
                not small,
                f"all clips ≥ {exp['min_side_px']} px"
                if not small
                else f"{len(small)} clips smaller than {exp['min_side_px']} px",
                "" if not small else "Increase the arena crop padding",
            )
        )

    if "input_size" in exp:
        want = float(exp["input_size"])
        off = sorted(
            {max(c.width, c.height) for c in clips if abs(max(c.width, c.height) / want - 1) > 0.1}
        )
        out.append(
            Check(
                f"{name}: input size",
                not off,
                f"model resizes frames to {want:.0f} px; clips are "
                f"{sorted({max(c.width, c.height) for c in clips})} px",
                ""
                if not off
                else "Animals would be rescaled before inference: retrain with this clip size "
                "or set the model's target size to the clips",
            )
        )

    if "fps" in exp:
        lo, hi = (exp["fps"], exp["fps"]) if isinstance(exp["fps"], int | float) else exp["fps"]
        bad = sorted({round(c.fps) for c in clips if not lo * 0.8 <= c.fps <= hi * 1.2})
        rates = sorted({round(c.fps) for c in clips})
        out.append(
            Check(
                f"{name}: frame rate",
                not bad,
                f"model trained at {lo}–{hi} fps; clips at {rates} fps",
                ""
                if not bad
                else "Pose models look at single frames, so this is usually fine; scores "
                "use the real frame rate. Retrain with these clips if poses get worse"
                if model.kind in POSE_KINDS
                else "Record at the model's frame rate, or retrain with "
                "frames at this rate (speeds and bout durations depend on it)",
                blocking=model.kind not in POSE_KINDS,
            )
        )
    return out


# ── before recording: model vs camera + preset ──────────────────────────


@dataclass
class CameraInfo:
    pixel_format: str | None  # e.g. "Mono8", "BayerRG8", "RGB8"
    fps: float | None  # AcquisitionFrameRate
    model_name: str | None = None

    @property
    def colour(self) -> bool | None:
        if not self.pixel_format:
            return None
        return not self.pixel_format.lower().startswith("mono")


def camera_info(client) -> CameraInfo:
    """Read what matters from a MotifClient (missing settings → None)."""

    def get(name):
        try:
            return client.read(name)
        except Exception:  # noqa: BLE001
            return None

    fps = get("AcquisitionFrameRate")
    return CameraInfo(get("PixelFormat"), float(fps) if fps else None, get("DeviceModelName"))


CHANNELS = "channels"
CAMERA_FPS = "camera fps"


def check_model_vs_camera(model: Model, cam: CameraInfo, preset) -> list[Check]:
    """Checks to run on the first page, before anything is recorded.

    ``Check.name`` ends in ``CHANNELS`` or ``CAMERA_FPS`` for the two problems the
    GUI offers a fix for (convert to grayscale / set the camera frame rate)."""
    out: list[Check] = []
    exp = model.expects or {}
    name = f"{model.id} vs camera"
    if cam.colour is not None and "channels" in exp:
        gray_out = (not cam.colour) or preset.gray
        if exp["channels"] == 1:
            ok = gray_out or model.grayscale_input
            detail = (
                "camera is mono"
                if not cam.colour
                else "colour camera, preset converts to grayscale"
                if preset.gray
                else "colour camera, model converts to gray itself"
                if ok
                else "colour camera, but this model reads grayscale only"
            )
            hint = "" if ok else "Convert the videos to grayscale, or pick a colour model"
        else:
            ok = not gray_out
            detail = f"model wants {exp['channels']}-channel colour, " + (
                "camera is mono" if not cam.colour else "preset converts to grayscale"
            )
            if ok:
                detail = "colour camera, colour model"
            hint = (
                ""
                if ok
                else "Pick a grayscale model"
                + ("" if not cam.colour else " or switch the preset to colour")
            )
        out.append(Check(f"{name}: {CHANNELS}", ok, detail, hint))
    if cam.fps and preset.fps:
        ok = abs(cam.fps - preset.fps) <= 0.5
        out.append(
            Check(
                f"{name}: {CAMERA_FPS}",
                ok,
                f"camera runs at {cam.fps:g} fps, preset '{preset.key}' needs {preset.fps:g} fps",
                ""
                if ok
                else f"Set the camera to {preset.fps:g} fps. Otherwise the clips play "
                f"{cam.fps / preset.fps:.2f}× too slow and every speed and duration is wrong",
            )
        )
    if cam.fps and "fps" in exp:
        lo, hi = (exp["fps"], exp["fps"]) if isinstance(exp["fps"], int | float) else exp["fps"]
        want = preset.fps or cam.fps
        ok = lo * 0.8 <= want <= hi * 1.2
        out.append(
            Check(
                f"{name}: model fps",
                ok,
                f"model trained at {exp['fps']} fps, recording at {want:g} fps",
                ""
                if ok
                else "Pose models look at single frames, so this is usually fine; motion "
                "blur and how far animals move between frames differ from the training data"
                if model.kind in POSE_KINDS
                else "Pick a model trained at this frame rate",
                blocking=model.kind not in POSE_KINDS,
            )
        )
    return out
