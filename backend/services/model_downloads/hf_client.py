"""Обёртки huggingface_hub: быстрый параллельный стриминг + прогресс/отмена.

Chrome качает на хосте несколькими соединениями. В Docker на Windows один поток
+ запись сразу на bind-mount к NTFS даёт ~1 МБ/с. Здесь:
  1) параллельные HTTP Range (как браузер);
  2) сначала пишем во временный диск контейнера (/tmp), потом один copy на том.
"""

from __future__ import annotations

import os
import shutil
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Callable, List, Optional, Tuple

import httpx

ProgressCb = Callable[[float, int, int, str], None]
CancelCb = Callable[[], bool]

# Сколько параллельных Range-запросов (Chrome часто держит несколько соединений)
_DEFAULT_CONNECTIONS = int(os.getenv("MODEL_DOWNLOAD_CONNECTIONS", "8"))
_MIN_PARALLEL_SIZE = 8 * 1024 * 1024  # <8MB — одним потоком
_CHUNK = 1024 * 1024  # 1 MiB


class DownloadCancelled(Exception):
    """Загрузка отменена пользователем."""


def list_repo_files(repo_id: str, token: Optional[str] = None, revision: Optional[str] = None) -> List[str]:
    from huggingface_hub import list_repo_files as _list

    files = _list(repo_id=repo_id, revision=revision, token=token)
    return list(files)


def _hf_headers(token: Optional[str]) -> dict:
    try:
        from huggingface_hub.utils import build_hf_headers

        return dict(build_hf_headers(token=token))
    except Exception:
        headers = {"User-Agent": "astrachat-model-downloads"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return headers


def _fmt_bytes(n: int) -> str:
    if n <= 0:
        return "0 B"
    units = ["B", "KB", "MB", "GB", "TB"]
    v = float(n)
    i = 0
    while v >= 1024 and i < len(units) - 1:
        v /= 1024
        i += 1
    return f"{v:.0f} {units[i]}" if i == 0 else f"{v:.2f} {units[i]}"


def _fmt_speed(bps: float) -> str:
    return f"{_fmt_bytes(int(bps))}/s"


def _fmt_eta(seconds: float) -> str:
    s = max(0, int(seconds))
    if s < 60:
        return f"{s}s"
    m, s = divmod(s, 60)
    if m < 60:
        return f"{m}m {s:02d}s"
    h, m = divmod(m, 60)
    return f"{h}h {m:02d}m"


def _progress_msg(label: str, done: int, total: int, speed: float) -> str:
    eta = ""
    if speed > 0 and total > done:
        eta = f" · ~{_fmt_eta((total - done) / speed)}"
    tot = _fmt_bytes(total) if total else "?"
    return f"{label} · {_fmt_bytes(done)}/{tot} · {_fmt_speed(speed)}{eta}"


def _local_tmp_path(suffix: str) -> Path:
    """Временный файл на диске контейнера (не bind-mount Windows)."""
    base = Path(os.getenv("MODEL_DOWNLOAD_TMP", "/tmp/astra-model-dl"))
    base.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix="dl-", suffix=suffix, dir=str(base))
    os.close(fd)
    return Path(name)


def _probe(url: str, headers: dict, timeout: float = 60.0) -> Tuple[int, bool, str]:
    """Вернуть (content_length, accept_ranges, final_url)."""
    with httpx.Client(follow_redirects=True, timeout=timeout) as client:
        try:
            r = client.head(url, headers=headers)
            if r.status_code < 400:
                total = int(r.headers.get("content-length") or 0)
                accepts = "bytes" in (r.headers.get("accept-ranges") or "").lower()
                return total, accepts, str(r.url)
        except Exception:
            pass
        # fallback: Range probe
        h = {**headers, "Range": "bytes=0-0"}
        r = client.get(url, headers=h)
        r.raise_for_status()
        cr = r.headers.get("content-range") or ""
        total = 0
        if "/" in cr:
            try:
                total = int(cr.rsplit("/", 1)[-1])
            except ValueError:
                total = int(r.headers.get("content-length") or 0)
        accepts = r.status_code == 206 or "bytes" in (r.headers.get("accept-ranges") or "").lower()
        return total, accepts, str(r.url)


def _stream_single(
    *,
    url: str,
    tmp_path: Path,
    headers: dict,
    progress: Optional[ProgressCb],
    is_cancelled: Optional[CancelCb],
    label: str,
    bytes_offset: int,
    bytes_grand_total: int,
    timeout: float,
) -> int:
    started = time.monotonic()
    last_report = 0.0
    done_file = 0
    with httpx.stream(
        "GET",
        url,
        headers=headers,
        follow_redirects=True,
        timeout=timeout,
    ) as resp:
        resp.raise_for_status()
        file_total = int(resp.headers.get("content-length") or 0)
        grand_total = bytes_grand_total or (bytes_offset + file_total)
        with open(tmp_path, "wb") as f:
            for chunk in resp.iter_bytes(chunk_size=_CHUNK):
                if is_cancelled and is_cancelled():
                    raise DownloadCancelled()
                if not chunk:
                    continue
                f.write(chunk)
                done_file += len(chunk)
                now = time.monotonic()
                if progress and (now - last_report >= 0.25):
                    last_report = now
                    done = bytes_offset + done_file
                    total = grand_total or file_total
                    pct = (done / total * 100.0) if total else 0.0
                    speed = done_file / max(now - started, 1e-3)
                    progress(pct, done, total, _progress_msg(label, done, total, speed))
    return done_file


def _stream_parallel(
    *,
    url: str,
    tmp_path: Path,
    headers: dict,
    total: int,
    progress: Optional[ProgressCb],
    is_cancelled: Optional[CancelCb],
    label: str,
    bytes_offset: int,
    bytes_grand_total: int,
    connections: int,
    timeout: float,
) -> int:
    """Параллельная загрузка по Range — ближе к поведению Chrome."""
    n = max(2, min(connections, 16))
    part = (total + n - 1) // n
    ranges: List[Tuple[int, int]] = []
    for i in range(n):
        start = i * part
        end = min(total - 1, (i + 1) * part - 1)
        if start <= end:
            ranges.append((start, end))

    # preallocate
    with open(tmp_path, "wb") as f:
        f.truncate(total)

    lock = threading.Lock()
    done_file = 0
    started = time.monotonic()
    last_report = 0.0
    errors: List[BaseException] = []

    def worker(start: int, end: int) -> None:
        nonlocal done_file, last_report
        h = {**headers, "Range": f"bytes={start}-{end}"}
        try:
            with httpx.Client(follow_redirects=True, timeout=timeout) as client:
                with client.stream("GET", url, headers=h) as resp:
                    if resp.status_code not in (200, 206):
                        resp.raise_for_status()
                    with open(tmp_path, "r+b") as f:
                        f.seek(start)
                        for chunk in resp.iter_bytes(chunk_size=_CHUNK):
                            if is_cancelled and is_cancelled():
                                raise DownloadCancelled()
                            if not chunk:
                                continue
                            f.write(chunk)
                            with lock:
                                done_file += len(chunk)
                                now = time.monotonic()
                                if progress and (now - last_report >= 0.25):
                                    last_report = now
                                    done = bytes_offset + done_file
                                    gtotal = bytes_grand_total or (bytes_offset + total)
                                    pct = (done / gtotal * 100.0) if gtotal else 0.0
                                    speed = done_file / max(now - started, 1e-3)
                                    progress(
                                        pct,
                                        done,
                                        gtotal,
                                        _progress_msg(label, done, gtotal, speed),
                                    )
        except DownloadCancelled:
            raise
        except BaseException as exc:
            errors.append(exc)
            raise

    with ThreadPoolExecutor(max_workers=len(ranges)) as pool:
        futs = [pool.submit(worker, s, e) for s, e in ranges]
        try:
            for fut in as_completed(futs):
                if is_cancelled and is_cancelled():
                    for f in futs:
                        f.cancel()
                    raise DownloadCancelled()
                fut.result()
        except DownloadCancelled:
            for f in futs:
                f.cancel()
            raise

    if errors:
        raise errors[0]
    return done_file


def _copy_with_progress(
    *,
    src: Path,
    dest: Path,
    progress: Optional[ProgressCb],
    is_cancelled: Optional[CancelCb],
    label: str,
) -> None:
    """Копирование чанками с прогрессом (bind-mount Windows часто медленный).

    Пишем сразу в финальное имя ``dest``, чтобы в проводнике был виден растущий
    размер (а не скрытый ``*.copying`` рядом со stub на 128 КБ).
    """
    total = src.stat().st_size
    dest.parent.mkdir(parents=True, exist_ok=True)
    # убрать старый stub / недокачанный файл и хвост от прошлой схемы
    for p in (dest, Path(str(dest) + ".copying"), dest.with_suffix(dest.suffix + ".copying")):
        try:
            if p.exists() and p.is_file():
                p.unlink()
        except OSError:
            pass

    started = time.monotonic()
    last_report = 0.0
    copied = 0
    if progress:
        progress(
            0.0,
            0,
            total,
            f"Копирование на том · 0/{_fmt_bytes(total)} · {label}",
        )

    try:
        with open(src, "rb") as rf, open(dest, "wb") as wf:
            while True:
                if is_cancelled and is_cancelled():
                    raise DownloadCancelled()
                chunk = rf.read(_CHUNK)
                if not chunk:
                    break
                wf.write(chunk)
                # периодический flush — иначе Explorer долго показывает 0 КБ
                if copied == 0 or (copied // (32 * _CHUNK)) != ((copied + len(chunk)) // (32 * _CHUNK)):
                    wf.flush()
                    try:
                        os.fsync(wf.fileno())
                    except OSError:
                        pass
                copied += len(chunk)
                now = time.monotonic()
                if progress and (now - last_report >= 0.25 or copied >= total):
                    last_report = now
                    speed = copied / max(now - started, 1e-3)
                    pct = (copied / total * 100.0) if total else 100.0
                    progress(
                        pct,
                        copied,
                        total,
                        _progress_msg("Копирование на том", copied, total, speed),
                    )
            wf.flush()
            try:
                os.fsync(wf.fileno())
            except OSError:
                pass
    except DownloadCancelled:
        dest.unlink(missing_ok=True)
        raise
    except Exception:
        dest.unlink(missing_ok=True)
        raise


def _finalize_to_dest(
    tmp_path: Path,
    dest_path: Path,
    progress: Optional[ProgressCb],
    label: str,
    is_cancelled: Optional[CancelCb] = None,
) -> Path:
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    if is_cancelled and is_cancelled():
        raise DownloadCancelled()

    # Быстрый путь: тот же filesystem → atomic rename (мгновенно)
    try:
        if dest_path.exists():
            dest_path.unlink()
        os.rename(str(tmp_path), str(dest_path))
        if progress:
            size = dest_path.stat().st_size
            progress(100.0, size, size, f"{label} · готово (перенос)")
        return dest_path
    except OSError:
        pass

    # Разные диски (типично /tmp контейнера → Windows bind-mount): копируем с прогрессом
    _copy_with_progress(
        src=tmp_path,
        dest=dest_path,
        progress=progress,
        is_cancelled=is_cancelled,
        label=label,
    )
    tmp_path.unlink(missing_ok=True)
    if progress:
        size = dest_path.stat().st_size
        progress(100.0, size, size, f"{label} · копирование завершено")
    return dest_path


def _stream_to_file(
    *,
    url: str,
    dest_path: Path,
    headers: Optional[dict] = None,
    progress: Optional[ProgressCb] = None,
    is_cancelled: Optional[CancelCb] = None,
    progress_label: Optional[str] = None,
    bytes_offset: int = 0,
    bytes_grand_total: int = 0,
    timeout: float = 3600.0,
    connections: Optional[int] = None,
) -> Path:
    if is_cancelled and is_cancelled():
        raise DownloadCancelled()

    hdrs = dict(headers or {})
    label = progress_label or dest_path.name
    conn = connections if connections is not None else _DEFAULT_CONNECTIONS

    total, accepts, final_url = _probe(url, hdrs, timeout=min(60.0, timeout))
    tmp_path = _local_tmp_path(suffix=dest_path.suffix or ".bin")
    try:
        if accepts and total >= _MIN_PARALLEL_SIZE and conn > 1:
            if progress:
                progress(
                    0.0,
                    bytes_offset,
                    bytes_grand_total or (bytes_offset + total),
                    f"{label} · parallel x{conn} · {_fmt_bytes(total)}",
                )
            _stream_parallel(
                url=final_url or url,
                tmp_path=tmp_path,
                headers=hdrs,
                total=total,
                progress=progress,
                is_cancelled=is_cancelled,
                label=label,
                bytes_offset=bytes_offset,
                bytes_grand_total=bytes_grand_total,
                connections=conn,
                timeout=timeout,
            )
        else:
            _stream_single(
                url=final_url or url,
                tmp_path=tmp_path,
                headers=hdrs,
                progress=progress,
                is_cancelled=is_cancelled,
                label=label,
                bytes_offset=bytes_offset,
                bytes_grand_total=bytes_grand_total or (bytes_offset + total),
                timeout=timeout,
            )

        if is_cancelled and is_cancelled():
            raise DownloadCancelled()

        _finalize_to_dest(
            tmp_path,
            dest_path,
            progress,
            label,
            is_cancelled=is_cancelled,
        )
        size = dest_path.stat().st_size if dest_path.is_file() else 0
        done = bytes_offset + size
        gtotal = bytes_grand_total or done
        if progress:
            progress(100.0, done, gtotal, f"{label} done")
        return dest_path
    except DownloadCancelled:
        tmp_path.unlink(missing_ok=True)
        raise
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise


def download_file(
    *,
    repo_id: str,
    filename: str,
    dest_path: Path,
    token: Optional[str] = None,
    revision: Optional[str] = None,
    progress: Optional[ProgressCb] = None,
    is_cancelled: Optional[CancelCb] = None,
) -> Path:
    from huggingface_hub import hf_hub_url

    if is_cancelled and is_cancelled():
        raise DownloadCancelled()

    # Опционально: официальный ускоритель HF (ещё быстрее на Linux/GPU-серверах)
    if os.getenv("HF_HUB_ENABLE_HF_TRANSFER", "").lower() in {"1", "true", "yes"}:
        try:
            return _download_via_hf_transfer(
                repo_id=repo_id,
                filename=filename,
                dest_path=dest_path,
                token=token,
                revision=revision,
                progress=progress,
                is_cancelled=is_cancelled,
            )
        except DownloadCancelled:
            raise
        except Exception:
            # fallback на parallel range
            pass

    url = hf_hub_url(repo_id=repo_id, filename=filename, revision=revision or "main")
    return _stream_to_file(
        url=url,
        dest_path=dest_path,
        headers=_hf_headers(token),
        progress=progress,
        is_cancelled=is_cancelled,
        progress_label=Path(filename).name,
        timeout=3600.0,
    )


def _download_via_hf_transfer(
    *,
    repo_id: str,
    filename: str,
    dest_path: Path,
    token: Optional[str],
    revision: Optional[str],
    progress: Optional[ProgressCb],
    is_cancelled: Optional[CancelCb],
) -> Path:
    """hf_transfer — multipart на стороне HF; прогресс через poll размера + известный total."""
    import threading

    from huggingface_hub import hf_hub_download, hf_hub_url

    label = Path(filename).name
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    headers = _hf_headers(token)
    url = hf_hub_url(repo_id=repo_id, filename=filename, revision=revision or "main")
    total = 0
    try:
        total, _, _ = _probe(url, headers)
    except Exception:
        total = 0

    if progress:
        progress(
            0.0,
            0,
            total,
            f"{label} · 0/{_fmt_bytes(total) if total else '?'} · hf_transfer",
        )

    tmp_dir = _local_tmp_path(suffix="-dir")
    tmp_dir.unlink(missing_ok=True)
    tmp_dir.mkdir(parents=True, exist_ok=True)

    stop = threading.Event()
    err: List[BaseException] = []
    started = time.monotonic()

    def _poll() -> None:
        while not stop.wait(0.35):
            if is_cancelled and is_cancelled():
                return
            if not progress:
                continue
            size = sum(f.stat().st_size for f in tmp_dir.rglob("*") if f.is_file())
            # hf_transfer может писать несколько временных кусков — не раздуваем > total
            done = min(size, total) if total > 0 else size
            pct = (done / total * 100.0) if total > 0 else 0.0
            speed = done / max(time.monotonic() - started, 1e-3)
            progress(pct, done, total, _progress_msg(f"{label} · hf_transfer", done, total, speed))

    def _run() -> None:
        try:
            hf_hub_download(
                repo_id=repo_id,
                filename=filename,
                revision=revision,
                token=token,
                local_dir=str(tmp_dir),
            )
        except BaseException as exc:
            err.append(exc)

    poller = threading.Thread(target=_poll, daemon=True)
    worker = threading.Thread(target=_run, daemon=True)
    poller.start()
    worker.start()
    while worker.is_alive():
        if is_cancelled and is_cancelled():
            stop.set()
            worker.join(timeout=2.0)
            shutil.rmtree(tmp_dir, ignore_errors=True)
            raise DownloadCancelled()
        worker.join(timeout=0.35)
    stop.set()
    poller.join(timeout=2.0)
    if err:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        raise err[0]

    candidate = tmp_dir / Path(filename).name
    if not candidate.is_file():
        matches = list(tmp_dir.rglob(Path(filename).name))
        if not matches:
            shutil.rmtree(tmp_dir, ignore_errors=True)
            raise FileNotFoundError(filename)
        candidate = matches[0]
    _finalize_to_dest(
        candidate,
        dest_path,
        progress,
        label,
        is_cancelled=is_cancelled,
    )
    shutil.rmtree(tmp_dir, ignore_errors=True)
    if progress:
        size = dest_path.stat().st_size
        final_total = total or size
        progress(100.0, size, final_total, f"{label} done")
    return dest_path


def download_snapshot(
    *,
    repo_id: str,
    dest_dir: Path,
    token: Optional[str] = None,
    revision: Optional[str] = None,
    allow_patterns: Optional[List[str]] = None,
    progress: Optional[ProgressCb] = None,
    is_cancelled: Optional[CancelCb] = None,
    use_hf_cache_layout: bool = False,
) -> Path:
    if use_hf_cache_layout:
        return _snapshot_cache_layout(
            repo_id=repo_id,
            dest_dir=dest_dir,
            token=token,
            revision=revision,
            allow_patterns=allow_patterns,
            progress=progress,
            is_cancelled=is_cancelled,
        )
    return _snapshot_file_by_file(
        repo_id=repo_id,
        dest_dir=dest_dir,
        token=token,
        revision=revision,
        allow_patterns=allow_patterns,
        progress=progress,
        is_cancelled=is_cancelled,
    )


def _dir_size(path: Path) -> int:
    total = 0
    if not path.exists():
        return 0
    for f in path.rglob("*"):
        try:
            if f.is_file():
                total += f.stat().st_size
        except OSError:
            continue
    return total


def _snapshot_cache_layout(
    *,
    repo_id: str,
    dest_dir: Path,
    token: Optional[str],
    revision: Optional[str],
    allow_patterns: Optional[List[str]],
    progress: Optional[ProgressCb],
    is_cancelled: Optional[CancelCb],
) -> Path:
    import threading

    from huggingface_hub import snapshot_download

    if is_cancelled and is_cancelled():
        raise DownloadCancelled()
    dest_dir.mkdir(parents=True, exist_ok=True)
    stop = threading.Event()
    baseline = _dir_size(dest_dir)

    def _poll() -> None:
        while not stop.wait(0.5):
            if is_cancelled and is_cancelled():
                return
            if not progress:
                continue
            cur = max(0, _dir_size(dest_dir) - baseline)
            progress(0.0, cur, 0, f"{repo_id} · {_fmt_bytes(cur)} downloaded…")

    poller = threading.Thread(target=_poll, name="hf-snap-poll", daemon=True)
    poller.start()
    try:
        kwargs: dict = {
            "repo_id": repo_id,
            "revision": revision,
            "token": token,
            "cache_dir": str(dest_dir),
            "allow_patterns": allow_patterns,
        }
        try:
            path = snapshot_download(**kwargs)
        except TypeError:
            kwargs.pop("allow_patterns", None)
            path = snapshot_download(**kwargs)
    finally:
        stop.set()
        poller.join(timeout=2.0)

    if is_cancelled and is_cancelled():
        raise DownloadCancelled()
    done = max(0, _dir_size(dest_dir) - baseline)
    if progress:
        progress(100.0, done, done, f"snapshot done → {path}")
    return Path(path)


def _snapshot_file_by_file(
    *,
    repo_id: str,
    dest_dir: Path,
    token: Optional[str],
    revision: Optional[str],
    allow_patterns: Optional[List[str]],
    progress: Optional[ProgressCb],
    is_cancelled: Optional[CancelCb],
) -> Path:
    from huggingface_hub import hf_hub_url

    if is_cancelled and is_cancelled():
        raise DownloadCancelled()

    dest_dir.mkdir(parents=True, exist_ok=True)
    if progress:
        progress(1.0, 0, 0, f"listing {repo_id}")

    files = list_repo_files(repo_id, token=token, revision=revision)
    if allow_patterns:
        try:
            from huggingface_hub.utils import filter_repo_objects

            files = list(filter_repo_objects(items=files, allow_patterns=allow_patterns))
        except Exception:
            pass
    files = [f for f in files if not f.startswith(".") and f != ".gitattributes"]
    if not files:
        raise ValueError(f"В репозитории нет файлов: {repo_id}")

    headers = _hf_headers(token)
    sizes: List[int] = []
    for name in files:
        if is_cancelled and is_cancelled():
            raise DownloadCancelled()
        url = hf_hub_url(repo_id=repo_id, filename=name, revision=revision or "main")
        try:
            total, _, _ = _probe(url, headers)
            sizes.append(total)
        except Exception:
            sizes.append(0)

    grand_total = sum(sizes)
    offset = 0
    for idx, name in enumerate(files):
        if is_cancelled and is_cancelled():
            raise DownloadCancelled()
        url = hf_hub_url(repo_id=repo_id, filename=name, revision=revision or "main")
        target = dest_dir / name
        target.parent.mkdir(parents=True, exist_ok=True)
        file_total = sizes[idx] if idx < len(sizes) else 0
        _stream_to_file(
            url=url,
            dest_path=target,
            headers=headers,
            progress=progress,
            is_cancelled=is_cancelled,
            progress_label=f"[{idx + 1}/{len(files)}] {name}",
            bytes_offset=offset,
            bytes_grand_total=grand_total or 0,
            timeout=3600.0,
        )
        offset += file_total or (target.stat().st_size if target.is_file() else 0)

    if progress:
        progress(100.0, offset, grand_total or offset, f"snapshot done → {dest_dir}")
    return dest_dir
