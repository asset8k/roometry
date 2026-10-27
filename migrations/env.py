"""Use the private DATABASE_URL setting; never copy credentials into alembic.ini."""

from alembic import context
from sqlalchemy import create_engine, pool

from roometry.agents import persistence_model as runtime_models  # noqa: F401
from roometry.config import Settings
from roometry.database import Base, database_url
from roometry.feed import model as feed_models  # noqa: F401
from roometry.projects import (
    model as project_models,  # noqa: F401 - register model metadata.
)
from roometry.quota import model as quota_models  # noqa: F401
from roometry.users.model import (  # noqa: F401 - register model metadata.
    AuthSession,
    User,
)

config = context.config
raw = config.attributes.get("database_url") or Settings().database_url
if raw is None:
    raise RuntimeError("Configure DATABASE_URL before running migrations.")
url = database_url(raw.get_secret_value() if hasattr(raw, "get_secret_value") else raw)
metadata = Base.metadata

if context.is_offline_mode():
    context.configure(
        url=url,
        target_metadata=metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()
else:
    engine = create_engine(url, poolclass=pool.NullPool, hide_parameters=True)
    try:
        with engine.connect() as connection:
            context.configure(
                connection=connection, target_metadata=metadata, compare_type=True
            )
            with context.begin_transaction():
                context.run_migrations()
    finally:
        engine.dispose()
