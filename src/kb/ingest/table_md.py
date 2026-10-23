def escape_cell(text: str) -> str:
    return " ".join(text.split()).replace("\\", "\\\\").replace("|", "\\|")


def rows_to_markdown(rows: list[list[str]]) -> str:
    if not rows:
        return ""
    width = max(map(len, rows))
    lines = [
        "| " + " | ".join(escape_cell(c) for c in row + [""] * (width - len(row))) + " |"
        for row in rows
    ]
    lines.insert(1, "| " + " | ".join(["---"] * width) + " |")
    return "\n".join(lines)


def docx_table_markdown(table) -> tuple[str, list[str]]:
    seen = set()
    rows, warnings = [], []
    for row in table.rows:
        values = []
        for cell in row.cells:
            # Keep empty placeholders to preserve columns after merged-cell deduplication.
            if cell._tc in seen:
                values.append("")
                continue
            seen.add(cell._tc)
            text = cell.text
            if cell.tables:
                warnings.append("嵌套表格已降级为文本，请人工复核")
                text = " ".join(cell._tc.itertext())
            values.append(text)
        rows.append(values)
    return rows_to_markdown(rows), warnings
