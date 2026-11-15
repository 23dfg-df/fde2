"""Provide Python 3.12 headers in this project, without changing the OS."""
import argparse
import json
import shutil
import subprocess
from pathlib import Path

root = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser()
parser.add_argument("--copy-from", type=Path, help="An existing extracted usr/include directory")
args = parser.parse_args()
target = root / ".tools/python-dev"
if args.copy_from:
    if not (args.copy_from / "python3.12/Python.h").is_file():
        raise FileNotFoundError("Source has no Python 3.12 header.")
    shutil.copytree(args.copy_from, target / "usr/include", dirs_exist_ok=True)
    source = str(args.copy_from)
else:
    downloads = root / ".tools/header-download"
    downloads.mkdir(parents=True, exist_ok=True)
    subprocess.run(["apt-get", "download", "libpython3.12-dev"], cwd=downloads, check=True)
    packages = list(downloads.glob("libpython3.12-dev_*.deb"))
    if len(packages) != 1:
        raise RuntimeError("Expected one development-header archive.")
    subprocess.run(["dpkg-deb", "--extract", str(packages[0]), str(target)], check=True)
    source = packages[0].name
assert (target / "usr/include/python3.12/Python.h").is_file()
(root / "evidence/python-headers.json").write_text(json.dumps({"source": source, "destination": ".tools/python-dev", "system_packages_modified": False}, indent=2))
print("Project-local Python 3.12 headers ready.")
