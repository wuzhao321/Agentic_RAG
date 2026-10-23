from typing import Literal

from pydantic import BaseModel, Field

DocType = Literal["技术规范", "交付报告", "接口说明书", "其他"]


class Block(BaseModel):
    text: str
    kind: Literal["text", "table"] = "text"
    page: int | None = None
    section_path: str = ""
    title: str = ""


class ParsedDocument(BaseModel):
    blocks: list[Block] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    page_count: int | None = None


class Chunk(BaseModel):
    id: str
    text: str
    metadata: dict[str, str | int]


class Hit(BaseModel):
    chunk: Chunk
    score: float
