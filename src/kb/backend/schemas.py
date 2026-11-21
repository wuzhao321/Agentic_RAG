from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

DocType = Literal["技术规范", "交付报告", "接口说明书", "其他"]
Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]


class DocumentMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")

    doc_type: DocType
    project_name: Name
    project_id: str = Field(default="", max_length=100)


class DocumentOut(DocumentMetadata):
    model_config = ConfigDict(from_attributes=True)

    id: str
    file_name: str
    sha256: str
    size_bytes: int
    created_at: datetime


class DocumentPage(BaseModel):
    items: list[DocumentOut]
    total: int
    offset: int
    limit: int


class JobRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    description: str = Field(default="", max_length=200)


class JobOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    status: str
    stage: str
    completed: int
    total: int
    attempt_count: int
    input_fingerprint: str
    result: dict | None
    error: str | None
    created_at: datetime
    enqueued_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


class QueryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["rag", "agent", "search"] = "rag"
    question: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)
    ]
    session: str = Field(pattern=r"^[a-zA-Z0-9-]{1,64}$")
