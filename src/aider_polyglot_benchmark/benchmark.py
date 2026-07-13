#!/usr/bin/env python3
import datetime
import io as io_module
import json
import os
import random
import re
import shutil
import signal
import stat
import subprocess
import sys
import threading
import time
import traceback
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager, nullcontext, redirect_stdout
from importlib import metadata as importlib_metadata
from pathlib import Path
from json.decoder import JSONDecodeError
from types import SimpleNamespace
from typing import List, Optional

REPO_ROOT = Path(__file__).resolve().parents[2]
_REEXEC_ENV_VAR = "AIDER_BENCHMARK_VENV_REEXEC"
_RATE_LIMIT_PATTERNS = (
    "rate limit",
    "too many requests",
    "status code 429",
    "status code: 429",
    "error code: 429",
    "retry-after",
)
_RATE_LIMITERS = {}
_RATE_LIMITERS_LOCK = threading.Lock()
_CANCEL_EVENT = threading.Event()


class BenchmarkCancelled(Exception):
    pass


def raise_if_cancelled():
    if _CANCEL_EVENT.is_set():
        raise BenchmarkCancelled()


def cancel_aware_sleep(seconds):
    if seconds > 0:
        _CANCEL_EVENT.wait(seconds)


def _raise_keyboard_interrupt(signum, frame):
    raise KeyboardInterrupt


def install_interrupt_handlers():
    if sys.platform == "win32" and hasattr(signal, "SIGBREAK"):
        signal.signal(signal.SIGBREAK, _raise_keyboard_interrupt)


def shutdown_executor(executor, cancelled):
    executor.shutdown(wait=not cancelled, cancel_futures=cancelled)


def find_local_venv_python(repo_root=REPO_ROOT):
    if os.name == "nt":
        candidate = Path(repo_root) / ".venv" / "Scripts" / "python.exe"
    else:
        candidate = Path(repo_root) / ".venv" / "bin" / "python"
    return candidate if candidate.exists() else None


def should_reexec_with_local_venv(current_python, venv_python):
    if not venv_python:
        return False
    return Path(current_python).resolve() != Path(venv_python).resolve()


def maybe_reexec_in_local_venv():
    if os.environ.get(_REEXEC_ENV_VAR) == "1":
        return False

    venv_python = find_local_venv_python()
    if not should_reexec_with_local_venv(sys.executable, venv_python):
        return False

    os.environ[_REEXEC_ENV_VAR] = "1"
    src_root = REPO_ROOT / "src"
    env = os.environ.copy()
    existing_pythonpath = env.get("PYTHONPATH")
    env["PYTHONPATH"] = str(src_root) if not existing_pythonpath else os.pathsep.join([str(src_root), existing_pythonpath])
    os.execve(
        str(venv_python),
        [str(venv_python), "-m", "aider_polyglot_benchmark.benchmark", *sys.argv[1:]],
        env,
    )
    return True

try:
    import git
    import importlib_resources
    import pandas as pd
    import typer
    from aider_polyglot_benchmark import leaderboard_report, prompts
    from dotenv import load_dotenv
    from rich.console import Console
    from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn, TimeElapsedColumn, TimeRemainingColumn

    load_dotenv(override=True)

    from aider import models, sendchat
    from aider.coders import Coder, base_coder
    from aider.dump import dump  # noqa: F401
    from aider.io import InputOutput
except ImportError as exc:
    maybe_reexec_in_local_venv()
    print(f"Missing Python dependency: {exc}")
    print("Install benchmark environment with: uv sync")
    raise SystemExit(1) from exc

BENCHMARK_DNAME = Path(os.environ.get("AIDER_BENCHMARK_DIR", REPO_ROOT / "tmp.benchmarks"))
BENCHMARK_REPORT_FILENAMES = (
    "leaderboard.csv",
    "leaderboard.md",
    "leaderboard.html",
    "polyglot_leaderboard.yml",
)
BENCHMARK_PROCESS_TRACKING_FILENAME = ".benchmark-pids.json"
BENCHMARK_RUN_DIR_RE = re.compile(r"^\d{4}-\d{2}-\d{2}-\d{2}-\d{2}-\d{2}--")
BENCHMARK_CONSOLE = Console(stderr=True, highlight=False)
_BENCHMARK_PROGRESS = None
_BENCHMARK_PROGRESS_LOCK = threading.RLock()

EXERCISES_DIR_DEFAULT = "exercises"
LEGACY_EXERCISES_DIR_DEFAULT = "exercism-tracks"

MAIN_HELP = """Run benchmark, stats, diffs, or cleanup for Exercism-style benchmark runs.

Modes:
- Run benchmark: benchmark my-run --model github/gpt-4o --languages csharp --unsafe
- Multi-model batch: benchmark my-run --model github/gpt-4o --model github/gpt-4.1 --languages csharp --unsafe
- Latest stats: benchmark --stats
- Named stats: benchmark 2026-07-05-12-00-00--my-run --stats
- Rebuild final report: benchmark --report
- Rebuild report from selected runs: benchmark run-a run-b --report
- Compare runs: benchmark run-a run-b --diffs
- Purge generated outputs: benchmark --purge

Outputs:
- Per-run data lives under tmp.benchmarks/YYYY-MM-DD-HH-MM-SS--name
- Aggregate reports are written to tmp.benchmarks/leaderboard.md, leaderboard.html, and polyglot_leaderboard.yml

Model selection:
- Pass any LiteLLM-supported model name with --model, for example openai/gpt-4o, anthropic/claude-sonnet-4, azure/my-deployment, github/gpt-4.1, gemini/gemini-2.5-flash, deepseek/deepseek-chat, or github_copilot/gpt-4
- If --model is omitted, benchmark uses MODEL from the environment when set

Exercise datasets are discovered under a root shaped like `<root>/<language>/exercises/practice/...`.
The default root is `exercises`, which you can populate with `clone-exercism-tracks`.

Run through the installed console entrypoint, for example `uv run benchmark ...`. If you prefer module execution, `python -m aider_polyglot_benchmark.benchmark ...` also works once dependencies are installed.
"""

app = typer.Typer(
    add_completion=False,
    pretty_exceptions_enable=False,
    help=MAIN_HELP,
)


def quiet_output(verbose):
    if verbose:
        return nullcontext()
    return redirect_stdout(io_module.StringIO())


def benchmark_print(message="", style=None):
    BENCHMARK_CONSOLE.print(message, style=style)


def make_benchmark_progress():
    return Progress(
        SpinnerColumn(),
        TextColumn("[bold cyan]{task.description}"),
        BarColumn(),
        TextColumn("{task.completed}/{task.total}"),
        TimeElapsedColumn(),
        TimeRemainingColumn(),
        TextColumn("{task.fields[status]}"),
        console=BENCHMARK_CONSOLE,
        transient=False,
    )


@contextmanager
def benchmark_progress(enabled=True):
    global _BENCHMARK_PROGRESS
    if not enabled:
        yield None
        return

    with _BENCHMARK_PROGRESS_LOCK:
        if _BENCHMARK_PROGRESS is not None:
            yield _BENCHMARK_PROGRESS
            return

        progress = make_benchmark_progress()
        _BENCHMARK_PROGRESS = progress

    try:
        with progress:
            yield progress
    finally:
        with _BENCHMARK_PROGRESS_LOCK:
            if _BENCHMARK_PROGRESS is progress:
                _BENCHMARK_PROGRESS = None


def add_benchmark_task(description, total, status="queued"):
    with _BENCHMARK_PROGRESS_LOCK:
        if _BENCHMARK_PROGRESS is None:
            return None
        return _BENCHMARK_PROGRESS.add_task(description, total=total, status=status)


def update_benchmark_task(task_id, advance=0, status=None):
    with _BENCHMARK_PROGRESS_LOCK:
        if _BENCHMARK_PROGRESS is None or task_id is None:
            return
        kwargs = {"advance": advance}
        if status is not None:
            kwargs["status"] = status
        _BENCHMARK_PROGRESS.update(task_id, **kwargs)


def find_latest_benchmark_dir():
    benchmark_dirs = [d for d in BENCHMARK_DNAME.iterdir() if d.is_dir()]
    if not benchmark_dirs:
        print("Error: No benchmark directories found under tmp.benchmarks.")
        sys.exit(1)

    # Get current time and 24 hours ago
    now = datetime.datetime.now()
    day_ago = now - datetime.timedelta(days=1)

    # Filter directories by name pattern YYYY-MM-DD-HH-MM-SS--
    recent_dirs = []
    for d in benchmark_dirs:
        try:
            # Extract datetime from directory name
            date_str = d.name[:19]  # Takes YYYY-MM-DD-HH-MM-SS
            dir_date = datetime.datetime.strptime(date_str, "%Y-%m-%d-%H-%M-%S")
            if dir_date >= day_ago:
                recent_dirs.append(d)
        except ValueError:
            # Skip directories that don't match the expected format
            continue

    if not recent_dirs:
        print("Error: No benchmark directories found from the last 24 hours.")
        sys.exit(1)

    # Find directory with most recently modified .md file
    latest_dir = None
    latest_time = 0

    for d in recent_dirs:
        # Look for .md files in subdirectories
        for md_file in d.glob("*/exercises/practice/*/.*.md"):
            if md_file.is_file():
                mtime = md_file.stat().st_mtime
                if mtime > latest_time:
                    latest_time = mtime
                    latest_dir = d

    if not latest_dir:
        print("Error: No .md files found in recent benchmark directories.")
        sys.exit(1)

    print(f"Using the most recently updated benchmark directory: {latest_dir.name}")
    return latest_dir


def show_stats(dirnames, stats_languages=None):
    raw_rows = []
    for dirname in dirnames:
        row = summarize_results(dirname, stats_languages)
        raw_rows.append(row)

    # return

    seen = dict()
    rows = []
    for row in raw_rows:
        if not row:
            continue

        if row.completed_tests != row.total_tests:
            print(
                f"Warning: {row.dir_name} is incomplete: {row.completed_tests} of {row.total_tests}"
            )

        try:
            kind = (row.model, row.edit_format)
        except AttributeError:
            return

        if kind in seen:
            dump(row.dir_name)
            dump(seen[kind])
            return

        seen[kind] = row.dir_name
        rows.append(vars(row))

    pd.DataFrame.from_records(rows)


def yaml_scalar(value):
    if value is None:
        return '""'
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if value.is_integer():
            return str(int(value))
        return f"{value:.4f}".rstrip("0").rstrip(".")

    text = str(value)
    if text == "":
        return '""'
    if any(ch in text for ch in ":#[]{}\n\r\t") or text != text.strip():
        return json.dumps(text)
    return text


def write_benchmark_report(dirname, report):
    report_path = Path(dirname) / "benchmark-report.yml"
    lines = [f"- dirname: {yaml_scalar(report['dirname'])}"]
    for key, value in report.items():
        if key == "dirname":
            continue
        lines.append(f"  {key}: {yaml_scalar(value)}")
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return report_path


def write_aggregate_reports_for_progress(dirname, quiet=False):
    try:
        write_aggregate_reports([dirname], quiet=quiet)
    except Exception as exc:
        benchmark_print(f"Warning: failed to update aggregate reports: {exc}", style="yellow")


def safe_console_rule(console, title=None):
    try:
        console.rule(title=title)
    except UnicodeEncodeError:
        rule = "-" * 80
        if title:
            print(rule)
            print(title)
        print(rule)


def get_commit_hash():
    try:
        repo = git.Repo(search_parent_directories=True)
    except (git.InvalidGitRepositoryError, git.NoSuchPathError):
        return "unknown"

    commit_hash = repo.head.object.hexsha[:7]
    if repo.is_dirty():
        commit_hash += "-dirty"
    return commit_hash


def get_script_command(script_name):
    script_path = REPO_ROOT / script_name
    if os.name == "nt":
        git_bash = Path("C:/Program Files/Git/bin/bash.exe")
        if git_bash.exists():
            return [str(git_bash), str(script_path)]
    return [str(script_path)]


def resolve_exercises_dir(exercises_dir):
    exercises_path = Path(exercises_dir)
    candidates = []

    if exercises_path.is_absolute():
        candidates.append(exercises_path)
    else:
        candidates.extend(
            [
                REPO_ROOT / exercises_path,
                BENCHMARK_DNAME / exercises_path,
                exercises_path,
            ]
        )

        if str(exercises_path) == EXERCISES_DIR_DEFAULT:
            candidates.append(REPO_ROOT / LEGACY_EXERCISES_DIR_DEFAULT)

    for candidate in candidates:
        if candidate.exists() and candidate.is_dir():
            return candidate

    return candidates[0] if candidates else exercises_path


def resolve_dirname(dirname, use_single_prior, make_new, choose_latest_prior=False):
    if len(dirname.parts) > 1:
        return dirname

    priors = list(BENCHMARK_DNAME.glob(f"*--{dirname}"))
    if priors and use_single_prior and (len(priors) == 1 or choose_latest_prior):
        dirname = sorted(priors, key=lambda path: path.name)[-1].name
        print(f"Using pre-existing {dirname}")
    elif len(priors):
        if not make_new:
            print(f"Prior runs of {dirname} exist, use --new or name one explicitly")
            print()
            for prior in priors:
                print(prior)
            return

    if not re.match(r"\d\d\d\d-\d\d-\d\d-", str(dirname)):
        now = datetime.datetime.now()
        now = now.strftime("%Y-%m-%d-%H-%M-%S--")
        dirname = now + dirname.name

    dirname = BENCHMARK_DNAME / dirname
    return dirname


def normalize_models(model_names):
    env_model = os.environ.get("MODEL", "").strip()
    if not model_names:
        return [env_model] if env_model else ["github/gpt-4o"]

    cleaned = [name.strip() for name in model_names if name and name.strip()]
    return cleaned or ([env_model] if env_model else ["github/gpt-4o"])


def slugify_model_name(model_name):
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", model_name).strip("-")
    return slug or "model"


def build_default_run_name(model_names):
    parts = []
    for model_name in model_names:
        model_leaf = model_name.rsplit("/", 1)[-1]
        parts.append(slugify_model_name(model_leaf))

    return "_".join(parts) or "benchmark"


def build_model_dirnames(base_dirname, model_names, use_base_dirname_for_first=False):
    if len(model_names) == 1:
        return [base_dirname]

    dirnames = []
    seen = set()
    for model_name in model_names:
        model_slug_source = model_name.rsplit("/", 1)[-1] if use_base_dirname_for_first else model_name
        dirname = base_dirname.parent / f"{base_dirname.name}--{slugify_model_name(model_slug_source)}"
        if dirname in seen:
            raise ValueError(f"Duplicate output directory for model: {model_name}")
        seen.add(dirname)
        dirnames.append(dirname)

    return dirnames


def build_generated_model_dirnames(model_names):
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d-%H-%M-%S--")
    dirnames = []
    seen = set()

    for model_name in model_names:
        model_slug = slugify_model_name(model_name.rsplit("/", 1)[-1])
        dirname = BENCHMARK_DNAME / f"{timestamp}{model_slug}"
        if dirname in seen:
            raise ValueError(f"Duplicate output directory for model: {model_name}")
        seen.add(dirname)
        dirnames.append(dirname)

    return dirnames


def get_rate_limit_scope(model_name):
    model_name = (model_name or "default").strip().lower()
    if "/" in model_name:
        return model_name.split("/", 1)[0]
    return model_name


def is_rate_limit_error(exc):
    parts = [exc.__class__.__name__, str(exc)]
    response = getattr(exc, "response", None)
    status_code = getattr(exc, "status_code", None)
    if response is not None:
        parts.append(str(getattr(response, "status_code", "")))
        parts.append(str(getattr(response, "text", "")))
        status_code = status_code or getattr(response, "status_code", None)

    if status_code == 429:
        return True

    message = " ".join(part for part in parts if part).lower()
    return any(pattern in message for pattern in _RATE_LIMIT_PATTERNS)


def extract_retry_after_seconds(exc):
    response = getattr(exc, "response", None)
    if response is not None:
        headers = getattr(response, "headers", None) or {}
        retry_after = headers.get("Retry-After") or headers.get("retry-after")
        if retry_after is not None:
            try:
                return max(0.0, float(retry_after))
            except (TypeError, ValueError):
                pass

    message = str(exc)
    match = re.search(r"retry-?after[^0-9]*([0-9]+(?:\.[0-9]+)?)", message, re.IGNORECASE)
    if match:
        return max(0.0, float(match.group(1)))

    return None


class SharedRateLimiter:
    def __init__(self, max_concurrency, backoff_initial, backoff_max):
        self.semaphore = threading.Semaphore(max(1, int(max_concurrency)))
        self.backoff_initial = max(0.0, float(backoff_initial))
        self.backoff_max = max(self.backoff_initial, float(backoff_max))
        self.cooldown_until = 0.0
        self.rate_limit_hits = 0
        self.lock = threading.Lock()

    def acquire(self):
        while True:
            raise_if_cancelled()
            if not self.semaphore.acquire(timeout=0.1):
                continue
            wait_for = 0.0
            with self.lock:
                now = time.monotonic()
                if self.cooldown_until > now:
                    wait_for = self.cooldown_until - now

            if wait_for <= 0:
                return

            self.semaphore.release()
            if _CANCEL_EVENT.wait(wait_for):
                raise BenchmarkCancelled()

    def release(self):
        self.semaphore.release()

    def record_rate_limit(self, retry_after=None):
        with self.lock:
            self.rate_limit_hits += 1
            backoff = self.backoff_initial * (2 ** min(self.rate_limit_hits - 1, 8))
            backoff = min(backoff, self.backoff_max)
            jitter = random.uniform(0.0, min(1.0, backoff * 0.1)) if backoff else 0.0
            cooldown = max(float(retry_after or 0.0), backoff + jitter)
            self.cooldown_until = max(self.cooldown_until, time.monotonic() + cooldown)
            return cooldown

    def mark_success(self):
        with self.lock:
            self.rate_limit_hits = 0


def reset_shared_rate_limiters():
    with _RATE_LIMITERS_LOCK:
        _RATE_LIMITERS.clear()


def get_shared_rate_limiter(scope, max_concurrency, backoff_initial, backoff_max):
    with _RATE_LIMITERS_LOCK:
        limiter = _RATE_LIMITERS.get(scope)
        if limiter is None:
            limiter = SharedRateLimiter(max_concurrency, backoff_initial, backoff_max)
            _RATE_LIMITERS[scope] = limiter
        return limiter


def run_with_rate_limit_retry(
    func,
    limiter,
    scope,
    max_retries,
):
    attempts = 0
    while True:
        raise_if_cancelled()
        limiter.acquire()
        try:
            result = func()
            limiter.mark_success()
            return result
        except Exception as exc:
            if not is_rate_limit_error(exc) or attempts >= max_retries:
                raise

            attempts += 1
            retry_after = extract_retry_after_seconds(exc)
            cooldown = limiter.record_rate_limit(retry_after)
            print(
                f"Rate limit hit for {scope}; retrying in {cooldown:.1f}s "
                f"(attempt {attempts}/{max_retries})"
            )
        finally:
            limiter.release()


def resolve_llm_concurrency(threads, max_llm_concurrency):
    if max_llm_concurrency is not None:
        return max(1, max_llm_concurrency)
    if threads > 1:
        return min(2, threads)
    return 1


def resolve_model_parallelism(model_names, requested_parallelism):
    if requested_parallelism is not None:
        return max(1, requested_parallelism)
    return 1


def run_single_model_benchmark(
    model_name,
    run_dirname,
    sleep,
    languages,
    edit_format,
    editor_model,
    editor_edit_format,
    replay,
    keywords,
    clean,
    no_unit_tests,
    no_aider,
    verbose,
    tries,
    threads,
    num_tests,
    num_ctx,
    read_model_settings,
    reasoning_effort,
    thinking_tokens,
    max_llm_concurrency,
    rate_limit_retries,
    rate_limit_backoff_initial,
    rate_limit_backoff_max,
    exercises_dir,
    commit_hash,
):
    if verbose:
        print(f"Running benchmark for model: {model_name}")
    else:
        benchmark_print(f"Running benchmark for model: {model_name}", style="bold cyan")
    return run_benchmark_for_model(
        run_dirname,
        model_name,
        sleep,
        languages,
        edit_format,
        editor_model,
        editor_edit_format,
        replay,
        keywords,
        clean,
        no_unit_tests,
        no_aider,
        verbose,
        tries,
        threads,
        num_tests,
        num_ctx,
        read_model_settings,
        reasoning_effort,
        thinking_tokens,
        max_llm_concurrency,
        rate_limit_retries,
        rate_limit_backoff_initial,
        rate_limit_backoff_max,
        exercises_dir,
        commit_hash,
    )


def write_aggregate_reports(dirnames=None, stats_languages=None, write_empty_yaml=False, quiet=False):
    from aider_polyglot_benchmark import leaderboard_report

    dirnames = leaderboard_report.find_benchmark_dirs(dirnames)
    rows = leaderboard_report.build_rows(dirnames, stats_languages=stats_languages)
    yaml_path = leaderboard_report.BENCHMARK_ROOT / "polyglot_leaderboard.yml"
    if not rows:
        if not quiet:
            print("No benchmark results found to aggregate.")
        if write_empty_yaml:
            leaderboard_report.write_yaml([], yaml_path)
            if not quiet:
                print(f"Wrote YAML: {yaml_path}")
        return

    yaml_entries = leaderboard_report.build_yaml_entries(dirnames, stats_languages=stats_languages)
    md_path = leaderboard_report.BENCHMARK_ROOT / "leaderboard.md"
    html_path = leaderboard_report.BENCHMARK_ROOT / "leaderboard.html"

    leaderboard_report.write_markdown(rows, md_path, "LLMs Benchmark Leaderboard")
    leaderboard_report.render_html(rows, html_path, "LLMs BenchmarkLLM Leaderboards")
    leaderboard_report.write_yaml(yaml_entries, yaml_path)

    if not quiet:
        print(f"Wrote Markdown: {md_path}")
        print(f"Wrote HTML: {html_path}")
        print(f"Wrote YAML: {yaml_path}")
        print(f"Rows: {len(rows)}")


def is_benchmark_result_dir(path):
    return path.is_dir() and bool(BENCHMARK_RUN_DIR_RE.match(path.name))


def is_staged_cleanup_dir(path):
    return path.is_dir() and path.name.startswith(".deleting-")


def _is_relative_to(path, parent):
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def _remove_readonly_and_retry(func, path, exc_info):
    exc = exc_info[1]
    if not isinstance(exc, PermissionError):
        raise exc

    os.chmod(path, stat.S_IWRITE | stat.S_IREAD)
    func(path)


def remove_tree(path):
    shutil.rmtree(path, onerror=_remove_readonly_and_retry)


def remove_tree_with_retries(path, attempts=3, delay_seconds=0.1):
    for attempt in range(attempts):
        try:
            remove_tree(path)
        except OSError:
            if attempt == attempts - 1:
                return False
        if not Path(path).exists():
            return True
        if attempt < attempts - 1:
            cancel_aware_sleep(delay_seconds * (attempt + 1))
    return not Path(path).exists()


def stage_dir_for_cleanup(path, benchmark_root):
    path = Path(path)
    benchmark_root = Path(benchmark_root)
    staged = benchmark_root / f".deleting-{path.name}"
    counter = 1
    while staged.exists():
        staged = benchmark_root / f".deleting-{counter}-{path.name}"
        counter += 1
    path.rename(staged)
    return staged


def get_process_tracking_path(benchmark_root=BENCHMARK_DNAME):
    return Path(benchmark_root) / BENCHMARK_PROCESS_TRACKING_FILENAME


def load_tracked_process_ids(benchmark_root=BENCHMARK_DNAME):
    tracking_path = get_process_tracking_path(benchmark_root)
    if not tracking_path.exists():
        return []

    try:
        payload = json.loads(tracking_path.read_text(encoding="utf-8"))
    except (OSError, JSONDecodeError):
        return []

    if not isinstance(payload, list):
        return []

    process_ids = []
    for value in payload:
        try:
            pid = int(value)
        except (TypeError, ValueError):
            continue
        if pid > 0:
            process_ids.append(pid)
    return sorted(set(process_ids))


def save_tracked_process_ids(process_ids, benchmark_root=BENCHMARK_DNAME):
    tracking_path = get_process_tracking_path(benchmark_root)
    tracking_path.parent.mkdir(parents=True, exist_ok=True)
    unique_ids = sorted({int(pid) for pid in process_ids if int(pid) > 0})
    if not unique_ids:
        try:
            tracking_path.unlink()
        except FileNotFoundError:
            pass
        return
    tracking_path.write_text(json.dumps(unique_ids), encoding="utf-8")


def register_tracked_process(pid, benchmark_root=BENCHMARK_DNAME):
    process_ids = load_tracked_process_ids(benchmark_root)
    process_ids.append(pid)
    save_tracked_process_ids(process_ids, benchmark_root)


def unregister_tracked_process(pid, benchmark_root=BENCHMARK_DNAME):
    process_ids = [value for value in load_tracked_process_ids(benchmark_root) if value != pid]
    save_tracked_process_ids(process_ids, benchmark_root)


def terminate_process_tree(pid, force=False):
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False

    if pid <= 0:
        return False

    if os.name == "nt":
        command = ["taskkill", "/PID", str(pid), "/T"]
        if force:
            command.append("/F")
        result = subprocess.run(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
        return result.returncode == 0

    sig = signal.SIGKILL if force else signal.SIGTERM
    try:
        os.killpg(os.getpgid(pid), sig)
        return True
    except OSError:
        try:
            os.kill(pid, sig)
            return True
        except OSError:
            return False


def kill_tracked_processes(benchmark_root=BENCHMARK_DNAME, force=True):
    process_ids = load_tracked_process_ids(benchmark_root)
    killed = []
    failed = []
    for pid in process_ids:
        if terminate_process_tree(pid, force=force):
            killed.append(pid)
        else:
            failed.append(pid)
    save_tracked_process_ids([], benchmark_root)
    return SimpleNamespace(killed=killed, failed=failed)


def stage_dir_for_cleanup_with_retries(path, benchmark_root, attempts=3, delay_seconds=0.1):
    for attempt in range(attempts):
        try:
            return stage_dir_for_cleanup(path, benchmark_root)
        except OSError:
            if attempt == attempts - 1:
                return None
            cancel_aware_sleep(delay_seconds * (attempt + 1))
    return None


def cleanup_benchmark_artifacts(dirnames=None, benchmark_root=BENCHMARK_DNAME, include_staged_dirs=False):
    benchmark_root = Path(benchmark_root)
    removed_files = []
    removed_dirs = []
    skipped_dirs = []

    for filename in (*BENCHMARK_REPORT_FILENAMES, BENCHMARK_PROCESS_TRACKING_FILENAME):
        report_path = benchmark_root / filename
        if report_path.exists():
            report_path.unlink()
            removed_files.append(report_path)

    if dirnames is None:
        if not benchmark_root.exists():
            return SimpleNamespace(
                removed_files=removed_files,
                removed_dirs=removed_dirs,
                skipped_dirs=skipped_dirs,
            )

        candidates = []
        for path in benchmark_root.iterdir():
            if is_benchmark_result_dir(path) or (include_staged_dirs and is_staged_cleanup_dir(path)):
                candidates.append(path)
    else:
        candidates = [Path(dirname) for dirname in dirnames]

    for candidate in candidates:
        if not candidate.exists():
            skipped_dirs.append(candidate)
            continue

        if not _is_relative_to(candidate, benchmark_root):
            raise ValueError(f"Refusing to delete outside benchmark root: {candidate}")

        if not candidate.is_dir():
            skipped_dirs.append(candidate)
            continue

        cleanup_target = stage_dir_for_cleanup_with_retries(candidate, benchmark_root)
        if cleanup_target is None:
            cleanup_target = candidate

        if not remove_tree_with_retries(cleanup_target):
            skipped_dirs.append(candidate)
            continue
        removed_dirs.append(candidate)

    return SimpleNamespace(
        removed_files=removed_files,
        removed_dirs=removed_dirs,
        skipped_dirs=skipped_dirs,
    )


def cleanup_duplicate_named_runs(dirname, benchmark_root=BENCHMARK_DNAME):
    dirname = Path(dirname)
    if len(dirname.parts) > 1:
        suffix = dirname.name.split("--", 1)[-1]
        keep_path = dirname
    else:
        suffix = dirname.name
        keep_matches = sorted(Path(benchmark_root).glob(f"*--{suffix}"), key=lambda path: path.name)
        keep_path = keep_matches[-1] if keep_matches else None

    if keep_path is None:
        return SimpleNamespace(removed_dirs=[], skipped_dirs=[])

    duplicates = [
        path for path in Path(benchmark_root).glob(f"*--{suffix}")
        if path != keep_path and path.is_dir()
    ]
    summary = cleanup_benchmark_artifacts(duplicates, benchmark_root=benchmark_root)
    return SimpleNamespace(removed_dirs=summary.removed_dirs, skipped_dirs=summary.skipped_dirs)


def hard_cleanup_benchmark_artifacts(dirnames=None, benchmark_root=BENCHMARK_DNAME):
    benchmark_root = Path(benchmark_root)
    kill_summary = kill_tracked_processes(benchmark_root)
    cleanup_summary = cleanup_benchmark_artifacts(
        dirnames,
        benchmark_root=benchmark_root,
        include_staged_dirs=True,
    )
    second_pass_summary = cleanup_benchmark_artifacts(
        dirnames,
        benchmark_root=benchmark_root,
        include_staged_dirs=True,
    )

    removed_files = cleanup_summary.removed_files + [
        path for path in second_pass_summary.removed_files if path not in cleanup_summary.removed_files
    ]
    removed_dirs = cleanup_summary.removed_dirs + [
        path for path in second_pass_summary.removed_dirs if path not in cleanup_summary.removed_dirs
    ]
    skipped_dirs = second_pass_summary.skipped_dirs or cleanup_summary.skipped_dirs

    return SimpleNamespace(
        removed_files=removed_files,
        removed_dirs=removed_dirs,
        skipped_dirs=skipped_dirs,
        killed_processes=kill_summary.killed,
        failed_processes=kill_summary.failed,
    )


def get_exercise_dirs(base_dir, languages=None):
    """Get all exercise directories for specified languages (or all if none specified)."""
    base_dir = Path(base_dir)

    lang_dirs = [d for d in base_dir.iterdir() if d.is_dir()]

    if languages:
        requested = set(lang.strip().lower() for lang in languages.split(","))
        lang_dirs = [d for d in lang_dirs if d.name.lower() in requested]
        if not lang_dirs:
            print(f"No matching language directories found for: {languages}")
            return []

    exercise_dirs = []
    for lang_dir in lang_dirs:
        practice_dir = lang_dir / "exercises" / "practice"
        if practice_dir.exists():
            exercise_dirs.extend(d for d in practice_dir.iterdir() if d.is_dir())

    return exercise_dirs


def parse_languages(languages):
    if not languages:
        return []
    return [lang.strip().lower() for lang in languages.split(",") if lang.strip()]


def ensure_requested_tracks_available(exercises_root, languages):
    requested_languages = parse_languages(languages)
    if not requested_languages:
        return True

    exercises_root = Path(exercises_root)
    missing_languages = []
    for language in requested_languages:
        practice_dir = exercises_root / language / "exercises" / "practice"
        if not practice_dir.exists():
            missing_languages.append(language)

    if not missing_languages:
        return True

    from aider_polyglot_benchmark.tracks import clone_tracks

    print(f"Cloning missing Exercism tracks: {', '.join(missing_languages)}")
    clone_tracks(missing_languages, exercises_root)
    return True


def copy_selected_exercise_dirs(source_root, destination_root, test_dnames):
    source_root = Path(source_root)
    destination_root = Path(destination_root)
    destination_root.mkdir(parents=True, exist_ok=True)

    for test_dname in test_dnames:
        relative_path = Path(test_dname)
        source_dir = source_root / relative_path
        destination_dir = destination_root / relative_path
        destination_dir.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(source_dir, destination_dir)


def run_benchmark_for_model(
    dirname,
    model,
    sleep,
    languages,
    edit_format,
    editor_model,
    editor_edit_format,
    replay,
    keywords,
    clean,
    no_unit_tests,
    no_aider,
    verbose,
    tries,
    threads,
    num_tests,
    num_ctx,
    read_model_settings,
    reasoning_effort,
    thinking_tokens,
    max_llm_concurrency,
    rate_limit_retries,
    rate_limit_backoff_initial,
    rate_limit_backoff_max,
    exercises_dir,
    commit_hash,
):
    original_dname = resolve_exercises_dir(exercises_dir)
    requested_languages = parse_languages(languages)

    try:
        if requested_languages:
            ensure_requested_tracks_available(original_dname, languages)
    except Exception as exc:
        print(f"Failed to clone requested Exercism tracks: {exc}")
        return 1

    if not original_dname.exists() or not original_dname.is_dir():
        print(f"Exercise dataset root not found: {original_dname}")
        print("Clone one or more Exercism tracks first, for example:")
        print("  uv run clone-exercism-tracks csharp")
        if not requested_languages:
            print("Or pass --languages so benchmark can clone missing tracks automatically.")
        print("Or point benchmark at an existing root with --exercises-dir.")
        return 1

    exercise_dirs = get_exercise_dirs(original_dname, languages)

    if not exercise_dirs:
        print("No exercise directories found")
        return 1

    test_dnames = sorted(str(d.relative_to(original_dname)) for d in exercise_dirs)

    if keywords:
        keywords = keywords.split(",")
        test_dnames = [dn for dn in test_dnames for keyword in keywords if keyword in dn]

    random.shuffle(test_dnames)
    if num_tests > 0:
        test_dnames = test_dnames[:num_tests]

    if not test_dnames:
        print("No matching exercise directories found")
        return 1

    if clean and dirname.exists():
        print("Cleaning up and replacing", dirname)
        if _is_relative_to(dirname, BENCHMARK_DNAME) and is_benchmark_result_dir(dirname):
            if not remove_tree_with_retries(dirname):
                print("ERROR: failed to delete benchmark run dir", dirname)
                return 1
        else:
            dir_files = set(fn.name for fn in dirname.glob("*"))
            original_files = set(fn.name for fn in original_dname.glob("*"))
            if dir_files != original_files:
                print("ERROR: will not delete dir that does not look like original tests", dirname)
                return 1

            dest = dirname.parent / "OLD" / dirname.name
            if dest.exists():
                old_now = datetime.datetime.now().strftime("%Y-%m-%d-%H-%M-%S")
                dest = dirname.parent / "OLD" / (old_now + dirname.name)

            dirname.rename(dest)

    if not dirname.exists():
        if verbose:
            print(f"Copying {original_dname} -> {dirname} ...")
        copy_selected_exercise_dirs(original_dname, dirname, test_dnames)
        if verbose:
            print("...done")

    resource_metadata = importlib_resources.files("aider.resources").joinpath("model-metadata.json")
    model_metadata_files_loaded = models.register_litellm_models([resource_metadata])
    if verbose:
        dump(model_metadata_files_loaded)

    if read_model_settings:
        try:
            files_loaded = models.register_models([read_model_settings])
            if verbose:
                if files_loaded:
                    print(f"Loaded model settings from: {files_loaded[0]}")
                else:
                    print(f"No model settings loaded from: {read_model_settings}")
        except Exception as e:
            print(f"Error loading model settings: {e}")
            return 1

    LONG_TIMEOUT = 24 * 60 * 60
    sendchat.RETRY_TIMEOUT = LONG_TIMEOUT
    base_coder.RETRY_TIMEOUT = LONG_TIMEOUT
    models.RETRY_TIMEOUT = LONG_TIMEOUT

    progress_task = add_benchmark_task(
        f"{model}",
        total=len(test_dnames),
        status=f"threads={threads}",
    )

    try:
        if threads == 1:
            all_results = []
            for test_path in test_dnames:
                if _CANCEL_EVENT.is_set():
                    break
                update_benchmark_task(progress_task, status=f"running {Path(test_path).name}")
                results = run_test(
                    original_dname,
                    dirname / test_path,
                    model,
                    edit_format,
                    tries,
                    no_unit_tests,
                    no_aider,
                    verbose,
                    commit_hash,
                    replay,
                    editor_model,
                    editor_edit_format,
                    num_ctx,
                    sleep,
                    reasoning_effort,
                    thinking_tokens,
                    max_llm_concurrency,
                    rate_limit_retries,
                    rate_limit_backoff_initial,
                    rate_limit_backoff_max,
                )

                all_results.append(results)
                update_benchmark_task(progress_task, advance=1, status=f"done {Path(test_path).name}")
                summarize_results(dirname, quiet=not verbose)
                write_aggregate_reports_for_progress(dirname, quiet=not verbose)
                if sleep:
                    cancel_aware_sleep(sleep)
        else:
            all_results = []
            cancelled = False
            executor = ThreadPoolExecutor(max_workers=threads)
            try:
                futures = []
                for test_path in test_dnames:
                    if _CANCEL_EVENT.is_set():
                        cancelled = True
                        break
                    update_benchmark_task(progress_task, status=f"queued {Path(test_path).name}")
                    future = executor.submit(
                        run_test,
                        original_dname,
                        dirname / test_path,
                        model,
                        edit_format,
                        tries,
                        no_unit_tests,
                        no_aider,
                        verbose,
                        commit_hash,
                        replay,
                        editor_model,
                        editor_edit_format,
                        num_ctx,
                        sleep,
                        reasoning_effort,
                        thinking_tokens,
                        max_llm_concurrency,
                        rate_limit_retries,
                        rate_limit_backoff_initial,
                        rate_limit_backoff_max,
                    )
                    futures.append(future)

                for future in futures:
                    if _CANCEL_EVENT.is_set():
                        cancelled = True
                        break
                    try:
                        result = future.result()
                        all_results.append(result)
                        test_name = Path(result.get("testcase", "case")).name if result else "case"
                        update_benchmark_task(progress_task, advance=1, status=f"done {test_name}")
                        summarize_results(dirname, quiet=not verbose)
                        write_aggregate_reports_for_progress(dirname, quiet=not verbose)
                    except BenchmarkCancelled:
                        _CANCEL_EVENT.set()
                        cancelled = True
                        break
                    except Exception:
                        traceback.print_exc()
            except KeyboardInterrupt:
                _CANCEL_EVENT.set()
                cancelled = True
                raise
            finally:
                shutdown_executor(executor, cancelled or _CANCEL_EVENT.is_set())

            if cancelled or _CANCEL_EVENT.is_set():
                benchmark_print(f"Benchmark cancelled for {model}", style="yellow")
                summarize_results(dirname, quiet=not verbose)
                return 2
    except KeyboardInterrupt:
        benchmark_print(f"Benchmark cancelled for {model}", style="yellow")
        _CANCEL_EVENT.set()
        summarize_results(dirname, quiet=not verbose)
        return 2

    update_benchmark_task(progress_task, status="complete")
    if verbose:
        print()
        print()
        print()
    summarize_results(dirname, quiet=not verbose)

    return 0


@app.command(help=MAIN_HELP)
def main(
    dirnames: Optional[List[str]] = typer.Argument(
        None,
        help="Run directory name(s). Use one name for benchmark, stats, or purge; use multiple only with --diffs or --stats.",
    ),
    model: Optional[List[str]] = typer.Option(
        None,
        "--model",
        "-m",
        help="LiteLLM model name. Repeat to benchmark multiple models.",
    ),
    sleep: float = typer.Option(
        0, "--sleep", help="Sleep seconds between tests when single threaded"
    ),
    languages: Optional[str] = typer.Option(
        None,
        "--languages",
        "-l",
        help="Required for benchmark runs. Only run tests for specific languages (comma separated)",
    ),
    edit_format: str = typer.Option(None, "--edit-format", "-e", help="Edit format"),
    editor_model: str = typer.Option(None, "--editor-model", help="Editor model name"),
    editor_edit_format: str = typer.Option(None, "--editor-edit-format", help="Editor edit format"),
    replay: str = typer.Option(
        None,
        "--replay",
        help="Replay previous .aider.chat.history.md responses from previous benchmark run",
    ),
    keywords: str = typer.Option(
        None, "--keywords", "-k", help="Only run exercises whose path contains one of these comma-separated keywords"
    ),
    clean: bool = typer.Option(
        False, "--clean", "-c", help="Replace an existing benchmark run directory with a fresh copy of the exercise set"
    ),
    cont: bool = typer.Option(
        False, "--cont", help="Continue a previously created matching benchmark run directory"
    ),
    make_new: bool = typer.Option(False, "--new", help="Force creation of a new dated run directory"),
    no_unit_tests: bool = typer.Option(False, "--no-unit-tests", help="Do not run unit tests"),
    no_aider: bool = typer.Option(False, "--no-aider", help="Do not run aider"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Verbose output"),
    stats_only: bool = typer.Option(
        False, "--stats", "-s", help="Do not run benchmarks; summarize completed result files for the provided run dirs or latest run"
    ),
    stats_languages: str = typer.Option(
        None,
        "--stats-languages",
        help="When using --stats, include only these comma-separated languages",
    ),
    diffs_only: bool = typer.Option(
        False, "--diffs", help="Compare pass/fail outcomes across the provided benchmark run dirs"
    ),
    report_only: bool = typer.Option(
        False,
        "--report",
        help="Generate aggregate CSV, HTML, and YAML reports from previously completed benchmark run dirs without running benchmarks",
    ),
    purge: bool = typer.Option(
        False,
        "--purge",
        help="Delete selected benchmark run dirs and generated aggregate report files, then exit",
    ),
    hard_purge: bool = typer.Option(
        False,
        "--hard-purge",
        help="Kill tracked benchmark child processes, remove staged cleanup dirs, and retry deletion until benchmark artifacts are gone",
    ),
    tries: int = typer.Option(2, "--tries", "-r", help="Number of tries for running tests"),
    threads: int = typer.Option(1, "--threads", "-t", help="Number of threads to run in parallel"),
    num_tests: int = typer.Option(-1, "--num-tests", "-n", help="Number of tests to run"),
    num_ctx: Optional[int] = typer.Option(
        None, "--num-ctx", help="Override model context window size"
    ),
    read_model_settings: str = typer.Option(
        None, "--read-model-settings", help="Load aider model settings from YAML file"
    ),
    reasoning_effort: Optional[str] = typer.Option(
        None, "--reasoning-effort", help="Set reasoning effort for models that support it"
    ),
    thinking_tokens: Optional[int] = typer.Option(
        None, "--thinking-tokens", help="Set thinking tokens for models that support it"
    ),
    max_llm_concurrency: Optional[int] = typer.Option(
        None,
        "--max-llm-concurrency",
        help="Maximum concurrent LLM requests per provider scope. Defaults to 2 when using parallel threads, otherwise 1.",
    ),
    rate_limit_retries: int = typer.Option(
        4,
        "--rate-limit-retries",
        help="How many times to retry a model call after a detected rate-limit error.",
    ),
    rate_limit_backoff_initial: float = typer.Option(
        5.0,
        "--rate-limit-backoff-initial",
        help="Initial backoff in seconds after a detected rate-limit error.",
    ),
    rate_limit_backoff_max: float = typer.Option(
        60.0,
        "--rate-limit-backoff-max",
        help="Maximum cooldown in seconds after repeated rate-limit errors.",
    ),
    model_parallelism: Optional[int] = typer.Option(
        None,
        "--model-parallelism",
        help="How many selected models to run in parallel. Defaults to 1.",
    ),
    exercises_dir: str = typer.Option(
        EXERCISES_DIR_DEFAULT,
        "--exercises-dir",
        help="Exercise dataset root directory containing <language>/exercises/practice, defaulting to cloned tracks under exercises",
    ),
    unsafe: bool = typer.Option(
        False,
        "--unsafe",
        help="Allow local execution outside docker after acknowledging that model-generated code will be run",
    ),
):
    install_interrupt_handlers()
    commit_hash = get_commit_hash()

    if stats_only and not dirnames:
        latest_dir = find_latest_benchmark_dir()
        dirnames = [str(latest_dir)]

    if dirnames is None:
        dirnames = []

    if len(dirnames) > 1 and not (stats_only or diffs_only or report_only or purge or hard_purge):
        print("Only provide 1 dirname unless running with --stats, --diffs, --report or --purge")
        raise typer.Exit(code=1)

    reuse_named_run_dir = not (stats_only or diffs_only or report_only or purge or make_new)
    reset_named_run_dir = reuse_named_run_dir and bool(dirnames) and not cont

    updated_dirnames = []
    for dirname in dirnames:
        dirname = Path(dirname)
        dirname = resolve_dirname(
            dirname,
            stats_only or cont or purge or hard_purge or reuse_named_run_dir,
            make_new,
            choose_latest_prior=reuse_named_run_dir,
        )
        if not dirname:
            raise typer.Exit(code=1)
        updated_dirnames.append(dirname)

    if stats_only:
        return show_stats(updated_dirnames, stats_languages)

    if diffs_only:
        return show_diffs(updated_dirnames)

    if report_only:
        write_aggregate_reports(updated_dirnames or None, stats_languages=stats_languages or languages)
        return 0

    if purge or hard_purge:
        cleanup_summary = hard_cleanup_benchmark_artifacts(updated_dirnames or None) if hard_purge else cleanup_benchmark_artifacts(updated_dirnames or None)
        for removed_dir in cleanup_summary.removed_dirs:
            print(f"Removed benchmark dir: {removed_dir}")
        for removed_file in cleanup_summary.removed_files:
            print(f"Removed aggregate report: {removed_file}")
        for skipped_dir in cleanup_summary.skipped_dirs:
            print(f"Skipped benchmark path: {skipped_dir}")
        for pid in getattr(cleanup_summary, "killed_processes", []):
            print(f"Killed tracked process: {pid}")
        for pid in getattr(cleanup_summary, "failed_processes", []):
            print(f"Failed to kill tracked process: {pid}")
        if not cleanup_summary.removed_dirs and not cleanup_summary.removed_files:
            print("No benchmark artifacts found to purge.")
        return 0

    if reset_named_run_dir:
        for dirname in updated_dirnames:
            duplicate_summary = cleanup_duplicate_named_runs(dirname)
            for removed_dir in duplicate_summary.removed_dirs:
                print(f"Removed duplicate benchmark dir: {removed_dir}")
            for skipped_dir in duplicate_summary.skipped_dirs:
                print(f"Skipped duplicate benchmark dir: {skipped_dir}")

    if not languages:
        print("--languages is required when running benchmarks")
        raise typer.Exit(code=1)

    model_names = normalize_models(model)

    if "AIDER_DOCKER" not in os.environ and not unsafe:
        print("Warning: benchmarking runs unvetted code from an LLM.")
        print("Run inside docker, or pass --unsafe if you accept local execution risk.")
        return

    BENCHMARK_DNAME.mkdir(parents=True, exist_ok=True)
    assert BENCHMARK_DNAME.is_dir(), BENCHMARK_DNAME

    try:
        if not updated_dirnames and len(model_names) > 1:
            run_dirnames = build_generated_model_dirnames(model_names)
        else:
            if not updated_dirnames:
                dirname = Path(build_default_run_name(model_names))
                dirname = resolve_dirname(dirname, False, make_new)
                if not dirname:
                    raise typer.Exit(code=1)
                updated_dirnames.append(dirname)

            assert len(updated_dirnames) == 1, updated_dirnames
            dirname = updated_dirnames[0]
            run_dirnames = build_model_dirnames(dirname, model_names, use_base_dirname_for_first=not dirnames)
    except ValueError as exc:
        print(exc)
        raise typer.Exit(code=1)

    llm_concurrency = resolve_llm_concurrency(threads, max_llm_concurrency)
    max_model_parallelism = resolve_model_parallelism(model_names, model_parallelism)
    model_runs = list(zip(model_names, run_dirnames))

    cancelled_models = set()
    _CANCEL_EVENT.clear()
    try:
        with benchmark_progress(enabled=not verbose):
            if max_model_parallelism == 1 or len(model_runs) == 1:
                for model_name, run_dirname in model_runs:
                    if _CANCEL_EVENT.is_set():
                        cancelled_models.add(model_name)
                        continue
                    status = run_single_model_benchmark(
                        model_name,
                        run_dirname,
                        sleep,
                        languages,
                        edit_format,
                        editor_model,
                        editor_edit_format,
                        replay,
                        keywords,
                        clean or reset_named_run_dir,
                        no_unit_tests,
                        no_aider,
                        verbose,
                        tries,
                        threads,
                        num_tests,
                        num_ctx,
                        read_model_settings,
                        reasoning_effort,
                        thinking_tokens,
                        llm_concurrency,
                        rate_limit_retries,
                        rate_limit_backoff_initial,
                        rate_limit_backoff_max,
                        exercises_dir,
                        commit_hash,
                    )
                    if status == 2:
                        cancelled_models.add(model_name)
                    elif status:
                        raise typer.Exit(code=status)
            else:
                cancelled = False
                executor = ThreadPoolExecutor(max_workers=min(max_model_parallelism, len(model_runs)))
                try:
                    futures = {
                        executor.submit(
                            run_single_model_benchmark,
                            model_name,
                            run_dirname,
                            sleep,
                            languages,
                            edit_format,
                            editor_model,
                            editor_edit_format,
                            replay,
                            keywords,
                            clean or reset_named_run_dir,
                            no_unit_tests,
                            no_aider,
                            verbose,
                            tries,
                            threads,
                            num_tests,
                            num_ctx,
                            read_model_settings,
                            reasoning_effort,
                            thinking_tokens,
                            llm_concurrency,
                            rate_limit_retries,
                            rate_limit_backoff_initial,
                            rate_limit_backoff_max,
                            exercises_dir,
                            commit_hash,
                        ): model_name
                        for model_name, run_dirname in model_runs
                    }

                    for future in futures:
                        if _CANCEL_EVENT.is_set():
                            cancelled_models.add(futures[future])
                            cancelled = True
                            continue
                        try:
                            status = future.result()
                        except BenchmarkCancelled:
                            _CANCEL_EVENT.set()
                            cancelled_models.add(futures[future])
                            cancelled = True
                            continue
                        if status == 2:
                            cancelled_models.add(futures[future])
                            cancelled = True
                        elif status:
                            raise typer.Exit(code=status)
                except KeyboardInterrupt:
                    _CANCEL_EVENT.set()
                    cancelled = True
                    raise
                finally:
                    shutdown_executor(executor, cancelled or _CANCEL_EVENT.is_set())
    except KeyboardInterrupt:
        benchmark_print("Benchmark cancelled.", style="yellow")
        _CANCEL_EVENT.set()
        cancelled_models.update(model_name for model_name, _ in model_runs)

    if cancelled_models:
        benchmark_print(f"Cancelled models: {', '.join(sorted(cancelled_models))}", style="yellow")
    benchmark_print("Writing aggregate reports for completed models...", style="cyan")
    write_aggregate_reports(
        run_dirnames,
        stats_languages=languages,
        write_empty_yaml=bool(cancelled_models),
        quiet=not verbose,
    )
    if not verbose:
        benchmark_print("Aggregate reports updated.", style="green")
    if cancelled_models:
        raise typer.Exit(code=2)
    return 0


def show_diffs(dirnames):
    dirnames = sorted(dirnames)

    all_results = dict((dirname, load_results(dirname)) for dirname in dirnames)
    testcases = set()
    for results in all_results.values():
        testcases.update(result["testcase"] for result in results)

    testcases = sorted(testcases)

    unchanged = set()

    for testcase in testcases:
        all_outcomes = []
        for dirname in dirnames:
            results = all_results[dirname]
            result = [r for r in results if r["testcase"] == testcase][0]

            outcomes = tuple(result["tests_outcomes"])
            all_outcomes.append(True in outcomes)

        if len(set(all_outcomes)) == 1:
            unchanged.add(testcase)
            continue

        print()
        print(testcase)
        for outcome, dirname in zip(all_outcomes, dirnames):
            print(outcome, f"{dirname}/{testcase}/.aider.chat.history.md")

    changed = set(testcases) - unchanged
    print()
    print("changed:", len(changed), ",".join(sorted(changed)))
    print()
    print("unchanged:", len(unchanged), ",".join(sorted(unchanged)))


def load_results(dirname, stats_languages=None):
    dirname = Path(dirname)
    all_results = []

    if stats_languages:
        languages = [lang.strip().lower() for lang in stats_languages.split(",")]
        glob_patterns = [f"{lang}/exercises/practice/*/.aider.results.json" for lang in languages]
    else:
        glob_patterns = ["*/exercises/practice/*/.aider.results.json"]

    for pattern in glob_patterns:
        for fname in dirname.glob(pattern):
            try:
                results = json.loads(fname.read_text())
                all_results.append(results)
            except json.JSONDecodeError:
                print("json.JSONDecodeError", fname)
                continue
    return all_results


def summarize_results(dirname, stats_languages=None, quiet=False):
    all_results = load_results(dirname, stats_languages)

    res = SimpleNamespace()
    res.total_tests = len(list(Path(dirname).glob("*/exercises/practice/*")))

    try:
        tries = max(len(results.get("tests_outcomes", [])) for results in all_results if results)
    except ValueError:
        tries = 0

    res.dir_name = str(dirname)

    passed_tests = [0] * tries

    res.completed_tests = 0
    res.duration = 0
    res.cost = 0
    res.error_outputs = 0
    res.user_asks = 0
    res.test_timeouts = 0
    res.exhausted_context_windows = 0
    res.num_malformed_responses = 0
    res.num_with_malformed_responses = 0
    res.syntax_errors = 0
    res.indentation_errors = 0
    res.lazy_comments = 0
    res.prompt_tokens = 0
    res.completion_tokens = 0

    res.reasoning_effort = None
    res.thinking_tokens = None
    variants = defaultdict(set)

    for results in all_results:
        if not results:
            continue

        res.completed_tests += 1
        tests_outcomes = results.get("tests_outcomes", [])
        for i, passed in enumerate(tests_outcomes):
            if passed:
                passed_tests[i] += 1

        res.cost += results.get("cost", 0)
        res.duration += results.get("duration", 0)
        res.test_timeouts += results.get("test_timeouts", 0)

        res.error_outputs += results.get("num_error_outputs", 0)
        res.user_asks += results.get("num_user_asks", 0)
        res.exhausted_context_windows += results.get("num_exhausted_context_windows", 0)
        res.num_malformed_responses += results.get("num_malformed_responses", 0)
        if results.get("num_malformed_responses"):
            res.num_with_malformed_responses += 1
        res.lazy_comments += results.get("lazy_comments", 0)

        res.syntax_errors += results.get("syntax_errors", 0)
        res.indentation_errors += results.get("indentation_errors", 0)

        res.prompt_tokens += results.get("prompt_tokens", 0)
        res.completion_tokens += results.get("completion_tokens", 0)

        res.reasoning_effort = results.get("reasoning_effort")
        res.thinking_tokens = results.get("thinking_tokens")

        for key in "model edit_format commit_hash editor_model editor_edit_format".split():
            val = results.get(key)
            if val:
                variants[key].add(val)

    if not res.completed_tests:
        return

    # if res.completed_tests < 133:
    #    return

    console = Console(highlight=False)
    if not quiet:
        safe_console_rule(console, title=str(dirname))

    commit_hashes = variants["commit_hash"]
    versions = get_versions(commit_hashes)
    date = dirname.name[:10]

    def show(stat, red="red"):
        val = getattr(res, stat)
        style = red if val else None
        console.print(f"  {stat}: {val}", style=style)

    percents = dict()
    for i in range(tries):
        pass_rate = 100 * passed_tests[i] / res.completed_tests
        percents[i] = pass_rate
        # console.print(f"{pass_rate:.1f}% correct after try {i+1}")
        setattr(res, f"pass_rate_{i + 1}", f"{pass_rate:.1f}")
        setattr(res, f"pass_num_{i + 1}", passed_tests[i])

    if not quiet:
        print(f"- dirname: {dirname.name}")
    style = None if res.completed_tests == res.total_tests else "red"
    if not quiet:
        console.print(f"  test_cases: {res.completed_tests}", style=style)
    for key, val in variants.items():
        if len(val) > 1:
            style = "red"
        else:
            style = None
        val = ", ".join(map(str, val))
        setattr(res, key, val)
        if not quiet:
            console.print(f"  {key}: {val}", style=style)

    if res.reasoning_effort is not None and not quiet:
        print(f"  reasoning_effort: {res.reasoning_effort}")
    if res.thinking_tokens is not None and not quiet:
        print(f"  thinking_tokens: {res.thinking_tokens}")

    if not quiet:
        for i in range(tries):
            print(f"  pass_rate_{i + 1}: {percents[i]:.1f}")
        for i in range(tries):
            print(f"  pass_num_{i + 1}: {passed_tests[i]}")

    pct_well_formed = 1.0 - res.num_with_malformed_responses / res.completed_tests
    if not quiet:
        print(f"  percent_cases_well_formed: {pct_well_formed * 100:.1f}")

    if not quiet:
        show("error_outputs")
        show("num_malformed_responses")
        show("num_with_malformed_responses")
        show("user_asks")
        show("lazy_comments")
        show("syntax_errors")
        show("indentation_errors")
        show("exhausted_context_windows")
        show("prompt_tokens", red=None)
        show("completion_tokens", red=None)
        show("test_timeouts")
        print(f"  total_tests: {res.total_tests}")

    command = ""
    if variants["model"]:
        a_model = set(variants["model"]).pop()
        command = f"aider --model {a_model}"
        if not quiet:
            print(f"  command: {command}")

    if not quiet:
        print(f"  date: {date}")
        print("  versions:", ",".join(versions))

    res.avg_duration = res.duration / res.completed_tests
    if not quiet:
        print(f"  seconds_per_case: {res.avg_duration:.1f}")

    if not quiet:
        print(f"  total_cost: {res.cost:.4f}")

    res.avg_cost = res.cost / res.completed_tests

    projected_cost = res.avg_cost * res.total_tests

    if not quiet:
        print()
        print(
            f"costs: ${res.avg_cost:.4f}/test-case, ${res.cost:.2f} total,"
            f" ${projected_cost:.2f} projected"
        )

    failed_num = max(0, res.completed_tests - sum(passed_tests[:2]))
    failed_rate = 100 * failed_num / res.completed_tests if res.completed_tests else 0

    report = {
        "dirname": dirname.name,
        "test_cases": res.completed_tests,
        "model": getattr(res, "model", ""),
        "edit_format": getattr(res, "edit_format", ""),
        "commit_hash": getattr(res, "commit_hash", ""),
    }
    for i in range(tries):
        report[f"pass_rate_{i + 1}"] = percents[i]
    for i in range(tries):
        report[f"pass_num_{i + 1}"] = passed_tests[i]
    report["failed_num"] = failed_num
    report["failed_rate"] = failed_rate
    if getattr(res, "editor_model", ""):
        report["editor_model"] = res.editor_model
    if getattr(res, "editor_edit_format", ""):
        report["editor_edit_format"] = res.editor_edit_format
    if res.reasoning_effort is not None:
        report["reasoning_effort"] = res.reasoning_effort
    if res.thinking_tokens is not None:
        report["thinking_tokens"] = res.thinking_tokens
    report["percent_cases_well_formed"] = pct_well_formed * 100
    report["error_outputs"] = res.error_outputs
    report["num_malformed_responses"] = res.num_malformed_responses
    report["num_with_malformed_responses"] = res.num_with_malformed_responses
    report["user_asks"] = res.user_asks
    report["lazy_comments"] = res.lazy_comments
    report["syntax_errors"] = res.syntax_errors
    report["indentation_errors"] = res.indentation_errors
    report["exhausted_context_windows"] = res.exhausted_context_windows
    report["prompt_tokens"] = res.prompt_tokens
    report["completion_tokens"] = res.completion_tokens
    report["test_timeouts"] = res.test_timeouts
    report["total_tests"] = res.total_tests
    report["command"] = command
    report["date"] = date
    report["versions"] = ",".join(versions)
    report["seconds_per_case"] = res.avg_duration
    report["total_cost"] = res.cost
    write_benchmark_report(dirname, report)

    if not quiet:
        safe_console_rule(console)

    # print(json.dumps(vars(res), indent=4, sort_keys=True))
    return res


def get_versions(commit_hashes):
    versions = set()
    installed_version = None

    try:
        installed_version = importlib_metadata.version("aider-chat")
    except importlib_metadata.PackageNotFoundError:
        installed_version = None

    for hsh in commit_hashes:
        if not hsh:
            continue
        hsh = hsh.split("-")[0]
        try:
            version = subprocess.check_output(
                ["git", "show", f"{hsh}:aider/__init__.py"],
                universal_newlines=True,
                stderr=subprocess.DEVNULL,
            )
            match = re.search(r'__version__ = "(.*)"', version)
            if match:
                versions.add(match.group(1))
        except subprocess.CalledProcessError:
            pass

    if not versions and installed_version:
        versions.add(installed_version)

    return versions


def get_replayed_content(replay_dname, test_dname):
    replay_dname = Path(replay_dname)
    test_dname = Path(test_dname)
    dump(replay_dname, test_dname)

    test_name = test_dname.name
    replay_fname = replay_dname / test_name / ".aider.chat.history.md"
    dump(replay_fname)

    res = replay_fname.read_text()
    return res

    res = res.splitlines(keepends=True)
    res = [line for line in res if not line.startswith("> ") and not line.startswith("#### ")]
    return "".join(res)


def run_test(original_dname, testdir, *args, **kwargs):
    try:
        return run_test_real(original_dname, testdir, *args, **kwargs)
    except Exception:
        print("=" * 40)
        print("Test failed")
        traceback.print_exc()

        testdir = Path(testdir)
        results_fname = testdir / ".aider.results.json"
        results_fname.write_text(json.dumps(dict(exception=traceback.format_exc())))


def run_test_real(
    original_dname,
    testdir,
    model_name,
    edit_format,
    tries,
    no_unit_tests,
    no_aider,
    verbose,
    commit_hash,
    replay,
    editor_model,
    editor_edit_format,
    num_ctx=None,
    sleep=0,
    reasoning_effort: Optional[str] = None,
    thinking_tokens: Optional[int] = None,
    max_llm_concurrency=1,
    rate_limit_retries=4,
    rate_limit_backoff_initial=5.0,
    rate_limit_backoff_max=60.0,
    read_model_settings=None,
):
    if not os.path.isdir(testdir):
        print("Not a dir:", testdir)
        return

    testdir = Path(testdir)

    history_fname = testdir / ".aider.chat.history.md"

    results_fname = testdir / ".aider.results.json"
    if results_fname.exists():
        try:
            res = json.loads(results_fname.read_text())
            # if res.get("test_timeouts", 0) > 0:
            #    print(f"{results_fname} test timeouts, redoing...")
            # else:
            return res
        except JSONDecodeError:
            print(f"{results_fname} failed to parse, redoing...")

    # Read solution and test files from config
    fnames = []
    config_file = testdir / ".meta/config.json"
    if not config_file.exists():
        raise ValueError(f"No config file found: {config_file}")

    with open(config_file) as f:
        config = json.loads(f.read())

    # Get file sets from config
    test_files = config.get("files", {}).get("test", [])
    example_files = config.get("files", {}).get("example", [])
    solution_files = set(config.get("files", {}).get("solution", []))

    # Forcibly ignore certain files not covered by test_files and example_files
    ignore_files = set(
        [
            "CMakeLists.txt",
            "Cargo.toml",
        ]
    )

    # Add all files under .meta and .docs directories
    ignore_files.update(str(p.relative_to(testdir)) for p in testdir.glob(".meta/**/*"))
    ignore_files.update(str(p.relative_to(testdir)) for p in testdir.glob(".docs/**/*"))

    # Also ignore test & example files
    ignore_files.update(test_files)
    ignore_files.update(example_files)

    # Remove any ignore files from the solution set that LLM will edit
    solution_files.difference_update(ignore_files)

    # Copy all solution files
    for file_path in solution_files:
        src = testdir / Path(file_path)
        if src.exists():
            fnames.append(src)
            # restore the original file, in case we interrupted a prev run
            # Find the original file in the language-specific practice dir
            lang_part = str(testdir).split("/exercises/practice/")[0]
            original_fname = (
                original_dname
                / Path(lang_part).name
                / "exercises"
                / "practice"
                / testdir.name
                / file_path
            )
            if original_fname.exists():
                os.makedirs(src.parent, exist_ok=True)
                shutil.copy(original_fname, src)
        else:
            print(f"Warning: Solution file not found: {src}")

    file_list = " ".join(fname.name for fname in fnames)

    instructions = ""

    introduction = testdir / ".docs/introduction.md"
    if introduction.exists():
        instructions += introduction.read_text(encoding="utf-8", errors="replace")
    instructions += (testdir / ".docs/instructions.md").read_text(
        encoding="utf-8", errors="replace"
    )
    instructions_append = testdir / ".docs/instructions.append.md"
    if instructions_append.exists():
        instructions += instructions_append.read_text(encoding="utf-8", errors="replace")

    instructions += prompts.instructions_addendum.format(file_list=file_list)

    io = InputOutput(
        pretty=False,
        yes=True,
        chat_history_file=history_fname,
    )

    # weak_model_name = model_name
    weak_model_name = None

    main_model = models.Model(
        model_name,
        weak_model=weak_model_name,
        editor_model=editor_model,
        editor_edit_format=editor_edit_format,
        verbose=verbose,
    )

    if reasoning_effort is not None:
        main_model.set_reasoning_effort(reasoning_effort)

    if thinking_tokens is not None:
        main_model.set_thinking_tokens(thinking_tokens)

    if verbose:
        dump(main_model.max_chat_history_tokens)

    if num_ctx:
        if not main_model.extra_params:
            main_model.extra_params = {}
        main_model.extra_params["num_ctx"] = num_ctx
    edit_format = edit_format or main_model.edit_format

    if verbose:
        dump(main_model)
        dump(edit_format)
    show_fnames = ",".join(map(str, fnames))
    if verbose:
        print("fnames:", show_fnames)

    with quiet_output(verbose):
        coder = Coder.create(
            main_model,
            edit_format,
            io,
            fnames=fnames,
            use_git=False,
            stream=False,
            verbose=verbose,
            # auto_lint=False,  # disabled for code-in-json experiments
            cache_prompts=True,
            suggest_shell_commands=False,
            ignore_mentions=ignore_files,
        )
    if verbose:
        dump(coder.ignore_mentions)

    with quiet_output(verbose):
        coder.show_announcements()
    coder.get_file_mentions = lambda x: set()  # No loading of any other files

    rate_limit_scope = get_rate_limit_scope(model_name)
    llm_limiter = get_shared_rate_limiter(
        rate_limit_scope,
        max_llm_concurrency,
        rate_limit_backoff_initial,
        rate_limit_backoff_max,
    )

    timeouts = 0

    syntax_errors = 0
    indentation_errors = 0
    lazy_comments = 0

    dur = 0
    test_outcomes = []
    for i in range(tries):
        raise_if_cancelled()
        start = time.time()

        if no_aider:
            pass
        elif replay:
            response = get_replayed_content(replay, testdir)
            coder.partial_response_content = response

            show = response.splitlines(keepends=True)
            show = [">> " + line for line in show]
            io.append_chat_history("".join(show))

            with quiet_output(verbose):
                coder.apply_updates()
        else:
            with quiet_output(verbose):
                response = run_with_rate_limit_retry(
                    lambda: coder.run(with_message=instructions, preproc=False),
                    llm_limiter,
                    rate_limit_scope,
                    rate_limit_retries,
                )

        dur += time.time() - start

        if not no_aider:
            pat = r"^[+]? *[#].* [.][.][.] "
            # Count the number of lines that match pat in response
            if verbose:
                dump(response)
            lazy_comments += len(re.findall(pat, response, re.MULTILINE))
            if verbose:
                dump(lazy_comments)

        if coder.last_keyboard_interrupt:
            raise KeyboardInterrupt

        if no_unit_tests:
            break

        try:
            errors = run_unit_tests(original_dname, testdir, history_fname, test_files, verbose=verbose)
        except subprocess.TimeoutExpired:
            # try:
            #    errors = run_unit_tests(original_dname, testdir, history_fname, test_files)
            # except subprocess.TimeoutExpired:
            errors = "Tests timed out!"
            timeouts += 1

        if errors:
            test_outcomes.append(False)
        else:
            test_outcomes.append(True)
            break

        if replay:
            io.append_chat_history(errors)

        errors = errors.splitlines()

        syntax_errors += sum(1 for line in errors if line.startswith("SyntaxError"))
        indentation_errors += sum(1 for line in errors if line.startswith("IndentationError"))

        if verbose:
            print(errors[-1])
        errors = "\n".join(errors)
        instructions = errors
        instructions += prompts.test_failures.format(file_list=file_list)

    # Clean up build directories after all attempts
    # Rust target/debug
    target_dir = testdir / "target" / "debug"
    if target_dir.exists():
        try:
            shutil.rmtree(target_dir)
            if verbose:
                print(f"Cleaned up Rust target/debug directory: {target_dir}")
        except (OSError, shutil.Error, PermissionError) as e:
            if verbose:
                print(f"Failed to clean up Rust target/debug directory: {e}")

    # Node.js node_modules directories
    node_modules_dir = testdir / "node_modules"
    if node_modules_dir.exists():
        try:
            shutil.rmtree(node_modules_dir)
            if verbose:
                print(f"Cleaned up Node.js node_modules directory: {node_modules_dir}")
        except (OSError, shutil.Error, PermissionError) as e:
            if verbose:
                print(f"Failed to clean up Node.js node_modules directory: {e}")

    results = dict(
        testdir=str(testdir),
        testcase=testdir.name,
        model=main_model.name,
        edit_format=edit_format,
        tests_outcomes=test_outcomes,
        cost=coder.total_cost,
        duration=dur,
        test_timeouts=timeouts,
        commit_hash=commit_hash,
        num_error_outputs=io.num_error_outputs,
        num_user_asks=io.num_user_asks,
        num_exhausted_context_windows=coder.num_exhausted_context_windows,
        num_malformed_responses=coder.num_malformed_responses,
        syntax_errors=syntax_errors,
        indentation_errors=indentation_errors,
        lazy_comments=lazy_comments,  # Add the count of pattern matches to the results
        reasoning_effort=reasoning_effort,
        prompt_tokens=coder.total_tokens_sent,
        completion_tokens=coder.total_tokens_received,
        thinking_tokens=thinking_tokens,
        chat_hashes=list(
            zip(
                coder.chat_completion_call_hashes,
                coder.chat_completion_response_hashes,
            )
        ),
    )

    if edit_format == "architect":
        results["editor_model"] = main_model.editor_model.name if main_model.editor_model else None
        results["editor_edit_format"] = main_model.editor_edit_format
    if verbose:
        dump(results)

    results_fname.write_text(json.dumps(results, indent=4))

    return results


def run_unit_tests(original_dname, testdir, history_fname, test_files, verbose=False):
    timeout = 60 * 3

    # Map of file extensions to test commands
    TEST_COMMANDS = {
        ".rs": ["cargo", "test", "--", "--include-ignored"],
        ".go": ["go", "test", "./..."],
        ".js": get_script_command("npm-test.sh"),
        ".cpp": get_script_command("cpp-test.sh"),
        ".cs": ["dotnet", "test", "--nologo", "--verbosity", "minimal"],
    }

    # Get unique file extensions from test files
    extensions = {Path(f).suffix for f in test_files}

    # Find matching test command
    command = None
    for ext in extensions:
        if ext in TEST_COMMANDS:
            command = TEST_COMMANDS[ext]
            break

    if not command:
        raise ValueError(f"No test command found for files with extensions: {extensions}")

    # Copy test files from original directory
    for file_path in test_files:
        src = original_dname / Path(*testdir.parts[-4:]) / file_path
        dst = testdir / file_path
        if src.exists():
            if verbose:
                print("copying", src, dst)
            os.makedirs(dst.parent, exist_ok=True)
            shutil.copy(src, dst)

    if verbose:
        print(" ".join(command))

    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        cwd=testdir,
        encoding="utf-8",
        errors="replace",
    )
    register_tracked_process(process.pid)
    try:
        stdout, _ = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        terminate_process_tree(process.pid, force=True)
        stdout, _ = process.communicate()
        raise
    finally:
        unregister_tracked_process(process.pid)

    success = process.returncode == 0
    res = stdout
    res = cleanup_test_output(res, testdir)
    if verbose:
        dump(res)

    with history_fname.open("a", encoding="utf-8", errors="replace") as fh:
        fh.write(f"```\n{res}\n```")

    if not success:
        if verbose:
            print(f"Tests failed: {testdir}")
        return res


def cleanup_test_output(output, testdir=None):
    # remove timing info, to avoid randomizing the response to GPT
    res = re.sub(r"^Ran \d+ tests? in \d+\.\d+s$", "", output, flags=re.MULTILINE)
    res = re.sub(r"^={6,}$", "====", res, flags=re.MULTILINE)
    res = re.sub(r"^-{6,}$", "----", res, flags=re.MULTILINE)
    if testdir is not None:
        res = res.replace(str(testdir), str(testdir.name))
    return res


if __name__ == "__main__":
    app()


def run():
    app()
