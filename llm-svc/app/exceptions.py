class ServiceUnavailableError(Exception):
    """Сервис временно недоступен"""
    pass
class ModelNotLoadedError(ServiceUnavailableError):
    """Модель не загружена"""
    pass
class PoolExhaustedError(ServiceUnavailableError):
    """Пул моделей исчерпан"""
    pass
