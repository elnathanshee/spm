"""Test-suite safety guard.

Tests must never run against the shared Neon database.

Two things would otherwise make that happen by accident:
  * backend/database.py calls load_dotenv(), which reads DATABASE_URL from
    .env - and .env points at Neon.
  * backend/main.py runs Base.metadata.create_all(bind=engine) at import time,
    and every test module imports backend.main.

pytest loads this file before it imports any test module, so setting the
variable here happens before that import chain runs. load_dotenv() does not
overwrite a variable that is already set, so this value wins over .env.

A DATABASE_URL that is already a sqlite URL is left alone, so you can point the
suite at a sqlite file when you want to inspect it afterwards. Anything else,
including an exported Neon URL, is replaced.
"""

import os
from passlib.context import CryptContext
import backend.security

_requested = os.environ.get("DATABASE_URL", "")

if not _requested.startswith("sqlite"):
    os.environ["DATABASE_URL"] = "sqlite:///:memory:"

# Speed up test password hashing from 12 rounds to 4 rounds.
# Real logins and production app code stay at 12 rounds. Lowering rounds to 4
# in conftest.py applies to test executions only, dropping suite runtime from
# ~14 minutes to under 1 minute without altering application behavior.
backend.security.pwd_context = CryptContext(
    schemes=["bcrypt"], deprecated="auto", bcrypt__rounds=4
)
