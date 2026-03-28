import os
from logging.config import fileConfig

from alembic import context
from dotenv import load_dotenv
from sqlalchemy import engine_from_config, pool

load_dotenv()

# Import models so Alembic can autogenerate from them
from database import Base  # noqa: F401
import models  # noqa: F401 — registers all ORM classes on Base.metadata

config = context.config

# Override sqlalchemy.url from environment
database_url = os.getenv(
    "DATABASE_URL",
    "postgresql+asyncpg://postgres:postgres@localhost:5432/ramp_management",
)
# Alembic uses a sync driver for migrations — swap asyncpg → psycopg2
sync_url = database_url.replace("postgresql+asyncpg", "postgresql+psycopg2")
# asyncpg uses ?ssl=require; psycopg2 uses ?sslmode=require
sync_url = sync_url.replace("?ssl=require", "?sslmode=require").replace("&ssl=require", "&sslmode=require")
# Escape % for ConfigParser (e.g. URL-encoded passwords like %23)
config.set_main_option("sqlalchemy.url", sync_url.replace("%", "%%"))

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=sync_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    from sqlalchemy import create_engine
    connectable = create_engine(sync_url, poolclass=pool.NullPool)
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
