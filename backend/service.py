import base64
import copy
import csv
import io
import json
import subprocess
import threading
import time
import uuid
from pathlib import Path

import httpx
from PIL import Image

from .evaluation import evaluate
from .images import decode_upload
from .repository import Repository, now

TASK_PROMPTS = {
    "structured_parse": "提取图中的文字。",
    "doc_parse": "提取文档图片中正文的所有信息用markdown格式表示，其中页眉、页脚部分忽略，表格用html格式表达，文档中公式用latex格式表示，按照阅读顺序组织进行解析。",
    "table": "把图中的表格解析为HTML。",
}
TERMINAL = {"completed", "partial", "failed", "cancelled", "interrupted"}


class ServiceError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def gpu_snapshot(index):
    try:
        result = subprocess.run(
            ["nvidia-smi", f"--id={index}", "--query-gpu=index,name,memory.total,memory.used,memory.free,utilization.gpu", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=4, check=True,
        )
        row = next(csv.reader(io.StringIO(result.stdout)))
        return {"index": int(row[0]), "name": row[1].strip(),
                "memory_total_mib": int(row[2]), "memory_used_mib": int(row[3]),
                "memory_free_mib": int(row[4]), "utilization_percent": int(row[5]),
                "sampled_at": now(), "scope": "whole_device_snapshot"}
    except (OSError, ValueError, StopIteration, subprocess.SubprocessError):
        return None


class Service:
    def __init__(self, settings, client=None, gpu_reader=None):
        self.settings = settings
        self.repository = Repository(settings.data)
        self.images_dir = settings.data / "images"
        self.images_dir.mkdir(parents=True, exist_ok=True)
        self.client = client or httpx.Client(timeout=httpx.Timeout(settings.request_timeout, connect=5), trust_env=False)
        self.owns_client = client is None
        self.gpu_reader = gpu_reader or gpu_snapshot
        self.lock = threading.RLock()
        self.active_id, self.worker = None, None
        self.cancel_event = threading.Event()
        self.pages_by_id = {page["id"]: page for page in self.repository.pages()}
        self._load_samples()
        # A previous process cannot still own this in-memory worker after restart.
        for job in self.repository.jobs():
            if job["status"] not in TERMINAL:
                job.update(status="interrupted", finished_at=now(), error="应用重启中断了任务；请重新提交。")
                for page in job["pages"]:
                    if page["status"] in {"queued", "running"}:
                        page.update(status="failed", error="应用重启，未获得完整结果。")
                job["summary"] = self._summary(job)
                self.repository.save_job(job)

    def _load_samples(self):
        directory = self.settings.root / "samples"
        manifest = directory / "manifest.json"
        if not manifest.exists():
            return
        value = json.loads(manifest.read_text("utf-8"))
        pages = value if isinstance(value, list) else value["pages"]
        for source in pages:
            path = (directory / source["image"]).resolve()
            try:
                path.relative_to(directory.resolve())
            except ValueError as error:
                raise ValueError("样本路径必须位于samples目录中。") from error
            with Image.open(path) as image:
                width, height = image.size
            self.pages_by_id[source["id"]] = {
                **source, "path": str(path), "width": width, "height": height,
                "image_url": f"/api/images/{source['id']}", "origin": "sample",
            }

    @staticmethod
    def public_page(page):
        return {**{k: v for k, v in page.items() if k not in {"path", "image"}},
                "has_reference": bool(page.get("reference_text") or page.get("expected_fields") or page.get("expected_cells"))}

    def pages(self):
        with self.lock:
            return [self.public_page(p) for p in self.pages_by_id.values()]

    def image(self, page_id):
        with self.lock:
            page = self.pages_by_id.get(page_id)
            if not page:
                raise ServiceError("页面不存在。", 404)
            return Path(page["path"])

    def upload(self, filename, raw):
        decoded = decode_upload(filename, raw)
        document_id = uuid.uuid4().hex
        safe_display_name = Path((filename or "upload").replace("\\", "/")).name[:120]
        pages = []
        with self.lock:
            for number, image in enumerate(decoded, 1):
                page_id = f"upload-{document_id}-{number}"
                path = self.images_dir / f"{page_id}.png"
                image.save(path, "PNG")
                page = {"id": page_id, "name": f"{safe_display_name} · 第{number}页", "kind": "uploaded",
                        "origin": "upload", "path": str(path), "width": image.width, "height": image.height,
                        "image_url": f"/api/images/{page_id}", "evaluation_mode": "none", "created_at": now()}
                self.repository.save_page(page)
                self.pages_by_id[page_id] = page
                pages.append(self.public_page(page))
        return pages

    def model_state(self):
        try:
            response = self.client.get(self.settings.model_url + "/models", timeout=5)
            response.raise_for_status()
            rows = response.json()["data"]
            names = [row["id"] for row in rows if isinstance(row, dict) and isinstance(row.get("id"), str)]
            ready = self.settings.model in names
            return {"model_ready": ready, "available_models": names,
                    "model_error": None if ready else "服务未列出配置的模型。"}
        except (httpx.HTTPError, ValueError, KeyError, TypeError):
            return {"model_ready": False, "available_models": [], "model_error": "模型服务暂未就绪，请检查独立推理服务。"}

    def status(self):
        state = self.model_state()
        with self.lock:
            active = self.repository.job(self.active_id) if self.active_id else None
        return {**state, "model": self.settings.model, "model_revision": self.settings.model_revision,
                "model_url": self.settings.model_url, "gpu": self.gpu_reader(self.settings.physical_gpu),
                "physical_gpu": self.settings.physical_gpu, "active_job": {"id": active["id"], "status": active["status"]} if active else None,
                "limits": {"max_pages": 6, "max_file_mib": 12, "max_image_side": 1600, "max_new_tokens": 4096, "concurrent_jobs": 1},
                "sampled_at": now(), "timing_note": "客户端非流式请求墙钟时间，包含服务往返；没有GPU内部TTFT测量。"}

    def submit(self, page_ids, task, max_tokens):
        with self.lock:
            if self.active_id:
                raise ServiceError("已有任务正在运行，请等待完成或取消。", 409)
            if len(set(page_ids)) != len(page_ids):
                raise ServiceError("同一任务不能重复选择页面。")
            missing = [x for x in page_ids if x not in self.pages_by_id]
            if missing:
                raise ServiceError("选择的页面不存在，请刷新页面列表。", 404)
            if not self.model_state()["model_ready"]:
                raise ServiceError("模型服务暂未就绪，没有创建推理任务。", 503)
            job_id = uuid.uuid4().hex
            pages = [{"page_id": x, "name": self.pages_by_id[x]["name"], "image_url": self.pages_by_id[x]["image_url"],
                      "status": "queued", "text": "", "raw_output": "", "error": None, "evaluation": None} for x in page_ids]
            job = {"id": job_id, "status": "queued", "created_at": now(), "started_at": None, "finished_at": None,
                   "task": task, "prompt": TASK_PROMPTS[task], "max_tokens": max_tokens,
                   "model": self.settings.model, "model_revision": self.settings.model_revision,
                   "physical_gpu": self.settings.physical_gpu, "pages": pages, "cancel_requested": False,
                   "summary": {}, "note": "合成样本规则分数不是人工语义验收；上传文件默认没有参考答案。"}
            self.repository.save_job(job)
            self.active_id = job_id
            self.cancel_event = threading.Event()
            self.worker = threading.Thread(target=self._run, args=(job_id, self.cancel_event), daemon=True, name="ocr-single-worker")
            self.worker.start()
            return copy.deepcopy(job)

    def _infer(self, page, task, max_tokens):
        raw = Path(page["path"]).read_bytes()
        payload = {"model": self.settings.model, "messages": [{"role": "user", "content": [
            {"type": "image_url", "image_url": {"url": "data:image/png;base64," + base64.b64encode(raw).decode("ascii")}},
            {"type": "text", "text": TASK_PROMPTS[task]},
        ]}], "temperature": 0, "top_p": 1, "top_k": -1, "repetition_penalty": 1.08,
                   "max_tokens": max_tokens, "stream": False}
        before = self.gpu_reader(self.settings.physical_gpu)
        start = time.perf_counter()
        response = self.client.post(self.settings.model_url + "/chat/completions", json=payload)
        response.raise_for_status()
        body = response.json()
        choice = body["choices"][0]
        text = choice["message"]["content"]
        elapsed = time.perf_counter() - start
        if not isinstance(text, str):
            raise ValueError("模型响应没有文本content。")
        if len(text) > 80000:
            raise ValueError("模型输出超过应用记录预算。")
        usage = body.get("usage") or {}
        def tokens(key):
            value = usage.get(key)
            return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None
        output_tokens = tokens("completion_tokens")
        finish = choice.get("finish_reason")
        completed = bool(text.strip()) and finish == "stop"
        return {"status": "completed" if completed else "partial" if text.strip() else "failed",
                "text": text, "raw_output": text, "finish_reason": finish,
                "model": body.get("model"), "input_tokens": tokens("prompt_tokens"), "output_tokens": output_tokens,
                "elapsed_seconds": round(elapsed, 4), "average_output_tokens_per_second": round(output_tokens / elapsed, 3) if output_tokens is not None and elapsed else None,
                "ttft_client_seconds": None, "timing_scope": "nonstream_http_wall_seconds",
                "gpu_before": before, "gpu_after": self.gpu_reader(self.settings.physical_gpu),
                "gpu_measurement_scope": "whole_device_before_after_snapshots_not_peak",
                "error": None if completed else "生成达到上限、未正常结束或返回空文本，请人工复核。",
                "evaluation": evaluate(page, text, task=task)}

    def _run(self, job_id, cancel_event):
        job = self.repository.job(job_id)
        job.update(status="running", started_at=now())
        self.repository.save_job(job)
        try:
            for result in job["pages"]:
                if cancel_event.is_set():
                    result.update(status="cancelled", error="任务取消，该页未发送到模型。")
                    continue
                result["status"] = "running"
                self.repository.save_job(job)
                start = time.perf_counter()
                try:
                    result.update(self._infer(self.pages_by_id[result["page_id"]], job["task"], job["max_tokens"]))
                except httpx.HTTPStatusError as error:
                    result.update(status="failed", error=f"模型接口返回HTTP {error.response.status_code}。", elapsed_seconds=round(time.perf_counter()-start, 4))
                except httpx.TimeoutException:
                    result.update(status="failed", error="模型请求超时；超时不代表模型端已停止计算，请检查服务。", elapsed_seconds=round(time.perf_counter()-start, 4))
                    cancel_event.set()
                except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError, OSError) as error:
                    result.update(status="failed", error=f"响应或文件处理失败：{type(error).__name__}", elapsed_seconds=round(time.perf_counter()-start, 4))
                job["cancel_requested"] = cancel_event.is_set()
                job["summary"] = self._summary(job)
                self.repository.save_job(job)
            statuses = [x["status"] for x in job["pages"]]
            if "cancelled" in statuses:
                job["status"] = "cancelled"
            elif all(s == "completed" for s in statuses):
                job["status"] = "completed"
            elif all(s == "failed" for s in statuses):
                job["status"] = "failed"
            else:
                job["status"] = "partial"
        except Exception as error:
            job.update(status="failed", error=f"任务执行异常：{type(error).__name__}")
            for result in job["pages"]:
                if result["status"] in {"queued", "running"}:
                    result.update(status="failed", error="任务异常中断。")
        finally:
            job.update(finished_at=now(), cancel_requested=cancel_event.is_set())
            job["summary"] = self._summary(job)
            self.repository.save_job(job)
            with self.lock:
                if self.active_id == job_id:
                    self.active_id = None

    @staticmethod
    def _summary(job):
        pages = job["pages"]
        return {"total": len(pages), **{status: sum(x["status"] == status for x in pages) for status in ["queued", "running", "completed", "partial", "failed", "cancelled"]},
                "elapsed_seconds": round(sum(x.get("elapsed_seconds", 0) for x in pages), 4),
                "semantic_accuracy": None,
                "note": "completed只表示接口完整返回，不代表识别正确；失败和截断保留在总页数内。"}

    def job(self, job_id):
        job = self.repository.job(job_id)
        if not job:
            raise ServiceError("任务不存在。", 404)
        with self.lock:
            if self.active_id == job_id and self.cancel_event.is_set():
                job["cancel_requested"] = True
        return job

    def jobs(self):
        return [{k: job.get(k) for k in ("id", "status", "created_at", "finished_at", "task", "summary", "model", "model_revision")} for job in self.repository.jobs()]

    def cancel(self, job_id):
        with self.lock:
            job = self.job(job_id)
            if self.active_id == job_id:
                self.cancel_event.set()
                job["cancel_requested"] = True
                job["cancel_note"] = "当前页完成或超时后停止，后续页不发送。"
            return job

    def close(self):
        self.cancel_event.set()
        if self.worker:
            self.worker.join(timeout=1)
        if self.owns_client and (not self.worker or not self.worker.is_alive()):
            self.client.close()
