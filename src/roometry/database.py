"""A lazy SQLAlchemy engine; schema changes are explicit Alembic migrations."""

from sqlalchemy import MetaData, create_engine, event
from sqlalchemy.engine import make_url
from sqlalchemy.orm import DeclarativeBase, sessionmaker
from sqlalchemy.pool import StaticPool


class Base(DeclarativeBase):
    metadata = MetaData(
        naming_convention={
            "ix": "ix_%(column_0_label)s",
            "uq": "uq_%(table_name)s_%(column_0_name)s",
            "ck": "ck_%(table_name)s_%(constraint_name)s",
            "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
            "pk": "pk_%(table_name)s",
        }
    )


def database_url(raw: str):
    url = make_url(raw)
    if url.drivername in {"postgres", "postgresql"}:
        url = url.set(drivername="postgresql+psycopg")
    if url.drivername not in {"postgresql+psycopg", "sqlite", "sqlite+pysqlite"}:
        raise ValueError(
            "Use PostgreSQL (psycopg), or SQLite for isolated local tests."
        )
    return url


class Database:
    def __init__(self, raw: str):
        url = database_url(raw)
        options = {"pool_pre_ping": True, "hide_parameters": True}
        if url.get_backend_name() == "sqlite":
            options["connect_args"] = {"check_same_thread": False}
            if url.database in (None, "", ":memory:"):
                options["poolclass"] = StaticPool
        else:
            options["connect_args"] = {"connect_timeout": 5}
        self.engine = create_engine(url, **options)
        if url.get_backend_name() == "sqlite":

            @event.listens_for(self.engine, "connect")
            def foreign_keys(connection, _record):
                connection.execute("PRAGMA foreign_keys=ON")

        self.sessions = sessionmaker(self.engine, expire_on_commit=False)

    def close(self):
        self.engine.dispose()
