#!/usr/bin/env python3
"""Монорепный раннер статического анализа и тестов BestFiend.

Запускает ruff/bandit/vulture/radon/pyright из изолированного QA-venv
(`tools/qa/.venv`) и тонко делегирует pytest в `.venv` каждого сервиса.
По умолчанию проверяет только сервисы, которых коснулся `git diff`.
"""

from __future__ import annotations

import argparse
import ast
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
from typing import Any


# ─── Константы и реестр целей ───────────────────────────────────────

REPO_ROOT = Path(__file__).resolve().parents[2]
QA_PROJECT = REPO_ROOT / "tools" / "qa"
QA_CACHE = QA_PROJECT / ".cache"
PYRIGHT_CONFIG_DIR = QA_CACHE / "pyright"
DEFAULT_REPORT_PATH = QA_CACHE / "report.json"

MAX_OUTPUT_EXCERPT_CHARS = 1500
MAX_OUTPUT_EXCERPT_LINES = 20
PYRIGHT_PYTHON_VERSION = "3.11"
RADON_RANKS = frozenset({"C", "D", "E", "F"})
VULTURE_MIN_CONFIDENCE = "80"
# vulture игнорирует только `self`; `cls` в classmethod-валидаторах для него — мёртвая переменная.
VULTURE_IGNORE_NAMES = "cls"
DEV_CODE_FIX_FILENAME = "dev_code_fix.py"

# Кросс-платформенный путь к python внутри venv сервиса.
VENV_PYTHON_RELATIVE = (
    Path(".venv") / "Scripts" / "python.exe"
    if os.name == "nt"
    else Path(".venv") / "bin" / "python"
)


@dataclass(slots=True, frozen=True)
class TargetSpec:
    """Описание одной цели проверки — сервиса или пакета."""

    name: str
    kind: str  # "service" | "package"
    root: Path  # абсолютный путь
    src_dirs: tuple[str, ...]
    test_dirs: tuple[str, ...]
    pyright_excludes: tuple[str, ...] = ()

    @property
    def rel_root(self) -> str:
        return self.root.relative_to(REPO_ROOT).as_posix()

    @property
    def has_tests(self) -> bool:
        return bool(self.test_dirs)

    def src_paths(self) -> list[Path]:
        return [self.root / d for d in self.src_dirs]

    def test_paths(self) -> list[Path]:
        return [self.root / d for d in self.test_dirs]

    def all_source_paths(self) -> list[Path]:
        return self.src_paths() + self.test_paths()


def _build_registry() -> dict[str, TargetSpec]:
    """Источник правды по тому, что мы считаем монорепной целью.

    Живая backend-цель одна: `core` (модульный монолит).
    """
    services: list[TargetSpec] = [
        TargetSpec(
            # core живёт в корне репо (flat-раскладка, миграция §6), не под services/.
            name="core",
            kind="service",
            root=REPO_ROOT / "core",
            src_dirs=("src",),
            test_dirs=("tests",),
        ),
    ]
    return {spec.name: spec for spec in services}


REGISTRY: dict[str, TargetSpec] = _build_registry()


# ─── Доменные ошибки и модели ───────────────────────────────────────


class QaError(Exception):
    """Базовая ошибка QA-раннера."""


class TargetResolutionError(QaError):
    """Не удалось определить набор целей."""


class QaEnvironmentError(QaError):
    """QA-venv не готов или uv недоступен."""


@dataclass(slots=True, frozen=True)
class Diagnostic:
    """Одна находка статического анализа."""

    target: str
    source: str
    file: str
    line: int
    code: str
    message: str
    severity: str  # "error" | "warning"


@dataclass(slots=True)
class CheckRun:
    """Метаданные одного вызова инструмента."""

    target: str
    check: str
    step: str
    command: list[str]
    returncode: int | None
    duration_ms: int
    stdout_len: int
    stderr_len: int
    stdout_excerpt: str = ""
    stderr_excerpt: str = ""
    truncated: bool = False


@dataclass(slots=True)
class CheckOutcome:
    """Агрегат по одному типу проверки и одной цели."""

    target: str
    check: str
    issues: int = 0
    duration_ms: int = 0
    runs: list[CheckRun] = field(default_factory=list)


# ─── Резолвер целей ─────────────────────────────────────────────────


def resolve_targets_from_changed(files: Iterable[Path]) -> list[TargetSpec]:
    """Определить набор целей по списку изменённых файлов."""
    selected: dict[str, TargetSpec] = {}
    for raw in files:
        spec = _spec_for_path(raw)
        if spec is None:
            continue
        selected[spec.name] = spec
    return _ordered_targets(selected.values())


def resolve_targets_from_git_diff() -> list[TargetSpec]:
    """Определить цели по `git diff` против HEAD и индекса."""
    files = _git_changed_files()
    return resolve_targets_from_changed(files)


def resolve_targets_all() -> list[TargetSpec]:
    return _ordered_targets(REGISTRY.values())


def resolve_target_by_name(name: str) -> TargetSpec:
    spec = REGISTRY.get(name)
    if spec is None:
        known = ", ".join(sorted(REGISTRY))
        raise TargetResolutionError(f"Неизвестная цель: {name!r}. Доступно: {known}")
    return spec


def _spec_for_path(file_path: Path) -> TargetSpec | None:
    """Найти QA-цель, которой принадлежит путь."""
    try:
        rel = file_path.resolve().relative_to(REPO_ROOT)
    except ValueError:
        return None
    parts = rel.parts
    if not parts:
        return None
    direct_target = REGISTRY.get(parts[0])
    if direct_target is not None:
        return direct_target
    if len(parts) < 2:
        return None
    bucket, name = parts[0], parts[1]
    if bucket not in {"services", "packages"}:
        return None
    return REGISTRY.get(name)


def _ordered_targets(targets: Iterable[TargetSpec]) -> list[TargetSpec]:
    return sorted(targets, key=lambda spec: (spec.kind != "service", spec.name))


def _git_changed_files() -> list[Path]:
    """Файлы, изменённые относительно HEAD (включая staged и untracked)."""
    cmd = [
        "git",
        "-C",
        str(REPO_ROOT),
        "status",
        "--porcelain",
        "--untracked-files=normal",
    ]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=False,
            encoding="utf-8",
            errors="replace",
        )
    except FileNotFoundError as exc:
        raise TargetResolutionError("git не найден в PATH") from exc
    if result.returncode != 0:
        raise TargetResolutionError(
            f"git status упал: {result.stderr.strip() or 'неизвестная ошибка'}"
        )
    files: list[Path] = []
    for line in result.stdout.splitlines():
        if len(line) < 4:
            continue
        # Формат: "XY path" или "XY orig -> path" для переименований.
        payload = line[3:].strip()
        if " -> " in payload:
            payload = payload.split(" -> ", 1)[1]
        files.append(REPO_ROOT / payload.strip('"'))
    return files


# ─── Исполнение subprocess'ов ───────────────────────────────────────


def _run(cmd: Sequence[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    return subprocess.run(
        list(cmd),
        cwd=str(cwd),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        check=False,
    )


def _qa_run(
    args: Sequence[str], *, cwd: Path | None = None
) -> subprocess.CompletedProcess[str]:
    """Запустить инструмент как модуль из изолированного QA-venv."""
    if not args:
        raise QaEnvironmentError("не указан QA-инструмент")
    qa_python = QA_PROJECT / VENV_PYTHON_RELATIVE
    if not qa_python.is_file():
        raise QaEnvironmentError(
            f"QA-venv не готов: интерпретатор не найден по пути {qa_python}"
        )
    tool, *tool_args = args
    full_cmd = [str(qa_python), "-m", tool, *tool_args]
    return _run(full_cmd, cwd=cwd or REPO_ROOT)


def _service_run(
    spec: TargetSpec, args: Sequence[str]
) -> subprocess.CompletedProcess[str]:
    """Запустить команду в venv конкретного сервиса через `uv run --directory`."""
    full_cmd = ["uv", "run", "--directory", str(spec.root), *args]
    try:
        return _run(full_cmd, cwd=spec.root)
    except FileNotFoundError as exc:
        raise QaEnvironmentError(
            "uv не найден в PATH — сервисные команды недоступны"
        ) from exc


def _build_excerpt(payload: str) -> tuple[str, bool]:
    if not payload:
        return "", False
    normalized = payload.strip()
    if not normalized:
        return "", False
    lines = normalized.splitlines()
    excerpt = "\n".join(lines[:MAX_OUTPUT_EXCERPT_LINES])
    truncated = len(lines) > MAX_OUTPUT_EXCERPT_LINES
    if len(excerpt) > MAX_OUTPUT_EXCERPT_CHARS:
        excerpt = excerpt[:MAX_OUTPUT_EXCERPT_CHARS].rstrip()
        truncated = True
    return excerpt, truncated


def _record_run(
    *,
    outcome: CheckOutcome,
    step: str,
    cmd: Sequence[str],
    result: subprocess.CompletedProcess[str],
    duration_ms: int,
) -> None:
    stdout_excerpt, stdout_truncated = _build_excerpt(result.stdout or "")
    stderr_excerpt, stderr_truncated = _build_excerpt(result.stderr or "")
    truncated = stdout_truncated or stderr_truncated
    outcome.runs.append(
        CheckRun(
            target=outcome.target,
            check=outcome.check,
            step=step,
            command=list(cmd),
            returncode=result.returncode,
            duration_ms=duration_ms,
            stdout_len=len(result.stdout or ""),
            stderr_len=len(result.stderr or ""),
            stdout_excerpt=stdout_excerpt if result.returncode != 0 else "",
            stderr_excerpt=stderr_excerpt,
            truncated=truncated,
        )
    )
    outcome.duration_ms += duration_ms


def _safe_json(payload: str) -> Any:
    try:
        return json.loads(payload)
    except json.JSONDecodeError:
        return None


# ─── Утилиты путей ──────────────────────────────────────────────────


def _normalize_path(file_path: str) -> str:
    if not file_path:
        return "."
    path = Path(file_path)
    if not path.is_absolute():
        path = (REPO_ROOT / path).resolve()
    try:
        return path.relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return path.as_posix()


def _is_excluded_file(rel_path: str) -> bool:
    """Файлы, которые мы никогда не считаем своим кодом."""
    parts = rel_path.split("/")
    return DEV_CODE_FIX_FILENAME in parts or any(
        part in {".venv", "__pycache__"} for part in parts
    )


# ─── Проверки: ruff ─────────────────────────────────────────────────


def run_ruff(spec: TargetSpec, *, fix: bool) -> tuple[CheckOutcome, list[Diagnostic]]:
    outcome = CheckOutcome(target=spec.name, check="ruff")
    diagnostics: list[Diagnostic] = []
    targets = [str(p) for p in spec.all_source_paths() if p.exists()]
    if not targets:
        return outcome, diagnostics

    cmd = ["ruff", "check", *targets, "--output-format", "json"]
    if fix:
        cmd.append("--fix")
    started = time.perf_counter()
    result = _qa_run(cmd)
    duration = int((time.perf_counter() - started) * 1000)
    _record_run(
        outcome=outcome, step="check", cmd=cmd, result=result, duration_ms=duration
    )

    if result.returncode not in (0, 1):
        diagnostics.append(
            Diagnostic(
                target=spec.name,
                source="ruff",
                file=spec.rel_root,
                line=0,
                code="RUFF_FATAL",
                message=(result.stderr or result.stdout or "ruff упал").strip(),
                severity="error",
            )
        )
        outcome.issues += 1
        return outcome, diagnostics

    data = _safe_json(result.stdout or "")
    if not isinstance(data, list):
        diagnostics.append(
            Diagnostic(
                target=spec.name,
                source="ruff",
                file=spec.rel_root,
                line=0,
                code="RUFF_FATAL",
                message=(
                    result.stderr or result.stdout or "ruff не вернул корректный JSON"
                ).strip(),
                severity="error",
            )
        )
        outcome.issues += 1
        return outcome, diagnostics

    for item in data:
        rel = _normalize_path(str(item.get("filename", "")))
        if _is_excluded_file(rel):
            continue
        location = item.get("location") or {}
        diagnostics.append(
            Diagnostic(
                target=spec.name,
                source="ruff",
                file=rel,
                line=int(location.get("row", 0) or 0),
                code=str(item.get("code", "unknown")),
                message=str(item.get("message", "")).strip(),
                severity="error",
            )
        )
        outcome.issues += 1

    if fix:
        fmt_cmd = ["ruff", "format", *targets]
        started = time.perf_counter()
        fmt_result = _qa_run(fmt_cmd)
        duration = int((time.perf_counter() - started) * 1000)
        _record_run(
            outcome=outcome,
            step="format",
            cmd=fmt_cmd,
            result=fmt_result,
            duration_ms=duration,
        )
        if fmt_result.returncode != 0:
            diagnostics.append(
                Diagnostic(
                    target=spec.name,
                    source="ruff",
                    file=spec.rel_root,
                    line=0,
                    code="RUFF_FORMAT_FATAL",
                    message=(
                        fmt_result.stderr or fmt_result.stdout or "ruff format упал"
                    ).strip(),
                    severity="error",
                )
            )
            outcome.issues += 1
    return outcome, diagnostics


# ─── Проверки: pyright ──────────────────────────────────────────────


def _has_service_venv(spec: TargetSpec) -> bool:
    """True, если у цели существует свой `.venv`."""
    return (spec.root / VENV_PYTHON_RELATIVE).exists()


def _relative_to_config(target: Path) -> str:
    """Путь от директории `pyrightconfig.json` до целевой папки.

    Pyright молча игнорирует абсолютные пути в include/exclude/venvPath
    и принимает только пути, относительные к директории конфига.
    """
    rel = os.path.relpath(target, PYRIGHT_CONFIG_DIR)
    return Path(rel).as_posix()


def _write_pyright_config(spec: TargetSpec) -> Path:
    """Сгенерировать ad-hoc pyrightconfig.json для одной цели."""
    PYRIGHT_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    config: dict[str, Any] = {
        "include": [
            _relative_to_config(spec.root / d)
            for d in (*spec.src_dirs, *spec.test_dirs)
            if (spec.root / d).exists()
        ],
        "exclude": [
            "**/__pycache__",
            "**/.venv",
            "**/*.egg-info",
            *[_relative_to_config(spec.root / item) for item in spec.pyright_excludes],
        ],
        "pythonVersion": PYRIGHT_PYTHON_VERSION,
        "typeCheckingMode": "basic",
        "reportMissingTypeStubs": "none",
        "reportUnknownArgumentType": "none",
        "reportUnknownLambdaType": "none",
        "reportUnknownMemberType": "none",
        "reportUnknownParameterType": "none",
        "reportUnknownVariableType": "none",
        "reportAttributeAccessIssue": "error",
        "reportArgumentType": "error",
        "reportCallIssue": "error",
        "reportReturnType": "error",
        # Корень цели — для абсолютных импортов пакета tests (общие стабы),
        # как их резолвит pytest (rootdir сервиса в sys.path).
        "extraPaths": [_relative_to_config(spec.root)],
    }
    # venvPath/venv — единственный поддерживаемый pyright способ показать
    # ему интерпретатор из чужого .venv. Тоже только относительные пути.
    if _has_service_venv(spec):
        config["venvPath"] = _relative_to_config(spec.root)
        config["venv"] = ".venv"
    config_path = PYRIGHT_CONFIG_DIR / f"{spec.name}.json"
    config_path.write_text(
        json.dumps(config, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return config_path


def run_pyright(spec: TargetSpec) -> tuple[CheckOutcome, list[Diagnostic]]:
    outcome = CheckOutcome(target=spec.name, check="pyright")
    diagnostics: list[Diagnostic] = []
    config_path = _write_pyright_config(spec)

    cmd = ["pyright", "--project", str(config_path), "--outputjson"]
    started = time.perf_counter()
    result = _qa_run(cmd)
    duration = int((time.perf_counter() - started) * 1000)
    _record_run(
        outcome=outcome, step="analyze", cmd=cmd, result=result, duration_ms=duration
    )

    if result.returncode not in (0, 1):
        diagnostics.append(
            Diagnostic(
                target=spec.name,
                source="pyright",
                file=spec.rel_root,
                line=0,
                code="PYRIGHT_FATAL",
                message=(result.stderr or result.stdout or "pyright упал").strip(),
                severity="error",
            )
        )
        outcome.issues += 1
        return outcome, diagnostics

    data = _safe_json(result.stdout or "")
    if not isinstance(data, dict):
        diagnostics.append(
            Diagnostic(
                target=spec.name,
                source="pyright",
                file=spec.rel_root,
                line=0,
                code="PYRIGHT_PARSE_ERROR",
                message="не удалось разобрать JSON pyright",
                severity="error",
            )
        )
        outcome.issues += 1
        return outcome, diagnostics

    items = data.get("generalDiagnostics") or []
    for item in items:
        rel = _normalize_path(str(item.get("file", "")))
        if _is_excluded_file(rel):
            continue
        range_data = item.get("range") or {}
        start = range_data.get("start") or {}
        diagnostics.append(
            Diagnostic(
                target=spec.name,
                source="pyright",
                file=rel,
                line=int(start.get("line", 0) or 0) + 1,
                code=str(item.get("rule") or "unknown"),
                message=str(item.get("message", "")).strip(),
                severity=str(item.get("severity") or "error"),
            )
        )
        outcome.issues += 1
    return outcome, diagnostics


# ─── Проверки: bandit / vulture / radon ─────────────────────────────


def run_bandit(spec: TargetSpec) -> tuple[CheckOutcome, list[Diagnostic]]:
    outcome = CheckOutcome(target=spec.name, check="bandit")
    diagnostics: list[Diagnostic] = []
    targets = [
        p.relative_to(REPO_ROOT).as_posix()
        for p in spec.src_paths()
        if p.exists()
    ]
    if not targets:
        return outcome, diagnostics

    cmd = ["bandit", "-r", *targets, "-f", "json", "-q"]
    started = time.perf_counter()
    result = _qa_run(cmd)
    duration = int((time.perf_counter() - started) * 1000)
    _record_run(
        outcome=outcome, step="scan", cmd=cmd, result=result, duration_ms=duration
    )

    if result.returncode not in (0, 1):
        diagnostics.append(
            Diagnostic(
                target=spec.name,
                source="bandit",
                file=spec.rel_root,
                line=0,
                code="BANDIT_FATAL",
                message=(result.stderr or result.stdout or "bandit упал").strip(),
                severity="error",
            )
        )
        outcome.issues += 1
        return outcome, diagnostics

    data = _safe_json(result.stdout or "")
    if not isinstance(data, dict):
        diagnostics.append(
            Diagnostic(
                target=spec.name,
                source="bandit",
                file=spec.rel_root,
                line=0,
                code="BANDIT_FATAL",
                message=(
                    result.stderr or result.stdout or "bandit не вернул корректный JSON"
                ).strip(),
                severity="error",
            )
        )
        outcome.issues += 1
        return outcome, diagnostics

    baseline_path = spec.root / ".bandit-baseline.json"
    accepted_findings = _load_bandit_baseline(baseline_path)
    if baseline_path.is_file() and accepted_findings is None:
        diagnostics.append(
            Diagnostic(
                target=spec.name,
                source="bandit",
                file=spec.rel_root,
                line=0,
                code="BANDIT_BASELINE_ERROR",
                message=f"некорректный baseline Bandit: {baseline_path}",
                severity="error",
            )
        )
        outcome.issues += 1
        return outcome, diagnostics

    # Файл, который bandit не смог разобрать, выпадает из скана: это дыра
    # покрытия безопасности, а не чистый результат — фиксируем как FATAL.
    for scan_error in data.get("errors") or []:
        error_info = scan_error if isinstance(scan_error, dict) else {}
        diagnostics.append(
            Diagnostic(
                target=spec.name,
                source="bandit",
                file=_normalize_path(str(error_info.get("filename", spec.rel_root))),
                line=0,
                code="BANDIT_SCAN_ERROR",
                message=str(
                    error_info.get("reason") or scan_error or "файл не просканирован"
                ).strip(),
                severity="error",
            )
        )
        outcome.issues += 1

    for item in data.get("results", []):
        if accepted_findings and _bandit_finding_key(item) in accepted_findings:
            continue
        rel = _normalize_path(str(item.get("filename", "")))
        if _is_excluded_file(rel):
            continue
        diagnostics.append(
            Diagnostic(
                target=spec.name,
                source="bandit",
                file=rel,
                line=int(item.get("line_number", 0) or 0),
                code=str(item.get("test_id", "SEC")),
                message=str(item.get("issue_text", "")).strip(),
                severity="warning",
            )
        )
        outcome.issues += 1
    return outcome, diagnostics


def _load_bandit_baseline(path: Path) -> set[tuple[str, str, str, str]] | None:
    """Загрузить точные проверенные находки Bandit для одной цели."""
    if not path.is_file():
        return set()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    accepted_results = data.get("accepted_results") if isinstance(data, dict) else None
    if not isinstance(accepted_results, list) or not all(
        isinstance(item, dict) for item in accepted_results
    ):
        return None
    return {_bandit_finding_key(item) for item in accepted_results}


def _bandit_finding_key(item: dict[str, Any]) -> tuple[str, str, str, str]:
    """Построить переносимый ключ конкретной находки Bandit."""
    filename = str(item.get("filename", "")).replace("\\", "/")
    code = "\n".join(
        re.sub(r"^\s*\d+\s+", "", line).rstrip()
        for line in str(item.get("code", "")).splitlines()
    ).strip()
    return (
        _normalize_path(filename),
        str(item.get("test_id", "")),
        str(item.get("issue_text", "")),
        code,
    )


def run_vulture(spec: TargetSpec) -> tuple[CheckOutcome, list[Diagnostic]]:
    outcome = CheckOutcome(target=spec.name, check="vulture")
    diagnostics: list[Diagnostic] = []
    targets = [str(p) for p in spec.src_paths() if p.exists()]
    if not targets:
        return outcome, diagnostics

    cmd = [
        "vulture",
        *targets,
        "--min-confidence",
        VULTURE_MIN_CONFIDENCE,
        "--ignore-names",
        VULTURE_IGNORE_NAMES,
    ]
    started = time.perf_counter()
    result = _qa_run(cmd)
    duration = int((time.perf_counter() - started) * 1000)
    _record_run(
        outcome=outcome, step="scan", cmd=cmd, result=result, duration_ms=duration
    )

    if result.returncode not in (0, 3):
        diagnostics.append(
            Diagnostic(
                target=spec.name,
                source="vulture",
                file=spec.rel_root,
                line=0,
                code="VULTURE_FATAL",
                message=(result.stderr or result.stdout or "vulture упал").strip(),
                severity="error",
            )
        )
        outcome.issues += 1
        return outcome, diagnostics

    malformed_lines: list[str] = []
    for line in (result.stdout or "").splitlines():
        parts = line.rsplit(":", 2)
        if len(parts) != 3:
            malformed_lines.append(line)
            continue
        rel = _normalize_path(parts[0])
        if _is_excluded_file(rel):
            continue
        try:
            line_no = int(parts[1])
        except ValueError:
            malformed_lines.append(line)
            continue
        diagnostics.append(
            Diagnostic(
                target=spec.name,
                source="vulture",
                file=rel,
                line=line_no,
                code="DEAD_CODE",
                message=parts[2].strip(),
                severity="warning",
            )
        )
        outcome.issues += 1
    if malformed_lines or (result.returncode == 3 and not diagnostics):
        details = (
            "\n".join(malformed_lines) or "vulture не вернул список найденного кода"
        )
        diagnostics.append(
            Diagnostic(
                target=spec.name,
                source="vulture",
                file=spec.rel_root,
                line=0,
                code="VULTURE_FATAL",
                message=details,
                severity="error",
            )
        )
        outcome.issues += 1
    return outcome, diagnostics


def run_radon(spec: TargetSpec) -> tuple[CheckOutcome, list[Diagnostic]]:
    outcome = CheckOutcome(target=spec.name, check="radon")
    diagnostics: list[Diagnostic] = []
    targets = [str(p) for p in spec.src_paths() if p.exists()]
    if not targets:
        return outcome, diagnostics

    cmd = ["radon", "cc", *targets, "-a", "--min", "C", "-j"]
    started = time.perf_counter()
    result = _qa_run(cmd)
    duration = int((time.perf_counter() - started) * 1000)
    _record_run(
        outcome=outcome, step="complexity", cmd=cmd, result=result, duration_ms=duration
    )

    if result.returncode != 0:
        diagnostics.append(
            Diagnostic(
                target=spec.name,
                source="radon",
                file=spec.rel_root,
                line=0,
                code="RADON_FATAL",
                message=(result.stderr or result.stdout or "radon упал").strip(),
                severity="error",
            )
        )
        outcome.issues += 1
        return outcome, diagnostics

    data = _safe_json(result.stdout or "")
    if not isinstance(data, dict):
        diagnostics.append(
            Diagnostic(
                target=spec.name,
                source="radon",
                file=spec.rel_root,
                line=0,
                code="RADON_FATAL",
                message=(
                    result.stderr or result.stdout or "radon не вернул корректный JSON"
                ).strip(),
                severity="error",
            )
        )
        outcome.issues += 1
        return outcome, diagnostics

    for file_name, blocks in data.items():
        rel = _normalize_path(str(file_name))
        if _is_excluded_file(rel):
            continue
        if not isinstance(blocks, list):
            # Radon кладёт {"error": ...} вместо списка блоков, когда файл
            # не разобран: ошибка анализа — не чистый файл, а дыра покрытия.
            error_text = (
                str(blocks.get("error", "")).strip()
                if isinstance(blocks, dict)
                else ""
            )
            diagnostics.append(
                Diagnostic(
                    target=spec.name,
                    source="radon",
                    file=rel,
                    line=0,
                    code="RADON_FILE_ERROR",
                    message=error_text or "radon не смог проанализировать файл",
                    severity="error",
                )
            )
            outcome.issues += 1
            continue
        for block in blocks:
            rank = str(block.get("rank", ""))
            if rank not in RADON_RANKS:
                continue
            diagnostics.append(
                Diagnostic(
                    target=spec.name,
                    source="radon",
                    file=rel,
                    line=int(block.get("lineno", 0) or 0),
                    code=f"COMPLEXITY_{rank}",
                    message=(
                        f"Function '{block.get('name', 'unknown')}' complexity is "
                        f"{block.get('complexity', '?')}"
                    ),
                    severity="warning",
                )
            )
            outcome.issues += 1
    return outcome, diagnostics


# ─── Проверки: imports (циклы) ──────────────────────────────────────


def run_imports(spec: TargetSpec) -> tuple[CheckOutcome, list[Diagnostic]]:
    """AST-сканер циклических импортов внутри одной цели."""
    outcome = CheckOutcome(target=spec.name, check="imports")
    diagnostics: list[Diagnostic] = []
    started = time.perf_counter()
    module_to_file, graph = _build_import_graph(spec, diagnostics, outcome)
    for component in _find_cycle_groups(graph):
        if len(component) == 1 and component[0] not in graph.get(component[0], set()):
            continue
        modules = sorted(component)
        cycle_group = ", ".join(modules)
        for module_name in modules:
            file_name = module_to_file.get(module_name, spec.rel_root)
            diagnostics.append(
                Diagnostic(
                    target=spec.name,
                    source="imports",
                    file=file_name,
                    line=1,
                    code="IMPORT_CYCLE",
                    message=f"Cyclic import group: {cycle_group}",
                    severity="error",
                )
            )
            outcome.issues += 1
    duration = int((time.perf_counter() - started) * 1000)
    outcome.duration_ms += duration
    return outcome, diagnostics


def _build_import_graph(
    spec: TargetSpec,
    diagnostics: list[Diagnostic],
    outcome: CheckOutcome,
) -> tuple[dict[str, str], dict[str, set[str]]]:
    files = _collect_python_files(spec.src_paths())
    module_to_file: dict[str, str] = {}
    for file_path in files:
        module_name = _module_name_from_path(spec, file_path)
        if not module_name:
            continue
        module_to_file[module_name] = _normalize_path(str(file_path))

    module_names = set(module_to_file)
    graph: dict[str, set[str]] = {name: set() for name in module_names}

    for module_name, rel_file in module_to_file.items():
        file_path = REPO_ROOT / rel_file
        try:
            tree = ast.parse(file_path.read_text(encoding="utf-8"), filename=rel_file)
        except SyntaxError as exc:
            diagnostics.append(
                Diagnostic(
                    target=spec.name,
                    source="imports",
                    file=rel_file,
                    line=int(exc.lineno or 0),
                    code="IMPORT_PARSE_ERROR",
                    message=str(exc.msg),
                    severity="error",
                )
            )
            outcome.issues += 1
            continue
        except OSError as exc:
            diagnostics.append(
                Diagnostic(
                    target=spec.name,
                    source="imports",
                    file=rel_file,
                    line=0,
                    code="IMPORT_READ_ERROR",
                    message=str(exc),
                    severity="error",
                )
            )
            outcome.issues += 1
            continue

        current_package = (
            module_name
            if rel_file.endswith("/__init__.py")
            else module_name.rpartition(".")[0]
        )

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    matched = _match_local_module(alias.name, module_names)
                    if matched:
                        graph[module_name].add(matched)
                continue
            if not isinstance(node, ast.ImportFrom):
                continue
            base = _resolve_import_base(
                current_package=current_package,
                module=node.module,
                level=node.level,
            )
            if base is None:
                continue
            matched_base = _match_local_module(base, module_names)
            if matched_base and node.module:
                graph[module_name].add(matched_base)
            for alias in node.names:
                if alias.name == "*":
                    continue
                candidate = f"{base}.{alias.name}" if base else alias.name
                matched = _match_local_module(candidate, module_names)
                if matched:
                    graph[module_name].add(matched)
    return module_to_file, graph


def _collect_python_files(roots: Sequence[Path]) -> list[Path]:
    files: set[Path] = set()
    for root in roots:
        if not root.exists():
            continue
        if root.is_file() and root.suffix == ".py":
            files.add(root.resolve())
            continue
        if root.is_dir():
            for file_path in root.rglob("*.py"):
                rel = _normalize_path(str(file_path))
                if _is_excluded_file(rel):
                    continue
                files.add(file_path.resolve())
    return sorted(files)


def _module_name_from_path(spec: TargetSpec, file_path: Path) -> str | None:
    """Имя модуля относительно `src/` сервиса."""
    src_root = spec.root / "src"
    try:
        rel = file_path.resolve().relative_to(src_root.resolve())
    except ValueError:
        return None
    name = rel.as_posix()
    if not name.endswith(".py"):
        return None
    name = name[:-3].replace("/", ".")
    if name.endswith(".__init__"):
        name = name[: -len(".__init__")]
    return name or None


def _resolve_import_base(
    *,
    current_package: str,
    module: str | None,
    level: int,
) -> str | None:
    if level <= 0:
        return module or ""
    if not current_package:
        return None
    parts = current_package.split(".")
    drop = level - 1
    if drop >= len(parts):
        return None
    base = ".".join(parts[: len(parts) - drop])
    if module:
        return f"{base}.{module}" if base else module
    return base


def _match_local_module(module_name: str, known: set[str]) -> str | None:
    candidate = module_name.strip(".")
    while candidate:
        if candidate in known:
            return candidate
        if "." not in candidate:
            return None
        candidate = candidate.rpartition(".")[0]
    return None


def _find_cycle_groups(graph: dict[str, set[str]]) -> list[list[str]]:
    """Tarjan strongly-connected components."""
    index = 0
    indices: dict[str, int] = {}
    lowlinks: dict[str, int] = {}
    stack: list[str] = []
    on_stack: set[str] = set()
    components: list[list[str]] = []

    def _strongconnect(node: str) -> None:
        nonlocal index
        indices[node] = index
        lowlinks[node] = index
        index += 1
        stack.append(node)
        on_stack.add(node)
        for neighbour in graph.get(node, set()):
            if neighbour not in indices:
                _strongconnect(neighbour)
                lowlinks[node] = min(lowlinks[node], lowlinks[neighbour])
                continue
            if neighbour in on_stack:
                lowlinks[node] = min(lowlinks[node], indices[neighbour])
        if lowlinks[node] != indices[node]:
            return
        component: list[str] = []
        while stack:
            current = stack.pop()
            on_stack.discard(current)
            component.append(current)
            if current == node:
                break
        components.append(component)

    for node in sorted(graph):
        if node not in indices:
            _strongconnect(node)
    return components


# ─── Тесты (тонкая обёртка над per-service pytest) ──────────────────


@dataclass(slots=True)
class TestResult:
    target: str
    returncode: int
    duration_ms: int
    skipped: bool = False
    skip_reason: str = ""


def run_pytest(spec: TargetSpec) -> TestResult:
    if not spec.has_tests or spec.kind != "service":
        return TestResult(
            target=spec.name,
            returncode=0,
            duration_ms=0,
            skipped=True,
            skip_reason="нет каталога tests/",
        )
    test_dirs = [str((spec.root / d).relative_to(spec.root)) for d in spec.test_dirs]
    args = ["pytest", *test_dirs]
    started = time.perf_counter()
    print(f"\n── pytest: {spec.name} ────────────────────────────────")
    sys.stdout.flush()
    result = _service_run(spec, args)
    duration = int((time.perf_counter() - started) * 1000)
    if result.stdout:
        print(result.stdout, end="" if result.stdout.endswith("\n") else "\n")
    if result.stderr:
        print(
            result.stderr,
            end="" if result.stderr.endswith("\n") else "\n",
            file=sys.stderr,
        )
    return TestResult(
        target=spec.name, returncode=result.returncode, duration_ms=duration
    )


# ─── Сессия и оркестрация ───────────────────────────────────────────


@dataclass(slots=True)
class QaSession:
    targets: list[TargetSpec]
    diagnostics: list[Diagnostic] = field(default_factory=list)
    outcomes: list[CheckOutcome] = field(default_factory=list)
    test_results: list[TestResult] = field(default_factory=list)
    ran_checks: list[str] = field(default_factory=list)

    def add(self, outcome: CheckOutcome, diagnostics: list[Diagnostic]) -> None:
        self.outcomes.append(outcome)
        self.diagnostics.extend(diagnostics)
        if outcome.check not in self.ran_checks:
            self.ran_checks.append(outcome.check)

    @property
    def has_errors(self) -> bool:
        if any(diag.severity == "error" for diag in self.diagnostics):
            return True
        return any(
            result.returncode != 0 and not result.skipped
            for result in self.test_results
        )


CHECK_DISPATCH = {
    "ruff": lambda spec, opts: run_ruff(spec, fix=opts.get("fix", False)),
    "pyright": lambda spec, _opts: run_pyright(spec),
    "bandit": lambda spec, _opts: run_bandit(spec),
    "vulture": lambda spec, _opts: run_vulture(spec),
    "radon": lambda spec, _opts: run_radon(spec),
    "imports": lambda spec, _opts: run_imports(spec),
}

COMMAND_CHECKS: dict[str, tuple[str, ...]] = {
    "default": ("imports", "ruff", "pyright"),
    "lint": ("ruff",),
    "types": ("pyright",),
    "sec": ("bandit",),
    "all": ("imports", "ruff", "pyright", "bandit", "vulture", "radon"),
}


def execute(
    *,
    session: QaSession,
    command: str,
    options: dict[str, Any],
) -> None:
    if command == "test":
        for spec in session.targets:
            session.test_results.append(run_pytest(spec))
        return

    checks = COMMAND_CHECKS.get(command)
    if checks is None:
        raise QaError(f"Неизвестная команда: {command}")
    for check in checks:
        runner = CHECK_DISPATCH[check]
        for spec in session.targets:
            outcome, diagnostics = runner(spec, options)
            session.add(outcome, diagnostics)


# ─── Отчёт ──────────────────────────────────────────────────────────


def _diagnostic_sort_key(diag: Diagnostic) -> tuple[str, str, int, str, str]:
    return (diag.target, diag.file, diag.line, diag.source, diag.code)


def _compact_message(message: str) -> str:
    return " ".join(message.split())


def build_report(session: QaSession, *, command: str) -> dict[str, Any]:
    issues_by_source: defaultdict[str, int] = defaultdict(int)
    issues_by_target: defaultdict[str, int] = defaultdict(int)
    issues_by_file: defaultdict[str, int] = defaultdict(int)
    diagnostics_payload: list[dict[str, Any]] = []
    compact_lines: list[str] = []
    errors = 0
    warnings = 0

    sorted_diags = sorted(session.diagnostics, key=_diagnostic_sort_key)
    for index, diag in enumerate(sorted_diags, start=1):
        diag_id = f"I{index:04d}"
        diagnostics_payload.append(
            {
                "id": diag_id,
                "target": diag.target,
                "source": diag.source,
                "file": diag.file,
                "line": diag.line,
                "code": diag.code,
                "message": diag.message,
                "severity": diag.severity,
            }
        )
        compact_lines.append(
            f"{diag_id}|{diag.severity}|{diag.target}|{diag.source}|{diag.code}|"
            f"{diag.file}:{diag.line}|{_compact_message(diag.message)}"
        )
        issues_by_source[diag.source] += 1
        issues_by_target[diag.target] += 1
        issues_by_file[diag.file] += 1
        if diag.severity == "error":
            errors += 1
        elif diag.severity == "warning":
            warnings += 1

    checks_payload: list[dict[str, Any]] = []
    for outcome in session.outcomes:
        checks_payload.append(
            {
                "target": outcome.target,
                "name": outcome.check,
                "issues": outcome.issues,
                "status": "ok" if outcome.issues == 0 else "issues",
                "duration_ms": outcome.duration_ms,
                "runs": [
                    {
                        "step": run.step,
                        "command": run.command,
                        "returncode": run.returncode,
                        "duration_ms": run.duration_ms,
                        "stdout_len": run.stdout_len,
                        "stderr_len": run.stderr_len,
                        **(
                            {"stdout_excerpt": run.stdout_excerpt}
                            if run.stdout_excerpt
                            else {}
                        ),
                        **(
                            {"stderr_excerpt": run.stderr_excerpt}
                            if run.stderr_excerpt
                            else {}
                        ),
                    }
                    for run in outcome.runs
                ],
            }
        )

    test_payload = [
        {
            "target": result.target,
            "returncode": result.returncode,
            "duration_ms": result.duration_ms,
            "skipped": result.skipped,
            "skip_reason": result.skip_reason,
        }
        for result in session.test_results
    ]

    summary = {
        "command": command,
        "targets": [spec.name for spec in session.targets],
        "total_issues": len(diagnostics_payload),
        "errors": errors,
        "warnings": warnings,
        "issues_by_source": dict(sorted(issues_by_source.items())),
        "issues_by_target": dict(sorted(issues_by_target.items())),
        "tests_failed": sum(
            1
            for result in session.test_results
            if result.returncode != 0 and not result.skipped
        ),
        "tests_passed": sum(
            1
            for result in session.test_results
            if result.returncode == 0 and not result.skipped
        ),
        "tests_skipped": sum(1 for result in session.test_results if result.skipped),
    }
    top_files = [
        {"file": file_name, "issues": count}
        for file_name, count in sorted(
            issues_by_file.items(), key=lambda item: (-item[1], item[0])
        )[:10]
    ]
    return {
        "meta": {
            "generated_at_utc": datetime.now(UTC).isoformat(),
            "repo_root": str(REPO_ROOT),
            "command": command,
        },
        "summary": summary,
        "checks": checks_payload,
        "tests": test_payload,
        "top_files": top_files,
        "diagnostics": diagnostics_payload,
        "compact_context": {
            "summary": (
                f"summary|command={command}|"
                f"targets={len(session.targets)}|"
                f"issues={summary['total_issues']}|"
                f"errors={summary['errors']}|"
                f"warnings={summary['warnings']}"
            ),
            "issues": compact_lines,
        },
    }


def print_report(report: dict[str, Any], *, context_mode: bool) -> None:
    summary = report["summary"]
    targets = ", ".join(summary["targets"]) if summary["targets"] else "(нет целей)"
    print("=" * 64)
    print(
        f"REPORT | command={summary['command']} | "
        f"issues={summary['total_issues']} | "
        f"errors={summary['errors']} | warnings={summary['warnings']}"
    )
    print(f"targets: {targets}")
    if summary["issues_by_source"]:
        print("issues by source:")
        for name, count in summary["issues_by_source"].items():
            print(f"  - {name}: {count}")
    if summary["issues_by_target"]:
        print("issues by target:")
        for name, count in summary["issues_by_target"].items():
            print(f"  - {name}: {count}")
    if report["tests"]:
        print(
            "tests: "
            f"passed={summary['tests_passed']} "
            f"failed={summary['tests_failed']} "
            f"skipped={summary['tests_skipped']}"
        )
    print("=" * 64)
    if context_mode:
        print("<analysis_context>")
        print(report["compact_context"]["summary"])
        for line in report["compact_context"]["issues"]:
            print(line)
        print("</analysis_context>")
        return
    if report["top_files"]:
        print("Top files with issues:")
        for item in report["top_files"]:
            print(f"  {item['issues']:4d} | {item['file']}")


def write_report(report: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )


# ─── CLI ────────────────────────────────────────────────────────────


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="dev_code_fix",
        description="Монорепный QA-раннер BestFiend.",
    )
    parser.add_argument(
        "command",
        nargs="?",
        default="default",
        choices=("default", "lint", "types", "sec", "all", "test"),
        help="Какой набор проверок гонять (по умолчанию: imports+ruff+pyright).",
    )
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument(
        "--all",
        dest="select_all",
        action="store_true",
        help="Все цели монорепо.",
    )
    selection.add_argument(
        "--service",
        dest="services",
        action="append",
        default=[],
        metavar="NAME",
        help="Конкретная цель (можно несколько раз).",
    )
    selection.add_argument(
        "--changed",
        dest="changed",
        action="store_true",
        help="Только цели, затронутые `git status` (поведение по умолчанию).",
    )
    parser.add_argument(
        "--from-files",
        dest="from_files",
        nargs="*",
        default=None,
        metavar="FILE",
        help="Список файлов от pre-commit; цели выводятся из путей.",
    )
    parser.add_argument(
        "--fix",
        action="store_true",
        help="Включить ruff --fix и ruff format.",
    )
    parser.add_argument(
        "--context",
        action="store_true",
        help="Дополнительный компактный <analysis_context> в stdout.",
    )
    parser.add_argument(
        "--report-json",
        default="",
        metavar="PATH",
        help="Куда писать полный JSON-отчёт. По умолчанию tools/qa/.cache/report.json.",
    )
    return parser


def _select_targets(args: argparse.Namespace) -> list[TargetSpec]:
    if args.from_files is not None:
        files = [Path(item) for item in args.from_files]
        return resolve_targets_from_changed(files)
    if args.services:
        return _ordered_targets(resolve_target_by_name(name) for name in args.services)
    if args.select_all:
        return resolve_targets_all()
    return resolve_targets_from_git_diff()


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        targets = _select_targets(args)
    except QaError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    if not targets:
        print("Нет целей для проверки — пропускаю.")
        return 0

    session = QaSession(targets=targets)
    options: dict[str, Any] = {"fix": args.fix}
    try:
        execute(session=session, command=args.command, options=options)
    except QaError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    report = build_report(session, command=args.command)
    print_report(report, context_mode=args.context)
    report_path = Path(args.report_json) if args.report_json else DEFAULT_REPORT_PATH
    if not report_path.is_absolute():
        report_path = REPO_ROOT / report_path
    write_report(report, report_path)
    return 1 if session.has_errors else 0


if __name__ == "__main__":
    sys.exit(main())
