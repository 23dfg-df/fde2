"""Validate the public single-GPU launcher without CUDA or model weights."""
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest


@pytest.fixture
def launcher(tmp_path):
    root = Path(__file__).resolve().parents[1]
    work = tmp_path / "project"
    (work / "scripts").mkdir(parents=True)
    for name in ["start_model.sh", "model_overrides.py"]:
        shutil.copyfile(root / "scripts" / name, work / "scripts" / name)
    model = work / "model"
    model.mkdir()
    (model / "download-manifest.json").write_text("{}")
    (model / "config.json").write_text(json.dumps({"model_type": "hunyuan_vl", "text_config": {"rope_parameters": {"rope_type": "xdrope", "xdrope_section": [16, 16, 16, 16], "factor": 1.0}}}))
    fake_bin = work / "fake-bin"
    fake_bin.mkdir()
    smi = fake_bin / "nvidia-smi"
    smi.write_text('#!/bin/sh\nprintf "%s\\n" "$*" > "$TEST_SMI_LOG"\nprintf "%s\\n" "$TEST_FREE_MIB"\n')
    smi.chmod(0o755)
    vllm = work / ".venv-model/bin/vllm"
    vllm.parent.mkdir(parents=True)
    vllm.write_text('#!/usr/bin/env python3\nimport json,os,sys\nfrom pathlib import Path\nPath(os.environ["TEST_VLLM_LOG"]).write_text(json.dumps({"gpu":os.environ["CUDA_VISIBLE_DEVICES"],"args":sys.argv[1:]}))\n')
    vllm.chmod(0o755)
    env = {**os.environ, "PATH": str(fake_bin) + os.pathsep + os.environ["PATH"], "OCR_MODEL_PATH": str(model), "OCR_PHYSICAL_GPU": "0", "TEST_FREE_MIB": "30000", "TEST_SMI_LOG": str(work / "smi.json"), "TEST_VLLM_LOG": str(work / "vllm.json")}
    def run(**overrides):
        return subprocess.run(["bash", "scripts/start_model.sh"], cwd=work, env={**env, **overrides}, capture_output=True, text=True, timeout=10)
    return run, work


def test_launcher_uses_selected_physical_gpu(launcher):
    run, work = launcher
    result = run(OCR_PHYSICAL_GPU="0", OCR_GPU_MEMORY_UTILIZATION="0.55")
    assert result.returncode == 0, result.stderr
    assert "--id=0 " in (work / "smi.json").read_text()
    launched = json.loads((work / "vllm.json").read_text())
    assert launched["gpu"] == "0"
    args = launched["args"]
    assert args[args.index("--gpu-memory-utilization") + 1] == "0.55"
    assert args[args.index("--max-num-seqs") + 1] == "1"
    assert json.loads(args[args.index("--hf-overrides") + 1])["text_config"]["rope_scaling"]["xdrope_section"] == [16, 16, 16, 16]


def test_launcher_refuses_insufficient_free_memory(launcher):
    run, work = launcher
    result = run(TEST_FREE_MIB="24575")
    assert result.returncode != 0
    assert "24 GiB" in result.stderr
    assert not (work / "vllm.json").exists()


def test_launcher_refuses_multiple_gpu_selector(launcher):
    run, work = launcher
    result = run(OCR_PHYSICAL_GPU="0,1")
    assert result.returncode != 0
    assert not (work / "smi.json").exists()
    assert not (work / "vllm.json").exists()


def test_user_services_preserve_selected_gpu(launcher):
    run, work = launcher
    root = Path(__file__).resolve().parents[1]
    shutil.copyfile(root / "scripts/install_user_services.py", work / "scripts/install_user_services.py")
    systemctl = work / "fake-bin/systemctl"
    systemctl.write_text('#!/bin/sh\nexit 0\n')
    systemctl.chmod(0o755)
    home = work / "test-home"
    env = {**os.environ, "HOME": str(home), "OCR_PHYSICAL_GPU": "0", "PATH": str(work / "fake-bin") + os.pathsep + os.environ["PATH"]}
    result = subprocess.run(["python3", "scripts/install_user_services.py"], cwd=work, env=env, capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    for kind in ("model", "app"):
        unit = (home / f".config/systemd/user/fde-doc-{kind}.service").read_text()
        assert "Environment=OCR_PHYSICAL_GPU=0" in unit
        assert "Restart=no" in unit
    assert "RuntimeMaxSec=28800" in (home / ".config/systemd/user/fde-doc-model.service").read_text()
