"""services/index_batching, stage_timer, index_failed_report: волны индексации, замеры, уведомление backend."""

import json
import logging

import httpx
import pytest
import respx

from app.services import index_batching as ib
from app.services import index_failed_report as ifr
from app.services.stage_timer import StageTimer

pytestmark = pytest.mark.unit

ROUTER = respx.MockRouter(assert_all_called=False, assert_all_mocked=True)


@pytest.fixture(autouse=True)
def _respx():
    with ROUTER:
        yield
    ROUTER.reset()


class FakeRepo:
    def __init__(self, fail_on_batch=None):
        self.batches = []
        self.deleted = []
        self.fail_on_batch = fail_on_batch
        self.delete_error = None

    async def create_vectors_batch(self, vectors):
        if self.fail_on_batch is not None and len(self.batches) == self.fail_on_batch:
            raise RuntimeError("db down")
        self.batches.append(list(vectors))
        return len(vectors)

    async def delete_vectors_by_document(self, document_id):
        if self.delete_error:
            raise self.delete_error
        self.deleted.append(document_id)


def make_embed(calls=None, wrong_count=False):
    async def embed(texts):
        if calls is not None:
            calls.append(list(texts))
        out = [[float(len(t)), 0.0] for t in texts]
        return out[:-1] if wrong_count else out

    return embed


def meta(idx, chunk_meta):
    return {"idx": idx, **chunk_meta}


def chunks(n):
    return [(f"chunk-{i}", {"p": i}) for i in range(n)]


class TestChunksPerBatch:
    def test_default(self, monkeypatch):
        monkeypatch.delenv("RAG_INDEX_CHUNKS_PER_BATCH", raising=False)
        assert ib.chunks_per_batch() == 256

    @pytest.mark.parametrize(
        "raw,expected", [("10", 10), ("0", 0), ("-5", -5), ("abc", 256), ("", 256), (" 7 ", 7)]
    )
    def test_values(self, monkeypatch, raw, expected):
        monkeypatch.setenv("RAG_INDEX_CHUNKS_PER_BATCH", raw)
        assert ib.chunks_per_batch() == expected


class TestEmbedAndStore:
    async def test_empty_input_does_nothing(self):
        repo = FakeRepo()
        result = await ib.embed_and_store(
            [], document_id=1, repo=repo, embed=make_embed(), make_metadata=meta
        )
        assert result == (0, []) and repo.batches == []

    async def test_waves_of_given_size_with_global_indexes(self, monkeypatch):
        monkeypatch.setenv("RAG_INDEX_CHUNKS_PER_BATCH", "2")
        repo, calls = FakeRepo(), []
        created, _ = await ib.embed_and_store(
            chunks(5), document_id=7, repo=repo, embed=make_embed(calls), make_metadata=meta
        )
        assert created == 5 and [len(b) for b in repo.batches] == [2, 2, 1]
        assert calls == [["chunk-0", "chunk-1"], ["chunk-2", "chunk-3"], ["chunk-4"]]
        flat = [v for b in repo.batches for v in b]
        assert [v.chunk_index for v in flat] == [0, 1, 2, 3, 4]
        assert flat[3].metadata == {"idx": 3, "p": 3} and flat[3].document_id == 7
        assert flat[0].embedding == [7.0, 0.0]

    async def test_stubs_carry_text_but_no_embedding(self, monkeypatch):
        monkeypatch.setenv("RAG_INDEX_CHUNKS_PER_BATCH", "2")
        _, stubs = await ib.embed_and_store(
            chunks(3), document_id=1, repo=FakeRepo(), embed=make_embed(), make_metadata=meta
        )
        assert [(s.chunk_index, s.content, s.embedding, s.metadata) for s in stubs] == [
            (0, "chunk-0", None, {}),
            (1, "chunk-1", None, {}),
            (2, "chunk-2", None, {}),
        ]

    @pytest.mark.parametrize("step", ["0", "-1", "1000"])
    async def test_non_positive_or_huge_step_means_single_batch(self, monkeypatch, step):
        monkeypatch.setenv("RAG_INDEX_CHUNKS_PER_BATCH", step)
        repo = FakeRepo()
        await ib.embed_and_store(
            chunks(4), document_id=1, repo=repo, embed=make_embed(), make_metadata=meta
        )
        assert [len(b) for b in repo.batches] == [4]

    async def test_failure_rolls_back_whole_document_and_reraises(self, monkeypatch):
        monkeypatch.setenv("RAG_INDEX_CHUNKS_PER_BATCH", "2")
        repo = FakeRepo(fail_on_batch=1)
        with pytest.raises(RuntimeError, match="db down"):
            await ib.embed_and_store(
                chunks(5), document_id=9, repo=repo, embed=make_embed(), make_metadata=meta
            )
        assert repo.deleted == [9]

    async def test_embedding_count_mismatch_is_an_error_with_rollback(self, monkeypatch):
        monkeypatch.setenv("RAG_INDEX_CHUNKS_PER_BATCH", "3")
        repo = FakeRepo()
        with pytest.raises(ValueError, match="эмбеддингов 2, а чанков 3"):
            await ib.embed_and_store(
                chunks(3),
                document_id=4,
                repo=repo,
                embed=make_embed(wrong_count=True),
                make_metadata=meta,
            )
        assert repo.deleted == [4] and repo.batches == []

    async def test_embed_error_propagates_and_rolls_back(self):
        async def broken(texts):
            raise ConnectionError("models down")

        repo = FakeRepo()
        with pytest.raises(ConnectionError):
            await ib.embed_and_store(
                chunks(2), document_id=3, repo=repo, embed=broken, make_metadata=meta
            )
        assert repo.deleted == [3]

    async def test_rollback_failure_does_not_mask_original_error(self):
        repo = FakeRepo(fail_on_batch=0)
        repo.delete_error = OSError("cleanup failed")
        with pytest.raises(RuntimeError, match="db down"):
            await ib.embed_and_store(
                chunks(2), document_id=3, repo=repo, embed=make_embed(), make_metadata=meta
            )

    async def test_timer_accumulates_embed_and_insert_stages(self, monkeypatch):
        monkeypatch.setenv("RAG_INDEX_CHUNKS_PER_BATCH", "1")
        timer = StageTimer("INDEX", store="kb")
        await ib.embed_and_store(
            chunks(3),
            document_id=1,
            repo=FakeRepo(),
            embed=make_embed(),
            make_metadata=meta,
            timer=timer,
        )
        assert [n for n, _ in timer.items()] == ["embed", "insert"] * 3
        assert set(timer.stages_ms()) == {"embed", "insert"}


class TestStageTimer:
    def test_records_stages_in_order(self):
        t = StageTimer("index", store="kb", file="a.pdf")
        with t.stage("parse"):
            pass
        with t.stage("embed"):
            pass
        assert [n for n, _ in t.items()] == ["parse", "embed"]
        assert all(sec >= 0 for _, sec in t.items())

    def test_blank_names_and_kind_get_defaults(self):
        t = StageTimer("  ")
        with t.stage("  "):
            pass
        t.mark(None, 1.5)
        assert t.kind == "OP"
        assert [n for n, _ in t.items()] == ["unknown", "unknown"]

    def test_stage_is_recorded_even_if_body_raises(self):
        t = StageTimer("x")
        with pytest.raises(ValueError):
            with t.stage("boom"):
                raise ValueError
        assert [n for n, _ in t.items()] == ["boom"]

    def test_nested_stage_closes_the_previous_one(self):
        t = StageTimer("x")
        with t.stage("outer"):
            with t.stage("inner"):
                pass
        assert [n for n, _ in t.items()] == ["outer", "inner"]

    def test_mark_clamps_negative_values(self):
        t = StageTimer("x")
        t.mark("ext", -3)
        t.mark("ext", 0.5)
        assert t.stages_ms() == {"ext": pytest.approx(500.0)}

    def test_same_named_stages_are_summed(self):
        t = StageTimer("x")
        t.mark("embed", 1.0)
        t.mark("embed", 2.0)
        assert t.stages_ms()["embed"] == pytest.approx(3000.0)

    def test_items_returns_a_copy(self):
        t = StageTimer("x")
        t.mark("a", 1)
        t.items().clear()
        assert len(t.items()) == 1

    def test_format_line(self):
        t = StageTimer("INDEX", store="kb", file="a.pdf", n=3, empty="", none=None)
        t.mark("parse", 1.2)
        t.mark("embed", 0.5)
        line = t.format_line()
        assert line.startswith("[RAG-TIMING INDEX kb] file='a.pdf' n=3 total=")
        assert "empty" not in line and "none" not in line
        assert line.endswith("| parse=1.200s | embed=0.500s")

    def test_format_line_without_stages_or_store(self):
        line = StageTimer("RETR").format_line()
        assert line.startswith("[RAG-TIMING RETR] total=") and line.endswith("(нет стадий)")

    def test_log_uses_level_and_swallows_logger_errors(self, caplog):
        t = StageTimer("x")
        with caplog.at_level(logging.INFO):
            t.log(logging.getLogger("timer-test"))
        assert any("RAG-TIMING x" in r.message for r in caplog.records)

        class Broken:
            def log(self, *a, **k):
                raise RuntimeError

        t.log(Broken())


class TestReportIndexFailed:
    URL = "http://backend.test/api/internal/rag/index-failed"

    @pytest.fixture(autouse=True)
    def _backend(self, monkeypatch):
        class Backend:
            base_url = "http://backend.test/"

        class Settings:
            backend = Backend()

        monkeypatch.setattr(ifr, "get_settings", lambda: Settings())
        self.backend = Settings.backend

    async def test_posts_payload_to_backend(self):
        route = ROUTER.post(self.URL).respond(200, json={})
        await ifr.report_index_failed(
            minio_object="o/key",
            minio_bucket="b",
            filename="a.pdf",
            reason="x" * 900,
            store="project",
        )
        assert route.called
        assert json.loads(route.calls.last.request.content) == {
            "minio_object": "o/key",
            "minio_bucket": "b",
            "filename": "a.pdf",
            "reason": "x" * 500,
            "store": "project",
        }

    @pytest.mark.parametrize("obj,bucket", [(None, "b"), ("o", None), ("", "b"), ("o", "")])
    async def test_nothing_to_report_without_minio_coordinates(self, obj, bucket):
        await ifr.report_index_failed(
            minio_object=obj, minio_bucket=bucket, filename="a", reason="r"
        )
        assert ROUTER.calls.call_count == 0

    async def test_missing_base_url_is_logged_not_raised(self, caplog):
        self.backend.base_url = ""
        with caplog.at_level(logging.WARNING):
            await ifr.report_index_failed(
                minio_object="o", minio_bucket="b", filename="a", reason="r"
            )
        assert ROUTER.calls.call_count == 0

    @pytest.mark.parametrize("status", [404, 500, 503])
    async def test_http_errors_are_swallowed(self, status):
        route = ROUTER.post(self.URL).respond(status)
        await ifr.report_index_failed(
            minio_object="o", minio_bucket="b", filename="a", reason="r"
        )
        assert route.called

    async def test_network_error_is_swallowed(self):
        ROUTER.post(self.URL).mock(side_effect=httpx.ConnectError("refused"))
        await ifr.report_index_failed(
            minio_object="o", minio_bucket="b", filename="a", reason="r"
        )

    async def test_request_sends_no_credentials_known_issue(self):
        """Известная особенность: уведомление уходит без какого-либо секрета —
        внутренний эндпоинт backend держится только на сетевой изоляции."""
        route = ROUTER.post(self.URL).respond(200)
        await ifr.report_index_failed(
            minio_object="o", minio_bucket="b", filename="a", reason="r"
        )
        headers = {k.lower() for k in route.calls.last.request.headers}
        assert "authorization" not in headers and "x-api-key" not in headers

    @pytest.mark.parametrize("raw,expected", [("30", 30.0), ("1", 3.0), ("junk", 15.0)])
    def test_timeout_has_floor_and_fallback(self, monkeypatch, raw, expected):
        monkeypatch.setenv("RAG_INDEX_FAILED_REPORT_TIMEOUT", raw)
        t = ifr._report_timeout()
        assert t.read == expected and t.connect == 5.0
