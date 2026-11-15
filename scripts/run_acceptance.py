"""Bounded real-model acceptance: six fixtures and one PDF through the API."""
import datetime
import io
import json
import time
from pathlib import Path

import httpx
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "evidence"
OUT.mkdir(exist_ok=True)
client = httpx.Client(base_url="http://127.0.0.1:18400", timeout=15, trust_env=False)


def save(name, value):
    (OUT / name).write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def run_job(page_ids, task, label):
    response = client.post("/api/jobs", json={"page_ids": page_ids, "task": task, "max_tokens": 2048})
    response.raise_for_status()
    job = response.json()
    deadline = time.monotonic() + 900
    while job["status"] in {"queued", "running"}:
        if time.monotonic() >= deadline:
            client.post(f"/api/jobs/{job['id']}/cancel")
            raise TimeoutError("Acceptance deadline reached; cancellation requested.")
        time.sleep(2)
        result = client.get(f"/api/jobs/{job['id']}")
        result.raise_for_status()
        job = result.json()
    save(label + ".json", job)
    export = client.get(f"/api/jobs/{job['id']}/export")
    export.raise_for_status()
    assert export.json() == job
    print(json.dumps({"label": label, "job_id": job["id"], "status": job["status"], "pages": [{"id": p["page_id"], "status": p["status"], "elapsed_seconds": p.get("elapsed_seconds"), "tokens": p.get("output_tokens"), "evaluation": p.get("evaluation"), "error": p.get("error")} for p in job["pages"]]}, ensure_ascii=False), flush=True)
    return job


status = client.get("/api/status").json()
save("status-before-acceptance.json", status)
if not status.get("model_ready"):
    raise RuntimeError("Model is not ready; no workload was submitted.")
jobs = []
jobs.append(run_job(["sample-01"], "structured_parse", "01-chinese-smoke"))
if jobs[0]["status"] != "completed":
    raise RuntimeError("First real page failed; do not continue batch blindly.")
jobs.append(run_job(["sample-02", "sample-03", "sample-05", "sample-06"], "structured_parse", "02-text-and-form"))
jobs.append(run_job(["sample-04"], "table", "03-table"))

# Exercise the actual PDF upload/rasterization route with an original fixture.
pdf = io.BytesIO()
with Image.open(ROOT / "samples/01-notice.png") as image:
    image.convert("RGB").save(pdf, "PDF", resolution=144.0)
response = client.post("/api/uploads", files={"file": ("synthetic-notice.pdf", pdf.getvalue(), "application/pdf")})
response.raise_for_status()
uploaded = response.json()["pages"]
save("04-pdf-upload.json", uploaded)
jobs.append(run_job([uploaded[0]["id"]], "doc_parse", "04-pdf-doc-parse"))
jobs.append(run_job(["sample-01"], "structured_parse", "05-chinese-warm"))
status = client.get("/api/status").json()
save("status-after-acceptance.json", status)
summary = {"recorded_at": datetime.datetime.now(datetime.timezone.utc).isoformat(), "model": status["model"], "model_revision": status["model_revision"], "physical_gpu": 1, "sample_pages": 6, "additional_pdf_pages": 1, "additional_warm_repeat_pages": 1, "jobs": [{"id": j["id"], "task": j["task"], "status": j["status"], "summary": j["summary"]} for j in jobs], "pages": [{"task": j["task"], **p} for j in jobs for p in j["pages"]], "human_verified": False, "scope": "synthetic_fixture_acceptance_not_business_accuracy"}
save("acceptance-summary.json", summary)
print("ACCEPTANCE_RECORDED", flush=True)
