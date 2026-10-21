"""Проверка исходящих URL от пользователя (защита от SSRF)."""

from __future__ import annotations

import ipaddress
import socket
from typing import Callable, Iterable, List
from urllib.parse import urlparse

ALLOWED_URL_SCHEMES = frozenset({"http", "https"})
MAX_URL_LENGTH = 2048
_BLOCKED_HOSTNAMES = frozenset(
    {
        "localhost",
        "metadata",
        "metadata.google.internal",
        "instance-data",
        "instance-data.ec2.internal",
    }
)
_BLOCKED_HOST_SUFFIXES = (".localhost", ".internal", ".local")

Resolver = Callable[[str], Iterable[str]]


class UnsafeUrlError(ValueError):
    """URL не прошёл проверку безопасности (текст безопасен для клиента)."""


def _default_resolver(host: str) -> List[str]:
    infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    return [str(info[4][0]) for info in infos]


def _is_forbidden_ip(raw_ip: str) -> bool:
    ip = ipaddress.ip_address(raw_ip.split("%", 1)[0])
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
        or not ip.is_global
    )


def validate_public_http_url(url: str, *, resolver: Resolver | None = None) -> str:
    """Возвращает URL, если он http(s) и указывает на публичный адрес.

    Блокирует другие схемы, localhost/metadata-имена и любые хосты, которые
    резолвятся в частные, loopback, link-local (169.254.0.0/16, в т.ч. cloud
    metadata), multicast и зарезервированные адреса.

    Ограничение: проверка выполняется до скачивания, поэтому DNS rebinding
    полностью не исключён — загрузчик должен дополнительно ограничивать сеть.
    """
    raw = str(url or "").strip()
    if not raw or len(raw) > MAX_URL_LENGTH:
        raise UnsafeUrlError("Некорректный URL")
    try:
        parsed = urlparse(raw)
        host = (parsed.hostname or "").strip().rstrip(".").lower()
    except ValueError as exc:
        raise UnsafeUrlError("Некорректный URL") from exc
    if parsed.scheme.lower() not in ALLOWED_URL_SCHEMES:
        raise UnsafeUrlError("Разрешены только http/https ссылки")
    if not host:
        raise UnsafeUrlError("Некорректный URL")
    if host in _BLOCKED_HOSTNAMES or host.endswith(_BLOCKED_HOST_SUFFIXES):
        raise UnsafeUrlError("Адрес недоступен")
    try:
        addresses = list((resolver or _default_resolver)(host))
    except (OSError, ValueError) as exc:
        raise UnsafeUrlError("Не удалось определить адрес хоста") from exc
    if not addresses:
        raise UnsafeUrlError("Не удалось определить адрес хоста")
    for address in addresses:
        try:
            forbidden = _is_forbidden_ip(address)
        except ValueError:
            forbidden = True
        if forbidden:
            raise UnsafeUrlError("Адрес недоступен")
    return raw
