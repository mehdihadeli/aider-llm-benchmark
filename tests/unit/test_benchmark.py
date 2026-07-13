# flake8: noqa: E501

import io
import os
import shutil
import subprocess
import sys
import threading
import time
import unittest
import json
from concurrent.futures import Future
from contextlib import redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from typer.testing import CliRunner

from aider_polyglot_benchmark.benchmark import (
    _CANCEL_EVENT,
    BENCHMARK_PROCESS_TRACKING_FILENAME,
    BENCHMARK_REPORT_FILENAMES,
    BenchmarkCancelled,
    app,
    build_default_run_name,
    build_generated_model_dirnames,
    build_model_dirnames,
    cleanup_benchmark_artifacts,
    cleanup_duplicate_named_runs,
    hard_cleanup_benchmark_artifacts,
    cleanup_test_output,
    copy_selected_exercise_dirs,
    ensure_requested_tracks_available,
    find_local_venv_python,
    get_versions,
    extract_retry_after_seconds,
    get_rate_limit_scope,
    get_shared_rate_limiter,
    is_rate_limit_error,
    LEGACY_EXERCISES_DIR_DEFAULT,
    normalize_models,
    parse_languages,
    load_tracked_process_ids,
    register_tracked_process,
    refresh_progress_artifacts,
    reset_shared_rate_limiters,
    resolve_model_parallelism,
    remove_tree,
    remove_tree_with_retries,
    resolve_report_mode,
    run_benchmark_for_model,
    run_with_rate_limit_retry,
    stage_dir_for_cleanup,
    stage_dir_for_cleanup_with_retries,
    resolve_exercises_dir,
    resolve_llm_concurrency,
    summarize_results,
    should_reexec_with_local_venv,
    write_aggregate_reports,
)


RUNNER = CliRunner()


class TestCleanupTestOutput(unittest.TestCase):
    def test_cleanup_test_output(self):
        output = "Ran 5 tests in 0.003s\nOK"
        expected = "\nOK"
        self.assertEqual(cleanup_test_output(output), expected)

        output = "OK"
        expected = "OK"
        self.assertEqual(cleanup_test_output(output), expected)

    def test_cleanup_test_output_replaces_testdir_path(self):
        testdir = Path("/tmp/example-exercise")
        output = f"FAIL in {testdir}\n"
        expected = "FAIL in example-exercise\n"
        self.assertEqual(cleanup_test_output(output, testdir), expected)

    def test_cleanup_test_output_lines(self):
        output = """F
======================================================================
FAIL: test_cleanup_test_output (test_benchmark.TestCleanupTestOutput.test_cleanup_test_output)
----------------------------------------------------------------------
Traceback (most recent call last):
  File \"/Users/gauthier/Projects/aider/benchmark/test_benchmark.py\", line 14, in test_cleanup_test_output
    self.assertEqual(cleanup_test_output(output), expected)
AssertionError: 'OK' != 'OKx'
- OK
+ OKx
?   +
"""

        expected = """F
====
FAIL: test_cleanup_test_output (test_benchmark.TestCleanupTestOutput.test_cleanup_test_output)
----
Traceback (most recent call last):
  File \"/Users/gauthier/Projects/aider/benchmark/test_benchmark.py\", line 14, in test_cleanup_test_output
    self.assertEqual(cleanup_test_output(output), expected)
AssertionError: 'OK' != 'OKx'
- OK
+ OKx
?   +
"""
        self.assertEqual(cleanup_test_output(output), expected)


class TestCleanupBenchmarkArtifacts(unittest.TestCase):
    def test_cleanup_benchmark_artifacts_removes_reports_and_run_dirs(self):
        with TemporaryDirectory() as tmpdir:
            benchmark_root = Path(tmpdir)
            run_dir = benchmark_root / "2026-07-05-12-00-00--sample-run"
            other_run_dir = benchmark_root / "2026-07-05-12-00-01--other-run"
            keep_dir = benchmark_root / "scratch"
            keep_file = benchmark_root / "keep.txt"

            run_dir.mkdir()
            other_run_dir.mkdir()
            keep_dir.mkdir()
            keep_file.write_text("keep", encoding="utf-8")

            for filename in BENCHMARK_REPORT_FILENAMES:
                (benchmark_root / filename).write_text("generated", encoding="utf-8")

            summary = cleanup_benchmark_artifacts(benchmark_root=benchmark_root)

            self.assertFalse(run_dir.exists())
            self.assertFalse(other_run_dir.exists())
            self.assertTrue(keep_dir.exists())
            self.assertTrue(keep_file.exists())
            self.assertEqual(
                sorted(path.name for path in summary.removed_dirs),
                sorted([other_run_dir.name, run_dir.name]),
            )
            self.assertEqual(
                sorted(path.name for path in summary.removed_files),
                sorted(BENCHMARK_REPORT_FILENAMES),
            )

    def test_cleanup_benchmark_artifacts_removes_selected_dir_only(self):
        with TemporaryDirectory() as tmpdir:
            benchmark_root = Path(tmpdir)
            run_dir = benchmark_root / "2026-07-05-12-00-00--sample-run"
            other_run_dir = benchmark_root / "2026-07-05-12-00-01--other-run"

            run_dir.mkdir()
            other_run_dir.mkdir()

            summary = cleanup_benchmark_artifacts([run_dir], benchmark_root=benchmark_root)

            self.assertFalse(run_dir.exists())
            self.assertTrue(other_run_dir.exists())
            self.assertEqual([path.name for path in summary.removed_dirs], [run_dir.name])

    def test_cleanup_benchmark_artifacts_skips_dir_when_delete_fails(self):
        with TemporaryDirectory() as tmpdir:
            benchmark_root = Path(tmpdir)
            run_dir = benchmark_root / "2026-07-05-12-00-00--sample-run"
            run_dir.mkdir()

            with patch("aider_polyglot_benchmark.benchmark.remove_tree", side_effect=PermissionError):
                summary = cleanup_benchmark_artifacts([run_dir], benchmark_root=benchmark_root)

            staged_dirs = [path for path in benchmark_root.iterdir() if path.name.startswith(".deleting-")]
            self.assertFalse(run_dir.exists())
            self.assertEqual(len(staged_dirs), 1)
            self.assertEqual(summary.removed_dirs, [])
            self.assertEqual(summary.skipped_dirs, [run_dir])

    def test_cleanup_benchmark_artifacts_skips_dir_when_delete_leaves_path(self):
        with TemporaryDirectory() as tmpdir:
            benchmark_root = Path(tmpdir)
            run_dir = benchmark_root / "2026-07-05-12-00-00--sample-run"
            run_dir.mkdir()

            with patch("aider_polyglot_benchmark.benchmark.remove_tree"):
                summary = cleanup_benchmark_artifacts([run_dir], benchmark_root=benchmark_root)

            staged_dirs = [path for path in benchmark_root.iterdir() if path.name.startswith(".deleting-")]
            self.assertFalse(run_dir.exists())
            self.assertEqual(len(staged_dirs), 1)
            self.assertEqual(summary.removed_dirs, [])
            self.assertEqual(summary.skipped_dirs, [run_dir])

    def test_cleanup_benchmark_artifacts_falls_back_to_direct_delete_when_staging_fails(self):
        with TemporaryDirectory() as tmpdir:
            benchmark_root = Path(tmpdir)
            run_dir = benchmark_root / "2026-07-05-12-00-00--sample-run"
            run_dir.mkdir()

            with patch(
                "aider_polyglot_benchmark.benchmark.stage_dir_for_cleanup_with_retries",
                return_value=None,
            ):
                summary = cleanup_benchmark_artifacts([run_dir], benchmark_root=benchmark_root)

            self.assertFalse(run_dir.exists())
            self.assertEqual(summary.removed_dirs, [run_dir])
            self.assertEqual(summary.skipped_dirs, [])

    def test_cleanup_duplicate_named_runs_keeps_latest_match(self):
        with TemporaryDirectory() as tmpdir:
            benchmark_root = Path(tmpdir)
            older_run = benchmark_root / "2026-07-14-00-17-13--sample-run"
            latest_run = benchmark_root / "2026-07-14-00-19-44--sample-run"
            older_run.mkdir(parents=True)
            latest_run.mkdir(parents=True)

            summary = cleanup_duplicate_named_runs(latest_run, benchmark_root=benchmark_root)

            self.assertFalse(older_run.exists())
            self.assertTrue(latest_run.exists())
            self.assertEqual(summary.removed_dirs, [older_run])
            self.assertEqual(summary.skipped_dirs, [])

    def test_hard_cleanup_benchmark_artifacts_kills_tracked_processes_and_staged_dirs(self):
        with TemporaryDirectory() as tmpdir:
            benchmark_root = Path(tmpdir)
            staged_dir = benchmark_root / ".deleting-2026-07-05-12-00-00--sample-run"
            staged_dir.mkdir(parents=True)
            tracking_file = benchmark_root / BENCHMARK_PROCESS_TRACKING_FILENAME
            tracking_file.write_text("[123, 456]", encoding="utf-8")

            with patch(
                "aider_polyglot_benchmark.benchmark.terminate_process_tree",
                side_effect=[True, False],
            ):
                summary = hard_cleanup_benchmark_artifacts(benchmark_root=benchmark_root)

            self.assertFalse(staged_dir.exists())
            self.assertFalse(tracking_file.exists())
            self.assertEqual(summary.killed_processes, [123])
            self.assertEqual(summary.failed_processes, [456])

    def test_remove_tree_retries_after_permission_error(self):
        with TemporaryDirectory() as tmpdir:
            target = Path(tmpdir) / "2026-07-05-12-00-00--sample-run"
            blocked_file = target / "bin" / "Debug" / "net10.0" / "Microsoft.Bcl.AsyncInterfaces.dll"
            blocked_file.parent.mkdir(parents=True)
            blocked_file.write_text("dll", encoding="utf-8")
            retried_paths = []

            def fake_rmtree(path, onerror):
                def retry(path):
                    retried_paths.append(path)

                onerror(
                    retry,
                    blocked_file,
                    (PermissionError, PermissionError("access denied"), None),
                )

            with patch("aider_polyglot_benchmark.benchmark.os.chmod") as chmod:
                with patch("aider_polyglot_benchmark.benchmark.shutil.rmtree", fake_rmtree):
                    remove_tree(target)

            chmod.assert_called_once()
            self.assertEqual(retried_paths, [blocked_file])

    def test_remove_tree_with_retries_succeeds_after_transient_failure(self):
        with TemporaryDirectory() as tmpdir:
            target = Path(tmpdir) / "2026-07-05-12-00-00--sample-run"
            target.mkdir()
            calls = []

            def flaky_remove(path):
                calls.append(Path(path))
                if len(calls) == 1:
                    raise OSError("busy")
                shutil.rmtree(path)

            with patch("aider_polyglot_benchmark.benchmark.remove_tree", side_effect=flaky_remove):
                self.assertTrue(remove_tree_with_retries(target, attempts=2, delay_seconds=0.0))

            self.assertFalse(target.exists())
            self.assertEqual(calls, [target, target])

    def test_stage_dir_for_cleanup_renames_to_hidden_path(self):
        with TemporaryDirectory() as tmpdir:
            benchmark_root = Path(tmpdir)
            target = benchmark_root / "2026-07-05-12-00-00--sample-run"
            target.mkdir()

            staged = stage_dir_for_cleanup(target, benchmark_root)

            self.assertFalse(target.exists())
            self.assertTrue(staged.exists())
            self.assertEqual(staged.name, ".deleting-2026-07-05-12-00-00--sample-run")

    def test_stage_dir_for_cleanup_with_retries_returns_none_after_repeated_failures(self):
        with TemporaryDirectory() as tmpdir:
            benchmark_root = Path(tmpdir)
            target = benchmark_root / "2026-07-05-12-00-00--sample-run"
            target.mkdir()

            with patch(
                "aider_polyglot_benchmark.benchmark.stage_dir_for_cleanup",
                side_effect=OSError("busy"),
            ):
                staged = stage_dir_for_cleanup_with_retries(target, benchmark_root, attempts=2, delay_seconds=0.0)

            self.assertIsNone(staged)
            self.assertTrue(target.exists())

    def test_register_tracked_process_keeps_all_ids_under_concurrent_updates(self):
        with TemporaryDirectory() as tmpdir:
            benchmark_root = Path(tmpdir)
            barrier = threading.Barrier(8)
            threads = []

            def worker(pid):
                barrier.wait()
                register_tracked_process(pid, benchmark_root=benchmark_root)

            for pid in range(101, 109):
                thread = threading.Thread(target=worker, args=(pid,))
                thread.start()
                threads.append(thread)

            for thread in threads:
                thread.join()

            self.assertEqual(load_tracked_process_ids(benchmark_root), list(range(101, 109)))


class TestLocalVenvHelpers(unittest.TestCase):
    def test_find_local_venv_python_prefers_repo_venv_layout(self):
        with TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir)
            if os.name == "nt":
                python_path = repo_root / ".venv" / "Scripts" / "python.exe"
            else:
                python_path = repo_root / ".venv" / "bin" / "python"

            python_path.parent.mkdir(parents=True)
            python_path.write_text("", encoding="utf-8")

            self.assertEqual(find_local_venv_python(repo_root), python_path)

    def test_should_reexec_with_local_venv_only_when_interpreters_differ(self):
        current_python = Path(sys.executable)
        self.assertFalse(should_reexec_with_local_venv(current_python, current_python))
        self.assertTrue(
            should_reexec_with_local_venv(
                current_python,
                current_python.parent / "alternate-python.exe",
            )
        )


class TestExercisesDirResolution(unittest.TestCase):
    def test_resolve_exercises_dir_prefers_repo_relative_path_when_missing(self):
        resolved = resolve_exercises_dir("exercises")
        self.assertEqual(resolved.name, "exercises")
        self.assertEqual(resolved.parent, Path.cwd())

    def test_resolve_exercises_dir_falls_back_to_legacy_track_root(self):
        resolved = resolve_exercises_dir("exercises")
        if resolved.exists():
            self.skipTest("root exercises directory exists; fallback path is not exercised")

        legacy_resolved = resolve_exercises_dir(LEGACY_EXERCISES_DIR_DEFAULT)
        self.assertEqual(legacy_resolved.name, LEGACY_EXERCISES_DIR_DEFAULT)


class TestLanguageTrackHelpers(unittest.TestCase):
    def test_parse_languages_normalizes_csv(self):
        self.assertEqual(parse_languages(" CSharp, java ,python "), ["csharp", "java", "python"])

    def test_ensure_requested_tracks_available_clones_missing_languages(self):
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            existing = root / "csharp" / "exercises" / "practice"
            existing.mkdir(parents=True)

            with patch("aider_polyglot_benchmark.tracks.clone_tracks") as clone_tracks:
                ensure_requested_tracks_available(root, "csharp,java")

            clone_tracks.assert_called_once_with(["java"], root)


class TestRateLimitHelpers(unittest.TestCase):
    def tearDown(self):
        _CANCEL_EVENT.clear()
        reset_shared_rate_limiters()

    def test_get_rate_limit_scope_groups_models_by_provider_prefix(self):
        self.assertEqual(get_rate_limit_scope("github/gpt-4.1"), "github")
        self.assertEqual(get_rate_limit_scope("openai/gpt-4o"), "openai")

    def test_is_rate_limit_error_detects_common_429_messages(self):
        self.assertTrue(is_rate_limit_error(RuntimeError("429 Too Many Requests")))
        self.assertTrue(is_rate_limit_error(RuntimeError("retry-after: 12")))
        self.assertFalse(is_rate_limit_error(RuntimeError("connection reset by peer")))

    def test_extract_retry_after_seconds_prefers_response_header(self):
        response = type("Response", (), {"headers": {"Retry-After": "7"}})()
        exc = type("RateLimitError", (Exception,), {})("slow down")
        exc.response = response

        self.assertEqual(extract_retry_after_seconds(exc), 7.0)

    def test_run_with_rate_limit_retry_retries_detected_rate_limit_errors(self):
        limiter = get_shared_rate_limiter("github", 1, 0.0, 0.0)
        calls = []

        def flaky_call():
            calls.append("call")
            if len(calls) == 1:
                raise RuntimeError("429 Too Many Requests")
            return "ok"

        result = run_with_rate_limit_retry(flaky_call, limiter, "github", 2)

        self.assertEqual(result, "ok")
        self.assertEqual(calls, ["call", "call"])

    def test_run_with_rate_limit_retry_stops_when_cancelled(self):
        limiter = get_shared_rate_limiter("github", 1, 0.0, 0.0)
        _CANCEL_EVENT.set()

        with self.assertRaises(BenchmarkCancelled):
            run_with_rate_limit_retry(lambda: "ok", limiter, "github", 2)

    def test_resolve_llm_concurrency_defaults_to_safe_parallel_cap(self):
        self.assertEqual(resolve_llm_concurrency(1, None), 1)
        self.assertEqual(resolve_llm_concurrency(8, None), 2)
        self.assertEqual(resolve_llm_concurrency(8, 5), 5)

    def test_resolve_model_parallelism_defaults_to_one(self):
        self.assertEqual(resolve_model_parallelism(["github/gpt-4o"], None), 1)
        self.assertEqual(resolve_model_parallelism(["github/gpt-4o", "github/gpt-4.1"], None), 1)
        self.assertEqual(resolve_model_parallelism(["github/gpt-4o"], 3), 3)

    def test_resolve_report_mode_defaults_to_end_when_parallel(self):
        self.assertEqual(resolve_report_mode("auto", 2, 1, 1), "end")
        self.assertEqual(resolve_report_mode("auto", 1, 2, 1), "end")
        self.assertEqual(resolve_report_mode("auto", 1, 1, 2), "end")
        self.assertEqual(resolve_report_mode("auto", 1, 1, 1), "live")
        self.assertEqual(resolve_report_mode("live", 4, 3, 2), "live")
        self.assertEqual(resolve_report_mode("end", 1, 1, 1), "end")


class TestMainCli(unittest.TestCase):
    def tearDown(self):
        _CANCEL_EVENT.clear()

    def test_main_requires_languages_for_benchmark_runs(self):
        result = RUNNER.invoke(app, ["sample-run", "--model", "github/gpt-4.1", "--unsafe"])

        self.assertEqual(result.exit_code, 1)
        self.assertIn("--languages is required when running benchmarks", result.stdout)

    def test_main_supports_parallel_model_runs(self):
        with TemporaryDirectory() as tmpdir:
            benchmark_root = Path(tmpdir) / "tmp.benchmarks"
            benchmark_root.mkdir(parents=True)
            exercises_root = Path(tmpdir) / "tracks"
            (exercises_root / "csharp" / "exercises" / "practice" / "alpha").mkdir(parents=True)
            env = {
                "AIDER_BENCHMARK_DIR": str(benchmark_root),
                "AIDER_DOCKER": "1",
            }
            called_models = []

            def fake_run_single_model_benchmark(model_name, run_dirname, *args):
                called_models.append((model_name, Path(run_dirname).name))
                return 0

            with patch(
                "aider_polyglot_benchmark.benchmark.run_single_model_benchmark",
                side_effect=fake_run_single_model_benchmark,
            ) as run_model:
                with patch("aider_polyglot_benchmark.benchmark.write_aggregate_reports") as write_reports:
                    result = RUNNER.invoke(
                        app,
                        [
                            "parallel-run",
                            "--model",
                            "github/gpt-4o",
                            "--model",
                            "openai/gpt-4o",
                            "--model-parallelism",
                            "2",
                            "--languages",
                            "csharp",
                            "--no-aider",
                            "--no-unit-tests",
                            "--exercises-dir",
                            str(exercises_root),
                        ],
                        env=env,
                    )

            self.assertEqual(result.exit_code, 0, result.stdout)
            self.assertEqual(run_model.call_count, 2)
            self.assertEqual(sorted(model for model, _ in called_models), ["github/gpt-4o", "openai/gpt-4o"])
            write_reports.assert_called_once()

    def test_main_parallel_keyboard_interrupt_shuts_down_executor_without_waiting(self):
        with TemporaryDirectory() as tmpdir:
            benchmark_root = Path(tmpdir) / "tmp.benchmarks"
            benchmark_root.mkdir(parents=True)
            exercises_root = Path(tmpdir) / "tracks"
            (exercises_root / "csharp" / "exercises" / "practice" / "alpha").mkdir(parents=True)
            env = {
                "AIDER_BENCHMARK_DIR": str(benchmark_root),
                "AIDER_DOCKER": "1",
            }
            shutdown_calls = []

            class FakeFuture:
                def __init__(self):
                    self._future = Future()
                    self._future.set_exception(KeyboardInterrupt())

                def __getattr__(self, name):
                    return getattr(self._future, name)

            class FakeExecutor:
                def __init__(self, *args, **kwargs):
                    pass

                def submit(self, *args, **kwargs):
                    return FakeFuture()

                def shutdown(self, wait, cancel_futures):
                    shutdown_calls.append((wait, cancel_futures))

            with patch("aider_polyglot_benchmark.benchmark.ThreadPoolExecutor", FakeExecutor):
                with patch("aider_polyglot_benchmark.benchmark.write_aggregate_reports") as write_reports:
                    result = RUNNER.invoke(
                        app,
                        [
                            "parallel-run",
                            "--model",
                            "github/gpt-4o",
                            "--model",
                            "openai/gpt-4o",
                            "--model-parallelism",
                            "2",
                            "--languages",
                            "csharp",
                            "--no-aider",
                            "--no-unit-tests",
                            "--exercises-dir",
                            str(exercises_root),
                        ],
                        env=env,
                    )

            self.assertEqual(result.exit_code, 2, result.stdout)
            self.assertEqual(shutdown_calls, [(False, True)])
            write_reports.assert_called_once()

    def test_main_uses_model_names_when_run_name_is_omitted(self):
        with TemporaryDirectory() as tmpdir:
            benchmark_root = Path(tmpdir) / "tmp.benchmarks"
            benchmark_root.mkdir(parents=True)
            exercises_root = Path(tmpdir) / "tracks"
            (exercises_root / "csharp" / "exercises" / "practice" / "alpha").mkdir(parents=True)
            env = {
                "AIDER_BENCHMARK_DIR": str(benchmark_root),
                "AIDER_DOCKER": "1",
            }
            called_dirnames = []

            def fake_run_single_model_benchmark(model_name, run_dirname, *args):
                called_dirnames.append(Path(run_dirname).name)
                return 0

            with patch(
                "aider_polyglot_benchmark.benchmark.run_single_model_benchmark",
                side_effect=fake_run_single_model_benchmark,
            ) as run_model:
                with patch("aider_polyglot_benchmark.benchmark.write_aggregate_reports"):
                    result = RUNNER.invoke(
                        app,
                        [
                            "--model",
                            "github_copilot/gpt-4",
                            "--model",
                            "github_copilot/gpt-4.1",
                            "--model",
                            "github_copilot/kimi",
                            "--languages",
                            "csharp",
                            "--num-tests",
                            "1",
                            "--no-aider",
                            "--no-unit-tests",
                            "--exercises-dir",
                            str(exercises_root),
                        ],
                        env=env,
                    )

            self.assertEqual(result.exit_code, 0, result.stdout)
            self.assertEqual(run_model.call_count, 3)
            self.assertTrue(any(name.endswith("--gpt-4") for name in called_dirnames))
            self.assertTrue(any(name.endswith("--gpt-4.1") for name in called_dirnames))
            self.assertTrue(any(name.endswith("--kimi") for name in called_dirnames))

    def test_main_reuses_latest_named_run_dir_when_prior_exists(self):
        with TemporaryDirectory() as tmpdir:
            benchmark_root = Path(tmpdir) / "tmp.benchmarks"
            benchmark_root.mkdir(parents=True)
            prior_run_dir = benchmark_root / "2026-07-14-00-17-13--ssssssssssss"
            newer_run_dir = benchmark_root / "2026-07-14-00-19-44--ssssssssssss"
            (prior_run_dir / "csharp" / "exercises" / "practice" / "alpha").mkdir(parents=True)
            (newer_run_dir / "csharp" / "exercises" / "practice" / "alpha").mkdir(parents=True)
            exercises_root = Path(tmpdir) / "tracks"
            (exercises_root / "csharp" / "exercises" / "practice" / "alpha").mkdir(parents=True)
            env = {
                "AIDER_BENCHMARK_DIR": str(benchmark_root),
                "AIDER_DOCKER": "1",
            }
            called_dirnames = []

            def fake_run_single_model_benchmark(model_name, run_dirname, *args):
                called_dirnames.append(Path(run_dirname).name)
                return 0

            with patch(
                "aider_polyglot_benchmark.benchmark.run_single_model_benchmark",
                side_effect=fake_run_single_model_benchmark,
            ) as run_model:
                with patch("aider_polyglot_benchmark.benchmark.BENCHMARK_DNAME", benchmark_root):
                    with patch("aider_polyglot_benchmark.benchmark.write_aggregate_reports"):
                        result = RUNNER.invoke(
                            app,
                            [
                                "ssssssssssss",
                                "--model",
                                "github_copilot/gpt-4",
                                "--languages",
                                "csharp",
                                "--num-tests",
                                "1",
                                "--no-aider",
                                "--no-unit-tests",
                                "--exercises-dir",
                                str(exercises_root),
                            ],
                            env=env,
                        )

            self.assertEqual(result.exit_code, 0, result.stdout)
            self.assertEqual(run_model.call_count, 1)
            self.assertEqual(called_dirnames, [newer_run_dir.name])


class TestThreadedProgress(unittest.TestCase):
    def test_run_benchmark_for_model_skips_live_refresh_in_end_mode(self):
        with TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            source_root = tmp_path / "tracks"
            run_dir = tmp_path / "tmp.benchmarks" / "2026-07-14-00-00-00--sample"
            alpha_dir = source_root / "csharp" / "exercises" / "practice" / "alpha"
            beta_dir = source_root / "csharp" / "exercises" / "practice" / "beta"
            alpha_dir.mkdir(parents=True)
            beta_dir.mkdir(parents=True)

            with patch("aider_polyglot_benchmark.benchmark.random.shuffle", lambda items: None):
                with patch("aider_polyglot_benchmark.benchmark.models.register_litellm_models", return_value=[]):
                    with patch("aider_polyglot_benchmark.benchmark.run_test", return_value={"testcase": "alpha"}):
                        with patch("aider_polyglot_benchmark.benchmark.refresh_progress_artifacts") as refresh:
                            with patch("aider_polyglot_benchmark.benchmark.summarize_results"):
                                status = run_benchmark_for_model(
                                    run_dir,
                                    "github_copilot/gpt-4",
                                    0,
                                    "csharp",
                                    None,
                                    None,
                                    None,
                                    None,
                                    None,
                                    False,
                                    True,
                                    True,
                                    False,
                                    1,
                                    2,
                                    2,
                                    None,
                                    None,
                                    None,
                                    None,
                                    1,
                                    0,
                                    0.0,
                                    0.0,
                                    "end",
                                    str(source_root),
                                    "deadbee",
                                )

            self.assertEqual(status, 0)
            refresh.assert_not_called()

    def test_run_benchmark_for_model_emits_worker_slot_progress_updates(self):
        with TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            source_root = tmp_path / "tracks"
            run_dir = tmp_path / "tmp.benchmarks" / "2026-07-14-00-00-00--sample"
            alpha_dir = source_root / "csharp" / "exercises" / "practice" / "alpha"
            beta_dir = source_root / "csharp" / "exercises" / "practice" / "beta"
            alpha_dir.mkdir(parents=True)
            beta_dir.mkdir(parents=True)

            added_tasks = []
            updated_tasks = []
            next_task_id = 0

            def fake_add_task(description, total, status="queued", visible=True):
                nonlocal next_task_id
                next_task_id += 1
                added_tasks.append(
                    {
                        "id": next_task_id,
                        "description": description,
                        "total": total,
                        "status": status,
                        "visible": visible,
                    }
                )
                return next_task_id

            def fake_update_task(task_id, advance=0, status=None, **kwargs):
                updated_tasks.append(
                    {
                        "task_id": task_id,
                        "advance": advance,
                        "status": status,
                        **kwargs,
                    }
                )

            def fake_run_test(original_dname, testdir, *args, **kwargs):
                time.sleep(0.02)
                return {"testcase": Path(testdir).name}

            with patch("aider_polyglot_benchmark.benchmark.random.shuffle", lambda items: None):
                with patch("aider_polyglot_benchmark.benchmark.models.register_litellm_models", return_value=[]):
                    with patch("aider_polyglot_benchmark.benchmark.add_benchmark_task", side_effect=fake_add_task):
                        with patch("aider_polyglot_benchmark.benchmark.update_benchmark_task", side_effect=fake_update_task):
                            with patch("aider_polyglot_benchmark.benchmark.run_test", side_effect=fake_run_test):
                                with patch("aider_polyglot_benchmark.benchmark.refresh_progress_artifacts"):
                                    status = run_benchmark_for_model(
                                            run_dir,
                                            "github_copilot/gpt-4",
                                            0,
                                            "csharp",
                                            None,
                                            None,
                                            None,
                                            None,
                                            None,
                                            False,
                                            True,
                                            True,
                                            False,
                                            1,
                                            2,
                                            2,
                                            None,
                                            None,
                                            None,
                                            None,
                                            1,
                                            0,
                                            0.0,
                                            0.0,
                                            "live",
                                            str(source_root),
                                            "deadbee",
                                        )

            self.assertEqual(status, 0)
            self.assertEqual(len(added_tasks), 3)
            self.assertEqual(added_tasks[0]["description"], "github_copilot/gpt-4")
            self.assertEqual(
                [task["description"] for task in added_tasks[1:]],
                ["gpt-4 worker 1", "gpt-4 worker 2"],
            )
            self.assertTrue(all(task["visible"] is False for task in added_tasks[1:]))

            worker_updates = [
                update for update in updated_tasks
                if update["task_id"] in {added_tasks[1]["id"], added_tasks[2]["id"]}
            ]
            self.assertTrue(any(update.get("visible") is True for update in worker_updates))
            self.assertTrue(any((update.get("status") or "").startswith("running ") for update in worker_updates))
            self.assertTrue(any(update.get("visible") is False and update.get("status") == "idle" for update in worker_updates))


class TestModelNormalization(unittest.TestCase):
    def test_build_generated_model_dirnames_use_timestamp_and_single_model_slug(self):
        with patch("aider_polyglot_benchmark.benchmark.datetime.datetime") as fake_datetime:
            fake_datetime.now.return_value.strftime.return_value = "2026-07-13-22-37-23--"

            dirnames = build_generated_model_dirnames(
                ["github_copilot/gpt-4", "github_copilot/gpt-4.1"]
            )

        self.assertEqual(
            [dirname.name for dirname in dirnames],
            [
                "2026-07-13-22-37-23--gpt-4",
                "2026-07-13-22-37-23--gpt-4.1",
            ],
        )

    def test_build_model_dirnames_always_adds_per_model_suffixes_for_generated_names(self):
        base_dirname = Path("tmp.benchmarks/2026-07-13-22-37-23--gpt-4_gpt-4.1")

        dirnames = build_model_dirnames(
            base_dirname,
            ["github_copilot/gpt-4", "github_copilot/gpt-4.1"],
            use_base_dirname_for_first=True,
        )

        self.assertEqual(
            [dirname.name for dirname in dirnames],
            [
                "2026-07-13-22-37-23--gpt-4_gpt-4.1--gpt-4",
                "2026-07-13-22-37-23--gpt-4_gpt-4.1--gpt-4.1",
            ],
        )

    def test_build_default_run_name_uses_model_leaf_names(self):
        self.assertEqual(
            build_default_run_name(
                ["github_copilot/gpt-4", "github_copilot/gpt-4.1", "github_copilot/kimi"]
            ),
            "gpt-4_gpt-4.1_kimi",
        )

    def test_normalize_models_uses_model_env_when_flag_missing(self):
        with patch.dict(os.environ, {"MODEL": "anthropic/claude-sonnet-4"}, clear=False):
            self.assertEqual(normalize_models(None), ["anthropic/claude-sonnet-4"])

    def test_normalize_models_prefers_explicit_models(self):
        with patch.dict(os.environ, {"MODEL": "anthropic/claude-sonnet-4"}, clear=False):
            self.assertEqual(normalize_models(["github/gpt-4.1"]), ["github/gpt-4.1"])


class TestVersionResolution(unittest.TestCase):
    def test_get_versions_falls_back_to_installed_aider_version(self):
        with patch(
            "aider_polyglot_benchmark.benchmark.importlib_metadata.version",
            return_value="0.86.2",
        ):
            with patch(
                "aider_polyglot_benchmark.benchmark.subprocess.check_output",
                side_effect=subprocess.CalledProcessError(128, "git"),
            ):
                self.assertEqual(get_versions({"7e9c51a-dirty"}), {"0.86.2"})


class TestSelectedExerciseCopy(unittest.TestCase):
    def test_copy_selected_exercise_dirs_copies_only_requested_tests(self):
        with TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            source_root = tmp_path / "polyglot-benchmark"
            destination_root = tmp_path / "tmp.benchmarks" / "sample-run"

            selected_paths = [
                Path("csharp/exercises/practice/two-fer"),
                Path("csharp/exercises/practice/leap"),
            ]
            unselected_path = Path("csharp/exercises/practice/bob")

            for relative_path in [*selected_paths, unselected_path]:
                exercise_dir = source_root / relative_path
                exercise_dir.mkdir(parents=True)
                (exercise_dir / "stub.txt").write_text(relative_path.name, encoding="utf-8")

            copy_selected_exercise_dirs(source_root, destination_root, [str(path) for path in selected_paths])

            for relative_path in selected_paths:
                self.assertTrue((destination_root / relative_path / "stub.txt").exists())

            self.assertFalse((destination_root / unselected_path).exists())


class TestBenchmarkReport(unittest.TestCase):
    def test_refresh_progress_artifacts_serializes_concurrent_updates(self):
        active_calls = 0
        max_active_calls = 0
        lock = threading.Lock()
        start_barrier = threading.Barrier(6)

        def fake_summarize(dirname, quiet=False):
            nonlocal active_calls, max_active_calls
            with lock:
                active_calls += 1
                max_active_calls = max(max_active_calls, active_calls)
            time.sleep(0.02)
            with lock:
                active_calls -= 1

        def call_refresh():
            start_barrier.wait()
            refresh_progress_artifacts(Path("tmp.benchmarks/sample-run"), quiet=True)

        threads = []
        with patch("aider_polyglot_benchmark.benchmark.summarize_results", side_effect=fake_summarize) as summarize:
            with patch("aider_polyglot_benchmark.benchmark.write_aggregate_reports_for_progress") as write_reports:
                for _ in range(6):
                    thread = threading.Thread(target=call_refresh)
                    thread.start()
                    threads.append(thread)

                for thread in threads:
                    thread.join()

        self.assertEqual(summarize.call_count, 6)
        self.assertEqual(write_reports.call_count, 6)
        self.assertEqual(max_active_calls, 1)

    def test_summarize_results_writes_failed_metrics_to_benchmark_report(self):
        with TemporaryDirectory() as tmpdir:
            run_dir = Path(tmpdir) / "2026-07-06-17-42-16--sample-run"

            alpha = run_dir / "csharp" / "exercises" / "practice" / "alpha"
            beta = run_dir / "csharp" / "exercises" / "practice" / "beta"
            gamma = run_dir / "csharp" / "exercises" / "practice" / "gamma"

            for path in (alpha, beta, gamma):
                path.mkdir(parents=True)

            (alpha / ".aider.results.json").write_text(
                json.dumps({"tests_outcomes": [True], "model": "github/gpt-4.1"}),
                encoding="utf-8",
            )
            (beta / ".aider.results.json").write_text(
                json.dumps({"tests_outcomes": [False, True], "model": "github/gpt-4.1"}),
                encoding="utf-8",
            )
            (gamma / ".aider.results.json").write_text(
                json.dumps({"tests_outcomes": [False, False], "model": "github/gpt-4.1"}),
                encoding="utf-8",
            )

            summarize_results(run_dir)

            report_text = (run_dir / "benchmark-report.yml").read_text(encoding="utf-8")

        self.assertIn("failed_num: 1", report_text)
        self.assertIn("failed_rate: 33.3333", report_text)

    def test_summarize_results_quiet_writes_report_without_console_dump(self):
        with TemporaryDirectory() as tmpdir:
            run_dir = Path(tmpdir) / "2026-07-06-17-42-16--sample-run"
            alpha = run_dir / "csharp" / "exercises" / "practice" / "alpha"
            alpha.mkdir(parents=True)
            (alpha / ".aider.results.json").write_text(
                json.dumps({"tests_outcomes": [True], "model": "github/gpt-4.1"}),
                encoding="utf-8",
            )

            stdout = io.StringIO()
            with patch("aider_polyglot_benchmark.benchmark.get_versions", return_value={"0.86.2"}):
                with redirect_stdout(stdout):
                    summarize_results(run_dir, quiet=True)

            self.assertEqual(stdout.getvalue(), "")
            self.assertTrue((run_dir / "benchmark-report.yml").exists())

    def test_write_aggregate_reports_quiet_writes_files_without_console_dump(self):
        with TemporaryDirectory() as tmpdir:
            benchmark_root = Path(tmpdir) / "tmp.benchmarks"
            run_dir = benchmark_root / "2026-07-06-17-42-16--sample-run"
            alpha = run_dir / "csharp" / "exercises" / "practice" / "alpha"
            alpha.mkdir(parents=True)
            (alpha / ".aider.results.json").write_text(
                json.dumps({"tests_outcomes": [True], "model": "github/gpt-4.1"}),
                encoding="utf-8",
            )

            stdout = io.StringIO()
            with patch("aider_polyglot_benchmark.leaderboard_report.BENCHMARK_ROOT", benchmark_root):
                with redirect_stdout(stdout):
                    write_aggregate_reports([run_dir], quiet=True)

            self.assertEqual(stdout.getvalue(), "")
            self.assertTrue((benchmark_root / "leaderboard.md").exists())
            self.assertTrue((benchmark_root / "leaderboard.html").exists())
            self.assertTrue((benchmark_root / "polyglot_leaderboard.yml").exists())