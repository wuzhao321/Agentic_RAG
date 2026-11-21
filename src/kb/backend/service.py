import csv
import hashlib
import json
import re
import uuid
import zipfile
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from kb.backend.models import Document, IngestJob, utcnow
from kb.backend.schemas import DocumentMetadata


class ServiceError(Exception):
    def __init__(self, status, code, message):
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


def digest(value):
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def file_digest(path):
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def validate_file(path, suffix, settings):
    if suffix == ".pdf":
        with path.open("rb") as handle:
            if not handle.read(1024).lstrip().startswith(b"%PDF-"):
                raise ServiceError(422, "invalid_file", "文件内容不是 PDF")
    else:
        try:
            with zipfile.ZipFile(path) as archive:
                names = set(archive.namelist())
                if not {"[Content_Types].xml", "word/document.xml"} <= names:
                    raise ValueError("缺少 DOCX 文档结构")
                if sum(item.file_size for item in archive.infolist()) > settings.max_unpacked_bytes:
                    raise ValueError("DOCX 解压大小超过限制")
                if any(item.flag_bits & 1 for item in archive.infolist()):
                    raise ValueError("不支持加密 DOCX")
        except (zipfile.BadZipFile, ValueError) as exc:
            raise ServiceError(422, "invalid_file", str(exc)) from exc


def store_file(stream, file_name, settings):
    suffix = Path(file_name).suffix.lower()
    if suffix not in {".pdf", ".docx"}:
        raise ServiceError(415, "unsupported_file", "仅支持 PDF 和 DOCX")
    raw = Path(settings.ingest_config()["raw_dir"])
    relative = Path(".backend_uploads") / f"{uuid.uuid4().hex}{suffix}"
    target = raw / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(".part")
    size, checksum = 0, hashlib.sha256()
    try:
        with temporary.open("xb") as output:
            while block := stream.read(1024 * 1024):
                size += len(block)
                if size > settings.max_upload_bytes:
                    raise ServiceError(413, "file_too_large", "文件超过上传大小限制")
                output.write(block)
                checksum.update(block)
        if size == 0:
            raise ServiceError(422, "empty_file", "文件为空")
        validate_file(temporary, suffix, settings)
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)
    return relative.as_posix(), checksum.hexdigest(), size


def clean_uncommitted(sessions, settings, documents):
    """Confirm rollback before deleting files; keep bytes if commit outcome is unknown."""
    try:
        with sessions() as session:
            for document in documents:
                if session.get(Document, document.id) is None:
                    (Path(settings.ingest_config()["raw_dir"]) / document.storage_path).unlink(
                        missing_ok=True
                    )
    except SQLAlchemyError:
        # A disconnected database may have committed. Never delete referenced bytes blindly.
        return


def upload_document(sessions, settings, stream, filename, metadata):
    filename = (filename or "").replace("\\", "/").rsplit("/", 1)[-1]
    if not filename or len(filename) > 512:
        raise ServiceError(422, "invalid_filename", "文件名为空或过长")
    path, sha256, size = store_file(stream, filename, settings)
    document = Document(
        id=uuid.uuid4().hex,
        file_name=filename,
        storage_path=path,
        sha256=sha256,
        size_bytes=size,
        **metadata.model_dump(),
    )
    try:
        with sessions.begin() as session:
            session.add(document)
    except Exception:
        clean_uncommitted(sessions, settings, [document])
        raise
    return document


def import_registry(sessions, settings):
    config = settings.ingest_config()
    registry, raw = Path(config["registry"]), Path(config["raw_dir"])
    if not registry.exists():
        return 0
    created = []
    try:
        with registry.open(encoding="utf-8-sig", newline="") as handle, sessions.begin() as session:
            for row in csv.DictReader(handle):
                identifier = row["doc_id"]
                if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", identifier):
                    raise ValueError("登记表 doc_id 不合法")
                if session.get(Document, identifier) is not None:
                    continue
                metadata = DocumentMetadata(
                    **{key: row.get(key, "") for key in ["doc_type", "project_name", "project_id"]}
                )
                source = (raw / row["file_name"]).resolve()
                if not source.is_relative_to(raw) or not source.is_file():
                    raise ValueError(f"登记表文件缺失或超出 raw_dir：{row['file_name']}")
                if len(row["file_name"]) > 512:
                    raise ValueError("登记表文件名过长")
                with source.open("rb") as stream:
                    path, checksum, size = store_file(stream, row["file_name"], settings)
                document = Document(
                    id=identifier,
                    file_name=row["file_name"],
                    storage_path=path,
                    sha256=checksum,
                    size_bytes=size,
                    **metadata.model_dump(),
                )
                created.append(document)
                session.add(document)
                session.flush()
    except Exception:
        clean_uncommitted(sessions, settings, created)
        raise
    return len(created)


def list_documents(sessions, offset, limit, project_name=None, doc_type=None):
    conditions = []
    if project_name is not None:
        conditions.append(Document.project_name == project_name)
    if doc_type is not None:
        conditions.append(Document.doc_type == doc_type)
    with sessions() as session:
        items = list(
            session.scalars(
                select(Document)
                .where(*conditions)
                .order_by(Document.created_at.desc(), Document.id)
                .offset(offset)
                .limit(limit)
            )
        )
        total = session.scalar(select(func.count()).select_from(Document).where(*conditions))
    return {"items": items, "total": total, "offset": offset, "limit": limit}


def snapshot_inputs(session, settings):
    documents = list(session.scalars(select(Document).order_by(Document.id)))
    if not documents:
        raise ServiceError(409, "empty_catalog", "请先上传文档或导入登记表")
    rows = [
        {
            "doc_id": d.id,
            "file_name": d.file_name,
            "storage_path": d.storage_path,
            "sha256": d.sha256,
            "doc_type": d.doc_type,
            "project_id": d.project_id,
            "project_name": d.project_name,
        }
        for d in documents
    ]
    return {"config": settings.ingest_config(), "documents": rows}


def replay(job, request_digest):
    if job.request_digest != request_digest:
        raise ServiceError(409, "idempotency_conflict", "此幂等键已经用于不同请求")
    return job


def create_job(sessions, settings, key, request):
    request_digest = digest(request.model_dump())
    try:
        with sessions.begin() as session:
            existing = session.scalar(select(IngestJob).where(IngestJob.idempotency_key == key))
            if existing is not None:
                return replay(existing, request_digest)
            snapshot = snapshot_inputs(session, settings)
            job = IngestJob(
                id=uuid.uuid4().hex,
                idempotency_key=key,
                request_digest=request_digest,
                input_fingerprint=digest(snapshot),
                snapshot=snapshot,
                total=len(snapshot["documents"]),
            )
            session.add(job)
        return job
    except IntegrityError:
        # The unique constraint, not a preceding SELECT, resolves concurrent submissions.
        with sessions() as session:
            existing = session.scalar(select(IngestJob).where(IngestJob.idempotency_key == key))
            if existing is None:
                raise
            return replay(existing, request_digest)


def get_job(sessions, identifier):
    with sessions() as session:
        job = session.get(IngestJob, identifier)
        if job is None:
            raise ServiceError(404, "job_not_found", "任务不存在")
        return job


def list_jobs(sessions, limit=50):
    with sessions() as session:
        return list(
            session.scalars(select(IngestJob).order_by(IngestJob.created_at.desc()).limit(limit))
        )


def assert_current(session, settings, job):
    if digest(snapshot_inputs(session, settings)) != job.input_fingerprint:
        raise ServiceError(409, "stale_input", "文档清单或配置已变化，请用新的幂等键提交任务")


def validate_snapshot_files(job):
    raw = Path(job.snapshot["config"]["raw_dir"]).resolve()
    for row in job.snapshot["documents"]:
        path = (raw / row["storage_path"]).resolve()
        if not path.is_relative_to(raw) or not path.is_file() or file_digest(path) != row["sha256"]:
            raise ServiceError(409, "changed_file", f"文档文件缺失或已改变：{row['file_name']}")


def retry_job(sessions, settings, identifier):
    with sessions.begin() as session:
        job = session.scalar(select(IngestJob).where(IngestJob.id == identifier).with_for_update())
        if job is None:
            raise ServiceError(404, "job_not_found", "任务不存在")
        if job.status in {"pending", "running"}:
            return job
        if job.status not in {"failed", "interrupted"}:
            raise ServiceError(409, "not_retryable", "仅失败或中断任务可重试")
        assert_current(session, settings, job)
        job.status, job.stage = "pending", "queued"
        job.completed, job.total = 0, len(job.snapshot["documents"])
        job.error, job.result = None, None
        job.started_at, job.finished_at = None, None
        job.enqueued_at = utcnow()
    return job
