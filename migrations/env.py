"""Alembic environment.

The URL comes from `gauntlet.config.settings`, not from `alembic.ini`. Keeping
one source of truth matters here: the app, the worker, and the migration runner
must agree on which database they are talking to, and a URL duplicated into an
ini file is the kind of thing that drifts silently until a migration lands on
the wrong database.
"""

from logging.config import fileConfig

from alembic import context

from gauntlet.config import settings
from gauntlet.db.models import Base

config = context.config
config.set_main_option("sqlalchemy.url", settings.database_url)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Emit SQL to stdout without connecting — for reviewing a migration."""
    context.configure(
        url=settings.database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    from gauntlet.db.session import get_engine

    with get_engine().connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            # Without this, a column type change (e.g. Float -> Numeric) is
            # invisible to autogenerate and silently omitted from the migration.
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
