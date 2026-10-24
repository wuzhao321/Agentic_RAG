import re
from collections import Counter
from pathlib import Path

import pymupdf

from kb.ingest.table_md import rows_to_markdown
from kb.schema import Block, ParsedDocument


def load_pdf(path: Path, margin_lines: int, repeat_ratio: float) -> ParsedDocument:
    result = ParsedDocument()
    with pymupdf.open(path) as doc:
        result.page_count = len(doc)
        pages = [page.get_text().splitlines() for page in doc]
        margins = Counter(
            line.strip()
            for lines in pages
            for line in set(lines[:margin_lines] + lines[-margin_lines:])
        )
        repeated = {
            line
            for line, count in margins.items()
            if len(doc) >= 3 and count / len(doc) >= repeat_ratio
        }
        for i, page in enumerate(doc):
            if not page.get_text().strip():
                result.warnings.append(f"第 {i + 1} 页无文本层；未启用 OCR")
                continue
            tables = list(page.find_tables().tables)
            items = []
            for table in tables:
                # Table.extract sorts individual glyphs by geometry, which can move
                # underscores below the baseline and break identifiers (timeout_ms).
                # Use table geometry, but let MuPDF read each cell's original text.
                rows = [
                    [page.get_text("text", clip=cell) if cell else "" for cell in row.cells]
                    for row in table.rows
                ]
                items.append(
                    (table.bbox[1], Block(text=rows_to_markdown(rows), kind="table", page=i + 1))
                )
            for block in page.get_text("blocks"):
                rect = pymupdf.Rect(block[:4])
                if any(
                    pymupdf.Rect(t.bbox).contains(rect.tl + (rect.br - rect.tl) / 2) for t in tables
                ):
                    continue
                lines = block[4].splitlines()
                # Remove only repeated margin lines, never identical text in the page body.
                at_margin = rect.y0 < page.rect.height * 0.1 or rect.y1 > page.rect.height * 0.9
                text = "\n".join(
                    line
                    for line in lines
                    if not (
                        at_margin
                        and (
                            line.strip() in repeated
                            or re.fullmatch(r"(?:第\s*)?\d+\s*(?:页)?", line.strip())
                        )
                    )
                )
                if text.strip():
                    items.append((rect.y0, Block(text=text, page=i + 1)))
            result.blocks.extend(block for _, block in sorted(items, key=lambda x: x[0]))
    return result
