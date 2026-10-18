"""Real frontend proxy -> backend integration. Never uses Neon or existing services."""
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import time
from urllib.request import urlopen

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from backend.database import Base
from backend.models import User, LoginSession, Event


def test_booking_through_real_frontend_proxy(tmp_path):
    root = Path(__file__).resolve().parents[2]
    node = shutil.which("node") or str(Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "nodejs/node.exe")
    if not Path(node).exists() or not (root / "frontend/node_modules/vite").exists():
        pytest.skip("Existing Node/Vite installation required; no dependencies installed")
    database_url = "sqlite:///" + (tmp_path / "booking-http.db").as_posix()
    engine = create_engine(database_url)
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        users = [User(username=role, role=role, password_hash="unused")
                 for role in ("coordinator", "lead", "venue_staff", "organiser")]
        db.add_all(users); db.flush()
        for user, token in zip(users, ("coordinator-http", "lead-http", "staff-http", "organiser-http")):
            db.add(LoginSession(user_id=user.id, token=token, valid=True))
        other_staff = User(username="staff2", role="venue_staff", password_hash="unused")
        db.add(other_staff); db.flush()
        db.add(LoginSession(user_id=other_staff.id, token="staff2-http", valid=True))
        event = Event(title="HTTP integration", organiser_id=users[3].id,
                      assigned_coordinator_id=users[0].id, status="Submitted")
        db.add(event); db.commit(); event_id = event.id
    engine.dispose()
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0)); port = sock.getsockname()[1]
    env = {**os.environ, "DATABASE_URL": database_url, "PYTHONDONTWRITEBYTECODE": "1"}
    process = subprocess.Popen([sys.executable, "-m", "uvicorn", "backend.main:app",
                                "--host", "127.0.0.1", "--port", str(port)],
                               cwd=root, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    try:
        for _ in range(80):
            if process.poll() is not None:
                pytest.fail("Temporary backend failed: " + process.stderr.read())
            try:
                with urlopen(f"http://127.0.0.1:{port}/openapi.json", timeout=.5) as response:
                    if response.status == 200:
                        break
            except OSError:
                time.sleep(.1)
        else:
            pytest.fail("Temporary backend startup timed out")
        result = subprocess.run([node, "scripts/verify-booking-http.mjs", f"http://127.0.0.1:{port}", str(event_id)],
                                cwd=root / "frontend", env=env, capture_output=True, text=True, timeout=60)
        assert result.returncode == 0, result.stdout + result.stderr
        print(result.stdout)
    finally:
        process.terminate()
        try:
            process.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill(); process.communicate()
