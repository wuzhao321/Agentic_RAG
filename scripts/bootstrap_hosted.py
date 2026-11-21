"""Initialize the hosted catalog and queue the first complete knowledge index."""

from kb.backend.db import make_engine, migrate, sessions_for
from kb.backend.schemas import JobRequest
from kb.backend.service import create_job, import_registry
from kb.backend.settings import load_settings
from kb.locking import file_lock


def main():
    settings = load_settings()
    engine = make_engine(settings)
    try:
        with file_lock(settings.index_dir / ".worker.lock"):
            migrate(engine)
            sessions = sessions_for(engine)
            imported = import_registry(sessions, settings)
            print(f"Imported {imported} existing documents", flush=True)
            if not (settings.index_dir / "active.json").exists():
                job = create_job(sessions, settings, "hosted-initial-index-v1", JobRequest())
                print(f"Initial index job: {job.id} ({job.status})", flush=True)
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
