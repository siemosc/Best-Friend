"""Доменные ошибки Telegram-шлюза."""


class TelegramGatewayError(Exception):
    """Базовая ошибка Telegram-шлюза."""


class TelegramContentInvariantError(TelegramGatewayError):
    """Подготовленный Telegram-контент нарушает внутренний контракт."""


class AuthorizationFailedError(TelegramGatewayError):
    """Авторизация входящего Telegram-сообщения завершилась отказом."""
