import re
from pathlib import Path

from docx import Document
from docx.table import Table
from docx.text.paragraph import Paragraph

from kb.ingest.table_md import docx_table_markdown
from kb.schema import Block, ParsedDocument


def load_docx(path: Path) -> ParsedDocument:
    doc = Document(path)
    result = ParsedDocument()
    headings: dict[int, str] = {}
    last_text = ""
    for element in doc.element.body.iterchildren():
        if element.tag.endswith("}p"):
            paragraph = Paragraph(element, doc)
            style = paragraph.style.name if paragraph.style else ""
            text = paragraph.text.rstrip() if style == "Source Code" else paragraph.text.strip()
            if not text.strip():
                continue
            heading = re.search(r"(?:Heading|标题)\s*(\d+)", style, re.I)
            if heading:
                level = int(heading[1])
                headings = {k: v for k, v in headings.items() if k < level}
                headings[level] = text
            section = " > ".join(headings.values())
            result.blocks.append(Block(text=text, section_path=section))
            last_text = text
        elif element.tag.endswith("}tbl"):
            text, warnings = docx_table_markdown(Table(element, doc))
            result.warnings.extend(warnings)
            if text:
                result.blocks.append(
                    Block(
                        text=text,
                        kind="table",
                        section_path=" > ".join(headings.values()),
                        title=last_text,
                    )
                )
    return result
