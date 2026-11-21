import os
from pathlib import Path

from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from kb.config import load_config


class Settings(BaseModel):
    model_config = ConfigDict(hide_input_in_errors=True)

    root: Path = Field(default_factory=lambda: Path.cwd().resolve())
    database_url: str = Field(repr=False)
    host: str = "127.0.0.1"
    access_token: str | None = Field(default=None, repr=False)
    allowed_hosts: list[str] = ["127.0.0.1", "localhost", "testserver"]
    port: int = Field(default=8780, ge=1024, le=65535)
    max_upload_bytes: int = Field(default=20 * 1024 * 1024, gt=0)
    max_unpacked_bytes: int = Field(default=100 * 1024 * 1024, gt=0)
    poll_interval_seconds: float = Field(default=1.0, ge=0.1)

    @field_validator("database_url")
    @classmethod
    def postgres_only(cls, value):
        if not value.startswith("postgresql+psycopg://"):
            raise ValueError("KB_DATABASE_URL 必须使用 postgresql+psycopg://")
        return value

    @model_validator(mode="after")
    def require_remote_auth(self):
        if self.host not in {"127.0.0.1", "localhost", "::1"} and (
            not self.access_token or len(self.access_token) < 32
        ):
            raise ValueError("对外监听必须设置至少 32 字符的 KB_ACCESS_TOKEN")
        return self

    def ingest_config(self):
        config = load_config("ingest", self.root)
        for key in ["raw_dir", "index_dir", "registry", "domain_dictionary"]:
            config[key] = str((self.root / config[key]).resolve())
        return config

    @property
    def index_dir(self):
        return Path(self.ingest_config()["index_dir"])


def load_settings(root: Path = Path(".")) -> Settings:
    root = root.resolve()
    load_dotenv(root / ".env")
    url = os.environ.get("KB_DATABASE_URL")
    if not url:
        raise ValueError("请在 .env 设置 KB_DATABASE_URL，参见 docs/backend.md")
    config = load_config("backend", root)
    config["host"] = os.environ.get("KB_HOST", "127.0.0.1")
    config["access_token"] = os.environ.get("KB_ACCESS_TOKEN") or None
    if os.environ.get("KB_ALLOWED_HOSTS"):
        config["allowed_hosts"] = os.environ["KB_ALLOWED_HOSTS"].split(",")
    return Settings(root=root, database_url=url, **config)
