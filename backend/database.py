import os
from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

# Automatically load environment variables from .env
load_dotenv()

def database_url() -> str:
    """The database to use: DATABASE_URL, or a local SQLite file (dev.db).

    Vercel sets VERCEL in every deployment. Its filesystem is read-only, so
    the dev.db fallback can't work there: a missing DATABASE_URL is an error
    that says what to set, instead of a SQLite crash on first use.
    """
    url = os.getenv("DATABASE_URL")
    if not url and os.getenv("VERCEL"):
        raise RuntimeError(
            "DATABASE_URL is not set. Add it in the Vercel project's "
            "Environment Variables, then redeploy."
        )
    if url is None:
        url = "sqlite:///./dev.db"
    # Normalize postgres:// to postgresql:// for SQLAlchemy compatibility
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql://", 1)
    return url


DATABASE_URL = database_url()

def engine_options(database_url: str) -> dict:
    """Return conservative connection settings for the configured database.

    PostgreSQL connections can be closed by the server while sitting in the
    pool.  Pre-ping replaces those stale connections before a request uses
    them, while recycling bounds how long an idle pooled connection is kept.
    """
    if database_url.startswith("sqlite"):
        return {"connect_args": {"check_same_thread": False}}
    if database_url.startswith("postgresql"):
        return {"connect_args": {}, "pool_pre_ping": True, "pool_recycle": 300}
    return {"connect_args": {}}

engine = create_engine(DATABASE_URL, **engine_options(DATABASE_URL))
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
