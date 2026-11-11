from rigconductor.compat import CAMERA_FPS, CHANNELS, CameraInfo, check_model_vs_camera
from rigconductor.models import Model
from rigconductor.presets import Preset, by_key, presets_for


def _model(**kw):
    d = {
        "id": "m",
        "organism": "drosophila",
        "kind": "sleap-topdown",
        "run": "r",
        "expects": {"channels": 1, "fps": 25},
        "grayscale_input": False,
    }
    d.update(kw)
    return Model(**d)


def test_builtin_heat_preset_and_rig_override(tmp_path):
    from rigconductor.config import ConductorConfig

    heat = by_key("drosophila_triad", "heat")
    assert (heat.gray, heat.fps, heat.reduce, heat.padding_px) == (True, None, True, 40)
    rig = tmp_path / "rig.yaml"
    rig.write_text(
        "profiles:\n  drosophila_triad:\n    presets:\n"
        "      heat: {padding_px: 60}\n      fast: {label: Fast, fps: 69, colour_note: x}\n",
        encoding="utf-8",
    )
    ps = {p.key: p for p in presets_for("drosophila_triad", ConductorConfig.load(rig))}
    assert ps["heat"].padding_px == 60 and ps["heat"].fps is None
    assert ps["fast"].fps == 69 and ps["fast"].extra == {"colour_note": "x"}
    assert presets_for("unknown")[0].key == "default"


def test_colour_camera_gray_model_needs_conversion():
    cam = CameraInfo("BayerRG8", 25.0)
    keep_colour = Preset("c", "c", gray=False, fps=25)
    (ch,) = [c for c in check_model_vs_camera(_model(), cam, keep_colour) if CHANNELS in c.name]
    assert not ch.ok and "grayscale" in ch.hint
    (ch,) = [
        c
        for c in check_model_vs_camera(_model(), cam, Preset("g", "g", gray=True, fps=25))
        if CHANNELS in c.name
    ]
    assert ch.ok
    # a model that converts by itself is fine with colour
    (ch,) = [
        c
        for c in check_model_vs_camera(_model(grayscale_input=True), cam, keep_colour)
        if CHANNELS in c.name
    ]
    assert ch.ok


def test_mono_camera_cannot_feed_colour_model():
    checks = check_model_vs_camera(
        _model(expects={"channels": 3}), CameraInfo("Mono8", 25.0), Preset("g", "g")
    )
    assert not checks[0].ok and "grayscale model" in checks[0].hint


def test_camera_fps_against_preset_blocks_and_model_fps_warns():
    # heat takes whatever the camera runs at: no camera-fps check, pose model only warns
    heat = check_model_vs_camera(
        _model(), CameraInfo("Mono8", 69.0), by_key("drosophila_triad", "heat")
    )
    assert not any(c.name.endswith(CAMERA_FPS) for c in heat)
    assert all(c.ok or not c.blocking for c in heat)
    checks = check_model_vs_camera(_model(), CameraInfo("Mono8", 69.0), Preset("f", "f", fps=25))
    fps = next(c for c in checks if c.name.endswith(CAMERA_FPS))
    assert not fps.ok and fps.blocking and "2.76× too slow" in fps.hint
    model_fps = next(c for c in checks if c.name.endswith("model fps"))
    assert model_fps.ok  # judged at the preset's 25 fps
    checks = check_model_vs_camera(
        _model(expects={"fps": 25}), CameraInfo("Mono8", 69.0), Preset("f", "f", fps=None)
    )
    assert not checks[0].ok and not checks[0].blocking  # pose model: warning only
