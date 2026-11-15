"""Record sampled whole-device usage, never claim a true process peak."""
import argparse
import csv
import datetime
import json
import subprocess
import time
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("--output", type=Path, required=True)
parser.add_argument("--seconds", type=int, default=900)
args = parser.parse_args()
args.output.parent.mkdir(parents=True, exist_ok=True)
end = time.monotonic() + args.seconds
with args.output.open("w") as stream:
    while time.monotonic() < end:
        result = subprocess.run(["nvidia-smi", "--query-gpu=index,memory.used,memory.free,utilization.gpu,power.draw", "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=5)
        rows = list(csv.reader(result.stdout.splitlines())) if result.returncode == 0 else []
        record = {"sampled_at": datetime.datetime.now(datetime.timezone.utc).isoformat(), "scope": "whole_device_sample_not_process_peak", "gpus": [{"index": int(r[0]), "used_mib": int(r[1]), "free_mib": int(r[2]), "utilization_percent": int(r[3]), "power_watts": float(r[4])} for r in rows]}
        stream.write(json.dumps(record) + "\n")
        stream.flush()
        time.sleep(1)
