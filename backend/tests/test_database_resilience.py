from backend.database import engine_options


def test_postgresql_pool_validates_and_recycles_connections():
    options = engine_options("postgresql://user:secret@example.test/connectsphere")

    assert options["pool_pre_ping"] is True
    assert options["pool_recycle"] == 300
    assert options["connect_args"] == {}


def test_sqlite_keeps_thread_compatibility_without_postgresql_pool_options():
    options = engine_options("sqlite:///./test.db")

    assert options == {"connect_args": {"check_same_thread": False}}
