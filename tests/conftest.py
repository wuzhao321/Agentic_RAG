import pytest

from kb.config import load_config
from kb.schema import Chunk


@pytest.fixture
def ingest_config():
    return load_config("ingest")


@pytest.fixture
def make_chunk():
    def create(id="c1", text="CreateOrder 订单接口", **metadata):
        return Chunk(
            id=id,
            text=text,
            metadata={
                "doc_id": "doc1",
                "doc_name": "接口.docx",
                "doc_type": "接口说明书",
                "project_name": "星河",
                "project_id": "P1",
                "source_format": "docx",
                "chunk_index": 0,
                "chunk_type": "text",
                **metadata,
            },
        )

    return create
