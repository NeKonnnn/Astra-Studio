"""api_agents: внутренние функции (обогащение, KB-файлы, клонирование графа) — вызываются напрямую."""

import sys
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from backend import api_agents as module
from backend.tests.api.conftest import ADMIN, ALICE

from ._agents_fake import FullAgentRepo, make_agent
from ._fakes import in_group, role_caches, with_mapping  # noqa: F401

pytestmark = pytest.mark.unit


@pytest.fixture
def repo():
    return FullAgentRepo()


def _raw_config(agent, raw):
    """БД может отдать config строкой JSON: валидацию модели обходим присваиванием."""
    agent.config = raw
    return agent


# ------------------------------------------------------------------- роли и обогащение


def test_platform_roles_pass_for_admin_and_when_mapping_is_off(with_mapping):
    module._require_platform_roles(ADMIN, "owner", detail="нельзя")
    module._require_platform_roles(in_group("Owners", ALICE), "owner", detail="нельзя")
    with pytest.raises(HTTPException) as exc:
        module._require_platform_roles(in_group("Viewers", ALICE), "owner", detail="нельзя")
    assert exc.value.status_code == 403 and exc.value.detail == "нельзя"


def test_platform_roles_are_not_checked_without_mapping():
    module._require_platform_roles(ALICE, "owner", detail="нельзя")


async def test_create_repository_exception_is_500(make_client, monkeypatch):
    """Исключение репозитория при создании → 500 с общим текстом (BUG-BE-08: текст исключения наружу не уходит)."""

    class Boom:
        async def find_documents_claimed_by_other_agents(self, *a):
            return []

        async def create_agent(self, **kw):
            raise RuntimeError("insert failed")

    monkeypatch.setattr(module, "get_agent_repository", lambda: Boom())

    response = await make_client(module.router).post(
        "/api/agents", json={"name": "Консультант", "system_prompt": "Ты полезный ассистент банка."}
    )

    assert response.status_code == 500 and response.json()["detail"] == "Внутренняя ошибка сервера"


async def test_enrich_does_nothing_for_empty_list(monkeypatch):
    called = []

    async def fake(items):
        called.append(items)

    monkeypatch.setattr(module, "enrich_items_author_full_names", fake)

    await module._enrich_agents_for_response([])

    assert called == []


async def test_enrich_adds_author_names_and_survives_subagent_failure(monkeypatch):
    import backend.agents.subagents.names as names_module

    authors, subagents = [], []

    async def fake_authors(items):
        authors.append(list(items))

    async def failing_subagents(items):
        subagents.append(list(items))
        raise RuntimeError("names down")

    monkeypatch.setattr(module, "enrich_items_author_full_names", fake_authors)
    monkeypatch.setattr(names_module, "enrich_agents_subagent_names", failing_subagents)
    agent = make_agent(1)

    await module._enrich_agents_for_response([agent])

    assert authors == [[agent]] and subagents == [[agent]]


# ------------------------------------------------------- чужие файлы в config агента


@pytest.mark.parametrize("config", [None, "строка", {}, {"kb_document_ids": "x"}, {"kb_document_ids": []}, {"kb_document_ids": ["a", None]}])
async def test_foreign_kb_check_ignores_irrelevant_configs(repo, config):
    repo.foreign_docs = [1, 2, 3]

    await module._reject_foreign_kb_documents(repo, 5, config)

    assert ("find_documents_claimed_by_other_agents",) not in repo.calls


async def test_foreign_kb_check_rejects_claimed_files(repo):
    repo.foreign_docs = [2, 3]

    with pytest.raises(HTTPException) as exc:
        await module._reject_foreign_kb_documents(repo, 5, {"kb_document_ids": ["1", 2, "x", 3]})

    assert exc.value.status_code == 400 and "2, 3" in exc.value.detail


async def test_foreign_kb_check_passes_free_files(repo):
    await module._reject_foreign_kb_documents(repo, 5, {"kb_document_ids": [1]})

    assert ("find_documents_claimed_by_other_agents",) in repo.calls


# --------------------------------------------------------------- чистка KB после удаления


class FakeRag:
    def __init__(self):
        self.deleted = []
        self.fail_for = set()
        self.result = {"minio_object": "obj", "minio_bucket": "bkt"}
        self.source_files = []
        self.source_file_error = False

    async def kb_delete_document(self, doc_id):
        if doc_id in self.fail_for:
            raise RuntimeError("svc-rag down")
        self.deleted.append(doc_id)
        return self.result


@pytest.fixture
def rag(monkeypatch):
    import backend.routes.rag as rag_routes
    import backend.settings.rag_client as rag_client_module

    fake = FakeRag()

    def delete_source(obj, bucket):
        if fake.source_file_error:
            raise OSError("диск")
        fake.source_files.append((obj, bucket))

    monkeypatch.setattr(rag_client_module, "get_rag_client", lambda: fake)
    monkeypatch.setattr(rag_routes, "_delete_rag_source_file", delete_source)
    return fake


async def test_cleanup_without_documents_does_not_touch_repository(repo, rag):
    await module._cleanup_agent_kb_documents(repo, 1, [])

    assert repo.calls == []


async def test_cleanup_deletes_only_orphans(repo, rag):
    repo.orphans = [1, 3]

    await module._cleanup_agent_kb_documents(repo, 7, [1, 2, 3])

    assert rag.deleted == [1, 3] and rag.source_files == [("obj", "bkt"), ("obj", "bkt")]


async def test_cleanup_with_no_orphans_deletes_nothing(repo, rag):
    await module._cleanup_agent_kb_documents(repo, 7, [1, 2])

    assert rag.deleted == [] and rag.source_files == []


async def test_cleanup_keeps_going_after_failures(repo, rag):
    repo.orphans = [1, 2, 3]
    rag.fail_for = {1}
    rag.source_file_error = True

    await module._cleanup_agent_kb_documents(repo, 7, [1, 2, 3])

    assert rag.deleted == [2, 3]  # падение на первом документе не останавливает остальные


async def test_cleanup_ignores_non_dict_answer(repo, rag):
    repo.orphans = [1]
    rag.result = None

    await module._cleanup_agent_kb_documents(repo, 7, [1])

    assert rag.deleted == [1] and rag.source_files == []


async def test_cleanup_swallows_repository_errors(repo, rag):
    repo.fail_on = {"find_orphan_kb_document_ids"}

    await module._cleanup_agent_kb_documents(repo, 7, [1])  # не должно бросать

    assert rag.deleted == []


# ------------------------------------------------------- граф связанных агентов


async def test_owned_linked_agents_are_collected_transitively(repo):
    repo.add(make_agent(1, "alice", config={"agent_ids": [2, 3, 0, -1, 99]}))
    repo.add(make_agent(2, "Alice", config={"subagents": {"agent_ids": [4, 1]}}))
    repo.add(make_agent(3, "bob"))
    repo.add(make_agent(4, "alice", config=None))
    repo.hidden_for_none = {5}

    result = await module._collect_owned_linked_agent_ids(repo, 1, " ALICE ")

    assert result == [2, 4]  # 3 чужой, 99 не существует, 0/-1 отброшены, цикл на 1 не зацикливает


async def test_owned_linked_agents_stop_at_the_cap(repo, monkeypatch):
    monkeypatch.setattr(module, "_CLONE_GRAPH_MAX_AGENTS", 2)
    repo.add(make_agent(1, "alice", config={"agent_ids": [2, 3, 4]}))
    for i in (2, 3, 4):
        repo.add(make_agent(i, "alice"))

    assert await module._collect_owned_linked_agent_ids(repo, 1, "alice") == [2, 3]


async def test_owned_linked_agents_skip_unknown_root(repo):
    assert await module._collect_owned_linked_agent_ids(repo, 42, "alice") == []


async def test_clone_graph_follows_chain_and_subagents_without_cycles(repo):
    repo.add(make_agent(1, config={"agent_ids": [2, "3"], "subagents": {"agent_ids": [2]}}))
    repo.add(_raw_config(make_agent(2), '{"agent_ids": [1, 4]}'))
    repo.add(make_agent(3, config=None))
    repo.add(make_agent(4))

    assert await module._collect_clone_graph_ids(repo, 1) == [1, 2, 3, 4]


async def test_clone_graph_skips_missing_nodes(repo):
    repo.add(make_agent(1, config={"agent_ids": [8, 2]}))
    repo.add(make_agent(2))

    assert await module._collect_clone_graph_ids(repo, 1) == [1, 2]


async def test_clone_graph_of_unknown_root_is_empty_and_non_positive_root_is_ignored(repo):
    assert await module._collect_clone_graph_ids(repo, 5) == []
    assert await module._collect_clone_graph_ids(repo, 0) == []


async def test_clone_graph_is_truncated(repo, monkeypatch):
    monkeypatch.setattr(module, "_CLONE_GRAPH_MAX_AGENTS", 2)
    repo.add(make_agent(1, config={"agent_ids": [2, 3]}))
    repo.add(make_agent(2))
    repo.add(make_agent(3))

    assert await module._collect_clone_graph_ids(repo, 1) == [1, 2]


# ------------------------------------------------ защита файлов исходника при клонировании


async def test_source_kb_check_does_nothing_when_nothing_expected(repo):
    await module._ensure_source_kb_ids_intact(repo, 1, [])

    assert repo.calls == []


async def test_source_kb_check_ignores_missing_source_and_intact_files(repo):
    await module._ensure_source_kb_ids_intact(repo, 1, [5])
    repo.add(_raw_config(make_agent(1), '{"kb_document_ids": [5, 6]}'))

    await module._ensure_source_kb_ids_intact(repo, 1, [5])

    assert not [c for c in repo.calls if c[0] == "update"]


async def test_source_kb_ids_are_restored_by_merging(repo):
    repo.add(make_agent(1, "alice", config={"kb_document_ids": [9], "x": 1}))

    await module._ensure_source_kb_ids_intact(repo, 1, [5, 6])

    update = [c for c in repo.calls if c[0] == "update"][0]
    assert update[1:4] == (1, "alice", True)
    assert update[4] == {"kb_document_ids": [5, 6, 9], "x": 1}


async def test_source_kb_check_swallows_errors(repo):
    repo.fail_on = {"get_agent"}

    await module._ensure_source_kb_ids_intact(repo, 1, [5])  # не должно бросать


# ----------------------------------------------------------- материализация клона


@pytest.fixture
def clone_deps(monkeypatch):
    log = SimpleNamespace(rag_settings=[], kb=[], ensured=[], kb_result=[99])

    async def settings(src, new, by):
        log.rag_settings.append((src, new, by))

    async def kb(**kw):
        log.kb.append(kw)
        return log.kb_result

    async def ensured(repo, src, ids):
        log.ensured.append((src, ids))

    monkeypatch.setattr(module, "_clone_agent_rag_settings", settings)
    monkeypatch.setattr(module, "_clone_agent_kb_documents", kb)
    monkeypatch.setattr(module, "_ensure_source_kb_ids_intact", ensured)
    return log


def _source(**kw):
    base = dict(
        id=50, name="Агент", description=None, system_prompt="Ты полезный ассистент банка.",
        config={"kb_document_ids": [5, "6"], "k": {"n": 1}}, tools=["t"], tags=[{"id": 7}, {"id": 0}, "bad", {"x": 1}],
    )
    base.update(kw)
    return SimpleNamespace(**base)


async def _materialize(repo, source):
    return await module._materialize_single_agent_clone(
        agent_repo=repo, source=source, current_user={"user_id": "alice", "username": "alice"},
        owner_uid="alice", username="alice",
    )


async def test_materialize_copies_card_and_clears_then_restores_kb_ids(repo, clone_deps):
    source = _source()

    new_id = await _materialize(repo, source)

    assert new_id == 1  # первый свободный id выдаёт репозиторий
    clone = repo.agents[new_id]
    assert clone.name == "Агент (копия)" and clone.tools == ["t"] and clone.is_public is False
    assert clone.config["kb_document_ids"] == [99] and clone.config["cloned_from"] == 50
    assert source.config["kb_document_ids"] == [5, "6"]  # исходный dict не тронут
    assert clone_deps.kb[0]["source_doc_ids"] == [5, 6] and clone_deps.kb[0]["new_agent_id"] == new_id
    assert clone_deps.rag_settings == [(50, new_id, "alice")]
    assert clone_deps.ensured == [(50, [5, 6])]
    assert repo.calls[0] == ("create_agent",)


async def test_materialize_normalizes_short_texts(repo, clone_deps):
    repo.next_ids = [10, 11, 12]

    short = await _materialize(repo, _source(id=2, name="ab", system_prompt="коротко", description="д" * 1500, tools=None, tags=None))
    blank = await _materialize(repo, _source(id=3, name="  ", system_prompt="", description=None, config=None))
    empty_name = await _materialize(repo, _source(id=4, name=None, system_prompt=None))

    assert repo.agents[short].name == "Агент 2 (копия)"
    assert repo.agents[short].system_prompt == "коротко (копия агента)"
    assert len(repo.agents[short].description) == 1000 and repo.agents[short].tools == []
    assert repo.agents[blank].name == "Агент (копия)" and repo.agents[blank].system_prompt == "(копия агента)"
    assert repo.agents[blank].description is None
    assert repo.agents[empty_name].name == "Агент (копия)"


async def test_materialize_stops_when_repository_gives_no_id(repo, clone_deps):
    repo.next_ids = [None]

    assert await _materialize(repo, _source()) is None
    assert clone_deps.kb == [] and clone_deps.ensured == []


async def test_materialize_never_writes_into_the_source_card(repo, clone_deps):
    repo.next_ids = [1]  # репозиторий вернул id исходника

    assert await _materialize(repo, _source(id=1)) is None
    assert clone_deps.kb == []


# ------------------------------------------------------------- RAG-настройки клона


@pytest.fixture
def entity_repo(monkeypatch):
    import backend.database.init_db as init_db

    store = SimpleNamespace(data={}, upserts=[], fail=False)

    class Repo:
        async def get(self, kind, key):
            if store.fail:
                raise RuntimeError("pg down")
            return store.data.get((kind, key))

        async def upsert(self, kind, key, payload, by):
            store.upserts.append((kind, key, payload, by))

    store.repo = Repo()
    monkeypatch.setattr(init_db, "get_entity_settings_repository", lambda: store.repo)
    return store


async def test_rag_settings_are_copied_as_stored(entity_repo):
    entity_repo.data[("agent", "1")] = {"top_k": 3}

    await module._clone_agent_rag_settings(1, 2, "alice")

    assert entity_repo.upserts == [("agent", "2", {"top_k": 3}, "alice")]


async def test_rag_settings_nothing_to_copy_or_no_repository(entity_repo, monkeypatch):
    import backend.database.init_db as init_db

    await module._clone_agent_rag_settings(1, 2, "alice")  # у источника нет настроек
    entity_repo.data[("agent", "1")] = {}
    await module._clone_agent_rag_settings(1, 2, "alice")  # пустые тоже не копируются
    monkeypatch.setattr(init_db, "get_entity_settings_repository", lambda: None)
    await module._clone_agent_rag_settings(1, 2, "alice")

    assert entity_repo.upserts == []


async def test_rag_settings_errors_are_swallowed(entity_repo):
    entity_repo.fail = True

    await module._clone_agent_rag_settings(1, 2, "alice")

    assert entity_repo.upserts == []


# ----------------------------------------------------------- чтение исходников KB


@pytest.fixture
def pvc(monkeypatch, tmp_path):
    import backend.storage.rag_pvc as rag_pvc

    state = SimpleNamespace(path=None, use_pvc=False, saved=[], save_result="kb/new.bin")
    monkeypatch.setattr(rag_pvc, "rag_pvc_file_path", lambda obj: state.path)

    def save(raw, filename, **kw):
        state.saved.append((raw, filename, kw))
        return state.save_result

    monkeypatch.setattr(rag_pvc, "save_rag_bytes_to_pvc", save)
    monkeypatch.setattr(rag_pvc, "use_rag_pvc", lambda: state.use_pvc)
    state.tmp = tmp_path
    return state


@pytest.fixture
def minio(monkeypatch):
    import backend.app_state as app_state

    client = SimpleNamespace(downloads=[], uploads=[], raises=False, data=b"MINIO")

    def download(obj, bucket_name=None):
        client.downloads.append((obj, bucket_name))
        if client.raises:
            raise RuntimeError("minio down")
        return client.data

    def upload(raw, obj, bucket_name=None):
        if client.raises:
            raise RuntimeError("minio down")
        client.uploads.append((raw, obj, bucket_name))

    client.download_file = download
    client.upload_file = upload
    monkeypatch.setattr(app_state, "minio_client", client, raising=False)
    return client


def test_read_source_requires_object_and_bucket():
    assert module._read_rag_source_bytes(None, "b") is None
    assert module._read_rag_source_bytes("o", "") is None


def test_read_source_from_pvc(pvc):
    file = pvc.tmp / "doc.bin"
    file.write_bytes(b"PVC")
    pvc.path = str(file)

    assert module._read_rag_source_bytes("kb/doc.bin", "__pvc_rag__") == b"PVC"


def test_read_source_from_pvc_missing_file_or_read_error(pvc):
    pvc.path = None
    assert module._read_rag_source_bytes("kb/doc.bin", "__pvc_rag__") is None

    pvc.path = str(pvc.tmp / "нет-такого")
    assert module._read_rag_source_bytes("kb/doc.bin", "__pvc_rag__") is None


def test_read_source_from_minio(pvc, minio):
    assert module._read_rag_source_bytes("obj", "bucket") == b"MINIO"
    assert minio.downloads == [("obj", "bucket")]


def test_read_source_minio_unavailable_or_failing(pvc, minio, monkeypatch):
    minio.raises = True
    assert module._read_rag_source_bytes("obj", "bucket") is None

    import backend.app_state as app_state

    monkeypatch.setattr(app_state, "minio_client", None, raising=False)
    assert module._read_rag_source_bytes("obj", "bucket") is None


# ------------------------------------------------------ клонирование KB-документов


class CloneRag:
    def __init__(self):
        self.docs = {}
        self.clone_calls = []
        self.clone_results = {}
        self.get_raises = set()

    async def kb_get_document(self, doc_id):
        if doc_id in self.get_raises:
            raise RuntimeError("svc-rag down")
        return self.docs.get(doc_id)

    async def kb_clone_document(self, doc_id, **kw):
        self.clone_calls.append((doc_id, kw))
        return self.clone_results.get(doc_id, {"ok": True, "document_id": doc_id + 100})


@pytest.fixture
def clone_rag(monkeypatch):
    import backend.settings.rag_client as rag_client_module

    fake = CloneRag()
    monkeypatch.setattr(rag_client_module, "get_rag_client", lambda: fake)
    return fake


async def _clone_docs(ids):
    return await module._clone_agent_kb_documents(
        source_doc_ids=ids, new_agent_id=9, owner_user_id="alice", username="Alice"
    )


async def test_kb_clone_without_documents():
    assert await _clone_docs([]) == []


async def test_kb_clone_without_rag_service(monkeypatch):
    import backend.settings.rag_client as rag_client_module

    monkeypatch.setattr(rag_client_module, "get_rag_client", lambda: None)

    assert await _clone_docs([1]) == []


async def test_kb_clone_skips_unknown_documents_and_failed_clones(clone_rag, pvc, monkeypatch):
    monkeypatch.setattr(module, "_read_rag_source_bytes", lambda o, b: None)
    clone_rag.docs = {2: {"filename": "a.txt", "metadata": None}, 3: {}, 4: {"metadata": "x"}}
    clone_rag.clone_results = {3: {"ok": False}, 4: "oops"}
    clone_rag.get_raises = {5}

    result = await _clone_docs([1, 2, 3, 4, 5])

    assert result == [102]  # 1 не найден, 3 и 4 не склонированы, 5 упал
    first_call = clone_rag.clone_calls[0][1]
    assert first_call == {
        "agent_id": 9, "owner_user_id": "alice", "uploaded_by": "Alice", "minio_object": "", "minio_bucket": "",
    }


async def test_kb_clone_into_pvc(clone_rag, pvc, monkeypatch):
    pvc.use_pvc = True
    monkeypatch.setattr(module, "_read_rag_source_bytes", lambda o, b: b"RAW")
    clone_rag.docs = {1: {"filename": "doc.pdf", "metadata": {"minio_object": "old", "minio_bucket": "b"}}}

    assert await _clone_docs([1]) == [101]

    assert pvc.saved == [(b"RAW", "doc.pdf", {"scope": "agent", "username": "Alice", "prefix": "kb_"})]
    kwargs = clone_rag.clone_calls[0][1]
    assert kwargs["minio_object"] == "kb/new.bin" and kwargs["minio_bucket"] == "__pvc_rag__"


async def test_kb_clone_into_pvc_that_refuses_to_save(clone_rag, pvc, monkeypatch):
    pvc.use_pvc = True
    pvc.save_result = None
    monkeypatch.setattr(module, "_read_rag_source_bytes", lambda o, b: b"RAW")
    clone_rag.docs = {1: {"filename": "doc.pdf", "metadata": {"minio_object": "old", "minio_bucket": "b"}}}

    await _clone_docs([1])

    assert clone_rag.clone_calls[0][1]["minio_object"] == "" and clone_rag.clone_calls[0][1]["minio_bucket"] == ""


async def test_kb_clone_copies_object_in_minio(clone_rag, pvc, minio, monkeypatch):
    monkeypatch.setattr(module, "_read_rag_source_bytes", lambda o, b: b"RAW")
    clone_rag.docs = {
        1: {"filename": "report.v2.docx", "metadata": {"minio_object": "old", "minio_bucket": "files"}},
        2: {"filename": "noext", "metadata": {"minio_object": "old2", "minio_bucket": "files"}},
    }

    assert await _clone_docs([1, 2]) == [101, 102]

    (raw1, obj1, bucket1), (raw2, obj2, _) = minio.uploads
    assert raw1 == b"RAW" and bucket1 == "files"
    assert obj1.startswith("kb_clone/9/") and obj1.endswith(".docx")
    assert obj2.startswith("kb_clone/9/") and "." not in obj2.rsplit("/", 1)[1]
    assert clone_rag.clone_calls[0][1]["minio_object"] == obj1 and clone_rag.clone_calls[0][1]["minio_bucket"] == "files"


async def test_kb_clone_minio_failure_resets_paths(clone_rag, pvc, minio, monkeypatch):
    monkeypatch.setattr(module, "_read_rag_source_bytes", lambda o, b: b"RAW")
    minio.raises = True
    clone_rag.docs = {1: {"filename": "a.txt", "metadata": {"minio_object": "old", "minio_bucket": "files"}}}

    assert await _clone_docs([1]) == [101]

    assert clone_rag.clone_calls[0][1]["minio_object"] == "" and clone_rag.clone_calls[0][1]["minio_bucket"] == ""


async def test_kb_clone_without_minio_client_or_for_pvc_bucket(clone_rag, pvc, monkeypatch):
    import backend.app_state as app_state

    monkeypatch.setattr(module, "_read_rag_source_bytes", lambda o, b: b"RAW")
    monkeypatch.setattr(app_state, "minio_client", None, raising=False)
    clone_rag.docs = {
        1: {"filename": "a.txt", "metadata": {"minio_object": "o", "minio_bucket": "files"}},
        2: {"filename": "b.txt", "metadata": {"minio_object": "o", "minio_bucket": "__pvc_rag__"}},
    }

    assert await _clone_docs([1, 2]) == [101, 102]

    assert all(call[1]["minio_object"] == "" for call in clone_rag.clone_calls)


# ------------------------------------------------------------ хвосты чистых функций


def test_clone_config_with_circular_reference_falls_back_to_deepcopy():
    cyc = {"a": 1}
    cyc["self"] = cyc

    result = module._clone_agent_config(cyc)

    assert result["a"] == 1 and result["self"] is result and result is not cyc


def test_remap_keeps_subagents_without_names_and_skips_unmapped_names():
    config = {"subagents": {"agent_ids": [1]}}
    assert module._remap_config_agent_refs(config, {1: 5})["subagents"] == {"agent_ids": [5]}

    config = {"subagents": {"agent_ids": [1], "agent_names": {"1": "A", "2": "B", "z": "C"}}}
    assert module._remap_config_agent_refs(config, {1: 5})["subagents"]["agent_names"] == {"5": "A"}

    config = {"subagents": {"agent_ids": [1], "agent_names": ["не словарь"]}}
    assert module._remap_config_agent_refs(config, {1: 5})["subagents"]["agent_names"] == ["не словарь"]


async def test_clone_graph_of_single_agent_without_links(repo):
    repo.add(make_agent(1, config={}))

    assert await module._collect_clone_graph_ids(repo, 1) == [1]
