# flake8: noqa: E501

import os
import subprocess
import sys
import unittest
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from typer.testing import CliRunner

from aider_polyglot_benchmark.benchmark import (
    BENCHMARK_REPORT_FILENAMES,
    app,
    cleanup_benchmark_artifacts,
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
    reset_shared_rate_limiters,
    resolve_model_parallelism,
    run_with_rate_limit_retry,
    resolve_exercises_dir,
    resolve_llm_concurrency,
    summarize_results,
    should_reexec_with_local_venv,
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

    def test_resolve_llm_concurrency_defaults_to_safe_parallel_cap(self):
        self.assertEqual(resolve_llm_concurrency(1, None), 1)
        self.assertEqual(resolve_llm_concurrency(8, None), 2)
        self.assertEqual(resolve_llm_concurrency(8, 5), 5)

    def test_resolve_model_parallelism_defaults_to_one(self):
        self.assertEqual(resolve_model_parallelism(["github/gpt-4o"], None), 1)
        self.assertEqual(resolve_model_parallelism(["github/gpt-4o", "github/gpt-4.1"], None), 1)
        self.assertEqual(resolve_model_parallelism(["github/gpt-4o"], 3), 3)


class TestMainCli(unittest.TestCase):
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


class TestModelNormalization(unittest.TestCase):
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