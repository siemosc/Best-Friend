"""Тесты отказоустойчивости QA-раннера."""

from pathlib import Path
import subprocess
import sys

import pytest


QA_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(QA_ROOT))

import dev_code_fix as qa  # noqa: E402


def _completed_process(
    *,
    returncode: int,
    stdout: str = "",
    stderr: str = "",
) -> subprocess.CompletedProcess[str]:
    """Создать результат процесса для теста."""
    return subprocess.CompletedProcess([], returncode, stdout, stderr)


def test_flat_core_path_resolves_to_core_target() -> None:
    """Путь из плоского core должен выбирать цель core."""
    path = qa.REPO_ROOT / "core" / "src" / "bestfiend" / "app.py"

    result = qa._spec_for_path(path)

    assert result is qa.REGISTRY["core"]


def test_qa_run_invokes_tool_as_python_module(monkeypatch: pytest.MonkeyPatch) -> None:
    """QA-инструмент должен запускаться модулем текущего venv."""
    observed: dict[str, object] = {}

    def fake_run(
        cmd: list[str],
        *,
        cwd: Path,
    ) -> subprocess.CompletedProcess[str]:
        """Сохранить команду без запуска процесса."""
        observed["cmd"] = cmd
        observed["cwd"] = cwd
        return _completed_process(returncode=0)

    monkeypatch.setattr(qa, "_run", fake_run)

    qa._qa_run(["bandit", "--version"])

    assert observed == {
        "cmd": [
            str(qa.QA_PROJECT / qa.VENV_PYTHON_RELATIVE),
            "-m",
            "bandit",
            "--version",
        ],
        "cwd": qa.REPO_ROOT,
    }


def test_qa_run_rejects_empty_command() -> None:
    """Пустая команда должна завершаться доменной ошибкой."""
    with pytest.raises(qa.QaEnvironmentError, match="не указан QA-инструмент"):
        qa._qa_run([])


@pytest.mark.parametrize(
    ("runner_name", "code"),
    [
        ("ruff", "RUFF_FATAL"),
        ("bandit", "BANDIT_FATAL"),
        ("radon", "RADON_FATAL"),
    ],
)
def test_json_runner_rejects_malformed_success_output(
    monkeypatch: pytest.MonkeyPatch,
    runner_name: str,
    code: str,
) -> None:
    """Некорректный JSON нельзя считать успешной проверкой."""
    monkeypatch.setattr(
        qa,
        "_qa_run",
        lambda _cmd: _completed_process(returncode=0, stdout="not-json"),
    )

    if runner_name == "ruff":
        _, diagnostics = qa.run_ruff(qa.REGISTRY["core"], fix=False)
    elif runner_name == "bandit":
        _, diagnostics = qa.run_bandit(qa.REGISTRY["core"])
    else:
        _, diagnostics = qa.run_radon(qa.REGISTRY["core"])

    assert [item.code for item in diagnostics] == [code]


def test_bandit_filters_exact_reviewed_finding(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Bandit должен скрывать только точную проверенную находку."""
    observed: dict[str, list[str]] = {}
    target_root = tmp_path / "target"
    (target_root / "src").mkdir(parents=True)
    finding = {
        "filename": "target/src/module.py",
        "test_id": "B608",
        "issue_text": "Possible SQL injection vector.",
        "code": '10 query = f"SELECT {COLUMNS}"',
        "line_number": 10,
        "issue_severity": "MEDIUM",
    }
    (target_root / ".bandit-baseline.json").write_text(
        qa.json.dumps({"accepted_results": [finding]}),
        encoding="utf-8",
    )
    spec = qa.TargetSpec(
        name="target",
        kind="service",
        root=target_root,
        src_dirs=("src",),
        test_dirs=(),
    )

    def fake_qa_run(cmd: list[str]) -> subprocess.CompletedProcess[str]:
        """Сохранить команду Bandit без запуска процесса."""
        observed["cmd"] = cmd
        return _completed_process(
            returncode=1,
            stdout=qa.json.dumps({"results": [finding]}),
        )

    monkeypatch.setattr(qa, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(qa, "_qa_run", fake_qa_run)

    outcome, diagnostics = qa.run_bandit(spec)
    accepted_findings = qa._load_bandit_baseline(
        target_root / ".bandit-baseline.json"
    )
    changed_finding = {**finding, "code": '10 query = f"DELETE {TABLE}"'}

    assert observed["cmd"][2] == "target/src"
    assert outcome.issues == 0
    assert diagnostics == []
    assert accepted_findings is not None
    assert qa._bandit_finding_key(changed_finding) not in accepted_findings


def test_vulture_rejects_invalid_input_exit_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Код 1 от Vulture должен считаться ошибкой запуска."""
    monkeypatch.setattr(
        qa,
        "_qa_run",
        lambda _cmd: _completed_process(returncode=1, stderr="invalid input"),
    )

    _, diagnostics = qa.run_vulture(qa.REGISTRY["core"])

    assert [item.code for item in diagnostics] == ["VULTURE_FATAL"]


def test_vulture_parses_windows_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """Двоеточие диска Windows не должно ломать разбор находки."""
    output = "C:\\repo\\module.py:12: unused function 'stale' (90% confidence)"
    monkeypatch.setattr(
        qa,
        "_qa_run",
        lambda _cmd: _completed_process(returncode=3, stdout=output),
    )

    _, diagnostics = qa.run_vulture(qa.REGISTRY["core"])

    assert len(diagnostics) == 1
    assert diagnostics[0].code == "DEAD_CODE"
    assert diagnostics[0].line == 12


def test_qa_run_requires_existing_venv_python(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Отсутствующий интерпретатор QA-venv — доменная ошибка, не запуск."""
    monkeypatch.setattr(qa, "QA_PROJECT", tmp_path)

    with pytest.raises(qa.QaEnvironmentError, match="QA-venv не готов"):
        qa._qa_run(["bandit", "--version"])


def test_bandit_scan_errors_are_fatal(monkeypatch: pytest.MonkeyPatch) -> None:
    """Файл, выпавший из bandit-скана, не должен выглядеть успехом."""
    payload = qa.json.dumps(
        {
            "results": [],
            "errors": [
                {
                    "filename": "core/src/bestfiend/module.py",
                    "reason": "syntax error while parsing AST",
                }
            ],
        }
    )
    monkeypatch.setattr(
        qa,
        "_qa_run",
        lambda _cmd: _completed_process(returncode=0, stdout=payload),
    )

    outcome, diagnostics = qa.run_bandit(qa.REGISTRY["core"])

    assert [item.code for item in diagnostics] == ["BANDIT_SCAN_ERROR"]
    assert diagnostics[0].severity == "error"
    assert "syntax error" in diagnostics[0].message
    assert outcome.issues == 1


def test_radon_file_error_is_fatal(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ошибка анализа файла в radon — FATAL, а не молчаливый пропуск."""
    payload = qa.json.dumps(
        {"core/src/bestfiend/module.py": {"error": "invalid syntax (line 3)"}}
    )
    monkeypatch.setattr(
        qa,
        "_qa_run",
        lambda _cmd: _completed_process(returncode=0, stdout=payload),
    )

    outcome, diagnostics = qa.run_radon(qa.REGISTRY["core"])

    assert [item.code for item in diagnostics] == ["RADON_FILE_ERROR"]
    assert diagnostics[0].severity == "error"
    assert "invalid syntax" in diagnostics[0].message
    assert outcome.issues == 1


def test_service_run_without_uv_gives_domain_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Отсутствие uv в PATH — доменная ошибка, не traceback."""

    def raising_run(
        cmd: list[str],
        *,
        cwd: Path,
    ) -> subprocess.CompletedProcess[str]:
        """Имитировать отсутствие исполняемого файла."""
        raise FileNotFoundError(cmd[0])

    monkeypatch.setattr(qa, "_run", raising_run)

    with pytest.raises(qa.QaEnvironmentError, match="uv не найден"):
        qa._service_run(qa.REGISTRY["core"], ["pytest", "-q"])
