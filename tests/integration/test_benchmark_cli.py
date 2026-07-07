import json
import os
import subprocess
import sys
from pathlib import Path


def test_benchmark_help_command_runs(run_cli):
    result = run_cli("benchmark", ["--help"])

    assert result.returncode == 0
    assert "Run benchmark, stats, diffs, or cleanup" in result.stdout
    assert "--languages" in result.stdout


def test_benchmark_requires_languages_in_real_command(run_cli):
    result = run_cli("benchmark", ["sample-run", "--model", "github/gpt-4.1", "--unsafe"])

    assert result.returncode == 1
    assert "--languages is required when running benchmarks" in result.stdout


def test_benchmark_report_stats_diffs_and_purge_commands_run(tmp_path, run_cli, write_result_file):
    benchmark_root = tmp_path / "tmp.benchmarks"
    run_a = benchmark_root / "2026-07-05-12-00-00--run-a"
    run_b = benchmark_root / "2026-07-05-12-00-01--run-b"
    write_result_file(run_a, "csharp", "alpha", [True])
    write_result_file(run_b, "csharp", "alpha", [False, True])

    env = {"AIDER_BENCHMARK_DIR": str(benchmark_root)}

    report = run_cli("benchmark", ["--report"], env=env)
    assert report.returncode == 0, report.stdout + report.stderr
    assert (benchmark_root / "leaderboard.csv").exists()
    assert (benchmark_root / "leaderboard.html").exists()
    assert (benchmark_root / "polyglot_leaderboard.yml").exists()

    stats = run_cli("benchmark", [str(run_a), "--stats"], env=env)
    assert stats.returncode == 0, stats.stdout + stats.stderr

    diffs = run_cli("benchmark", [str(run_a), str(run_b), "--diffs"], env=env)
    assert diffs.returncode == 0, diffs.stdout + diffs.stderr
    assert "changed:" in diffs.stdout or "unchanged:" in diffs.stdout

    purge = run_cli("benchmark", ["--purge"], env=env)
    assert purge.returncode == 0, purge.stdout + purge.stderr
    assert not run_a.exists()
    assert not run_b.exists()
    assert not (benchmark_root / "leaderboard.csv").exists()


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

    assert (benchmark_root / "leaderboard.csv").exists()
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

    assert (benchmark_root / "leaderboard.csv").exists()
    assert (benchmark_root / "leaderboard.html").exists()
    assert (benchmark_root / "polyglot_leaderboard.yml").exists()

    csv_text = (benchmark_root / "leaderboard.csv").read_text(encoding="utf-8")
    assert "github/gpt-4o" in csv_text
    assert "github/gpt-4.1" in csv_text


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
    tmp_path, init_track_repo
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
    command = (
        "from typer.testing import CliRunner; "
        "from aider_polyglot_benchmark.benchmark import app; "
        "runner = CliRunner(); "
        "result = runner.invoke(app, ["
        "'real-world-sim', "
        "'--model', 'github/gpt-4o', "
        "'--model', 'github/gpt-4.1', "
        "'--model', 'openai/gpt-4o', "
        "'--model-parallelism', '3', "
        "'--max-llm-concurrency', '1', "
        "'--rate-limit-retries', '2', "
        "'--rate-limit-backoff-initial', '0', "
        "'--rate-limit-backoff-max', '0', "
        "'--languages', 'csharp', "
        "'--num-tests', '1', "
        f"'--exercises-dir', {str(tracks_root)!r}, "
        "'--unsafe', "
        "'--no-unit-tests'"
        "]); "
        "print(result.stdout, end=''); "
        "raise SystemExit(result.exit_code)"
    )
    result = subprocess.run(
        [sys.executable, "-c", command],
        cwd=tmp_path,
        env={**os.environ, **env},
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
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