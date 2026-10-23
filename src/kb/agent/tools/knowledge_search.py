from pydantic import BaseModel, ConfigDict, Field

from kb.schema import DocType


class KnowledgeArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: str = Field(min_length=1, description="独立、完整的文档检索问题")
    doc_type: DocType | None = None
    project_name: str | None = None
