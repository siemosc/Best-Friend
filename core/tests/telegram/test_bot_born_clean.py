"""Структурные тесты telegram-bot: отсутствие HTTP-клиентов и HITL-следов.

Фиксирует, что:
- HITL не присутствует (нет `send_elicitation`, `on_hitl_cancel_callback`,
  `_HITL_CANCEL_KEYBOARD`, `pending_elicitations`, `hitl:c`).
- HTTP-клиенты не присутствуют (нет `httpx`, `httpx_sse`, `OrchestrationClient`,
  `ControlPlaneClient`, `ArtifactsClient`).
- TelegramBot собирается с in-process refs (UserService/GraphRuntime/ArtifactService/
  StreamPublisher + AuthService), не с HTTP-клиентами.
- `/web` команда — in-process через AuthService.
"""

import inspect
from pathlib import Path

from bestfiend.telegram import TelegramBot


_TELEGRAM_SRC = Path(__file__).resolve().parents[2] / "src" / "bestfiend" / "telegram"


def test_telegram_module_files_have_no_hitl_traces() -> None:
    """Grep по telegram/*.py: HITL-токенов не остаётся."""
    forbidden = (
        "send_elicitation",
        "on_hitl_cancel_callback",
        "_HITL_CANCEL_KEYBOARD",
        "HITL_CANCEL_CALLBACK_DATA",
        "PendingElicitations",
        "hitl:c",
        "HITL_TIMEOUT",
    )
    offenders: list[str] = []
    for path in _TELEGRAM_SRC.rglob("*.py"):
        content = path.read_text(encoding="utf-8")
        for token in forbidden:
            if token in content:
                offenders.append(f"{path.name}: '{token}'")
    assert not offenders, f"HITL leaked into telegram module: {offenders}"


def test_telegram_module_has_no_http_clients() -> None:
    """Telegram module не импортит httpx / sse-старые-клиенты."""
    forbidden_imports = (
        "import httpx",
        "from httpx",
        "httpx_sse",
        "from bestfiend.telegram.orchestration_client",
        "from bestfiend.telegram.control_plane_client",
        "from bestfiend.telegram.artifacts_client",
    )
    offenders: list[str] = []
    for path in _TELEGRAM_SRC.rglob("*.py"):
        content = path.read_text(encoding="utf-8")
        for token in forbidden_imports:
            if token in content:
                offenders.append(f"{path.name}: '{token}'")
    assert not offenders, f"HTTP clients leaked into telegram module: {offenders}"


def test_telegram_module_has_web_command_in_process() -> None:
    """`/web` команда использует in-process AuthService, не HTTP-клиент."""
    bot_source = (_TELEGRAM_SRC / "bot.py").read_text(encoding="utf-8")
    assert 'Command("web")' in bot_source
    assert "_handle_web" in bot_source
    assert "auth_service" in bot_source
    forbidden_http = (
        "request_binding_code",  # старое имя HTTP-метода CP-client'а
        "BindingCodeClientProtocol",
    )
    for token in forbidden_http:
        assert token not in bot_source, (
            f"HTTP binding-code remnant leaked into bot.py: {token}"
        )


def test_telegram_bot_init_signature_uses_in_process_refs() -> None:
    """Constructor TelegramBot принимает in-process deps, не HTTP-клиенты."""
    sig = inspect.signature(TelegramBot.__init__)
    params = set(sig.parameters.keys())
    expected_in_process = {
        "user_service",
        "publish_input_event",
        "artifacts",
        "outbound_source",
    }
    assert expected_in_process.issubset(params), (
        f"TelegramBot missing in-process deps: {expected_in_process - params}"
    )
    forbidden_http = {
        "core_client",
        "user_resolver",
        "identity_authorizer",
        "binding_code_client",
        "artifacts_client",
    }
    leaked = forbidden_http & params
    assert not leaked, f"HTTP-client deps leaked into TelegramBot: {leaked}"


def test_telegram_bot_has_no_elicitation_methods() -> None:
    """`TelegramBot` не имеет `send_elicitation`/`handle_system_message`."""
    assert not hasattr(TelegramBot, "send_elicitation")
    assert not hasattr(TelegramBot, "handle_system_message")
