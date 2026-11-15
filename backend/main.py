from contextlib import asynccontextmanager
from typing import Literal

from fastapi import FastAPI, File, Query, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

from .config import Settings
from .images import MAX_FILE_BYTES, UploadError
from .service import Service, ServiceError


class JobInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    page_ids: list[str] = Field(min_length=1, max_length=6)
    task: Literal["structured_parse", "doc_parse", "table"] = "doc_parse"
    max_tokens: int = Field(default=2048, ge=256, le=4096)


class RequestSizeLimit:
    """Bound multipart bytes before Starlette can spool an unbounded upload."""
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] not in {"POST", "PUT", "PATCH"}:
            return await self.app(scope, receive, send)
        cap = MAX_FILE_BYTES + 65536 if scope["path"] == "/api/uploads" else 65536
        data = bytearray()
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            data.extend(message.get("body", b""))
            if len(data) > cap:
                return await JSONResponse({"detail": "请求超过大小上限；上传文件最多12 MiB。"}, status_code=413)(scope, receive, send)
            if not message.get("more_body", False):
                break
        replayed = False
        async def replay():
            nonlocal replayed
            if not replayed:
                replayed = True
                return {"type": "http.request", "body": bytes(data), "more_body": False}
            return await receive()
        return await self.app(scope, replay, send)


def create_app(settings=None, supplied_service=None):
    settings = settings or Settings.load()
    @asynccontextmanager
    async def lifespan(app):
        app.state.service = supplied_service or Service(settings)
        yield
        app.state.service.close()

    app = FastAPI(title="Hunyuan Document Lab", version="0.1.0", lifespan=lifespan)
    app.add_middleware(RequestSizeLimit)

    @app.exception_handler(ServiceError)
    async def service_error(request, error):
        return JSONResponse({"detail": str(error)}, status_code=error.status)

    @app.exception_handler(UploadError)
    async def upload_error(request, error):
        return JSONResponse({"detail": str(error)}, status_code=400)

    @app.get("/api/health")
    def health():
        return {"status": "ok"}

    @app.get("/api/status")
    def status(request: Request):
        return request.app.state.service.status()

    @app.get("/api/pages")
    def pages(request: Request):
        return {"pages": request.app.state.service.pages()}

    @app.get("/api/images/{page_id}")
    def image(page_id: str, request: Request):
        return FileResponse(request.app.state.service.image(page_id), media_type="image/png")

    @app.post("/api/uploads", status_code=201)
    async def upload(request: Request, file: UploadFile = File(...)):
        from starlette.concurrency import run_in_threadpool
        raw = await file.read(MAX_FILE_BYTES + 1)
        if len(raw) > MAX_FILE_BYTES:
            return JSONResponse({"detail": "文件超过12 MiB。"}, status_code=413)
        result = await run_in_threadpool(request.app.state.service.upload, file.filename, raw)
        return {"pages": result}

    @app.post("/api/jobs", status_code=202)
    def submit(body: JobInput, request: Request):
        return request.app.state.service.submit(body.page_ids, body.task, body.max_tokens)

    @app.get("/api/jobs")
    def jobs(request: Request):
        return {"jobs": request.app.state.service.jobs()}

    @app.get("/api/jobs/{job_id}")
    def job(job_id: str, request: Request):
        return request.app.state.service.job(job_id)

    @app.post("/api/jobs/{job_id}/cancel")
    def cancel(job_id: str, request: Request):
        return request.app.state.service.cancel(job_id)

    @app.get("/api/jobs/{job_id}/export")
    def export(job_id: str, request: Request, format: Literal["json", "markdown"] = Query("json")):
        value = request.app.state.service.job(job_id)
        if format == "json":
            return JSONResponse(value, headers={"Content-Disposition": f'attachment; filename="ocr-{job_id}.json"'})
        lines = ["# HunyuanOCR 解析记录", "", f"任务：{job_id}", f"状态：{value['status']}", ""]
        for page in value["pages"]:
            lines.extend([f"## {page['name']}", f"状态：{page['status']}", "", page.get("text", ""), ""])
        return PlainTextResponse("\n".join(lines), headers={"Content-Disposition": f'attachment; filename="ocr-{job_id}.md"'})

    web = settings.root / "web"
    if web.exists():
        app.mount("/static", StaticFiles(directory=web), name="static")

    @app.get("/")
    def index():
        path = web / "index.html"
        if not path.exists():
            return PlainTextResponse("文档实验平台页面尚未构建。", status_code=503)
        return FileResponse(path)

    return app


app = create_app()
