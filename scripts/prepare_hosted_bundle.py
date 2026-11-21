"""Export deployable source and the existing knowledge catalog, without secrets or models."""

import argparse
import csv
import hashlib
import json
import shutil
import tarfile
from pathlib import Path

from kb.config import load_config


def prepare(root: Path, output: Path):
    root, output = root.resolve(), output.resolve()
    if output.exists() or output.with_suffix(".tar.gz").exists():
        raise ValueError("输出路径已存在，请使用一个新目录，避免覆盖部署数据")
    output.mkdir(parents=True)

    def copy(path):
        path = path.resolve()
        if not path.is_relative_to(root) or not path.is_file():
            raise ValueError(f"导出文件缺失或超出项目目录：{path.name}")
        target = output / path.relative_to(root)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)

    for name in [
        "pyproject.toml",
        "uv.lock",
        "requirements-backend.txt",
        "Dockerfile",
        ".dockerignore",
        "compose.backend.yaml",
        "compose.hosted.yaml",
        "README.md",
        ".env.example",
    ]:
        copy(root / name)
    for name in ["src", "configs", "scripts", "tests", "deploy"]:
        for path in sorted((root / name).rglob("*")):
            if any(
                part in {"__pycache__", "node_modules", "dist", ".git", ".openai"}
                for part in path.relative_to(root).parts
            ):
                continue
            if (
                path.is_file()
                and path.suffix not in {".pyc", ".pyo"}
                and not path.name.startswith(".env")
            ):
                copy(path)
    for name in [
        "usage",
        "design",
        "demo",
        "backend",
        "sites-deployment",
        "evaluation",
        "ablation",
        "dify",
        "langsmith",
    ]:
        copy(root / "docs" / f"{name}.md")
    for path in (root / "docs" / "public-data").iterdir():
        if path.is_file():
            copy(path)

    ingest, agent = load_config("ingest", root), load_config("agent", root)
    registry = root / ingest["registry"]
    with registry.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    copy(registry)
    documents = []
    raw = (root / ingest["raw_dir"]).resolve()
    for row in rows:
        path = (raw / row["file_name"]).resolve()
        if not path.is_relative_to(raw):
            raise ValueError("登记表中存在超出文档目录的路径")
        copy(path)
        documents.append(
            {
                "id": row["doc_id"],
                "file_name": row["file_name"],
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
        )
    copy(root / ingest["domain_dictionary"])
    if (root / agent["ledger_path"]).exists():
        copy(root / agent["ledger_path"])
    demo = root / "data/demo"
    for name in ["manifest.json", "review.json", "qa.jsonl", "agent_tasks.jsonl"]:
        if (demo / name).exists():
            copy(demo / name)
    latest = demo / "latest-run.txt"
    if latest.exists():
        run = Path(latest.read_text(encoding="utf-8").strip())
        run = run if run.is_absolute() else root / run
        if (
            run.resolve().is_relative_to((demo / "runs").resolve())
            and (run / "summary.json").exists()
        ):
            copy(run / "summary.json")
            (output / "data/demo/latest-run.txt").write_text(
                run.resolve().relative_to(root).as_posix() + "\n", encoding="utf-8"
            )
    for kind in ["ragas", "conversation", "agent_baseline", "agent_retest"]:
        candidates = sorted((demo / "cloud-runs").glob(f"*_{kind}_*/summary.json"))
        if candidates:
            summary = candidates[-1]
            copy(summary)
            directory = output / summary.parent.relative_to(root)
            snapshot = json.loads(summary.with_name("snapshot.json").read_text(encoding="utf-8"))
            status = json.loads(summary.with_name("status.json").read_text(encoding="utf-8"))
            (directory / "snapshot.json").write_text(
                json.dumps(
                    {"index": {"fingerprint": snapshot.get("index", {}).get("fingerprint")}}
                ),
                encoding="utf-8",
            )
            (directory / "status.json").write_text(
                json.dumps({"status": status["status"]}), encoding="utf-8"
            )
    review = root / "data/review/public-v1/review.html"
    if review.exists():
        copy(review)
    for path in (root / "data/eval").glob("*"):
        if path.is_file() and path.suffix in {".json", ".jsonl", ".csv"}:
            copy(path)
    (output / "models").mkdir(exist_ok=True)
    (output / "KNOWLEDGE_MANIFEST.json").write_text(
        json.dumps(
            {
                "document_count": len(documents),
                "documents": documents,
                "index_strategy": "rebuild_on_server",
                "includes_secrets": False,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    archive = output.with_suffix(".tar.gz")
    with tarfile.open(archive, "w:gz") as handle:
        handle.add(output, arcname=output.name)
    print(
        json.dumps(
            {
                "directory": str(output),
                "archive": str(archive),
                "document_count": len(documents),
                "archive_bytes": archive.stat().st_size,
            }
        )
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--root", type=Path, default=Path("."))
    arguments = parser.parse_args()
    prepare(arguments.root, arguments.output)
