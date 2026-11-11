from rigconductor import constants as c
from rigconductor.config import ReduceConfig
from rigconductor.presets import Preset, by_key


def test_defaults_come_from_constants():
    assert ReduceConfig().padding_px == c.REDUCE_PADDING_PX
    assert ReduceConfig().crf == c.REDUCE_CRF
    assert Preset("k", "l").padding_px == c.REDUCE_PADDING_PX
    assert by_key("drosophila_triad", "heat").padding_px == c.REDUCE_PADDING_PX


def test_overlay_colours_are_wong():
    wong = set(c.WONG.values())
    assert {c.COLOUR_RIG_LAYOUT, c.COLOUR_ARENAS_OK, c.COLOUR_ARENAS_PARTIAL} <= wong


def test_clips_are_on_by_default():
    from rigconductor.config import ExportConfig
    from rigconductor.session import ConductorSpec

    assert c.DEFAULT_CLIP_MODE == "plain" and ExportConfig().clips == "plain"
    assert ConductorSpec("p", "s", "t", 1).clips == "plain"
    assert set(c.CLIP_MODES) == {"none", "plain", "skeleton", "events", "both"}
