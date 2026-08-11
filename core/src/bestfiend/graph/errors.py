"""Классификация ошибок графа + нативный error_handler-фабрика и retry-предикат.

`error_handler=to_error` вешается на регулярные ноды (init/react/tools); на
исключении после ретраев langgraph зовёт `to_error`,
тот классифицирует `NodeError.error` и роутит в error-ноду. classify —
структурный (по `status_code` / stdlib-типам / именам и паттернам), т.к. стек
нативный (openai/groq/httpx SDK). Имена классов и `httpx`
`response.status_code` читаем структурно — без жёсткого импорта SDK (провайдеры
разные).
"""

from typing import Literal, cast

from langgraph.errors import NodeError
from langgraph.types import Command, RetryPolicy
from loguru import logger

from bestfiend.graph.state import ErrorSignal, OrchestrationState


ErrorKind = Literal["provider_down", "context_exceeded", "loop_exhausted", "unexpected"]
ErrorNode = Literal["init", "react", "tools", "error", "graph"]

_MAX_MESSAGE = 500
_KNOWN_NODES = frozenset({"init", "react", "tools", "error", "graph"})
_CONTEXT_PATTERNS = (
    "context length",
    "context_length_exceeded",
    "maximum context",
    "too many tokens",
    "reduce the length",
    "string too long",
)
# httpx connection/timeout-исключения по имени класса (без жёсткого импорта SDK) —
# транзиентные. НЕ включаем status-обёртки (HTTPStatusError/APIStatusError) — их
# ведёт _status_code (по `response.status_code`), а не широкое имя.
_HTTPX_TRANSIENT = frozenset(
    {
        "TransportError",
        "ConnectError",
        "ConnectTimeout",
        "ReadTimeout",
        "WriteTimeout",
        "PoolTimeout",
        "TimeoutException",
        "ReadError",
        "WriteError",
        "RemoteProtocolError",
    }
)
_TRANSIENT_NAMES = (
    frozenset(
        {
            "APIConnectionError",
            "APITimeoutError",
            "RateLimitError",
            "InternalServerError",
            "ServiceUnavailableError",
        }
    )
    | _HTTPX_TRANSIENT
)
_PROVIDER_DOWN_NAMES = (
    frozenset(
        {
            "APIConnectionError",
            "APITimeoutError",
            "RateLimitError",
            "InternalServerError",
            "ServiceUnavailableError",
            "AuthenticationError",
            "PermissionDeniedError",
            "NotFoundError",
        }
    )
    | _HTTPX_TRANSIENT
)
_PROVIDER_DOWN_STATUSES = frozenset({401, 403, 404, 408, 409, 429})
_PROVIDER_DOWN_PATTERNS = (
    "rate limit",
    "timeout",
    "timed out",
    "connection",
    "service unavailable",
    "temporarily unavailable",
)
_TRANSIENT_STATUSES = frozenset({408, 429})


def classify(exc: BaseException, node: str) -> ErrorSignal:
    """Категоризирует исключение ноды в `ErrorSignal` (kind/node/message)."""
    return ErrorSignal(
        kind=_kind(exc),
        node=_safe_node(node),
        message=f"{type(exc).__name__}: {exc}"[:_MAX_MESSAGE],
    )


def to_error(state: OrchestrationState, error: NodeError) -> Command:
    """error_handler-фабрика: классифицирует сбой ноды → `goto error`."""
    logger.warning(
        "graph: node '{}' failed → error path: {}: {}",
        error.node,
        type(error.error).__name__,
        error.error,
    )
    return Command(
        update={"error_signal": classify(error.error, error.node)},
        goto="error",
    )


def _retry_transient(exc: Exception) -> bool:
    """Ретраить только транзиентные сбои (сеть/таймаут/429/5xx); баги — нет."""
    if isinstance(exc, (ConnectionError, TimeoutError)):
        return True
    status = _status_code(exc)
    if status is not None and (status in _TRANSIENT_STATUSES or status >= 500):
        return True
    return type(exc).__name__ in _TRANSIENT_NAMES


RETRY_POLICY = RetryPolicy(retry_on=_retry_transient)


def _kind(exc: BaseException) -> ErrorKind:
    status = _status_code(exc)
    name = type(exc).__name__
    text = str(exc).lower()
    if _is_context_length(status, name, text):
        return "context_exceeded"
    if _is_provider_down(exc, status, name, text):
        return "provider_down"
    return "unexpected"


def _status_code(exc: BaseException) -> int | None:
    """HTTP-статус из исключения: прямой `status_code` (openai/groq) или `response.status_code` (httpx)."""
    status = getattr(exc, "status_code", None)
    if isinstance(status, int):
        return status
    response = getattr(exc, "response", None)
    response_status = getattr(response, "status_code", None)
    return response_status if isinstance(response_status, int) else None


def _is_context_length(status: int | None, name: str, text: str) -> bool:
    looks_bad_request = status == 400 or "badrequest" in name.lower()
    return looks_bad_request and any(p in text for p in _CONTEXT_PATTERNS)


def _is_provider_down(
    exc: BaseException, status: int | None, name: str, text: str
) -> bool:
    if isinstance(exc, (ConnectionError, TimeoutError)):
        return True
    if status is not None and (status in _PROVIDER_DOWN_STATUSES or status >= 500):
        return True
    if name in _PROVIDER_DOWN_NAMES:
        return True
    return any(p in text for p in _PROVIDER_DOWN_PATTERNS)


def _safe_node(name: str) -> ErrorNode | None:
    return cast(ErrorNode, name) if name in _KNOWN_NODES else None
