"""exceptions: сообщения и атрибуты общих исключений backend."""

import inspect

import pytest

from backend import exceptions as ex

pytestmark = pytest.mark.unit


def _all_exception_classes():
    return [
        cls
        for _, cls in inspect.getmembers(ex, inspect.isclass)
        if issubclass(cls, BaseException) and cls.__module__ == ex.__name__
    ]


def _sample_args(cls):
    """Подставляет по именам параметров значения, при которых конструктор отрабатывает."""
    sig = inspect.signature(cls.__init__)
    args, kwargs = [], {}
    for name, param in sig.parameters.items():
        if name == "self" or param.kind in (param.VAR_POSITIONAL, param.VAR_KEYWORD):
            continue
        if param.default is not inspect.Parameter.empty:
            continue
        value = {
            "name": "NAME",
            "field": "field",
            "locations": "a.b",
            "provider_id": "p",
            "kind": "k",
            "module": "mod",
            "expected": "exp",
            "actual": "act",
            "raw": "raw",
            "names": ["A", "B"],
            "code": "token_validation_failed",
            "reason": "r",
            "item_type": int,
            "server_id": "srv",
            "cause": ValueError("boom"),
            "paths": ["/a", "/b"],
            "key": "key",
            "env_key": "ENV",
            "docker_field": "df",
            "port_field": "pf",
            "config_path": "/cfg.yml",
            "method": "GET",
            "url": "http://x",
            "status_code": 502,
            "detail": "bad gateway",
        }[name]
        if param.kind is param.KEYWORD_ONLY:
            kwargs[name] = value
        else:
            args.append(value)
    return args, kwargs


def test_the_module_defines_a_meaningful_number_of_exceptions():
    assert len(_all_exception_classes()) >= 45


@pytest.mark.parametrize("cls", _all_exception_classes(), ids=lambda c: c.__name__)
def test_every_exception_can_be_raised_with_a_readable_message(cls):
    args, kwargs = _sample_args(cls)
    error = cls(*args, **kwargs)
    assert isinstance(error, Exception)
    assert str(error).strip()
    with pytest.raises(cls):
        raise error


@pytest.mark.parametrize("cls", _all_exception_classes(), ids=lambda c: c.__name__)
def test_every_exception_has_a_docstring(cls):
    assert (cls.__doc__ or "").strip()


class TestConfigErrors:
    def test_missing_value_with_and_without_alternative(self):
        e = ex.ConfigValueMissingError("JWT_SECRET")
        assert e.name == "JWT_SECRET" and e.alt is None and str(e) == "JWT_SECRET не задан в YAML или ENV"
        e = ex.ConfigValueMissingError("A", alt="B")
        assert e.alt == "B" and str(e) == "A или B не задан в YAML или ENV"

    def test_missing_field_keeps_location(self):
        e = ex.ConfigFieldMissingError("host", "mongodb.host")
        assert (e.field, e.locations) == ("host", "mongodb.host")
        assert "host" in str(e) and "mongodb.host" in str(e)

    def test_url_errors_name_the_keys(self):
        e = ex.ConfigUrlKeyMissingError("rag", "RAG_URL")
        assert "urls.rag" in str(e) and "RAG_URL" in str(e)
        e = ex.ConfigUrlPortMissingError("rag_docker", "rag_port")
        assert "urls.rag_docker" in str(e) and "urls.rag_port" in str(e)

    def test_load_error_keeps_path_and_cause(self):
        cause = OSError("denied")
        e = ex.ConfigLoadError("/etc/c.yml", cause)
        assert e.cause is cause and e.config_path == "/etc/c.yml" and "denied" in str(e)

    def test_value_errors_are_catchable_as_value_error(self):
        for cls, args in (
            (ex.ConfigValueMissingError, ("x",)),
            (ex.ConfigFieldMissingError, ("f", "l")),
            (ex.LLMProviderBaseUrlRequiredError, ("p", "k")),
            (ex.McpServerIdRequiredError, ()),
            (ex.AuthSessionTimeoutNotConfiguredError, ()),
        ):
            with pytest.raises(ValueError):
                raise cls(*args)

    def test_provider_url_error_mentions_id_and_kind(self):
        e = ex.LLMProviderBaseUrlRequiredError("gw", "openai-compat")
        assert "gw" in str(e) and "openai-compat" in str(e) and e.provider_id == "gw"

    def test_module_attribute_error_is_an_attribute_error(self):
        with pytest.raises(AttributeError, match="has no attribute 'x'"):
            raise ex.ModuleAttributeNotFoundError("backend.mod", "x")


class TestJwtErrors:
    def test_audience_and_issuer_keep_values(self):
        e = ex.JwtInvalidAudienceError("astra", ["other"])
        assert (e.expected, e.actual) == ("astra", ["other"]) and "astra" in str(e)
        e = ex.JwtInvalidIssuerError("iss", None)
        assert e.actual is None and "iss" in str(e)

    def test_env_errors(self):
        assert "JWT_SECRET" in str(ex.JwtEnvVarMissingError("JWT_SECRET"))
        invalid = ex.JwtEnvVarInvalidError("JWT_TTL", "abc")
        assert invalid.raw == "abc" and "JWT_TTL" in str(invalid) and "abc" in str(invalid)
        many = ex.JwtEnvVarsMissingError(["A", "B"])
        assert many.names == ["A", "B"] and str(many).endswith("A, B")

    @pytest.mark.parametrize(
        "code,expected",
        [
            ("missing_client_binding", "Токен не привязан к сессии браузера"),
            ("client_binding_mismatch", "Токен не соответствует сессии браузера"),
            ("missing_session_id", "Сессия завершена"),
            ("session_not_in_memory", "Сессия завершена: сервер перезапущен"),
            ("invalid_token_type", "Неверный тип токена"),
            ("missing_sub", "Неверный токен"),
            ("token_validation_failed", "Не удалось валидировать токен"),
        ],
    )
    def test_access_token_messages(self, code, expected):
        e = ex.JwtAuthError(code, reason="why")
        assert e.detail == expected == str(e)
        assert (e.code, e.reason, e.token_type) == (code, "why", "access")

    def test_refresh_token_variants(self):
        assert ex.JwtAuthError("missing_sub", reason="r", token_type="refresh").detail == "Неверный refresh токен"
        assert (
            ex.JwtAuthError("token_validation_failed", reason="r", token_type="refresh").detail
            == "Не удалось валидировать refresh токен"
        )

    def test_superseded_session_explains_the_limit(self):
        single = ex.JwtAuthError("session_superseded", reason="r")
        assert "с другого устройства" in single.detail
        one = ex.JwtAuthError("session_superseded", reason="r", max_sessions=1)
        assert "с другого устройства" in one.detail
        many = ex.JwtAuthError("session_superseded", reason="r", max_sessions=3)
        assert "(3)" in many.detail and "лимит" in many.detail

    def test_unknown_code_is_returned_as_is(self):
        assert ex.JwtAuthError("brand_new_code", reason="r").detail == "brand_new_code"


class TestDatabaseAndStorageErrors:
    def test_not_connected_uses_the_database_name(self):
        assert str(ex.DatabaseNotConnectedError()) == "База данных не подключена. Вызовите connect() сначала."
        assert str(ex.DatabaseNotConnectedError("Redis")).startswith("Redis не подключена")

    def test_mongo_unavailable_names_the_operation(self):
        assert "сохранить сообщение" in str(ex.MongoDBUnavailableError())
        assert "удалить диалог" in str(ex.MongoDBUnavailableError("удалить диалог"))

    def test_mongo_save_error_wraps_cause(self):
        cause = RuntimeError("disk full")
        e = ex.MongoDBSaveError(cause)
        assert e.cause is cause and "disk full" in str(e)

    def test_settings_unavailable_hint(self):
        assert "Проверьте" in str(ex.SettingsModuleUnavailableError())
        assert str(ex.SettingsModuleUnavailableError(hint="X")).endswith("X")

    def test_settings_file_not_found_is_an_os_error(self):
        e = ex.SettingsFileNotFoundError(["/a", "/b"])
        assert isinstance(e, OSError) and e.paths == ["/a", "/b"] and "/a" in str(e)

    def test_minio_import_error_is_an_import_error(self):
        with pytest.raises(ImportError):
            raise ex.MinioLibraryNotInstalledError()

    def test_llm_provider_json_errors(self):
        assert isinstance(ex.LLMProvidersJsonTypeError(), TypeError)
        e = ex.LLMProvidersJsonEntryTypeError(int)
        assert e.item_type is int and "int" in str(e)


class TestMcpAndRealtimeErrors:
    def test_credentials_key_messages(self):
        assert str(ex.McpCredentialsKeyNotConfiguredError()) == "MCP_CREDENTIALS_ENCRYPTION_KEY is not configured"
        assert "cannot store credentials" in str(ex.McpCredentialsKeyNotConfiguredError(for_store=True))

    def test_session_unavailable_names_the_server(self):
        e = ex.McpSessionUnavailableError("jira")
        assert e.server_id == "jira" and str(e) == "MCP session unavailable: jira"

    def test_socket_errors_refuse_the_connection(self):
        for cls in (ex.SocketNotAuthorizedError, ex.SocketInvalidTokenError):
            assert issubclass(cls, ConnectionRefusedError)
        assert str(ex.SocketNotAuthorizedError()) == "Не авторизован"
        assert "просроченный" in str(ex.SocketInvalidTokenError())


class TestRagErrors:
    def test_request_error_exposes_response_details(self):
        e = ex.RagServiceRequestError("POST", "http://rag/search", 502, "bad gateway")
        assert (e.method, e.url, e.status_code, e.detail) == ("POST", "http://rag/search", 502, "bad gateway")
        assert str(e) == "SVC-RAG POST http://rag/search failed: 502 bad gateway"

    def test_generic_error_wraps_the_cause(self):
        cause = TimeoutError("slow")
        e = ex.RagServiceError("GET", "http://rag/x", cause)
        assert e.cause is cause and str(e) == "SVC-RAG GET http://rag/x error: slow"

    def test_the_two_errors_are_distinguishable(self):
        assert not issubclass(ex.RagServiceError, ex.RagServiceRequestError)
        assert not issubclass(ex.RagServiceRequestError, ex.RagServiceError)


class TestValidationErrors:
    def test_messages(self):
        assert str(ex.InvalidAudioDataError()) == "Некорректные аудио данные"
        assert str(ex.InvalidFilenameError()) == "Invalid filename"

    def test_transcription_errors_mention_the_switch(self):
        for cls in (
            ex.LocalTranscribersUnavailableError,
            ex.SttUnavailableError,
            ex.LocalWhisperXUnavailableError,
            ex.WhisperXNotInstalledError,
        ):
            assert "USE_LLM_SVC" in str(cls())
