import hashlib

from langchain_text_splitters import RecursiveCharacterTextSplitter

from kb.schema import Block, Chunk, ParsedDocument


def merge_section_paragraphs(blocks: list[Block]) -> list[Block]:
    """Keep adjacent prose/code lines together without crossing a page, table or heading."""
    merged = []
    for block in blocks:
        if (
            merged
            and block.kind == merged[-1].kind == "text"
            and (block.page, block.section_path) == (merged[-1].page, merged[-1].section_path)
        ):
            merged[-1].text += "\n\n" + block.text
        else:
            merged.append(block.model_copy())
    return merged


def table_chunks(text: str, prefix: str, limit: int) -> list[str]:
    lines = text.splitlines()
    header = prefix + "\n" + "\n".join(lines[:2])
    chunks, current = [], header
    for row in lines[2:]:
        if len(current) + len(row) + 1 > limit and current != header:
            chunks.append(current)
            current = header
        current += "\n" + row
    if current != header or len(lines) <= 2:
        chunks.append(current)
    return chunks


def chunk_document(parsed: ParsedDocument, metadata: dict, config: dict) -> list[Chunk]:
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=config["chunk_size"],
        chunk_overlap=config["chunk_overlap"],
        separators=config["separators"],
    )
    result = []
    for block in merge_section_paragraphs(parsed.blocks):
        prefix = " > ".join(filter(None, [metadata["doc_name"], block.section_path, block.title]))
        pieces = (
            table_chunks(block.text, prefix, config["chunk_size"])
            if block.kind == "table"
            else splitter.split_text(block.text)
        )
        for piece in pieces:
            index = len(result)
            digest = hashlib.sha256(piece.encode()).hexdigest()[:16]
            meta = {
                **metadata,
                "section_path": block.section_path,
                "chunk_type": block.kind,
                "chunk_index": index,
            }
            if block.page is not None:
                meta["page"] = block.page
            result.append(
                Chunk(id=f"{metadata['doc_id']}:{index}:{digest}", text=piece, metadata=meta)
            )
    return result
