"""Помощники тестов CEF: разбор строки и фабрика starlette Request."""

import re
from typing import Dict, Optional, Tuple

from starlette.requests import Request


def parse_cef(line: str) -> Tuple[Dict[str, str], Dict[str, str]]:
    r"""'CEF:0|v|p|ver|id|name|sev|k=v k2=v2' -> (заголовок, расширение).

    Разделитель пар - пробел перед `ключ=`; экранированное `\=` ключом не считается.
    """
    parts = line.split("|", 7)
    assert parts[0] == "CEF:0", line
    header = dict(zip(["vendor", "product", "version", "event_id", "name", "severity"], parts[1:7]))
    ext: Dict[str, str] = {}
    for chunk in re.split(r" (?=\w+=)", parts[7]):
        key, _, val = chunk.partition("=")
        ext[key] = val
    return header, ext


def make_request(
    headers: Optional[Dict[str, str]] = None,
    client: Optional[Tuple[str, int]] = ("10.0.0.5", 4321),
    scheme: str = "http",
    path: str = "/x",
) -> Request:
    scope = {
        "type": "http",
        "method": "GET",
        "scheme": scheme,
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "headers": [(k.lower().encode(), v.encode("utf-8")) for k, v in (headers or {}).items()],
        "client": client,
        "server": ("svc", 80),
    }
    return Request(scope)
