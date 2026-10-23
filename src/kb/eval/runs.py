import hashlib
import importlib.metadata
import json
import os
import platform
import uuid
from datetime import UTC, datetime
from pathlib import Path


def write_json(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8"
    )
    temporary.replace(path)


def append_jsonl(path: Path, value):
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(value, ensure_ascii=False, allow_nan=False) + "\n")
        f.flush()
        os.fsync(f.fileno())


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def source_hash(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted((root / "src/kb").rglob("*.py")):
        digest.update(path.relative_to(root).as_posix().encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def environment_info() -> dict:
    packages = {}
    for name in [
        "ragas",
        "langchain-core",
        "langgraph",
        "chromadb",
        "sentence-transformers",
        "torch",
    ]:
        try:
            packages[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            packages[name] = None
    return {
        "system": platform.system(),
        "machine": platform.machine(),
        "python": platform.python_version(),
        "cpu_count": os.cpu_count(),
        "packages": packages,
    }


class Run:
    def __init__(self, parent: Path, kind: str, snapshot: dict):
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
        self.path = parent / f"{stamp}_{kind}_{uuid.uuid4().hex[:8]}"
        self.path.mkdir(parents=True, exist_ok=False)
        write_json(self.path / "snapshot.json", snapshot)
        write_json(self.path / "environment.json", environment_info())
        self.status("running")

    def status(self, status: str, **details):
        write_json(self.path / "status.json", {"status": status, **details})
