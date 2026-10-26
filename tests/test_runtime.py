from concurrent.futures import ThreadPoolExecutor
from unittest.mock import MagicMock, Mock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.engine import make_url

from app import main, runtime
from app.db import session
from app.settings import Settings, validate_runtime_settings


@pytest.fixture
def cloud_settings():
    return Settings(
        _env_file=None,
        APP_ENV="cloud",
        DATABASE_URL="postgresql://cloud-db/forseti",
        GOOGLE_CLOUD_PROJECT="test-project",
        VERTEX_AI_PROJECT="test-project",
        RAG_FAIL_LOUD=True,
        DEBUG_LLM_IO=False,
        ALLOW_PIPELINE_OVERRIDE=False,
        PIPELINE_MODE="agentic",
        FUNDAMENTAL_AGENT_MODE="enforced",
        TECHNICAL_AGENT_MODE="shadow",
        CRITIC_MODE="off",
        SYNTHESIZER_CONTRACT="typed",
    )


@pytest.mark.parametrize(("name", "value"), [
    ("DEBUG_LLM_IO", True),
    ("ALLOW_PIPELINE_OVERRIDE", True),
    ("RAG_FAIL_LOUD", False),
    ("DATABASE_URL", Settings.model_fields["DATABASE_URL"].default),
    ("DATABASE_URL", ""),
    ("DATABASE_URL", " "),
    ("GOOGLE_CLOUD_PROJECT", None),
    ("GOOGLE_CLOUD_PROJECT", " "),
    ("VERTEX_AI_PROJECT", None),
    ("VERTEX_AI_PROJECT", ""),
    ("PIPELINE_MODE", "invalid"),
    ("FUNDAMENTAL_AGENT_MODE", "invalid"),
    ("TECHNICAL_AGENT_MODE", "invalid"),
    ("CRITIC_MODE", "invalid"),
    ("SYNTHESIZER_CONTRACT", "invalid"),
])
def test_cloud_rejects_unsafe_settings(cloud_settings, name, value):
    settings = cloud_settings.model_copy(update={name: value})
    with pytest.raises(ValueError, match=name):
        validate_runtime_settings(settings)


def test_valid_cloud_settings_and_local_debugging(cloud_settings):
    validate_runtime_settings(cloud_settings)
    validate_runtime_settings(cloud_settings.model_copy(update={
        "APP_ENV": "local", "DEBUG_LLM_IO": True, "ALLOW_PIPELINE_OVERRIDE": True, "RAG_FAIL_LOUD": False,
    }))


@pytest.mark.parametrize("name", ["DB_POOL_SIZE", "DB_POOL_RECYCLE_SECONDS"])
def test_pool_settings_must_be_positive(name):
    with pytest.raises(ValueError, match=name):
        Settings(_env_file=None, **{name: 0})


def test_overflow_cannot_be_unbounded():
    with pytest.raises(ValueError, match="DB_MAX_OVERFLOW"):
        Settings(_env_file=None, DB_MAX_OVERFLOW=-1)


def test_unknown_environment_rejected():
    with pytest.raises(ValueError, match="APP_ENV"):
        Settings(_env_file=None, APP_ENV="production")


@pytest.fixture
def engine_cache():
    session.dispose_engines()
    yield
    session.dispose_engines()


def test_engine_shared_between_callers_and_threads(monkeypatch, cloud_settings, engine_cache):
    monkeypatch.setattr(session, "get_settings", lambda: cloud_settings)
    with ThreadPoolExecutor(max_workers=8) as executor:
        engines = list(executor.map(lambda _: session.get_engine(), range(50)))
    assert all(engine is engines[0] for engine in engines)
    assert main.get_analysis_engine() is engines[0]
    assert session.get_engine(cloud_settings.DATABASE_URL) is engines[0]
    assert session.get_engine("postgresql://other-db/forseti") is not engines[0]


@pytest.mark.parametrize(("pool_size", "overflow", "recycle"), [(3, 2, 1800), (1, 0, 60)])
def test_pool_configuration_and_disposal(monkeypatch, cloud_settings, engine_cache, pool_size, overflow, recycle):
    cloud_settings = cloud_settings.model_copy(update={
        "DB_POOL_SIZE": pool_size, "DB_MAX_OVERFLOW": overflow, "DB_POOL_RECYCLE_SECONDS": recycle,
    })
    monkeypatch.setattr(session, "get_settings", lambda: cloud_settings)
    engine = session.get_engine()
    assert engine.pool.size() == pool_size
    assert engine.pool._max_overflow == overflow
    assert engine.pool._recycle == recycle
    assert engine.pool._pre_ping is True
    dispose = Mock(wraps=engine.dispose)
    monkeypatch.setattr(engine, "dispose", dispose)
    session.dispose_engines()
    dispose.assert_called_once()
    assert session.get_engine() is not engine


def test_cloud_sql_socket_engine_and_parsing(engine_cache):
    url = "postgresql://cloud-user@/forseti?host=/cloudsql/project:region:instance"
    engine = session.get_engine(url)
    assert engine.url == make_url(url)
    assert engine.dialect.create_connect_args(engine.url)[1]["host"] == "/cloudsql/project:region:instance"
    parsed = main._parse_database_url(url)
    assert parsed["host"] == "/cloudsql/project:region:instance"
    assert parsed["port"] is None


def test_sqlite_engine_remains_supported(engine_cache):
    engine = session.get_engine("sqlite://")
    with engine.connect() as connection:
        assert connection.exec_driver_sql("SELECT 1").scalar() == 1


def test_livez_has_no_database_dependency(monkeypatch):
    engine = Mock(side_effect=AssertionError("liveness must not access DB"))
    monkeypatch.setattr(main, "get_engine", engine)
    response = TestClient(main.app).get("/livez")
    assert response.status_code == 200
    assert response.json() == {"app": "ok"}
    engine.assert_not_called()


@pytest.mark.parametrize("reachable", [True, False])
def test_cloud_health_redacts_details(monkeypatch, cloud_settings, caplog, reachable):
    monkeypatch.setattr(main, "get_settings", lambda: cloud_settings)
    engine = MagicMock()
    if not reachable:
        engine.connect.side_effect = RuntimeError("private-host: secret failure")
    monkeypatch.setattr(main, "get_engine", lambda _: engine)
    response = TestClient(main.app).get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"app": "ok", "db": "ok" if reachable else "unreachable"}
    if reachable:
        engine.connect.return_value.__enter__.return_value.execute.assert_called_once()
    else:
        assert "private-host: secret failure" in caplog.text


def test_local_health_keeps_diagnostics(monkeypatch, cloud_settings):
    monkeypatch.setattr(main, "get_settings", lambda: cloud_settings.model_copy(update={"APP_ENV": "local"}))
    monkeypatch.setenv("DATABASE_URL", "postgresql://local-db:5432/forseti")
    engine = Mock()
    engine.connect.side_effect = RuntimeError("connection refused")
    monkeypatch.setattr(main, "get_engine", lambda _: engine)
    response = TestClient(main.app).get("/healthz")
    assert response.json()["db"] == {
        "status": "unreachable", "detail": "Database connection failed; see server logs", "host": "local-db", "port": 5432,
    }


def test_health_handles_malformed_url_without_exposing_it(monkeypatch, cloud_settings):
    monkeypatch.setattr(main, "get_settings", lambda: cloud_settings)
    monkeypatch.setenv("DATABASE_URL", "malformed-private-database-url")
    response = TestClient(main.app).get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"app": "ok", "db": "unreachable"}


def test_lifespan_validates_and_disposes(monkeypatch, cloud_settings):
    monkeypatch.setattr(main, "get_settings", lambda: cloud_settings)
    dispose = Mock()
    monkeypatch.setattr(main, "dispose_engines", dispose)
    with TestClient(main.app) as client:
        assert client.get("/livez").status_code == 200
        dispose.assert_not_called()
    dispose.assert_called_once()


def test_lifespan_rejects_unsafe_cloud_startup(monkeypatch, cloud_settings):
    monkeypatch.setattr(main, "get_settings", lambda: cloud_settings.model_copy(update={"DEBUG_LLM_IO": True}))
    with pytest.raises(ValueError, match="DEBUG_LLM_IO"):
        with TestClient(main.app):
            pytest.fail("unsafe application started")


@pytest.mark.parametrize("fails", [False, True])
def test_job_validates_before_work_and_disposes(monkeypatch, fails):
    events = []
    monkeypatch.setattr(runtime, "validate_runtime_settings", lambda: events.append("validate"))
    monkeypatch.setattr(runtime, "dispose_engines", lambda: events.append("dispose"))

    def job():
        events.append("work")
        if fails:
            raise RuntimeError("job failed")
        return 0

    if fails:
        with pytest.raises(RuntimeError, match="job failed"):
            runtime.run_job(job)
    else:
        assert runtime.run_job(job) == 0
    assert events == ["validate", "work", "dispose"]


def test_invalid_job_settings_prevent_work(monkeypatch):
    monkeypatch.setattr(runtime, "validate_runtime_settings", Mock(side_effect=ValueError("unsafe settings")))
    dispose = Mock()
    work = Mock()
    monkeypatch.setattr(runtime, "dispose_engines", dispose)
    with pytest.raises(ValueError, match="unsafe settings"):
        runtime.run_job(work)
    work.assert_not_called()
    dispose.assert_called_once()
