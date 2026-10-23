import os
from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

# Automatically load environment variables from .env
load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./dev.db")

# Normalize postgres:// to postgresql:// for SQLAlchemy compatibility
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

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
