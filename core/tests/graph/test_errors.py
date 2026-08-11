"""Тесты errors.py: classify (структурный), _retry_transient, to_error."""

from langgraph.errors import NodeError

from bestfiend.graph.errors import RETRY_POLICY, _retry_transient, classify, to_error
from bestfiend.graph.state import InputContext, OrchestrationState


class _StatusError(Exception):
    """Исключение с `status_code` (как у openai/groq SDK)."""

    def __init__(self, message: str, status_code: int) -> None:
        super().__init__(message)
        self.status_code = status_code


class _ResponseError(Exception):
    """Исключение со статусом в `response.status_code` (как у httpx)."""

    def __init__(self, message: str, status_code: int) -> None:
        super().__init__(message)
        self.response = type("_Resp", (), {"status_code": status_code})()


class ReadTimeout(Exception):
    """Имя как у httpx.ReadTimeout — classify матчит по имени класса."""


class ConnectError(Exception):
    """Имя как у httpx.ConnectError."""


class HTTPStatusError(Exception):
    """Имя как у httpx.HTTPStatusError — ведём по response.status_code, не по имени-обёртке."""

    def __init__(self, message: str, status_code: int) -> None:
        super().__init__(message)
        self.response = type("_Resp", (), {"status_code": status_code})()


def test_classify_rate_limit_is_provider_down() -> None:
    sig = classify(_StatusError("rate limited", 429), "react")
    assert sig.kind == "provider_down"
    assert sig.node == "react"
    assert sig.message  # message строится всегда


def test_classify_context_length_is_context_exceeded() -> None:
    sig = classify(_StatusError("maximum context length exceeded", 400), "react")
    assert sig.kind == "context_exceeded"


def test_classify_5xx_is_provider_down() -> None:
    assert classify(_StatusError("upstream", 503), "dispatcher").kind == "provider_down"


def test_classify_connection_is_provider_down() -> None:
    assert classify(ConnectionError("boom"), "dispatcher").kind == "provider_down"


def test_classify_unexpected_and_message_truncated() -> None:
    sig = classify(ValueError("x" * 1000), "tools")
    assert sig.kind == "unexpected"
    assert sig.node == "tools"
    assert 0 < len(sig.message) <= 500


def test_classify_unknown_node_becomes_none() -> None:
    assert classify(ValueError("x"), "weird-node").node is None


def test_retry_transient_predicate() -> None:
    assert _retry_transient(_StatusError("x", 429)) is True
    assert _retry_transient(_StatusError("x", 503)) is True
    assert _retry_transient(ConnectionError("x")) is True
    assert _retry_transient(_StatusError("bad", 400)) is False
    assert _retry_transient(ValueError("x")) is False


def test_retry_policy_uses_predicate() -> None:
    assert RETRY_POLICY.retry_on is _retry_transient


def test_to_error_routes_to_error_node() -> None:
    state = OrchestrationState(input=InputContext(message="x", request_id="r1"))
    cmd = to_error(state, NodeError(node="react", error=ValueError("boom")))

    assert cmd.goto == "error"
    update = cmd.update
    assert update is not None
    sig = update["error_signal"]
    assert sig.kind == "unexpected"
    assert sig.node == "react"


def test_classify_httpx_response_status_is_provider_down() -> None:
    assert classify(_ResponseError("upstream", 503), "react").kind == "provider_down"
    assert classify(_ResponseError("rate", 429), "react").kind == "provider_down"


def test_classify_httpx_style_names_are_provider_down() -> None:
    assert classify(ReadTimeout("slow"), "react").kind == "provider_down"
    assert classify(ConnectError("down"), "dispatcher").kind == "provider_down"


def test_retry_transient_httpx() -> None:
    assert _retry_transient(_ResponseError("x", 429)) is True
    assert _retry_transient(_ResponseError("x", 503)) is True
    assert _retry_transient(ReadTimeout("x")) is True
    assert _retry_transient(ConnectError("x")) is True
    assert _retry_transient(_ResponseError("bad", 400)) is False


def test_classify_status_wrapper_400_is_unexpected() -> None:
    """HTTPStatusError(400) без контекст-паттерна → unexpected (status, не широкое имя)."""
    sig = classify(HTTPStatusError("invalid request body", 400), "react")
    assert sig.kind == "unexpected"


def test_classify_status_wrapper_400_context_is_context_exceeded() -> None:
    sig = classify(HTTPStatusError("maximum context length exceeded", 400), "react")
    assert sig.kind == "context_exceeded"


def test_classify_status_wrapper_429_is_provider_down() -> None:
    assert classify(HTTPStatusError("rate", 429), "react").kind == "provider_down"
