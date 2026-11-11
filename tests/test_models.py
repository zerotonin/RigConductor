import pytest

from rigconductor import ConductorError
from rigconductor.models import Model, by_id, models_for, parse_registry

TXT = """
- id: flies13_base
  organism: drosophila
  kind: sleap-topdown
  trained: 2026-11-05
  validation: {frames: 0}
  notes: public flies13 only; does not transfer
  deprecated: true
- id: flies13_lab_v1
  organism: drosophila
  kind: sleap-topdown
  run: flies13_lab_v1
  trained: 2026-11-20
  validation: {mAP: 0.82, frames: 150}
- id: tad_sleap_oct26
  organism: xenopus
  kind: tadpose-assign
  env: sleap
"""


def test_parse_and_filter():
    ms = parse_registry(TXT)
    assert [m.id for m in ms] == ["flies13_base", "flies13_lab_v1", "tad_sleap_oct26"]
    assert ms[0].run == "flies13_base"  # default run = id
    fl = models_for(ms, "drosophila")
    assert [m.id for m in fl] == ["flies13_lab_v1"]
    assert len(models_for(ms, "drosophila", include_deprecated=True)) == 2
    assert models_for(ms, "xenopus")[0].env == "sleap"


def test_label_shows_date_and_headline_metric():
    m = parse_registry(TXT)[1]
    assert m.label == "flies13_lab_v1 · 2026-11-20 · mAP 0.82"
    assert "(deprecated)" in parse_registry(TXT)[0].label


def test_by_id_and_errors():
    ms = parse_registry(TXT)
    assert by_id(ms, "tad_sleap_oct26").kind == "tadpose-assign"
    with pytest.raises(ConductorError):
        by_id(ms, "nope")
    with pytest.raises(ConductorError):
        parse_registry("- id: x\n  organism: y\n")


def test_dict_form_and_empty():
    assert parse_registry("") == []
    ms = parse_registry("models:\n- id: a\n  organism: o\n  kind: k\n")
    assert isinstance(ms[0], Model)


def test_promoted_model_is_the_default():
    from rigconductor.models import default_for, parse_registry

    ms = parse_registry(
        "- {id: a, organism: drosophila, kind: sleap-topdown}\n"
        "- {id: b, organism: drosophila, kind: sleap-topdown, default: true}\n"
        "- {id: c, organism: drosophila, kind: sleap-topdown, default: true}\n"
        "- {id: t, organism: xenopus, kind: dlc}\n"
    )
    assert default_for(ms, "drosophila") == "c" and default_for(ms, "xenopus") is None
