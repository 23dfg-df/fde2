import copy

import pytest

from scripts.model_overrides import build_overrides


def test_rope_bridge_preserves_original_and_parameters():
    original = {"model_type": "hunyuan_vl", "text_config": {"rope_parameters": {"rope_type": "xdrope", "xdrope_section": [16, 16, 16, 16], "factor": 1.0, "alpha": 1000.0}}}
    before = copy.deepcopy(original)
    override = build_overrides(original)
    assert original == before
    assert override["text_config"]["rope_scaling"] == original["text_config"]["rope_parameters"]


def test_rope_bridge_rejects_unknown_config_and_conflict():
    with pytest.raises(ValueError):
        build_overrides({"model_type": "other"})
    with pytest.raises(ValueError):
        build_overrides({"model_type": "hunyuan_vl", "text_config": {"rope_parameters": {"rope_type": "xdrope", "xdrope_section": [16, 16, 16, 16]}, "rope_scaling": {"factor": 2}}})
