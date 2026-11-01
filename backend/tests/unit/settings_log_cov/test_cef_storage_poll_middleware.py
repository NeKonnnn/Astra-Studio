"""Тесты storage_audit.py, poll_audit.py и cef_audit_middleware.py (CEF-аудит хранилищ, поллинг, middleware)."""

import contextvars
import sys
from types import SimpleNamespace

import pytest

from backend.settings.cef_logger import cef_audit_context as actx
from backend.settings.cef_logger import cef_logger as cl
from backend.settings.cef_logger import poll_audit as pa
from backend.settings.cef_logger import storage_audit as sa
from backend.settings.cef_logger.cef_audit_middleware import CefAuditMiddleware


@pytest.fixture
def emitted(monkeypatch):
    """Перехват log_cef_event: список (event_id, kwargs)."""
    calls = []
    monkeypatch.setattr(cl, "log_cef_event", lambda eid, **kw: calls.append((eid, kw)))
    return calls


@pytest.fixture(autouse=True)
def storage_env(monkeypatch):
    for var in ("MONGODB_HOST", "MONGODB_PORT", "MINIO_ENDPOINT", "MINIO_PORT", "RAG_PVC_DIR"):
        monkeypatch.delenv(var, raising=False)
    # верхнеуровневого пакета settings нет в окружении тестов -> ветки с env
    monkeypatch.setitem(sys.modules, "settings", None)
    # log_cef_event вызывает socket.getfqdn(): исключаем реальный DNS
    monkeypatch.setattr(cl.socket, "getfqdn", lambda *a: "host.test")


def _fake_settings(monkeypatch, **sections):
    ns = SimpleNamespace(**{k: SimpleNamespace(**v) for k, v in sections.items()})
    monkeypatch.setitem(sys.modules, "settings", SimpleNamespace(get_settings=lambda: ns))


# ---------------- storage_audit: адреса ----------------


def test_mongo_endpoint_from_env_fallback(monkeypatch):
    assert sa._mongo_endpoint() == ("mongodb", 27017)
    monkeypatch.setenv("MONGODB_HOST", "mongo.corp")
    monkeypatch.setenv("MONGODB_PORT", "27999")
    assert sa._mongo_endpoint() == ("mongo.corp", 27999)


def test_mongo_endpoint_from_settings(monkeypatch):
    _fake_settings(monkeypatch, mongodb={"host": "m.cfg", "port": 1234})
    assert sa._mongo_endpoint() == ("m.cfg", 1234)
    _fake_settings(monkeypatch, mongodb={"host": None, "port": None})
    assert sa._mongo_endpoint() == ("mongodb", 27017)


def test_integration_extra_shape():
    assert sa._integration_extra(service_name="S", method_name="m", request_uuid="r", dhost="h", dpt=5) == {
        "_deviceDirection": 1,
        "dhost": "h",
        "dpt": 5,
        "methodName": "m",
        "serviceName": "S",
        "requestUuid": "r",
    }


# ---------------- storage_audit: _emit_cef ----------------


def test_emit_cef_passes_audit_context_to_logger(emitted):
    def body():
        req, user = object(), {"username": "u"}
        actx.cef_audit_set(request=req, user=user)
        sa._emit_cef("INT005", status_code=200, extra={"a": 1})
        return req, user

    req, user = contextvars.copy_context().run(body)
    ((eid, kw),) = emitted
    assert eid == "INT005"
    assert kw == {"request": req, "current_user": user, "status_code": 200, "extra": {"a": 1}}


def test_emit_cef_swallows_logger_errors(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("аудит упал")

    monkeypatch.setattr(cl, "log_cef_event", boom)
    sa._emit_cef("INT005")  # исключение не должно выйти наружу и сломать бизнес-операцию


# ---------------- storage_audit: Mongo / Postgres ----------------


def test_log_mongo_success(emitted):
    sa.log_mongo_success("find", request_uuid="uid-1")
    ((eid, kw),) = emitted
    assert eid == "INT005" and kw["status_code"] == 200
    assert kw["extra"] == {
        "_deviceDirection": 1, "dhost": "mongodb", "dpt": 27017, "methodName": "find",
        "serviceName": "AstraChat-MongoDB", "requestUuid": "uid-1",
    }


def test_log_mongo_success_generates_request_uuid(emitted):
    sa.log_mongo_success("find")
    assert len(emitted[0][1]["extra"]["requestUuid"]) == 32


def test_log_mongo_failure_truncates_error_and_has_no_status(emitted):
    sa.log_mongo_failure("insert", "x" * 700)
    ((eid, kw),) = emitted
    assert eid == "INT006" and kw["status_code"] is None
    assert kw["extra"]["codeStatus"] == "EXCEPTION" and len(kw["extra"]["textStatus"]) == 512
    assert len(kw["extra"]["requestUuid"]) == 32


def test_log_postgres_success_and_failure(emitted):
    sa.log_postgres_success("select", dhost="pg", dpt=5432, request_uuid="r")
    sa.log_postgres_failure("select", "ошибка", dhost="pg", dpt=5432)
    (e1, k1), (e2, k2) = emitted
    assert (e1, k1["extra"]["serviceName"], k1["extra"]["dhost"], k1["extra"]["dpt"]) == (
        "INT005", "AstraChat-PostgreSQL", "pg", 5432)
    assert (e2, k2["extra"]["textStatus"], k2["status_code"]) == ("INT006", "ошибка", None)


def test_storage_failure_is_audited_as_failure(cef_event_text):
    sa.log_postgres_failure("select", "boom", dhost="pg", dpt=5432)
    assert "outcome=failure" in cef_event_text()


@pytest.fixture
def cef_event_text(capture_logger):
    handler = capture_logger("cef", 20)

    def _get():
        return handler.messages[-1]

    return _get


# ---------------- storage_audit: MinIO ----------------


def test_minio_read_write_events(emitted):
    sa.log_minio_read_success("a/b.txt", "bkt")
    sa.log_minio_read_failure("a/b.txt", "bkt", "x" * 400)
    sa.log_minio_write_success("a/b.txt", "bkt")
    sa.log_minio_write_success("a/b.txt", "bkt", display_name="Отчёт.txt")
    sa.log_minio_write_failure("a/b.txt", "bkt", "e" * 400)
    sa.log_minio_write_failure("a/b.txt", "bkt", "e", display_name="Имя")
    ev = [(eid, kw["status_code"], kw["extra"]) for eid, kw in emitted]
    assert ev[0] == ("FS001", 200, {"file": "a/b.txt", "bucket": "minio://bkt/a/b.txt"})
    assert ev[1][0:2] == ("FS002", 500) and len(ev[1][2]["reason"]) == 300
    assert ev[2][2]["file"] == "a/b.txt"
    assert ev[3][2]["file"] == "Отчёт.txt" and ev[3][2]["bucket"] == "minio://bkt/a/b.txt"
    assert ev[4][0:2] == ("FS004", 500) and ev[4][2]["bucket"] == "minio://bkt"  # без имени объекта
    assert ev[5][2]["file"] == "Имя"


def test_minio_bucket_uri():
    assert sa._minio_bucket_uri("b") == "minio://b"
    assert sa._minio_bucket_uri("b", "o/1") == "minio://b/o/1"


@pytest.mark.parametrize(
    ("endpoint", "dhost", "dpt"),
    [
        ("http://minio.corp:9100", "minio.corp", 9100),
        ("minio.corp:9200", "minio.corp", 9200),
        ("minio.corp", "minio.corp", 9000),
        ("https://minio.corp", "minio.corp", 9000),
    ],
)
def test_minio_remove_events_resolve_endpoint(monkeypatch, emitted, endpoint, dhost, dpt):
    monkeypatch.setenv("MINIO_ENDPOINT", endpoint)
    sa.log_minio_remove_success("obj", "bkt")
    sa.log_minio_remove_failure("obj", "bkt", "ошибка")
    (e1, k1), (e2, k2) = emitted
    assert e1 == "INT005" and e2 == "INT006"
    for k in (k1, k2):
        assert k["extra"]["dhost"] == dhost and k["extra"]["dpt"] == dpt
        assert k["extra"]["serviceName"] == "AstraChat-MinIO"
        assert k["extra"]["methodName"] == "MinIO.removeObject(bkt/obj)"
    assert k2["extra"]["textStatus"] == "ошибка" and k2["status_code"] is None


def test_minio_remove_uses_port_env_and_default_host(monkeypatch, emitted):
    monkeypatch.setenv("MINIO_PORT", "9999")
    sa.log_minio_remove_success("o", "b")
    extra = emitted[0][1]["extra"]
    assert (extra["dhost"], extra["dpt"]) == ("minio", 9999)


def test_minio_remove_endpoint_from_settings(monkeypatch, emitted):
    _fake_settings(monkeypatch, minio={"endpoint": "http://cfg-minio:9500"})
    sa.log_minio_remove_success("o", "b")
    sa.log_minio_remove_failure("o", "b", "e")
    assert all(kw["extra"]["dhost"] == "cfg-minio" and kw["extra"]["dpt"] == 9500 for _, kw in emitted)


def test_minio_remove_empty_endpoint_defaults_to_minio(monkeypatch, emitted):
    monkeypatch.setenv("MINIO_ENDPOINT", "")
    sa.log_minio_remove_success("o", "b")
    sa.log_minio_remove_failure("o", "b", "e")
    assert [kw["extra"]["dhost"] for _, kw in emitted] == ["minio", "minio"]


# ---------------- storage_audit: PVC ----------------


def test_pvc_uri(monkeypatch):
    assert sa._pvc_uri() == "file:///ragdb"
    assert sa._pvc_uri("/a/b") == "file:///ragdb/a/b"
    monkeypatch.setenv("RAG_PVC_DIR", "/data/rag/")
    assert sa._pvc_uri("x/y.txt") == "file:///data/rag/x/y.txt"


def test_rag_pvc_write_success(emitted):
    sa.log_rag_pvc_write_success("f.pdf", object_key="u1/f.pdf", scope="user", file_size=10)
    sa.log_rag_pvc_write_success("f.pdf", object_key="u1/f.pdf", scope="user")
    (eid, kw), (_, kw2) = emitted
    assert eid == "FS009" and kw["status_code"] == 200
    assert kw["extra"] == {
        "fname": "f.pdf", "file": "u1/f.pdf", "bucket": "file:///ragdb/u1/f.pdf",
        "cs1": "user", "cs1Label": "RagPvcScope", "cs2": "u1/f.pdf", "cs2Label": "PvcRagObject",
        "fsize": 10,
    }
    assert "fsize" not in kw2["extra"]


def test_rag_pvc_write_failure(emitted):
    sa.log_rag_pvc_write_failure("f.pdf", "r" * 400, scope="project", file_size=0)
    sa.log_rag_pvc_write_failure("f.pdf", "причина", scope="project")
    (eid, kw), (_, kw2) = emitted
    assert eid == "FS010" and kw["status_code"] == 500
    assert len(kw["extra"]["reason"]) == 300 and kw["extra"]["fsize"] == 0
    assert kw["extra"]["bucket"] == "file:///ragdb"
    assert "fsize" not in kw2["extra"] and kw2["extra"]["reason"] == "причина"


def test_rag_pvc_remove_events_extract_file_name(emitted):
    sa.log_rag_pvc_remove_success("u1/sub/doc.txt", scope="user")
    sa.log_rag_pvc_remove_success("", scope="user")
    sa.log_rag_pvc_remove_failure("u1/doc.txt", "user", "e" * 400)
    sa.log_rag_pvc_remove_failure("plain.txt", "user", "ошибка")
    (e1, k1), (_, k2), (e3, k3), (_, k4) = emitted
    assert e1 == "FS011" and k1["status_code"] == 200
    assert k1["extra"]["fname"] == "doc.txt" and k1["extra"]["bucket"] == "file:///ragdb/u1/sub/doc.txt"
    assert k2["extra"]["fname"] == ""
    assert e3 == "FS012" and k3["status_code"] == 500 and len(k3["extra"]["reason"]) == 300
    assert k3["extra"]["fname"] == "doc.txt" and k4["extra"]["fname"] == "plain.txt"


def test_storage_events_render_valid_cef_lines(capture_logger):
    """Сквозная проверка: события хранилищ собираются в корректные строки CEF."""
    h = capture_logger("cef", 20)
    sa.log_minio_write_success("o.txt", "bkt", display_name="Имя=1\nX")
    line = h.messages[-1]
    assert line.startswith("CEF:0|") and "|FS003|MinIO File Write|" in line
    assert "\n" not in line and "file=Имя\\=1\\nX" in line


# ---------------- poll_audit ----------------


@pytest.fixture(autouse=False)
def poll_state(monkeypatch):
    pa.reset_state()
    for var in (pa._ENV_ENABLED, pa._ENV_INTERVAL):
        monkeypatch.delenv(var, raising=False)
    yield
    pa.reset_state()


def test_poll_setting_env_bool_and_int(poll_state, monkeypatch):
    monkeypatch.setenv("X_B", "YES")
    assert pa._setting("X_B", "f", False) is True
    monkeypatch.setenv("X_B", "off")
    assert pa._setting("X_B", "f", True) is False
    monkeypatch.setenv("X_I", "45")
    assert pa._setting("X_I", "f", 60) == 45
    monkeypatch.setenv("X_I", "abc")
    assert pa._setting("X_I", "f", 60) == 60


def test_poll_setting_from_config_and_defaults(poll_state, monkeypatch):
    monkeypatch.delenv("X_I", raising=False)
    monkeypatch.setattr(
        "backend.settings.get_settings", lambda: SimpleNamespace(audit=SimpleNamespace(f=77))
    )
    assert pa._setting("X_I", "f", 60) == 77
    monkeypatch.setattr(
        "backend.settings.get_settings", lambda: SimpleNamespace(audit=SimpleNamespace(f=None))
    )
    assert pa._setting("X_I", "f", 60) == 60
    monkeypatch.setattr("backend.settings.get_settings", lambda: SimpleNamespace())
    assert pa._setting("X_I", "f", 60) == 60


def test_poll_setting_survives_config_failure(poll_state, monkeypatch):
    def boom():
        raise RuntimeError("конфиг не прочитан")

    monkeypatch.setattr("backend.settings.get_settings", boom)
    assert pa._setting("X_I", "f", 60) == 60


@pytest.mark.parametrize(
    ("method", "path", "status", "expected"),
    [
        ("GET", "/api/rag/reindex/status", 200, True),
        ("head", "/api/chats/1/documents/", 304, True),
        ("GET", "/api/chats/1/documents", None, True),
        ("POST", "/api/chats/1/documents", 200, False),
        ("GET", "/api/chats/1/documents", 404, False),
        ("GET", "/api/chats/1/documents", 500, False),
        ("GET", "/api/chats/1/documents", "oops", False),
        ("GET", "/api/other", 200, False),
        (None, "/documents", 200, False),
        ("GET", None, 200, False),
    ],
)
def test_is_polling_read(method, path, status, expected):
    assert pa.is_polling_read(method, path, status) is expected


def test_poll_decision_disabled(poll_state, monkeypatch):
    monkeypatch.setenv(pa._ENV_ENABLED, "false")
    assert pa.poll_audit_decision({"username": "u"}, "GET", "/documents") == (False, 0)


def test_poll_decision_throttles_within_interval(poll_state, monkeypatch):
    monkeypatch.setenv(pa._ENV_INTERVAL, "60")
    clock = iter([100.0, 110.0, 120.0, 161.0])
    monkeypatch.setattr(pa.time, "monotonic", lambda: next(clock))
    u = {"username": "u"}
    assert pa.poll_audit_decision(u, "get", "/documents/") == (True, 1)
    assert pa.poll_audit_decision(u, "GET", "/documents") == (False, 0)
    assert pa.poll_audit_decision(u, "GET", "/documents") == (False, 0)
    # окно истекло: событие представляет 1 + 2 пропущенных обращения
    assert pa.poll_audit_decision(u, "GET", "/documents") == (True, 3)


def test_poll_decision_keys_are_per_user_method_path(poll_state, monkeypatch):
    monkeypatch.setattr(pa.time, "monotonic", lambda: 1.0)
    assert pa.poll_audit_decision({"username": "a"}, "GET", "/documents") == (True, 1)
    assert pa.poll_audit_decision({"username": "b"}, "GET", "/documents") == (True, 1)
    assert pa.poll_audit_decision({"user_id": "7"}, "GET", "/documents") == (True, 1)
    assert pa.poll_audit_decision(None, "GET", "/documents") == (True, 1)
    assert pa.poll_audit_decision({}, "GET", "/documents") == (False, 0)  # тоже SYSTEM
    assert pa.poll_audit_decision({"username": "a"}, "GET", "/reindex/status") == (True, 1)


def test_poll_decision_zero_interval_always_emits(poll_state, monkeypatch):
    monkeypatch.setenv(pa._ENV_INTERVAL, "0")
    assert pa.poll_audit_decision(None, "GET", "/documents") == (True, 1)
    assert pa.poll_audit_decision(None, "GET", "/documents") == (True, 1)
    assert pa._LAST_EMIT == {}


def test_poll_decision_bad_interval_falls_back_to_default(poll_state, monkeypatch):
    monkeypatch.setattr(pa, "_setting", lambda env, field, default: "не число" if env == pa._ENV_INTERVAL else True)
    monkeypatch.setattr(pa.time, "monotonic", lambda: 5.0)
    assert pa.poll_audit_decision(None, "GET", "/documents") == (True, 1)
    assert pa.poll_audit_decision(None, "GET", "/documents") == (False, 0)  # действует интервал по умолчанию


def test_poll_decision_evicts_stale_keys(poll_state, monkeypatch):
    monkeypatch.setenv(pa._ENV_INTERVAL, "1")
    monkeypatch.setattr(pa, "_KEYS_MAX", 2)
    now = {"t": 0.0}
    monkeypatch.setattr(pa.time, "monotonic", lambda: now["t"])
    pa.poll_audit_decision({"username": "old1"}, "GET", "/documents")
    pa.poll_audit_decision({"username": "old2"}, "GET", "/documents")
    pa._SUPPRESSED[("old1", "GET", "/documents")] = 4
    now["t"] = 100.0  # старые записи старше interval * 10
    assert pa.poll_audit_decision({"username": "new"}, "GET", "/documents") == (True, 1)
    assert list(pa._LAST_EMIT) == [("new", "GET", "/documents")]
    assert pa._SUPPRESSED == {}


def test_poll_count_extra():
    assert pa.poll_count_extra(1) == {}
    assert pa.poll_count_extra(0) == {}
    assert pa.poll_count_extra(5) == {"cn1": 5, "cn1Label": "RequestCount"}


def test_poll_reset_state(poll_state):
    pa._LAST_EMIT[("u", "GET", "/p")] = 1.0
    pa._SUPPRESSED[("u", "GET", "/p")] = 2
    pa.reset_state()
    assert pa._LAST_EMIT == {} and pa._SUPPRESSED == {}


# ---------------- CefAuditMiddleware ----------------


def _mw():
    return CefAuditMiddleware(app=lambda *a, **k: None)


async def test_middleware_sets_context_for_request_and_resets(monkeypatch):
    monkeypatch.setattr("backend.auth.jwt_handler.try_user_from_request", lambda r: {"username": "mw-user"})
    request = SimpleNamespace(tag="req")
    seen = {}

    async def call_next(req):
        seen["peek"] = actx.cef_audit_peek()
        seen["remote"] = actx.cef_audit_peek_socket_remote()
        return "response"

    token = actx.cef_audit_set(socket_remote={"src": "1.1.1.1"})
    try:
        result = await _mw().dispatch(request, call_next)
        # внутри запроса: request/user заданы, подсказка сокета сброшена (REST)
        assert result == "response"
        assert seen["peek"][0] is request and seen["peek"][1] == {"username": "mw-user"}
        assert seen["remote"] is None
        # после запроса контекст возвращается к прежнему
        assert actx.cef_audit_peek() == (None, None, None)
        assert actx.cef_audit_peek_socket_remote() == {"src": "1.1.1.1"}
    finally:
        actx.cef_audit_reset(token)


async def test_middleware_anonymous_when_token_parsing_fails(monkeypatch):
    def boom(r):
        raise RuntimeError("битый токен")

    monkeypatch.setattr("backend.auth.jwt_handler.try_user_from_request", boom)
    seen = {}

    async def call_next(req):
        seen["peek"] = actx.cef_audit_peek()
        return "ok"

    request = SimpleNamespace()
    assert await _mw().dispatch(request, call_next) == "ok"
    assert seen["peek"][0] is request and seen["peek"][1] is None


async def test_middleware_resets_context_when_handler_raises(monkeypatch):
    monkeypatch.setattr("backend.auth.jwt_handler.try_user_from_request", lambda r: {"username": "u"})

    async def call_next(req):
        raise ValueError("обработчик упал")

    with pytest.raises(ValueError):
        await _mw().dispatch(SimpleNamespace(), call_next)
    assert actx.cef_audit_peek() == (None, None, None)
