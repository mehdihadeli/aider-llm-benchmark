import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

from aider_polyglot_benchmark.leaderboard_report import load_benchmark_report, load_leaderboard_yaml


def test_benchmark_help_command_runs(run_cli):
    result = run_cli("benchmark", ["--help"])

    assert result.returncode == 0
    assert "Run benchmark, stats, diffs, or cleanup" in result.stdout
    assert "--languages" in result.stdout


def test_benchmark_requires_languages_in_real_command(run_cli):
    result = run_cli("benchmark", ["sample-run", "--model", "github/gpt-4.1", "--unsafe"])

    assert result.returncode == 1
    assert "--languages is required when running benchmarks" in result.stdout


def test_benchmark_rejects_invalid_report_mode(run_cli, tmp_path):
    benchmark_root = tmp_path / "tmp.benchmarks"

    result = run_cli(
        "benchmark",
        [
            "invalid-report-mode",
            "--model",
            "github/gpt-4.1",
            "--report-mode",
            "foo",
            "--languages",
            "csharp",
            "--unsafe",
            "--no-aider",
            "--no-unit-tests",
        ],
        env={"AIDER_BENCHMARK_DIR": str(benchmark_root)},
    )

    assert result.returncode == 1
    assert "Unsupported report mode: foo" in result.stdout


def test_benchmark_report_stats_diffs_and_purge_commands_run(tmp_path, run_cli, write_result_file):
    benchmark_root = tmp_path / "tmp.benchmarks"
    run_a = benchmark_root / "2026-07-05-12-00-00--run-a"
    run_b = benchmark_root / "2026-07-05-12-00-01--run-b"
    write_result_file(run_a, "csharp", "alpha", [True])
    write_result_file(run_b, "csharp", "alpha", [False, True])
    (benchmark_root / "docker-run-2026-07-14.log").write_text("docker log", encoding="utf-8")

    env = {"AIDER_BENCHMARK_DIR": str(benchmark_root)}

    report = run_cli("benchmark", ["--report"], env=env)
    assert report.returncode == 0, report.stdout + report.stderr
    assert (benchmark_root / "leaderboard.md").exists()
    assert (benchmark_root / "leaderboard.html").exists()
    assert (benchmark_root / "polyglot_leaderboard.yml").exists()

    stats = run_cli("benchmark", [str(run_a), "--stats"], env=env)
    assert stats.returncode == 0, stats.stdout + stats.stderr

    diffs = run_cli("benchmark", [str(run_a), str(run_b), "--diffs"], env=env)
    assert diffs.returncode == 0, diffs.stdout + diffs.stderr
    assert "changed:" in diffs.stdout or "unchanged:" in diffs.stdout

    purge = run_cli("benchmark", ["--purge"], env=env)
    assert purge.returncode == 0, purge.stdout + purge.stderr
    assert not benchmark_root.exists()


def test_benchmark_command_runs_real_offline_smoke_with_auto_clone(tmp_path, run_cli, init_track_repo):
    source_parent = tmp_path / "source"
    benchmark_root = tmp_path / "tmp.benchmarks"
    tracks_root = tmp_path / "tracks"
    init_track_repo(source_parent, "csharp", ["alpha", "beta"])

    env = {
        "AIDER_BENCHMARK_DIR": str(benchmark_root),
        "EXERCISM_TRACK_REPO_BASE_URL": source_parent.as_uri(),
    }
    result = run_cli(
        "benchmark",
        [
            "offline-smoke",
            "--model",
            "github/gpt-4.1",
            "--languages",
            "csharp",
            "--keywords",
            "alpha",
            "--num-tests",
            "1",
            "--exercises-dir",
            str(tracks_root),
            "--unsafe",
            "--no-aider",
            "--no-unit-tests",
        ],
        env=env,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "Cloning missing Exercism tracks: csharp" in result.stdout

    run_dirs = [path for path in benchmark_root.iterdir() if path.is_dir()]
    assert len(run_dirs) == 1

    run_dir = run_dirs[0]
    assert (run_dir / "csharp" / "exercises" / "practice" / "alpha" / ".aider.results.json").exists()
    assert not (run_dir / "csharp" / "exercises" / "practice" / "beta").exists()


def test_benchmark_multi_model_offline_smoke_generates_per_model_dirs_and_aggregates(
    tmp_path, run_cli, init_track_repo
):
    source_parent = tmp_path / "source"
    benchmark_root = tmp_path / "tmp.benchmarks"
    tracks_root = tmp_path / "tracks"
    init_track_repo(source_parent, "csharp", ["alpha"])

    env = {
        "AIDER_BENCHMARK_DIR": str(benchmark_root),
        "EXERCISM_TRACK_REPO_BASE_URL": source_parent.as_uri(),
    }
    result = run_cli(
        "benchmark",
        [
            "multi-model-smoke",
            "--model",
            "github/gpt-4o",
            "--model",
            "github/gpt-4.1",
            "--languages",
            "csharp",
            "--num-tests",
            "1",
            "--exercises-dir",
            str(tracks_root),
            "--unsafe",
            "--no-aider",
            "--no-unit-tests",
        ],
        env=env,
    )

    assert result.returncode == 0, result.stdout + result.stderr

    run_dirs = sorted(path for path in benchmark_root.iterdir() if path.is_dir())
    assert len(run_dirs) == 2
    assert any("gpt-4o" in d.name for d in run_dirs)
    assert any("gpt-4.1" in d.name for d in run_dirs)

    for run_dir in run_dirs:
        assert (run_dir / "csharp" / "exercises" / "practice" / "alpha" / ".aider.results.json").exists()

    assert (benchmark_root / "leaderboard.md").exists()
    assert (benchmark_root / "leaderboard.html").exists()
    assert (benchmark_root / "polyglot_leaderboard.yml").exists()


def test_benchmark_separate_model_runs_then_aggregate_report(
    tmp_path, run_cli, init_track_repo
):
    source_parent = tmp_path / "source"
    benchmark_root = tmp_path / "tmp.benchmarks"
    tracks_root = tmp_path / "tracks"
    init_track_repo(source_parent, "csharp", ["alpha"])

    env = {
        "AIDER_BENCHMARK_DIR": str(benchmark_root),
        "EXERCISM_TRACK_REPO_BASE_URL": source_parent.as_uri(),
    }

    first = run_cli(
        "benchmark",
        [
            "model-a-run",
            "--model",
            "github/gpt-4o",
            "--languages",
            "csharp",
            "--num-tests",
            "1",
            "--exercises-dir",
            str(tracks_root),
            "--unsafe",
            "--no-aider",
            "--no-unit-tests",
        ],
        env=env,
    )
    assert first.returncode == 0, first.stdout + first.stderr

    second = run_cli(
        "benchmark",
        [
            "model-b-run",
            "--model",
            "github/gpt-4.1",
            "--languages",
            "csharp",
            "--num-tests",
            "1",
            "--exercises-dir",
            str(tracks_root),
            "--unsafe",
            "--no-aider",
            "--no-unit-tests",
        ],
        env=env,
    )
    assert second.returncode == 0, second.stdout + second.stderr

    report = run_cli("benchmark", ["--report"], env=env)
    assert report.returncode == 0, report.stdout + report.stderr

    assert (benchmark_root / "leaderboard.md").exists()
    assert (benchmark_root / "leaderboard.html").exists()
    assert (benchmark_root / "polyglot_leaderboard.yml").exists()

    markdown_text = (benchmark_root / "leaderboard.md").read_text(encoding="utf-8")
    assert "github/gpt-4o" in markdown_text
    assert "github/gpt-4.1" in markdown_text


def test_benchmark_num_tests_limits_exercises(tmp_path, run_cli, init_track_repo):
    source_parent = tmp_path / "source"
    benchmark_root = tmp_path / "tmp.benchmarks"
    tracks_root = tmp_path / "tracks"
    init_track_repo(source_parent, "csharp", ["one", "two", "three"])

    env = {
        "AIDER_BENCHMARK_DIR": str(benchmark_root),
        "EXERCISM_TRACK_REPO_BASE_URL": source_parent.as_uri(),
    }
    result = run_cli(
        "benchmark",
        [
            "num-tests-run",
            "--model",
            "github/gpt-4o",
            "--languages",
            "csharp",
            "--num-tests",
            "2",
            "--exercises-dir",
            str(tracks_root),
            "--unsafe",
            "--no-aider",
            "--no-unit-tests",
        ],
        env=env,
    )

    assert result.returncode == 0, result.stdout + result.stderr

    run_dirs = [path for path in benchmark_root.iterdir() if path.is_dir()]
    assert len(run_dirs) == 1

    result_files = list(run_dirs[0].glob("csharp/exercises/practice/*/.aider.results.json"))
    assert len(result_files) == 2


def test_benchmark_model_parallelism_and_rate_limit_retry_simulate_real_world(
    tmp_path, init_track_repo, run_cli
):
    source_parent = tmp_path / "source"
    benchmark_root = tmp_path / "tmp.benchmarks"
    tracks_root = source_parent
    hook_root = tmp_path / "hook"
    hook_root.mkdir()
    log_path = tmp_path / "fake-coder-log.jsonl"
    state_dir = tmp_path / "fake-coder-state"
    state_dir.mkdir()

    init_track_repo(source_parent, "csharp", ["alpha"])

    sitecustomize_path = hook_root / "sitecustomize.py"
    repo_src = Path(__file__).resolve().parents[2] / "src"

    sitecustomize_path.write_text(
        """
import json
import os
import threading
import time
from pathlib import Path

import aider_polyglot_benchmark.benchmark as benchmark


LOG_PATH = Path(os.environ[\"AIDER_BENCHMARK_TEST_LOG\"])
STATE_DIR = Path(os.environ[\"AIDER_BENCHMARK_TEST_STATE_DIR\"])
LOCK = threading.Lock()
ACTIVE_BY_PROVIDER = {}
CALLS_BY_MODEL = {}


def _append_log(payload):
    with LOG_PATH.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload) + "\\n")


class FakeCoder:
    def __init__(self, main_model, ignore_mentions=None):
        self.main_model = main_model
        self.ignore_mentions = ignore_mentions or set()
        self.last_keyboard_interrupt = False
        self.total_cost = 0.0
        self.num_exhausted_context_windows = 0
        self.num_malformed_responses = 0
        self.total_tokens_sent = 10
        self.total_tokens_received = 5
        self.chat_completion_call_hashes = []
        self.chat_completion_response_hashes = []

    def show_announcements(self):
        return None

    def run(self, with_message=None, preproc=False):
        model_name = self.main_model.name
        provider = model_name.split("/", 1)[0]
        marker = STATE_DIR / f"{provider}.rate_limit_once"

        with LOCK:
            active_now = ACTIVE_BY_PROVIDER.get(provider, 0) + 1
            ACTIVE_BY_PROVIDER[provider] = active_now
            attempt = CALLS_BY_MODEL.get(model_name, 0) + 1
            CALLS_BY_MODEL[model_name] = attempt

        started = time.monotonic()
        time.sleep(0.25)
        should_rate_limit = provider == "github" and not marker.exists()
        if should_rate_limit:
            marker.write_text("1", encoding="utf-8")

        finished = time.monotonic()
        with LOCK:
            ACTIVE_BY_PROVIDER[provider] -= 1

        _append_log(
            {
                "provider": provider,
                "model": model_name,
                "attempt": attempt,
                "active_at_entry": active_now,
                "started": started,
                "finished": finished,
                "rate_limited": should_rate_limit,
            }
        )

        if should_rate_limit:
            raise RuntimeError("429 Too Many Requests retry-after: 0")

        return "updated"


def fake_create(main_model, edit_format, io, **kwargs):
    return FakeCoder(main_model, kwargs.get("ignore_mentions"))


benchmark.Coder.create = staticmethod(fake_create)
""".strip(),
        encoding="utf-8",
    )

    env = {
        "AIDER_BENCHMARK_DIR": str(benchmark_root),
        "AIDER_BENCHMARK_TEST_LOG": str(log_path),
        "AIDER_BENCHMARK_TEST_STATE_DIR": str(state_dir),
        "PYTHONIOENCODING": "utf-8",
        "PYTHONPATH": str(hook_root) + os.pathsep + str(repo_src) + os.pathsep + os.environ.get("PYTHONPATH", ""),
    }
    result = run_cli(
        "benchmark",
        [
            "real-world-sim",
            "--model",
            "github/gpt-4o",
            "--model",
            "github/gpt-4.1",
            "--model",
            "openai/gpt-4o",
            "--model-parallelism",
            "3",
            "--max-llm-concurrency",
            "1",
            "--rate-limit-retries",
            "2",
            "--rate-limit-backoff-initial",
            "0",
            "--rate-limit-backoff-max",
            "0",
            "--languages",
            "csharp",
            "--num-tests",
            "1",
            "--exercises-dir",
            str(tracks_root),
            "--unsafe",
            "--no-unit-tests",
        ],
        cwd=tmp_path,
        env=env,
    )

    assert result.returncode == 0, result.stdout + result.stderr

    run_dirs = sorted(path for path in benchmark_root.iterdir() if path.is_dir())
    assert len(run_dirs) == 3
    for run_dir in run_dirs:
        assert (run_dir / "csharp" / "exercises" / "practice" / "alpha" / ".aider.results.json").exists()

    log_entries = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]
    github_entries = [entry for entry in log_entries if entry["provider"] == "github"]
    openai_entries = [entry for entry in log_entries if entry["provider"] == "openai"]

    assert len(github_entries) == 3
    assert len(openai_entries) == 1
    assert max(entry["active_at_entry"] for entry in github_entries) == 1
    assert any(entry["rate_limited"] for entry in github_entries)

    assert any(
        min(github_entry["finished"], openai_entry["finished"])
        > max(github_entry["started"], openai_entry["started"])
        for github_entry in github_entries
        for openai_entry in openai_entries
    )


def test_benchmark_threads_runs_exercise_cases_in_parallel_within_one_model(
    tmp_path, init_track_repo, run_cli
):
    source_parent = tmp_path / "source"
    benchmark_root = tmp_path / "tmp.benchmarks"
    tracks_root = source_parent
    hook_root = tmp_path / "hook"
    hook_root.mkdir()
    log_path = tmp_path / "threads-only-log.jsonl"

    init_track_repo(source_parent, "csharp", ["alpha", "beta"])

    sitecustomize_path = hook_root / "sitecustomize.py"
    repo_src = Path(__file__).resolve().parents[2] / "src"

    sitecustomize_path.write_text(
        """
import json
import os
import threading
import time
from pathlib import Path

import aider_polyglot_benchmark.benchmark as benchmark


LOG_PATH = Path(os.environ["AIDER_BENCHMARK_TEST_LOG"])
LOCK = threading.Lock()
ACTIVE_BY_MODEL = {}


def _append_log(payload):
    with LOCK:
        with LOG_PATH.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload) + "\\n")


class FakeCoder:
    def __init__(self, main_model, ignore_mentions=None):
        self.main_model = main_model
        self.ignore_mentions = ignore_mentions or set()
        self.last_keyboard_interrupt = False
        self.total_cost = 0.0
        self.num_exhausted_context_windows = 0
        self.num_malformed_responses = 0
        self.total_tokens_sent = 10
        self.total_tokens_received = 5
        self.chat_completion_call_hashes = []
        self.chat_completion_response_hashes = []

    def show_announcements(self):
        return None

    def run(self, with_message=None, preproc=False):
        model_name = self.main_model.name
        with LOCK:
            active_now = ACTIVE_BY_MODEL.get(model_name, 0) + 1
            ACTIVE_BY_MODEL[model_name] = active_now

        started = time.monotonic()
        time.sleep(0.25)
        finished = time.monotonic()

        with LOCK:
            ACTIVE_BY_MODEL[model_name] -= 1

        _append_log(
            {
                "model": model_name,
                "active_at_entry": active_now,
                "started": started,
                "finished": finished,
            }
        )
        return "updated"


def fake_create(main_model, edit_format, io, **kwargs):
    return FakeCoder(main_model, kwargs.get("ignore_mentions"))


benchmark.Coder.create = staticmethod(fake_create)
""".strip(),
        encoding="utf-8",
    )

    env = {
        "AIDER_BENCHMARK_DIR": str(benchmark_root),
        "AIDER_BENCHMARK_TEST_LOG": str(log_path),
        "PYTHONIOENCODING": "utf-8",
        "PYTHONPATH": str(hook_root) + os.pathsep + str(repo_src) + os.pathsep + os.environ.get("PYTHONPATH", ""),
    }
    result = run_cli(
        "benchmark",
        [
            "threads-only",
            "--model",
            "github/gpt-4o",
            "--threads",
            "2",
            "--max-llm-concurrency",
            "2",
            "--report-mode",
            "end",
            "--languages",
            "csharp",
            "--num-tests",
            "2",
            "--exercises-dir",
            str(tracks_root),
            "--unsafe",
            "--no-unit-tests",
        ],
        cwd=tmp_path,
        env=env,
    )

    assert result.returncode == 0, result.stdout + result.stderr

    run_dirs = sorted(path for path in benchmark_root.iterdir() if path.is_dir())
    assert len(run_dirs) == 1
    result_files = list(run_dirs[0].glob("csharp/exercises/practice/*/.aider.results.json"))
    assert len(result_files) == 2

    log_entries = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]
    assert len(log_entries) == 2
    assert max(entry["active_at_entry"] for entry in log_entries) == 2
    assert min(entry[0] for entry in [(entry["finished"],) for entry in log_entries])
    assert min(log_entries[0]["finished"], log_entries[1]["finished"]) > max(log_entries[0]["started"], log_entries[1]["started"])


def test_benchmark_model_parallelism_runs_models_in_parallel_when_threads_are_serial(
    tmp_path, init_track_repo, run_cli
):
    source_parent = tmp_path / "source"
    benchmark_root = tmp_path / "tmp.benchmarks"
    tracks_root = source_parent
    hook_root = tmp_path / "hook"
    hook_root.mkdir()
    log_path = tmp_path / "model-parallelism-log.jsonl"

    init_track_repo(source_parent, "csharp", ["alpha"])

    sitecustomize_path = hook_root / "sitecustomize.py"
    repo_src = Path(__file__).resolve().parents[2] / "src"

    sitecustomize_path.write_text(
        """
import json
import os
import threading
import time
from pathlib import Path

import aider_polyglot_benchmark.benchmark as benchmark


LOG_PATH = Path(os.environ["AIDER_BENCHMARK_TEST_LOG"])
LOCK = threading.Lock()


def _append_log(payload):
    with LOCK:
        with LOG_PATH.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload) + "\\n")


class FakeCoder:
    def __init__(self, main_model, ignore_mentions=None):
        self.main_model = main_model
        self.ignore_mentions = ignore_mentions or set()
        self.last_keyboard_interrupt = False
        self.total_cost = 0.0
        self.num_exhausted_context_windows = 0
        self.num_malformed_responses = 0
        self.total_tokens_sent = 10
        self.total_tokens_received = 5
        self.chat_completion_call_hashes = []
        self.chat_completion_response_hashes = []

    def show_announcements(self):
        return None

    def run(self, with_message=None, preproc=False):
        started = time.monotonic()
        time.sleep(0.25)
        finished = time.monotonic()
        _append_log({"model": self.main_model.name, "started": started, "finished": finished})
        return "updated"


def fake_create(main_model, edit_format, io, **kwargs):
    return FakeCoder(main_model, kwargs.get("ignore_mentions"))


benchmark.Coder.create = staticmethod(fake_create)
""".strip(),
        encoding="utf-8",
    )

    env = {
        "AIDER_BENCHMARK_DIR": str(benchmark_root),
        "AIDER_BENCHMARK_TEST_LOG": str(log_path),
        "PYTHONIOENCODING": "utf-8",
        "PYTHONPATH": str(hook_root) + os.pathsep + str(repo_src) + os.pathsep + os.environ.get("PYTHONPATH", ""),
    }
    result = run_cli(
        "benchmark",
        [
            "models-only",
            "--model",
            "github/gpt-4o",
            "--model",
            "openai/gpt-4o",
            "--threads",
            "1",
            "--model-parallelism",
            "2",
            "--max-llm-concurrency",
            "1",
            "--report-mode",
            "end",
            "--languages",
            "csharp",
            "--num-tests",
            "1",
            "--exercises-dir",
            str(tracks_root),
            "--unsafe",
            "--no-unit-tests",
        ],
        cwd=tmp_path,
        env=env,
    )

    assert result.returncode == 0, result.stdout + result.stderr

    run_dirs = sorted(path for path in benchmark_root.iterdir() if path.is_dir())
    assert len(run_dirs) == 2

    log_entries = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]
    assert len(log_entries) == 2
    assert {entry["model"] for entry in log_entries} == {"github/gpt-4o", "openai/gpt-4o"}
    assert min(log_entries[0]["finished"], log_entries[1]["finished"]) > max(log_entries[0]["started"], log_entries[1]["started"])


def test_benchmark_max_llm_concurrency_caps_same_provider_requests_across_parallel_work(
    tmp_path, init_track_repo, run_cli
):
    source_parent = tmp_path / "source"
    benchmark_root = tmp_path / "tmp.benchmarks"
    tracks_root = source_parent
    hook_root = tmp_path / "hook"
    hook_root.mkdir()
    log_path = tmp_path / "llm-cap-log.jsonl"

    init_track_repo(source_parent, "csharp", ["alpha", "beta"])

    sitecustomize_path = hook_root / "sitecustomize.py"
    repo_src = Path(__file__).resolve().parents[2] / "src"

    sitecustomize_path.write_text(
        """
import json
import os
import threading
import time
from pathlib import Path

import aider_polyglot_benchmark.benchmark as benchmark


LOG_PATH = Path(os.environ["AIDER_BENCHMARK_TEST_LOG"])
LOCK = threading.Lock()
ACTIVE_BY_PROVIDER = {}


def _append_log(payload):
    with LOCK:
        with LOG_PATH.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload) + "\\n")


class FakeCoder:
    def __init__(self, main_model, ignore_mentions=None):
        self.main_model = main_model
        self.ignore_mentions = ignore_mentions or set()
        self.last_keyboard_interrupt = False
        self.total_cost = 0.0
        self.num_exhausted_context_windows = 0
        self.num_malformed_responses = 0
        self.total_tokens_sent = 10
        self.total_tokens_received = 5
        self.chat_completion_call_hashes = []
        self.chat_completion_response_hashes = []

    def show_announcements(self):
        return None

    def run(self, with_message=None, preproc=False):
        provider = self.main_model.name.split("/", 1)[0]
        with LOCK:
            active_now = ACTIVE_BY_PROVIDER.get(provider, 0) + 1
            ACTIVE_BY_PROVIDER[provider] = active_now

        started = time.monotonic()
        time.sleep(0.2)
        finished = time.monotonic()

        with LOCK:
            ACTIVE_BY_PROVIDER[provider] -= 1

        _append_log(
            {
                "provider": provider,
                "model": self.main_model.name,
                "active_at_entry": active_now,
                "started": started,
                "finished": finished,
            }
        )
        return "updated"


def fake_create(main_model, edit_format, io, **kwargs):
    return FakeCoder(main_model, kwargs.get("ignore_mentions"))


benchmark.Coder.create = staticmethod(fake_create)
""".strip(),
        encoding="utf-8",
    )

    env = {
        "AIDER_BENCHMARK_DIR": str(benchmark_root),
        "AIDER_BENCHMARK_TEST_LOG": str(log_path),
        "PYTHONIOENCODING": "utf-8",
        "PYTHONPATH": str(hook_root) + os.pathsep + str(repo_src) + os.pathsep + os.environ.get("PYTHONPATH", ""),
    }
    result = run_cli(
        "benchmark",
        [
            "llm-cap-only",
            "--model",
            "github/gpt-4o",
            "--model",
            "github/gpt-4.1",
            "--threads",
            "2",
            "--model-parallelism",
            "2",
            "--max-llm-concurrency",
            "1",
            "--report-mode",
            "end",
            "--languages",
            "csharp",
            "--num-tests",
            "2",
            "--exercises-dir",
            str(tracks_root),
            "--unsafe",
            "--no-unit-tests",
        ],
        cwd=tmp_path,
        env=env,
    )

    assert result.returncode == 0, result.stdout + result.stderr

    run_dirs = sorted(path for path in benchmark_root.iterdir() if path.is_dir())
    assert len(run_dirs) == 2

    log_entries = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]
    github_entries = [entry for entry in log_entries if entry["provider"] == "github"]
    assert len(github_entries) == 4
    assert max(entry["active_at_entry"] for entry in github_entries) == 1


def test_benchmark_parallel_reports_remain_parseable_during_multi_model_run(
    tmp_path, start_cli, init_track_repo
):
    source_parent = tmp_path / "source"
    benchmark_root = tmp_path / "tmp.benchmarks"
    tracks_root = source_parent
    hook_root = tmp_path / "hook"
    hook_root.mkdir()
    log_path = tmp_path / "fake-coder-log.jsonl"

    init_track_repo(source_parent, "csharp", ["alpha", "beta"])

    sitecustomize_path = hook_root / "sitecustomize.py"
    repo_src = Path(__file__).resolve().parents[2] / "src"

    sitecustomize_path.write_text(
        """
import json
import os
import threading
import time
from pathlib import Path

import aider_polyglot_benchmark.benchmark as benchmark


LOG_PATH = Path(os.environ["AIDER_BENCHMARK_TEST_LOG"])
LOCK = threading.Lock()
ACTIVE_BY_PROVIDER = {}
CALLS_BY_MODEL = {}


def _append_log(payload):
    with LOCK:
        with LOG_PATH.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload) + "\\n")


class FakeCoder:
    def __init__(self, main_model, ignore_mentions=None):
        self.main_model = main_model
        self.ignore_mentions = ignore_mentions or set()
        self.last_keyboard_interrupt = False
        self.total_cost = 0.0
        self.num_exhausted_context_windows = 0
        self.num_malformed_responses = 0
        self.total_tokens_sent = 10
        self.total_tokens_received = 5
        self.chat_completion_call_hashes = []
        self.chat_completion_response_hashes = []

    def show_announcements(self):
        return None

    def run(self, with_message=None, preproc=False):
        model_name = self.main_model.name
        provider = model_name.split("/", 1)[0]

        with LOCK:
            active_now = ACTIVE_BY_PROVIDER.get(provider, 0) + 1
            ACTIVE_BY_PROVIDER[provider] = active_now
            attempt = CALLS_BY_MODEL.get(model_name, 0) + 1
            CALLS_BY_MODEL[model_name] = attempt

        started = time.monotonic()
        time.sleep(0.2)
        finished = time.monotonic()

        with LOCK:
            ACTIVE_BY_PROVIDER[provider] -= 1

        _append_log(
            {
                "provider": provider,
                "model": model_name,
                "attempt": attempt,
                "active_at_entry": active_now,
                "started": started,
                "finished": finished,
            }
        )

        return "updated"


def fake_create(main_model, edit_format, io, **kwargs):
    return FakeCoder(main_model, kwargs.get("ignore_mentions"))


benchmark.Coder.create = staticmethod(fake_create)
""".strip(),
        encoding="utf-8",
    )

    env = {
        "AIDER_BENCHMARK_DIR": str(benchmark_root),
        "AIDER_BENCHMARK_TEST_LOG": str(log_path),
        "PYTHONIOENCODING": "utf-8",
        "PYTHONPATH": str(hook_root) + os.pathsep + str(repo_src) + os.pathsep + os.environ.get("PYTHONPATH", ""),
    }
    proc = start_cli(
        "benchmark",
        [
            "parallel-report-race",
            "--model",
            "github/gpt-4o",
            "--model",
            "github/gpt-4.1",
            "--model",
            "openai/gpt-4o",
            "--model-parallelism",
            "3",
            "--threads",
            "2",
            "--max-llm-concurrency",
            "2",
            "--report-mode",
            "live",
            "--languages",
            "csharp",
            "--num-tests",
            "2",
            "--exercises-dir",
            str(tracks_root),
            "--unsafe",
            "--no-unit-tests",
        ],
        env=env,
    )

    output_lines = []
    parse_errors = []
    stop_polling = threading.Event()

    def reader():
        try:
            for line in proc.stdout:
                output_lines.append(line)
        except Exception as exc:
            parse_errors.append(f"stdout-read:{exc}")

    def validate_benchmark_report(path: Path):
        report = load_benchmark_report(path.parent)
        if report is None:
            return
        required_keys = {"dirname", "test_cases", "model", "total_tests"}
        missing = required_keys.difference(report)
        if missing:
            raise AssertionError(f"Incomplete benchmark report {path}: missing {sorted(missing)}")

    def validate_root_yaml(path: Path):
        entries = load_leaderboard_yaml(path)
        for entry in entries:
            required_keys = {"dirname", "model", "test_cases"}
            missing = required_keys.difference(entry)
            if missing:
                raise AssertionError(f"Incomplete leaderboard entry in {path}: missing {sorted(missing)}")

    def validate_text_report(path: Path, needle: str):
        text = path.read_text(encoding="utf-8")
        if text and needle not in text:
            raise AssertionError(f"Missing marker {needle!r} in {path}")

    def poll_reports():
        while not stop_polling.is_set():
            try:
                if benchmark_root.exists():
                    for report_path in benchmark_root.glob("**/benchmark-report.yml"):
                        validate_benchmark_report(report_path)
                    yaml_path = benchmark_root / "polyglot_leaderboard.yml"
                    if yaml_path.exists():
                        validate_root_yaml(yaml_path)
                    markdown_path = benchmark_root / "leaderboard.md"
                    if markdown_path.exists():
                        validate_text_report(markdown_path, "LLMs Benchmark")
                    html_path = benchmark_root / "leaderboard.html"
                    if html_path.exists():
                        validate_text_report(html_path, "LLMs Benchmark")
            except Exception as exc:
                parse_errors.append(str(exc))
                stop_polling.set()
                return
            time.sleep(0.01)

    reader_thread = threading.Thread(target=reader)
    poller_thread = threading.Thread(target=poll_reports)
    reader_thread.start()
    poller_thread.start()

    try:
        returncode = proc.wait(timeout=60)
    finally:
        stop_polling.set()
        poller_thread.join(timeout=5)
        reader_thread.join(timeout=5)

    output = "".join(output_lines)
    assert returncode == 0, output
    assert not parse_errors, f"Report parse errors during live run: {parse_errors}\nOutput:\n{output}"

    run_dirs = sorted(path for path in benchmark_root.iterdir() if path.is_dir())
    assert len(run_dirs) == 3
    for run_dir in run_dirs:
        assert (run_dir / "benchmark-report.yml").exists()
        assert load_benchmark_report(run_dir) is not None
        result_files = list(run_dir.glob("csharp/exercises/practice/*/.aider.results.json"))
        assert len(result_files) == 2

    yaml_entries = load_leaderboard_yaml(benchmark_root / "polyglot_leaderboard.yml")
    assert len(yaml_entries) == 3

    log_entries = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]
    github_entries = [entry for entry in log_entries if entry["provider"] == "github"]
    openai_entries = [entry for entry in log_entries if entry["provider"] == "openai"]

    assert len(github_entries) == 4
    assert len(openai_entries) == 2
    assert max(entry["active_at_entry"] for entry in github_entries) <= 2
    assert max(entry["active_at_entry"] for entry in openai_entries) <= 2
    assert any(
        min(github_entry["finished"], openai_entry["finished"])
        > max(github_entry["started"], openai_entry["started"])
        for github_entry in github_entries
        for openai_entry in openai_entries
    )


def test_benchmark_parallel_auto_mode_defers_reports_until_model_or_batch_end(
    tmp_path, start_cli, init_track_repo
):
    source_parent = tmp_path / "source"
    benchmark_root = tmp_path / "tmp.benchmarks"
    tracks_root = source_parent
    hook_root = tmp_path / "hook"
    hook_root.mkdir()

    init_track_repo(source_parent, "csharp", ["alpha", "beta", "gamma", "delta"])

    sitecustomize_path = hook_root / "sitecustomize.py"
    repo_src = Path(__file__).resolve().parents[2] / "src"

    sitecustomize_path.write_text(
        """
import time

import aider_polyglot_benchmark.benchmark as benchmark


class FakeCoder:
    def __init__(self, main_model, ignore_mentions=None):
        self.main_model = main_model
        self.ignore_mentions = ignore_mentions or set()
        self.last_keyboard_interrupt = False
        self.total_cost = 0.0
        self.num_exhausted_context_windows = 0
        self.num_malformed_responses = 0
        self.total_tokens_sent = 10
        self.total_tokens_received = 5
        self.chat_completion_call_hashes = []
        self.chat_completion_response_hashes = []

    def show_announcements(self):
        return None

    def run(self, with_message=None, preproc=False):
        time.sleep(0.2)
        return "updated"


def fake_create(main_model, edit_format, io, **kwargs):
    return FakeCoder(main_model, kwargs.get("ignore_mentions"))


benchmark.Coder.create = staticmethod(fake_create)
""".strip(),
        encoding="utf-8",
    )

    env = {
        "AIDER_BENCHMARK_DIR": str(benchmark_root),
        "AIDER_BENCHMARK_FORCE_TERMINAL": "1",
        "PYTHONIOENCODING": "utf-8",
        "PYTHONPATH": str(hook_root) + os.pathsep + str(repo_src) + os.pathsep + os.environ.get("PYTHONPATH", ""),
    }
    proc = start_cli(
        "benchmark",
        [
            "auto-report-mode",
            "--model",
            "github/gpt-4o",
            "--model",
            "openai/gpt-4o",
            "--model-parallelism",
            "2",
            "--threads",
            "2",
            "--max-llm-concurrency",
            "2",
            "--report-mode",
            "end",
            "--languages",
            "csharp",
            "--num-tests",
            "4",
            "--exercises-dir",
            str(tracks_root),
            "--unsafe",
            "--no-unit-tests",
        ],
        env=env,
    )

    output_lines = []
    observed_intermediate = False
    premature_errors = []

    def reader():
        try:
            for line in proc.stdout:
                output_lines.append(line)
        except Exception as exc:
            premature_errors.append(f"stdout-read:{exc}")

    reader_thread = threading.Thread(target=reader)
    reader_thread.start()

    expected_results_per_model = 4
    expected_total_results = 8
    root_report_paths = [
        benchmark_root / "leaderboard.md",
        benchmark_root / "leaderboard.html",
        benchmark_root / "polyglot_leaderboard.yml",
    ]

    try:
        deadline = time.time() + 60
        while proc.poll() is None and time.time() < deadline:
            run_dirs = [path for path in benchmark_root.iterdir() if path.is_dir()] if benchmark_root.exists() else []
            total_results = 0

            for run_dir in run_dirs:
                result_files = list(run_dir.glob("csharp/exercises/practice/*/.aider.results.json"))
                total_results += len(result_files)
                report_path = run_dir / "benchmark-report.yml"
                if len(result_files) < expected_results_per_model and report_path.exists():
                    premature_errors.append(
                        f"Per-model report appeared before model completed: {report_path} with {len(result_files)} results"
                    )
                    break

            if premature_errors:
                break

            if 0 < total_results < expected_total_results:
                observed_intermediate = True
                premature_root_reports = [path for path in root_report_paths if path.exists()]
                if premature_root_reports:
                    premature_errors.append(
                        "Root reports appeared before batch completed: "
                        + ", ".join(str(path) for path in premature_root_reports)
                    )
                    break

            time.sleep(0.01)
    finally:
        try:
            returncode = proc.wait(timeout=60)
        except subprocess.TimeoutExpired:
            proc.kill()
            reader_thread.join(timeout=5)
            output = "".join(output_lines)
            raise AssertionError(f"Benchmark timed out. Output:\n{output}")
        reader_thread.join(timeout=5)

    output = "".join(output_lines)
    assert returncode == 0, output
    assert observed_intermediate, f"Did not observe intermediate parallel progress. Output:\n{output}"
    assert not premature_errors, f"Unexpected early reports: {premature_errors}\nOutput:\n{output}"

    run_dirs = sorted(path for path in benchmark_root.iterdir() if path.is_dir())
    assert len(run_dirs) == 2
    for run_dir in run_dirs:
        result_files = list(run_dir.glob("csharp/exercises/practice/*/.aider.results.json"))
        assert len(result_files) == expected_results_per_model
        assert (run_dir / "benchmark-report.yml").exists()
        assert load_benchmark_report(run_dir) is not None

    for path in root_report_paths:
        assert path.exists()


def test_benchmark_progress_output_keeps_changing_during_slow_parallel_run(
    tmp_path, start_cli, init_track_repo
):
    source_parent = tmp_path / "source"
    benchmark_root = tmp_path / "tmp.benchmarks"
    tracks_root = source_parent
    hook_root = tmp_path / "hook"
    hook_root.mkdir()

    init_track_repo(source_parent, "csharp", ["alpha", "beta", "gamma"])

    sitecustomize_path = hook_root / "sitecustomize.py"
    repo_src = Path(__file__).resolve().parents[2] / "src"

    sitecustomize_path.write_text(
        """
import time

import aider_polyglot_benchmark.benchmark as benchmark


class FakeCoder:
    def __init__(self, main_model, ignore_mentions=None):
        self.main_model = main_model
        self.ignore_mentions = ignore_mentions or set()
        self.last_keyboard_interrupt = False
        self.total_cost = 0.0
        self.num_exhausted_context_windows = 0
        self.num_malformed_responses = 0
        self.total_tokens_sent = 10
        self.total_tokens_received = 5
        self.chat_completion_call_hashes = []
        self.chat_completion_response_hashes = []

    def show_announcements(self):
        return None

    def run(self, with_message=None, preproc=False):
        time.sleep(0.35)
        return "updated"


def fake_create(main_model, edit_format, io, **kwargs):
    return FakeCoder(main_model, kwargs.get("ignore_mentions"))


benchmark.Coder.create = staticmethod(fake_create)
""".strip(),
        encoding="utf-8",
    )

    env = {
        "AIDER_BENCHMARK_DIR": str(benchmark_root),
        "PYTHONIOENCODING": "utf-8",
        "PYTHONPATH": str(hook_root) + os.pathsep + str(repo_src) + os.pathsep + os.environ.get("PYTHONPATH", ""),
    }
    proc = start_cli(
        "benchmark",
        [
            "progress-refresh-smoke",
            "--model",
            "github/gpt-4o",
            "--model",
            "openai/gpt-4o",
            "--model-parallelism",
            "2",
            "--threads",
            "2",
            "--report-mode",
            "end",
            "--languages",
            "csharp",
            "--num-tests",
            "3",
            "--exercises-dir",
            str(tracks_root),
            "--unsafe",
            "--no-unit-tests",
        ],
        env=env,
    )

    output_chunks = []
    read_errors = []

    def reader():
        try:
            while True:
                chunk = proc.stdout.read(1)
                if not chunk:
                    break
                output_chunks.append(chunk)
        except Exception as exc:
            read_errors.append(f"stdout-read:{exc}")

    reader_thread = threading.Thread(target=reader)
    reader_thread.start()

    observed_growth = []
    previous_length = -1
    deadline = time.time() + 20
    while proc.poll() is None and time.time() < deadline:
        current_length = len(output_chunks)
        if current_length > previous_length:
            observed_growth.append(current_length)
            previous_length = current_length
        if len(observed_growth) >= 3 and current_length > 0:
            break
        time.sleep(0.1)

    returncode = proc.wait(timeout=60)
    reader_thread.join(timeout=5)

    output = "".join(output_chunks)
    assert returncode == 0, output
    assert not read_errors, f"Unexpected output read errors: {read_errors}\nOutput:\n{output}"
    assert len(observed_growth) >= 3, f"Expected progress output to change multiple times during run. Output:\n{output}"
    assert "github/gpt-4o" in output
    assert "openai/gpt-4o" in output


def test_benchmark_ctrl_c_cancels_gracefully(tmp_path, start_cli, send_interrupt, init_track_repo):
    source_parent = tmp_path / "source"
    benchmark_root = tmp_path / "tmp.benchmarks"
    tracks_root = tmp_path / "tracks"
    exercises = [f"ex{i:03d}" for i in range(30)]
    init_track_repo(source_parent, "csharp", exercises)

    env = {
        "AIDER_BENCHMARK_DIR": str(benchmark_root),
        "EXERCISM_TRACK_REPO_BASE_URL": source_parent.as_uri(),
    }
    proc = start_cli(
        "benchmark",
        [
            "ctrl-c-test",
            "--model",
            "github/gpt-4.1",
            "--languages",
            "csharp",
            "--num-tests",
            "30",
            "--exercises-dir",
            str(tracks_root),
            "--unsafe",
            "--no-aider",
            "--no-unit-tests",
        ],
        env=env,
    )

    output_lines = []
    signal_sent = threading.Event()

    def reader():
        try:
            for line in proc.stdout:
                output_lines.append(line)
                if not signal_sent.is_set() and "fnames:" in line:
                    try:
                        send_interrupt(proc)
                    except (OSError, ProcessLookupError):
                        pass
                    signal_sent.set()
        except Exception:
            pass

    reader_thread = threading.Thread(target=reader)
    reader_thread.start()

    try:
        returncode = proc.wait(timeout=30)
    except subprocess.TimeoutExpired:
        proc.kill()
        reader_thread.join(timeout=5)
        output = "".join(output_lines)
        raise AssertionError(f"Benchmark did not exit after Ctrl+C. Output:\n{output}")

    reader_thread.join(timeout=5)
    output = "".join(output_lines)

    assert signal_sent.is_set(), (
        "Ctrl+C was never sent; benchmark finished before processing started. "
        f"Output:\n{output}"
    )
    assert returncode == 2, output
    assert "Benchmark cancelled" in output, output
    assert (
        (benchmark_root / "leaderboard.md").exists()
        or (benchmark_root / "polyglot_leaderboard.yml").exists()
    )