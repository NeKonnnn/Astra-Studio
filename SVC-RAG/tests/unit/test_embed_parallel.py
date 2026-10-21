"""clients/embed_parallel: порядок векторов при параллельных батчах, лимит параллелизма, дробление батча по таймауту."""

import asyncio

import httpx
import pytest

from app.clients import embed_parallel as ep

pytestmark = pytest.mark.unit


def vec(text):
    """Детерминированный «эмбеддинг»: по нему видно, какому тексту принадлежит вектор."""
    return [float(len(text)), float(sum(map(ord, text)) % 997)]


def texts(n):
    return [f"текст-{i}-" + "x" * (i % 5) for i in range(n)]


class Recorder:
    def __init__(self, delay=None, fail_on=None, wrong_len_on=None):
        self.calls = []
        self.active = 0
        self.peak = 0
        self.delay, self.fail_on, self.wrong_len_on = delay or (lambda start: 0), fail_on, wrong_len_on

    async def __call__(self, start, batch):
        self.calls.append((start, list(batch)))
        self.active += 1
        self.peak = max(self.peak, self.active)
        try:
            await asyncio.sleep(self.delay(start))
            if self.fail_on and self.fail_on(start, batch):
                raise self.fail_on.error
            out = [vec(t) for t in batch]
            return out[:-1] if self.wrong_len_on == start else out
        finally:
            self.active -= 1


class TestOrdering:
    async def test_empty(self):
        assert await ep.embed_texts_in_batches([], batch_size=4, concurrency=2, embed_batch=Recorder()) == []

    async def test_sequential_mode_preserves_order(self):
        data = texts(10)
        rec = Recorder()

        out = await ep.embed_texts_in_batches(data, batch_size=3, concurrency=1, embed_batch=rec)

        assert out == [vec(t) for t in data] and [s for s, _ in rec.calls] == [0, 3, 6, 9] and rec.peak == 1

    async def test_parallel_mode_preserves_order_even_when_batches_finish_in_reverse(self):
        data = texts(12)
        rec = Recorder(delay=lambda start: 0.05 - start * 0.01)

        out = await ep.embed_texts_in_batches(data, batch_size=3, concurrency=4, embed_batch=rec)

        assert out == [vec(t) for t in data] and rec.peak == 4

    async def test_parallelism_is_bounded(self):
        rec = Recorder(delay=lambda start: 0.01)

        await ep.embed_texts_in_batches(texts(20), batch_size=2, concurrency=3, embed_batch=rec)

        assert rec.peak == 3 and len(rec.calls) == 10

    async def test_single_batch_runs_directly(self):
        rec = Recorder()

        out = await ep.embed_texts_in_batches(texts(3), batch_size=10, concurrency=8, embed_batch=rec)

        assert len(out) == 3 and len(rec.calls) == 1 and rec.calls[0][0] == 0

    @pytest.mark.parametrize(("batch_size", "concurrency"), [(0, 0), (None, None), (-5, -1)])
    async def test_bad_limits_are_normalised_to_one(self, batch_size, concurrency):
        rec = Recorder()

        out = await ep.embed_texts_in_batches(texts(3), batch_size=batch_size, concurrency=concurrency, embed_batch=rec)

        assert len(out) == 3 and len(rec.calls) == 3 and rec.peak == 1

    async def test_batches_cover_the_texts_exactly_once(self):
        data = texts(11)
        rec = Recorder()

        await ep.embed_texts_in_batches(data, batch_size=4, concurrency=3, embed_batch=rec)

        assert sorted(rec.calls) == [(0, data[0:4]), (4, data[4:8]), (8, data[8:11])]


class TestFailures:
    @pytest.mark.parametrize("concurrency", [1, 4])
    async def test_a_wrong_vector_count_is_an_error(self, concurrency):
        rec = Recorder(wrong_len_on=3)

        with pytest.raises(ValueError, match="эмбеддингов"):
            await ep.embed_texts_in_batches(texts(9), batch_size=3, concurrency=concurrency, embed_batch=rec)

    @pytest.mark.parametrize("concurrency", [1, 4])
    async def test_a_failing_batch_fails_the_whole_call(self, concurrency):
        def fail(start, batch):
            return start == 3

        fail.error = ValueError("bad request")
        rec = Recorder(fail_on=fail)

        with pytest.raises(ValueError, match="bad request"):
            await ep.embed_texts_in_batches(texts(9), batch_size=3, concurrency=concurrency, embed_batch=rec)


class TestTimeoutDetection:
    @staticmethod
    def status_error(code):
        response = httpx.Response(code, request=httpx.Request("POST", "http://x"))
        return httpx.HTTPStatusError("boom", request=response.request, response=response)

    @pytest.mark.parametrize(
        "error",
        [
            ep.EmbedBatchTimeout("slow"), httpx.ReadTimeout("slow"), httpx.ConnectTimeout("slow"), httpx.PoolTimeout("slow"),
            RuntimeError("HTTP 504: Gateway Time-out"), RuntimeError("upstream: gateway timeout"), RuntimeError("got http 524 from proxy"), RuntimeError("HTTP 408"),
        ],
    )
    def test_timeouts(self, error):
        assert ep.is_timeout_error(error) is True

    @pytest.mark.parametrize("code", [408, 504, 524, 598, 599])
    def test_gateway_statuses(self, code):
        assert ep.is_timeout_error(self.status_error(code)) is True

    @pytest.mark.parametrize("error", [ValueError("bad"), RuntimeError("HTTP 500"), RuntimeError("HTTP 400: bad request")])
    def test_not_timeouts(self, error):
        assert ep.is_timeout_error(error) is False

    @pytest.mark.parametrize("code", [400, 401, 404, 429, 500, 502, 503])
    def test_other_statuses_are_not_timeouts(self, code):
        assert ep.is_timeout_error(self.status_error(code)) is False


class TimeoutAbove:
    """Шлюз: батчи крупнее limit не успевают (504), меньшие считаются."""

    def __init__(self, limit, error=None):
        self.limit, self.calls, self.error = limit, [], error or RuntimeError("HTTP 504: Gateway Time-out")

    async def __call__(self, start, batch):
        self.calls.append((start, len(batch)))
        if len(batch) > self.limit:
            raise self.error
        return [vec(t) for t in batch]


class TestSplitOnTimeout:
    async def test_a_timed_out_batch_is_halved_until_it_fits_and_order_is_kept(self):
        data = texts(8)
        gate = TimeoutAbove(2)

        out = await ep.embed_texts_in_batches(data, batch_size=8, concurrency=1, embed_batch=gate)

        assert out == [vec(t) for t in data]
        assert gate.calls[0] == (0, 8) and max(size for _, size in gate.calls[1:]) <= 4

    async def test_learned_size_is_reused_without_probing_again(self):
        data = texts(16)
        gate = TimeoutAbove(2)

        out = await ep.embed_texts_in_batches(data, batch_size=8, concurrency=1, embed_batch=gate)

        assert out == [vec(t) for t in data]
        oversized = [c for c in gate.calls if c[1] > 2]
        assert len(oversized) <= 3  # первый батч разведал потолок, второй режется по выученному размеру без новых 504

    async def test_shrink_callback_reports_the_failing_size(self):
        seen = []

        await ep.embed_texts_in_batches(texts(8), batch_size=8, concurrency=1, embed_batch=TimeoutAbove(2), on_batch_shrink=seen.append)

        assert seen and seen[0] == 8

    async def test_a_failing_shrink_callback_is_ignored(self):
        def bad(size):
            raise RuntimeError("callback bug")

        out = await ep.embed_texts_in_batches(texts(4), batch_size=4, concurrency=1, embed_batch=TimeoutAbove(2), on_batch_shrink=bad)

        assert len(out) == 4

    async def test_parallel_splitting_keeps_order_and_parallelism_limit(self):
        data = texts(16)
        gate = TimeoutAbove(2)

        out = await ep.embed_texts_in_batches(data, batch_size=8, concurrency=2, embed_batch=gate)

        assert out == [vec(t) for t in data]

    async def test_a_single_text_that_times_out_is_an_error(self):
        with pytest.raises(RuntimeError, match="504"):
            await ep.embed_texts_in_batches(["один"], batch_size=1, concurrency=1, embed_batch=TimeoutAbove(0))

    async def test_non_timeout_errors_are_not_retried(self):
        gate = TimeoutAbove(0, error=ValueError("HTTP 400: bad input"))

        with pytest.raises(ValueError):
            await ep.embed_texts_in_batches(texts(8), batch_size=8, concurrency=1, embed_batch=gate)

        assert len(gate.calls) == 1

    async def test_the_old_behaviour_can_be_restored(self, monkeypatch):
        monkeypatch.setattr(ep, "_SPLIT_ON_TIMEOUT", False)
        gate = TimeoutAbove(2)

        with pytest.raises(RuntimeError):
            await ep.embed_texts_in_batches(texts(8), batch_size=8, concurrency=1, embed_batch=gate)

        assert len(gate.calls) == 1


class TestFingerprints:
    def test_text_fingerprint(self):
        assert ep._text_fp("a") == ep._text_fp("a") != ep._text_fp("b") and len(ep._text_fp("a")) == 10 and len(ep._text_fp(None)) == 10

    def test_embedding_fingerprint(self):
        assert ep._emb_fp([]) == "empty" and ep._emb_fp(None) == "empty"
        assert ep._emb_fp([0.123456, 1, 2, 3, 4, 5]) == "d6:0.12346,1.00000,2.00000,3.00000"
        assert ep._emb_fp([1, 2]) != ep._emb_fp([2, 1])
