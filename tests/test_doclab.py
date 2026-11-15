import io
import json
import threading
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from backend.config import Settings
from backend.evaluation import evaluate
from backend.images import MAX_FILE_BYTES
from backend.main import create_app
from backend.service import Service


MODEL = "tencent/HunyuanOCR"


def png(width=200, height=300):
    data = io.BytesIO()
    Image.new("RGB", (width, height), "white").save(data, "PNG")
    return data.getvalue()


def answer(text="实验室通知", finish="stop", usage=True):
    body = {"model": MODEL, "choices": [{"message": {"content": text}, "finish_reason": finish}]}
    if usage:
        body["usage"] = {"prompt_tokens": 20, "completion_tokens": 8}
    return httpx.Response(200, json=body)


@pytest.fixture
def factory(tmp_path):
    services, clients = [], []
    def make(handler=None, ready=True):
        root = tmp_path / str(len(services))
        (root / "samples").mkdir(parents=True)
        (root / "web").mkdir()
        (root / "web/index.html").write_text("<h1>Document Lab</h1>")
        rows = []
        for index in (1, 2):
            filename = f"{index}.png"
            (root / "samples" / filename).write_bytes(png())
            rows.append({"id": f"sample-{index}", "name": f"样本{index}", "image": filename,
                         "kind": "synthetic", "reference_text": "实验室通知", "evaluation_mode": "text"})
        (root / "samples/manifest.json").write_text(json.dumps({"pages": rows}))
        def handle(request):
            if request.url.path.endswith("/models"):
                return httpx.Response(200, json={"data": [{"id": MODEL}] if ready else []})
            return handler(request) if handler else answer()
        client = httpx.Client(transport=httpx.MockTransport(handle))
        service = Service(Settings(root=root, data=root / "data"), client, gpu_reader=lambda _: None)
        services.append(service); clients.append(client)
        return TestClient(create_app(service.settings, service)), service
    yield make
    for service in services:
        service.close()
    for client in clients:
        client.close()


def wait(service):
    service.worker.join(timeout=4)
    assert not service.worker.is_alive(), "mock worker did not finish"


def test_complete_real_contract_and_export(factory):
    captured = []
    def handler(request):
        captured.append(json.loads(request.content))
        return answer()
    client, service = factory(handler)
    with client:
        assert client.get("/").status_code == 200
        assert client.get("/api/status").json()["model_ready"] is True
        page = client.get("/api/pages").json()["pages"][0]
        assert page["has_reference"] is True and "path" not in page
        assert client.get(page["image_url"]).headers["content-type"] == "image/png"
        response = client.post("/api/jobs", json={"page_ids": ["sample-1"], "task": "structured_parse"})
        assert response.status_code == 202
        job_id = response.json()["id"]
        wait(service)
        job = client.get(f"/api/jobs/{job_id}").json()
        result = job["pages"][0]
        assert job["status"] == "completed" and result["evaluation"]["cer"] == 0
        assert result["ttft_client_seconds"] is None
        assert result["input_tokens"] == 20 and result["output_tokens"] == 8
        assert result["gpu_before"] is None
        assert result["raw_output"] == "实验室通知"
        assert job["summary"]["semantic_accuracy"] is None
        assert client.get(f"/api/jobs/{job_id}/export").json()["id"] == job_id
        assert "实验室通知" in client.get(f"/api/jobs/{job_id}/export?format=markdown").text
        assert client.get("/api/jobs").json()["jobs"][0]["id"] == job_id
    payload = captured[0]
    assert payload["stream"] is False and payload["max_tokens"] == 2048
    assert payload["messages"][0]["content"][0]["image_url"]["url"].startswith("data:image/png;base64,")
    assert payload["messages"][0]["content"][1]["text"] == "提取图中的文字。"
    assert payload["repetition_penalty"] == 1.08 and payload["top_k"] == -1


@pytest.mark.parametrize("task", ["doc_parse", "table"])
def test_full_text_reference_requires_matching_task(factory, task):
    client, service = factory()
    with client:
        response = client.post("/api/jobs", json={"page_ids": ["sample-1"], "task": task})
        assert response.status_code == 202
        wait(service)
        job = client.get(f"/api/jobs/{response.json()['id']}").json()
        assert job["status"] == "completed"
        evaluation = job["pages"][0]["evaluation"]
        assert evaluation["available"] is False
        assert evaluation["cer"] is None and evaluation["edit_distance"] is None
        assert "口径不一致" in evaluation["note"]


def test_offline_page_browsing_does_not_fake_inference(factory):
    client, service = factory(ready=False)
    with client:
        assert client.get("/api/pages").status_code == 200
        assert client.get("/api/status").json()["model_ready"] is False
        assert client.post("/api/jobs", json={"page_ids": ["sample-1"]}).status_code == 503
        assert service.repository.jobs() == []


@pytest.mark.parametrize("body,status", [
    ({"page_ids": []}, 422),
    ({"page_ids": ["missing"]}, 404),
    ({"page_ids": ["sample-1", "sample-1"]}, 400),
    ({"page_ids": ["sample-1"], "max_tokens": 4097}, 422),
    ({"page_ids": ["sample-1"], "task": "arbitrary"}, 422),
    ({"page_ids": ["sample-1"], "model": "other"}, 422),
])
def test_job_input_limits(factory, body, status):
    client, _ = factory()
    with client:
        assert client.post("/api/jobs", json=body).status_code == status


def test_busy_and_cancel_keep_current_page_then_skip_next(factory):
    entered, release = threading.Event(), threading.Event()
    calls = []
    def handler(request):
        calls.append(request)
        entered.set()
        assert release.wait(timeout=3)
        return answer()
    client, service = factory(handler)
    with client:
        job_id = client.post("/api/jobs", json={"page_ids": ["sample-1", "sample-2"]}).json()["id"]
        assert entered.wait(timeout=2)
        assert client.post("/api/jobs", json={"page_ids": ["sample-1"]}).status_code == 409
        cancelled = client.post(f"/api/jobs/{job_id}/cancel")
        assert cancelled.json()["cancel_requested"] is True
        release.set(); wait(service)
        job = client.get(f"/api/jobs/{job_id}").json()
        assert job["status"] == "cancelled"
        assert [x["status"] for x in job["pages"]] == ["completed", "cancelled"]
        assert len(calls) == 1 and job["summary"]["total"] == 2


@pytest.mark.parametrize("response,expected", [
    (answer("unfinished", "length"), "partial"),
    (answer("", "stop"), "failed"),
    (httpx.Response(500, json={"error": "error"}), "failed"),
    (httpx.Response(200, text="not-json"), "failed"),
])
def test_partial_and_failed_are_not_counted_as_completed(factory, response, expected):
    client, service = factory(lambda _: response)
    with client:
        job_id = client.post("/api/jobs", json={"page_ids": ["sample-1"]}).json()["id"]
        wait(service)
        job = client.get(f"/api/jobs/{job_id}").json()
        assert job["status"] == expected
        assert job["pages"][0]["status"] == expected
        assert job["summary"]["completed"] == 0 and job["summary"]["total"] == 1
        assert job["pages"][0]["error"]


def test_missing_usage_remains_null(factory):
    client, service = factory(lambda _: answer(usage=False))
    with client:
        job_id = client.post("/api/jobs", json={"page_ids": ["sample-1"]}).json()["id"]
        wait(service)
        result = client.get(f"/api/jobs/{job_id}").json()["pages"][0]
        assert result["output_tokens"] is None
        assert result["average_output_tokens_per_second"] is None


def test_image_upload_resize_sanitized_id_and_persistence(factory):
    client, service = factory()
    with client:
        response = client.post("/api/uploads", files={"file": ("../../picture.png", png(2400, 1600), "image/png")})
        assert response.status_code == 201
        page = response.json()["pages"][0]
        assert page["width"] == 1600 and page["height"] <= 1600
        assert "../" not in page["id"] and "../" not in page["name"]
        assert page["has_reference"] is False
        assert client.get(page["image_url"]).status_code == 200
        assert service.repository.pages()[0]["id"] == page["id"]


@pytest.mark.parametrize("name,raw,status", [
    ("x.png", b"not a real image", 400),
    ("x.pdf", b"not a pdf", 400),
    ("x.svg", b"<svg></svg>", 400),
    ("x.png", b"x"*(MAX_FILE_BYTES+1), 413),
])
def test_upload_failures_are_bounded(factory, name, raw, status):
    client, _ = factory()
    with client:
        assert client.post("/api/uploads", files={"file": (name, raw)}).status_code == status


def test_pdf_rasterization_and_page_limit(factory):
    import fitz
    client, _ = factory()
    with fitz.open() as document:
        document.new_page().insert_text((50,50), "Synthetic upload fixture")
        single = document.tobytes()
        for _ in range(6):
            document.new_page()
        too_many = document.tobytes()
    with client:
        success = client.post("/api/uploads", files={"file": ("sample.pdf", single)})
        assert success.status_code == 201
        assert len(success.json()["pages"]) == 1
        assert client.post("/api/uploads", files={"file": ("sample.pdf", too_many)}).status_code == 400


def test_normalized_cer_counts_edits_and_can_exceed_one():
    page = {"evaluation_mode": "text", "reference_text": "Ａ B中"}
    assert evaluate(page, "AB 中")["cer"] == 0
    assert evaluate(page, "AB国")["edit_distance"] == 1
    assert evaluate({"evaluation_mode": "text", "reference_text": "a"}, "abcdef")["cer"] == 5
    assert evaluate({}, "anything")["available"] is False


def test_field_and_table_coverage_do_not_claim_semantic_accuracy():
    fields = evaluate({"evaluation_mode": "fields", "expected_fields": {"city": "北京", "asset": "S-02"}}, "北京")
    assert fields["field_coverage"] == .5 and fields["missing_fields"] == ["asset"]
    table = {"evaluation_mode": "table", "expected_cells": ["名称", "1", "1"]}
    result = evaluate(table, "<table><tr><th>名称</th><td>1</td></tr></table>")
    assert result["matched_cell_count"] == 2 and result["expected_cell_count"] == 3
    assert result["semantic_accuracy"] is None and result["human_verified"] is False
    assert evaluate(table, "名称1")["matched_cell_count"] == 0


def test_restart_marks_incomplete_job_interrupted(factory):
    _, service = factory()
    service.repository.save_job({"id": "unfinished", "status": "running", "created_at": "2026-01-01", "pages": [{"status": "running"}]})
    recovered = Service(service.settings, service.client, gpu_reader=lambda _: None)
    assert recovered.job("unfinished")["status"] == "interrupted"
    assert recovered.job("unfinished")["pages"][0]["status"] == "failed"
