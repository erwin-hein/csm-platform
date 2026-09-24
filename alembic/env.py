import os
from logging.config import fileConfig

from sqlalchemy import create_engine, pool

from alembic import context
from app.config import _normalize_db_url, settings
from app.models import Base

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _url() -> str:
    # An explicit URL on the Alembic config (used by the test suite) wins over the env.
    return config.get_main_option("sqlalchemy.url") or _normalize_db_url(
        os.environ.get("DATABASE_URL", settings.database_url)
    )


def run_migrations_offline() -> None:
    context.configure(url=_url(), target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = create_engine(_url(), poolclass=pool.NullPool)
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
