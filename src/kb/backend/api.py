import base64
import hashlib
import logging
import re
import secrets
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import FastAPI, File, Form, Header, HTTPException, Query, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.trustedhost import TrustedHostMiddleware

from kb.backend.db import make_engine, sessions_for
from kb.backend.schemas import (
    DocType,
    DocumentMetadata,
    DocumentOut,
    DocumentPage,
    JobOut,
    JobRequest,
    QueryRequest,
)
from kb.backend.service import (
    ServiceError,
    create_job,
    get_job,
    list_documents,
    list_jobs,
    retry_job,
    upload_document,
)
from kb.backend.settings import load_settings

LOG = logging.getLogger(__name__)


def error_response(status, code, message, **details):
    return JSONResponse(
        status_code=status,
        content={"error": {"code": code, "message": message, **details}},
    )


class RequestLimitMiddleware:
    """Bound multipart bodies before Starlette spools the full upload to disk."""

    def __init__(self, app, limit):
        self.app, self.limit = app, limit

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        headers = dict(scope["headers"])
        try:
            length = int(headers.get(b"content-length", b"0"))
        except ValueError:
            return await error_response(400, "invalid_length", "Content-Length 无效")(
                scope, receive, send
            )
        if length < 0 or length > self.limit:
            return await error_response(413, "body_too_large", "请求超过大小限制")(
                scope, receive, send
            )
        received = 0

        async def bounded_receive():
            nonlocal received
            message = await receive()
            received += len(message.get("body", b""))
            if received > self.limit:
                raise HTTPException(413, "请求超过大小限制")
            return message

        await self.app(scope, bounded_receive, send)


def create_app(settings=None, engine=None):
    settings = settings or load_settings()
    owns_engine = engine is None
    engine = engine or make_engine(settings)
    sessions = sessions_for(engine)
    from kb.backend.query import HostedQueryService

    query_service = HostedQueryService(settings)

    @asynccontextmanager
    async def lifespan(app):
        yield
        query_service.close()
        if owns_engine:
            engine.dispose()

    app = FastAPI(
        title="知序 RAG 知识库",
        version="0.1.0",
        description="知识问答、混合检索、Agent、文档管理与异步入库。",
        lifespan=lifespan,
    )
    app.state.settings, app.state.sessions = settings, sessions
    app.add_middleware(RequestLimitMiddleware, limit=settings.max_upload_bytes + 256 * 1024)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.allowed_hosts)

    @app.middleware("http")
    async def same_origin(request: Request, call_next):
        if settings.access_token:
            expected_token = "Bearer " + settings.access_token
            if not secrets.compare_digest(
                request.headers.get("authorization", "").encode(), expected_token.encode()
            ):
                return error_response(401, "unauthorized", "需要后端服务访问凭证")
        origin = request.headers.get("origin")
        expected = f"{request.url.scheme}://{request.headers.get('host', '')}"
        if origin and origin != expected:
            return error_response(403, "invalid_origin", "仅允许同源访问")
        return await call_next(request)

    @app.exception_handler(ServiceError)
    async def service_error(request, exc):
        return error_response(exc.status, exc.code, exc.message)

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request, exc):
        return error_response(exc.status_code, "http_error", str(exc.detail))

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        issues = [{"field": e["loc"], "message": e["msg"]} for e in exc.errors()]
        return error_response(422, "validation_error", "请求参数不合法", issues=issues)

    @app.exception_handler(SQLAlchemyError)
    async def database_error(request, exc):
        LOG.error("数据库访问失败", exc_info=exc)
        return error_response(503, "database_unavailable", "数据库不可用，请检查连接及表结构迁移")

    @app.exception_handler(Exception)
    async def unexpected_error(request, exc):
        LOG.error("后端请求失败", exc_info=exc)
        return error_response(500, "internal_error", "请求失败，请查看服务日志")

    @app.post("/documents", status_code=201, response_model=DocumentOut)
    def upload(
        file: Annotated[UploadFile, File()],
        doc_type: Annotated[DocType, Form()],
        project_name: Annotated[str, Form(min_length=1, max_length=200)],
        project_id: Annotated[str, Form(max_length=100)] = "",
    ):
        if not project_name.strip():
            raise ServiceError(422, "invalid_project", "项目名称不能为空")
        metadata = DocumentMetadata(
            doc_type=doc_type, project_name=project_name, project_id=project_id
        )
        try:
            return upload_document(sessions, settings, file.file, file.filename, metadata)
        finally:
            file.file.close()

    @app.get("/documents", response_model=DocumentPage)
    def documents(
        offset: Annotated[int, Query(ge=0)] = 0,
        limit: Annotated[int, Query(ge=1, le=100)] = 20,
        project_name: Annotated[str | None, Query(max_length=200)] = None,
        doc_type: DocType | None = None,
    ):
        return list_documents(sessions, offset, limit, project_name, doc_type)

    @app.post("/ingest-jobs", status_code=202, response_model=JobOut)
    def submit_job(
        body: JobRequest,
        idempotency_key: Annotated[
            str, Header(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._:-]+$")
        ],
    ):
        return create_job(sessions, settings, idempotency_key, body)

    @app.get("/ingest-jobs/{job_id}", response_model=JobOut)
    def job_status(job_id: str):
        return get_job(sessions, job_id)

    @app.post("/ingest-jobs/{job_id}/retry", status_code=202, response_model=JobOut)
    def retry(job_id: str):
        return retry_job(sessions, settings, job_id)

    @app.get("/ingest-jobs", response_model=list[JobOut])
    def jobs(limit: Annotated[int, Query(ge=1, le=100)] = 50):
        return list_jobs(sessions, limit)

    @app.get("/api/status")
    def workspace_status():
        return query_service.workspace_status(sessions)

    @app.post("/api/query")
    def query(body: QueryRequest):
        try:
            return query_service.query(body.mode, body.question, body.session)
        except ValueError as exc:
            raise ServiceError(422, "query_failed", str(exc)) from exc

    @app.get("/health")
    def health():
        from sqlalchemy import text

        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        return {"status": "ok"}

    @app.get("/api/review")
    def review_page():
        path = settings.root / "data/review/public-v1/review.html"
        if not path.exists():
            raise ServiceError(404, "review_missing", "尚未生成逐题审核页面")
        html = path.read_text(encoding="utf-8")

        def hashes(tag):
            return " ".join(
                "'sha256-" + base64.b64encode(hashlib.sha256(s.encode()).digest()).decode() + "'"
                for s in re.findall(rf"<{tag}(?:\s[^>]*)?>(.*?)</{tag}>", html, re.S)
            )

        policy = (
            f"default-src 'none'; script-src {hashes('script')}; "
            f"style-src {hashes('style')}; frame-ancestors 'self'"
        )
        return {"html": html, "policy": policy}

    return app
