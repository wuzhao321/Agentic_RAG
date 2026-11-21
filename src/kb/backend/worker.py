import json
import logging
import time
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import select, text, update

from kb.backend.models import IngestJob, utcnow
from kb.backend.service import assert_current, digest, validate_snapshot_files
from kb.config import resolve_model_path
from kb.ingest.indexer import ingest
from kb.locking import LockBusyError, file_lock

LOG = logging.getLogger(__name__)


class Worker:
    def __init__(self, settings, sessions, embedding_factory=None):
        self.settings, self.sessions = settings, sessions
        self.index_dir = settings.index_dir
        self.embedding_factory = embedding_factory
        self.embedding, self.embedding_key = None, None

    def published_result(self, job):
        active = self.index_dir / "active.json"
        if not active.exists():
            return None
        manifest = json.loads(active.read_text(encoding="utf-8"))
        marker = manifest.get("job", {})
        if marker == {"id": job.id, "input_fingerprint": job.input_fingerprint}:
            return manifest.get("result")
        return None

    def finish(self, identifier, *, result=None, error=None, interrupted=False):
        with self.sessions.begin() as session:
            job = session.get(IngestJob, identifier)
            if result is not None:
                job.status, job.stage = "succeeded", "completed"
                job.completed = job.total = result["total_chunks"]
                job.result, job.error = result, None
            else:
                job.status = "interrupted" if interrupted else "failed"
                job.error = error
            job.finished_at = utcnow()

    def recover(self):
        """Call only after acquiring the singleton worker lock."""
        with file_lock(self.index_dir / ".write.lock"):
            with self.sessions() as session:
                jobs = list(session.scalars(select(IngestJob).where(IngestJob.status == "running")))
            for job in jobs:
                result = self.published_result(job)
                self.finish(
                    job.id,
                    result=result,
                    error="Worker 上次运行中断，可手动重试",
                    interrupted=result is None,
                )
        return len(jobs)

    def claim(self):
        with self.sessions.begin() as session:
            job = session.scalar(
                select(IngestJob)
                .where(IngestJob.status == "pending")
                .order_by(IngestJob.enqueued_at, IngestJob.id)
                .with_for_update(skip_locked=True)
                .limit(1)
            )
            if job is None:
                return None
            job.status, job.stage = "running", "validating"
            job.attempt_count += 1
            job.started_at, job.finished_at = utcnow(), None
            job.error = None
        return job

    def progress(self, identifier, stage, completed, total):
        with self.sessions.begin() as session:
            session.execute(
                update(IngestJob)
                .where(IngestJob.id == identifier, IngestJob.status == "running")
                .values(stage=stage, completed=completed, total=total)
            )

    def guard(self, job):
        if Path(job.snapshot["config"]["index_dir"]).resolve() != self.index_dir:
            raise ValueError("任务索引目录与当前 Worker 不一致，请重新提交任务")
        with self.sessions() as session:
            assert_current(session, self.settings, job)
        validate_snapshot_files(job)

    @contextmanager
    def guard_publication(self, job):
        validate_snapshot_files(job)
        with self.sessions.begin() as session:
            # Serialize only the final catalog check and manifest swap with document commits.
            if session.get_bind().dialect.name == "sqlite":
                session.execute(text("BEGIN IMMEDIATE"))
            else:
                session.execute(text("LOCK TABLE documents IN SHARE MODE"))
            assert_current(session, self.settings, job)
            yield

    def model(self, config):
        key = digest(config)
        if self.embedding is None or self.embedding_key != key:
            factory = self.embedding_factory
            if factory is None:
                from kb.retrieval.vector import LocalEmbedding

                factory = LocalEmbedding
            self.embedding = factory(resolve_model_path(config, self.settings.root))
            self.embedding_key = key
        return self.embedding

    def process_one(self):
        job = self.claim()
        if job is None:
            return False
        try:
            self.guard(job)
            config = job.snapshot["config"]
            result = ingest(
                config,
                self.model(config["embedding"]),
                rows=job.snapshot["documents"],
                job={"id": job.id, "input_fingerprint": job.input_fingerprint},
                progress=lambda *args: self.progress(job.id, *args),
                publish_guard=lambda: self.guard_publication(job),
            )
        except Exception as exc:
            LOG.exception("入库任务失败：%s", job.id)
            # Publication can succeed even if the following database update fails.
            result = self.published_result(job)
            self.finish(job.id, result=result, error=f"{type(exc).__name__}: {exc}"[:4000])
        else:
            self.finish(job.id, result=result)
        return True

    def run(self, once=False):
        with file_lock(self.index_dir / ".worker.lock"):
            self.recover()
            while True:
                handled = self.process_one()
                if once:
                    return handled
                if not handled:
                    time.sleep(self.settings.poll_interval_seconds)


def run_worker(settings, sessions, once=False):
    try:
        return Worker(settings, sessions).run(once=once)
    except LockBusyError as exc:
        raise ValueError(f"无法启动 Worker：{exc}") from exc
