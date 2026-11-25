"""
Модуль для функций безопасности (аутентификация, авторизация и т.д.)
"""

from fastapi import HTTPException, status, Depends, Header
from app.core.config import get_settings

settings = get_settings()

async def verify_api_key(x_api_key: str = Header(None)):
    """
    Проверка API ключа из заголовка запроса.
    """
    
    # Если аутентификация отключена, пропускаем проверку
    if not settings.security.enabled:
        return True
    
    # Если аутентификация включена, но ключ не задан в конфигурации
    if not settings.security.api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="API key is not configured on server",
            headers={"WWW-Authenticate": "API-Key"},
        )
    
    # Если заголовок не передан
    if not x_api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="API key is missing",
            headers={"WWW-Authenticate": "API-Key"},
        )
    
    # Проверка ключа
    if x_api_key != settings.security.api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API key",
            headers={"WWW-Authenticate": "API-Key"},
        )
    
    return True