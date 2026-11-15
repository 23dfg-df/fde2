"""Bridge the pinned HF model's RoPE schema to vLLM 0.18.1 at runtime.

The original config and weights stay unchanged. Both runner dimension detection
and Hunyuan image-position construction read rope_scaling in this vLLM release.
"""
import json
import sys
from pathlib import Path


def build_overrides(config):
    if config.get("model_type") != "hunyuan_vl":
        raise ValueError("This compatibility bridge is only for hunyuan_vl.")
    text = config["text_config"]
    parameters = text["rope_parameters"]
    if parameters.get("rope_type") != "xdrope" or parameters.get("xdrope_section") != [16, 16, 16, 16]:
        raise ValueError("Unexpected positional encoding; review this model revision.")
    if text.get("rope_scaling") not in (None, parameters):
        raise ValueError("Conflicting rope_scaling; refusing to silently replace it.")
    return {"text_config": {"rope_scaling": dict(parameters)}}


if __name__ == "__main__":
    config = json.loads((Path(sys.argv[1]) / "config.json").read_text())
    print(json.dumps(build_overrides(config)))
