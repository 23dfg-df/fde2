"""Install only this project's user-level units; no boot enablement."""
from pathlib import Path
import subprocess
import os
import re

physical_gpu = os.getenv("OCR_PHYSICAL_GPU", "1")
if not re.fullmatch(r"[0-9]+", physical_gpu):
    raise ValueError("OCR_PHYSICAL_GPU must select one physical GPU index.")
root = Path(__file__).resolve().parents[1]
units = Path.home() / ".config/systemd/user"
units.mkdir(parents=True, exist_ok=True)
(root / "logs").mkdir(exist_ok=True)
for kind in ("model", "app"):
    extra = "RuntimeMaxSec=28800\n" if kind == "model" else ""
    content = f"""[Unit]
Description=FDE Document Lab {kind}

[Service]
Type=simple
Environment=OCR_PHYSICAL_GPU={physical_gpu}
WorkingDirectory={root}
ExecStart=/bin/bash {root}/scripts/start_{kind}.sh
Restart=no
TimeoutStopSec=45
KillMode=control-group
{extra}StandardOutput=append:{root}/logs/{kind}.log
StandardError=append:{root}/logs/{kind}.log

[Install]
WantedBy=default.target
"""
    (units / f"fde-doc-{kind}.service").write_text(content)
subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
print("已安装 fde-doc-model / fde-doc-app；未启用开机自启。模型单次运行上限8小时。")
