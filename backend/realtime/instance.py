"""
socket/instance.py — создание AsyncServer (Socket.IO)

Выделено в отдельный модуль, чтобы избежать circular imports:
    main.py        -> импортирует sio и socket_app
    handlers.py    -> импортирует sio отсюда же
"""

from socketio import AsyncServer, ASGIApp
from starlette.applications import Starlette
from backend.app_state import settings
from backend.realtime.socket_limits import (
    socket_max_http_buffer_size,
    socket_ping_interval_sec,
    socket_ping_timeout_sec,
)

# -- собираем список разрешённых origins из конфига
urls = settings.urls
_origins_raw = [
    getattr(urls, "frontend_port", None),
    getattr(urls, "frontend_port_ipv4", None),
    getattr(urls, "backend_port", None),
    getattr(urls, "backend_port_ipv4", None),
    getattr(urls, "frontend_docker", None),
    getattr(urls, "backend_docker", None),
    getattr(urls, "ingress_port", None),
]
socketio_origins = [o for o in _origins_raw if o]

# ping_timeout выше обычных чатов: первая загрузка Flux/ComfyUI часто >5 мин.
sio = AsyncServer(
    async_mode="asgi",
    cors_allowed_origins=socketio_origins,
    ping_timeout=socket_ping_timeout_sec(),
    ping_interval=socket_ping_interval_sec(),
    max_http_buffer_size=socket_max_http_buffer_size(),
    logger=False,
    engineio_logger=False,
)

_starlette_app = Starlette()
socket_app = ASGIApp(sio, _starlette_app)
