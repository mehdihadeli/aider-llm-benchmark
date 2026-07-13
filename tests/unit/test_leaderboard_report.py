# flake8: noqa: E501

from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import json

import aider_polyglot_benchmark.leaderboard_report as leaderboard_report

from aider_polyglot_benchmark.benchmark import write_aggregate_reports


def test_render_html_uses_template_and_details_popup():
    row = {
        "dirname": "2026-07-05-12-17-30--sample-run",
        "model": "github/gpt-4.1",
        "pass_rate_1": 75.0,
        "pass_rate_2": 90.0,
        "failed_rate": 10.0,
        "failed_num": 1,
        "pass_num_2": 9,
        "last_pass_index": 2,
        "failed_count": 1,
        "test_cases": 10,
        "percent_well_formed": 100.0,
        "seconds_per_case": 2.9,
        "cost_per_case": 0.0123,
        "total_cost": 0.1234,
        "is_complete": True,
        "completed_tests": 10,
        "total_tests": 10,
        "syntax_errors": 1,
        "test_timeouts": 0,
        "exhausted_context_windows": 0,
        "user_asks": 0,
        "lazy_comments": 0,
        "prompt_tokens": 2100,
        "completion_tokens": 59,
        "edit_format": "diff",
        "commit_hash": "abc1234",
        "command": "aider --model github/gpt-4.1 --edit-format diff",
    }

    with TemporaryDirectory() as tmpdir:
        output_path = Path(tmpdir) / "leaderboard.html"
        leaderboard_report.render_html([row], output_path, "Demo Leaderboard")
        rendered = output_path.read_text(encoding="utf-8")

        assert "Percent correct" in rendered
        assert "Cost" in rendered
        assert "Correct edit format" in rendered
        assert "Solved First Try" in rendered
        assert "Solved Second Try" in rendered
        assert "Avg Use Cases per Model" in rendered
        assert "Avg Successes per Model" in rendered
        assert "Avg Failures per Model" in rendered
        assert "Avg First-Try Successes per Model" in rendered
        assert "Avg Second-Try Successes per Model" in rendered
        assert "% success rate" in rendered
        assert "% failure rate" in rendered
        assert "Use Cases Run" not in rendered
        assert "Failed Rate: 10.0%" in rendered
        assert "detail-row-0" in rendered
        assert "Pass rate 1" in rendered
        assert "Failed rate" in rendered
        assert "Failed num" in rendered
        assert "Total cost" in rendered
        assert "Command:" not in rendered
        assert "View details" not in rendered
        assert "detail-modal" not in rendered


def test_write_markdown_renders_summary_and_rows():
    row = {
        "dirname": "2026-07-05-12-17-30--sample-run",
        "date": "2026-07-05",
        "model": "github/gpt-4.1",
        "test_cases": 10,
        "pass_percent": 90.0,
        "pass_rate_1": 70.0,
        "pass_num_1": 7,
        "pass_rate_2": 20.0,
        "pass_num_2": 2,
        "last_pass_rate": 20.0,
        "failed_rate": 10.0,
        "failed_num": 1,
        "total_cost": 0.1234,
        "cost_per_case": 0.0123,
        "seconds_per_case": 1.5,
        "completed_tests": 10,
        "total_tests": 10,
        "is_complete": True,
        "percent_well_formed": 100.0,
        "error_outputs": 0,
        "num_malformed_responses": 0,
        "num_with_malformed_responses": 0,
        "user_asks": 0,
        "lazy_comments": 0,
        "syntax_errors": 0,
        "indentation_errors": 0,
        "exhausted_context_windows": 0,
        "prompt_tokens": 100,
        "completion_tokens": 20,
        "test_timeouts": 0,
        "edit_format": "diff",
        "editor_model": "",
        "editor_edit_format": "",
        "reasoning_effort": "",
        "thinking_tokens": "",
        "command": "aider --model github/gpt-4.1 --edit-format diff",
        "commit_hash": "abc1234",
        "versions": "",
        "last_pass_index": 2,
        "last_pass_num": 2,
        "failed_count": 1,
    }

    with TemporaryDirectory() as tmpdir:
        output_path = Path(tmpdir) / "leaderboard.md"
        leaderboard_report.write_markdown([row], output_path, "Demo Leaderboard")
        rendered = output_path.read_text(encoding="utf-8")

        assert "# Demo Leaderboard" in rendered
        assert "## Summary" in rendered
        assert "## Runs" in rendered
        assert "| Model | Run | % Correct |" in rendered
        assert "github/gpt-4.1" in rendered
        assert "$0.1234" in rendered


def test_write_aggregate_reports_uses_all_runs_when_none_selected():
    with TemporaryDirectory() as tmpdir:
        benchmark_root = Path(tmpdir)
        run_a = benchmark_root / "2026-07-05-12-00-00--run-a"
        run_b = benchmark_root / "2026-07-05-12-00-01--run-b"
        run_a.mkdir()
        run_b.mkdir()

        with patch.object(leaderboard_report, "BENCHMARK_ROOT", benchmark_root), \
             patch.object(leaderboard_report, "build_rows", return_value=[{"model": "github/gpt-4.1"}]) as build_rows, \
             patch.object(leaderboard_report, "build_yaml_entries", return_value=[{"model": "github/gpt-4.1"}]) as build_yaml_entries, \
               patch.object(leaderboard_report, "write_markdown") as write_markdown, \
             patch.object(leaderboard_report, "render_html") as render_html, \
             patch.object(leaderboard_report, "write_yaml") as write_yaml:
            write_aggregate_reports(None)

        expected_dirs = [run_a, run_b]
        build_rows.assert_called_once_with(expected_dirs, stats_languages=None)
        build_yaml_entries.assert_called_once_with(expected_dirs, stats_languages=None)
        write_markdown.assert_called_once()
        render_html.assert_called_once()
        write_yaml.assert_called_once()


def test_load_leaderboard_yaml_and_convert_entry_to_row():
    yaml_text = """- dirname: 2026-07-12-sim-gpt-4
  test_cases: 10
  model: github_copilot/gpt-4
  edit_format: diff
  pass_rate_1: 30
  pass_rate_2: 40
  pass_num_1: 3
  pass_num_2: 4
  failed_rate: 30
  failed_num: 3
  percent_cases_well_formed: 100
  total_tests: 10
  command: aider --model github_copilot/gpt-4
  date: 2026-07-12
  versions: 0.86.2
  seconds_per_case: 0
  total_cost: 0
"""

    with TemporaryDirectory() as tmpdir:
        yaml_path = Path(tmpdir) / "polyglot_leaderboard.yml"
        yaml_path.write_text(yaml_text, encoding="utf-8")

        entries = leaderboard_report.load_leaderboard_yaml(yaml_path)
        row = leaderboard_report.row_from_yaml_entry(entries[0])

    assert len(entries) == 1
    assert row["dirname"] == "2026-07-12-sim-gpt-4"
    assert row["pass_rate_1"] == 30.0
    assert row["pass_rate_2"] == 40.0
    assert row["pass_num_1"] == 3
    assert row["pass_num_2"] == 4
    assert row["failed_num"] == 3
    assert row["pass_percent"] == 70.0


def test_row_from_yaml_entry_uses_pass_counts_for_percent_correct():
    entry = {
        "dirname": "2026-07-13-sim-gpt-4.1",
        "test_cases": 5,
        "total_tests": 5,
        "pass_rate_1": 60,
        "pass_rate_2": 20,
        "pass_num_1": 3,
        "pass_num_2": 1,
        "failed_rate": 20,
        "failed_num": 1,
    }

    row = leaderboard_report.row_from_yaml_entry(entry)

    assert row["pass_percent"] == 80.0
    assert row["percent_well_formed"] == 100.0
    assert row["is_complete"] is True


def test_summarize_dir_reports_task_pass_rates_and_failures_by_attempt():
    with TemporaryDirectory() as tmpdir:
        run_dir = Path(tmpdir) / "2026-07-05-12-00-00--sample-run"
        alpha = run_dir / "csharp" / "exercises" / "practice" / "alpha"
        beta = run_dir / "csharp" / "exercises" / "practice" / "beta"
        gamma = run_dir / "csharp" / "exercises" / "practice" / "gamma"
        alpha.mkdir(parents=True)
        beta.mkdir(parents=True)
        gamma.mkdir(parents=True)

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

        row = leaderboard_report.summarize_dir(run_dir)

    assert row is not None
    assert row["test_cases"] == 3
    assert row["pass_num_1"] == 1
    assert row["pass_num_2"] == 1
    assert row["pass_rate_1"] == 33.3
    assert row["pass_rate_2"] == 33.3
    assert row["failed_num"] == 1
    assert row["failed_rate"] == 33.3
    assert row["failed_count"] == 1


def test_build_summary_reports_average_per_model_counts():
    rows = [
        {
            "test_cases": 10,
            "failed_num": 2,
            "pass_num_1": 6,
            "pass_num_2": 2,
        },
        {
            "test_cases": 6,
            "failed_num": 1,
            "pass_num_1": 4,
            "pass_num_2": 1,
        },
    ]

    summary = leaderboard_report.build_summary(rows)

    assert summary["total_models"] == 2
    assert summary["total_test_cases"] == 16
    assert summary["use_cases_per_model"] == 8.0
    assert summary["available_cases_per_model"] == 8.0
    assert summary["use_cases_completion_rate"] == 100.0
    assert summary["avg_successes_per_model"] == 6.5
    assert summary["avg_failures_per_model"] == 1.5
    assert summary["avg_first_try_successes"] == 5.0
    assert summary["avg_second_try_successes"] == 1.5
    assert summary["avg_success_rate"] == 81.25
    assert summary["avg_failure_rate"] == 18.75
    assert summary["avg_first_try_success_rate"] == 62.5
    assert summary["avg_second_try_success_rate"] == 18.75