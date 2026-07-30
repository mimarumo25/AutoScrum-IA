"""Subprocesos Git acotados para que un hook o prompt no congele la corrida."""
import os
import subprocess
import tomllib
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


@lru_cache(maxsize=1)
def _runtime() -> dict[str, object]:
    config = tomllib.loads((ROOT / "pipeline.toml").read_text(encoding="utf-8"))
    return dict(config["runtime"])


def timeout_seconds(name: str) -> float:
    value = float(_runtime()[name])
    if value <= 0:
        raise ValueError(f"runtime.{name} debe ser mayor que cero")
    return value


def output_text(value: object) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value or "")


def _output(value: object, text: bool) -> str | bytes:
    rendered = output_text(value)
    return rendered if text else rendered.encode("utf-8")


def run_bounded(command: list[str], *, cwd: str | Path | None = None,
                env: dict[str, str] | None = None,
                timeout_seconds_value: float) -> tuple[subprocess.CompletedProcess, bool]:
    try:
        return subprocess.run(
            command, cwd=cwd, capture_output=True, text=True, env=env,
            timeout=timeout_seconds_value), False
    except subprocess.TimeoutExpired as error:
        return subprocess.CompletedProcess(
            command, 124, stdout=output_text(error.stdout),
            stderr=output_text(error.stderr)), True


def run_git(repo: str | Path, *args: str, data: bytes | None = None,
            text: bool = False, check: bool = False) -> subprocess.CompletedProcess:
    command = ["git", "-C", str(repo), *args]
    env = os.environ.copy()
    env.update(GIT_TERMINAL_PROMPT="0", GIT_EDITOR="true",
               GIT_MERGE_AUTOEDIT="no")
    try:
        result = subprocess.run(
            command, input=data, capture_output=True, text=text, env=env,
            timeout=timeout_seconds("git_timeout_seconds"))
    except subprocess.TimeoutExpired as error:
        suffix = "git excedio el tiempo configurado" if text else \
            b"git excedio el tiempo configurado"
        stdout = _output(error.stdout, text)
        stderr = _output(error.stderr, text)
        if stderr:
            stderr += "\n" if text else b"\n"
        result = subprocess.CompletedProcess(
            command, 124, stdout=stdout, stderr=stderr + suffix)
    if check and result.returncode != 0:
        raise subprocess.CalledProcessError(
            result.returncode, command, output=result.stdout, stderr=result.stderr)
    return result
