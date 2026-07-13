import json
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
PYTHON = sys.executable
SCRIPTS_DIR = Path(PYTHON).resolve().parent


def _resolve_console_script(command_name: str) -> Path:
    candidates = [
        SCRIPTS_DIR / command_name,
        SCRIPTS_DIR / f"{command_name}.exe",
        SCRIPTS_DIR / f"{command_name}.cmd",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    raise FileNotFoundError(f"Console script not found for {command_name}: {candidates}")


def _send_interrupt(process: subprocess.Popen) -> None:
    """Send the platform equivalent of Ctrl+C to a subprocess."""
    if sys.platform == "win32":
        process.send_signal(signal.CTRL_BREAK_EVENT)
    else:
        process.send_signal(signal.SIGINT)


@pytest.fixture
def run_cli():
    def _run_cli(command_name, args=None, cwd=REPO_ROOT, env=None):
        merged_env = os.environ.copy()
        if env:
            merged_env.update(env)
        command = [_resolve_console_script(command_name)]
        if args:
            command.extend(args)
        return subprocess.run(
            command,
            cwd=cwd,
            env=merged_env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )

    return _run_cli


@pytest.fixture
def start_cli():
    def _start_cli(command_name, args=None, cwd=REPO_ROOT, env=None, **popen_kwargs):
        merged_env = os.environ.copy()
        if env:
            merged_env.update(env)
        command = [_resolve_console_script(command_name)]
        if args:
            command.extend(args)
        kwargs = dict(
            cwd=cwd,
            env=merged_env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if sys.platform == "win32":
            kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        kwargs.update(popen_kwargs)
        return subprocess.Popen(command, **kwargs)

    return _start_cli


@pytest.fixture
def send_interrupt():
    return _send_interrupt


def _git(args, cwd):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


def _write_fake_exercise(track_root: Path, language: str, exercise: str):
    exercise_root = track_root / language / "exercises" / "practice" / exercise
    (exercise_root / ".docs").mkdir(parents=True, exist_ok=True)
    (exercise_root / ".meta").mkdir(parents=True, exist_ok=True)
    (exercise_root / "example.py").write_text("def answer():\n    return 1\n", encoding="utf-8")
    (exercise_root / ".docs" / "instructions.md").write_text(
        f"Update example.py for {exercise}.\n",
        encoding="utf-8",
    )
    (exercise_root / ".meta" / "config.json").write_text(
        json.dumps(
            {
                "files": {
                    "solution": ["example.py"],
                    "test": ["example_test.py"],
                    "example": [],
                }
            }
        ),
        encoding="utf-8",
    )


@pytest.fixture
def init_track_repo():
    def _init_track_repo(parent_dir: Path, language: str, exercises):
        if shutil.which("git") is None:
            pytest.skip("git not available")

        repo_dir = parent_dir / language
        repo_dir.mkdir(parents=True, exist_ok=True)
        _git(["init"], cwd=repo_dir)
        _git(["config", "user.email", "test@example.com"], cwd=repo_dir)
        _git(["config", "user.name", "Test User"], cwd=repo_dir)

        for exercise in exercises:
            _write_fake_exercise(parent_dir, language, exercise)

        _git(["add", "."], cwd=repo_dir)
        _git(["commit", "-m", "initial track"], cwd=repo_dir)
        return repo_dir

    return _init_track_repo


@pytest.fixture
def add_exercise_and_commit():
    def _add_exercise_and_commit(parent_dir: Path, language: str, exercise: str, message: str):
        repo_dir = parent_dir / language
        _write_fake_exercise(parent_dir, language, exercise)
        _git(["add", "."], cwd=repo_dir)
        _git(["commit", "-m", message], cwd=repo_dir)

    return _add_exercise_and_commit


@pytest.fixture
def write_result_file():
    def _write_result_file(run_dir: Path, language: str, exercise: str, outcomes, model="github/gpt-4.1"):
        exercise_dir = run_dir / language / "exercises" / "practice" / exercise
        exercise_dir.mkdir(parents=True, exist_ok=True)
        (exercise_dir / ".aider.results.json").write_text(
            json.dumps(
                {
                    "testdir": str(exercise_dir),
                    "testcase": exercise,
                    "model": model,
                    "edit_format": "diff",
                    "tests_outcomes": outcomes,
                    "cost": 0.01,
                    "duration": 1.0,
                    "test_timeouts": 0,
                    "commit_hash": "abc1234",
                    "num_error_outputs": 0,
                    "num_user_asks": 0,
                    "num_exhausted_context_windows": 0,
                    "num_malformed_responses": 0,
                    "syntax_errors": 0,
                    "indentation_errors": 0,
                    "lazy_comments": 0,
                    "reasoning_effort": None,
                    "prompt_tokens": 10,
                    "completion_tokens": 5,
                    "thinking_tokens": None,
                    "chat_hashes": [],
                }
            ),
            encoding="utf-8",
        )

    return _write_result_file