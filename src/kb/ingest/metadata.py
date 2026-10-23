import csv
import hashlib
from pathlib import Path

from kb.ingest.loaders_docx import load_docx
from kb.ingest.loaders_pdf import load_pdf

FIELDS = [
    "file_name",
    "doc_id",
    "format",
    "page_count",
    "has_table",
    "has_text_layer",
    "doc_type",
    "project_id",
    "project_name",
    "warnings",
]


def parse(path: Path, config: dict):
    if path.suffix.lower() == ".docx":
        return load_docx(path)
    return load_pdf(path, config["pdf_margin_lines"], config["pdf_repeat_ratio"])


def scan(raw: Path, registry: Path, config: dict) -> list[dict]:
    existing = {}
    if registry.exists():
        with registry.open(encoding="utf-8-sig", newline="") as f:
            existing = {row["file_name"]: row for row in csv.DictReader(f)}
    rows = []
    for path in sorted(raw.rglob("*")):
        if path.suffix.lower() not in {".pdf", ".docx"} or not path.is_file():
            continue
        name = path.relative_to(raw).as_posix()
        old = existing.get(name, {})
        row = {key: old.get(key, "") for key in FIELDS}
        row.update(
            file_name=name,
            doc_id=old.get("doc_id") or hashlib.sha256(name.encode()).hexdigest()[:16],
            format=path.suffix[1:].lower(),
        )
        try:
            parsed = parse(path, config)
            row.update(
                page_count=parsed.page_count or "",
                has_table=any(b.kind == "table" for b in parsed.blocks),
                has_text_layer=bool(parsed.blocks),
                warnings="; ".join(parsed.warnings),
            )
        except Exception as exc:
            row.update(warnings=f"解析失败: {type(exc).__name__}: {exc}", has_text_layer=False)
        rows.append(row)
    registry.parent.mkdir(parents=True, exist_ok=True)
    with registry.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    return rows
