from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from kb.backend.settings import Settings


def make_engine(settings: Settings):
    return create_engine(settings.database_url, pool_pre_ping=True, pool_size=5, max_overflow=10)


def sessions_for(engine):
    return sessionmaker(engine, expire_on_commit=False)


def migrate(engine):
    config = Config()
    config.set_main_option("script_location", str(Path(__file__).parent / "migrations"))
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "head")
