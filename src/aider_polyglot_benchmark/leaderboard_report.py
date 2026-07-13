#!/usr/bin/env python3
import argparse
import json
import os
import re
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape


REPO_ROOT = Path(__file__).resolve().parents[2]
BENCHMARK_ROOT = Path(os.environ.get("AIDER_BENCHMARK_DIR", REPO_ROOT / "tmp.benchmarks"))
TEMPLATE_ROOT = REPO_ROOT / "templates"
LEADERBOARD_TEMPLATE = "leaderboard.html.j2"

YAML_PREFERRED_FIELDS = [
    "dirname",
    "test_cases",
    "model",
    "edit_format",
    "commit_hash",
    "editor_model",
    "editor_edit_format",
    "reasoning_effort",
    "thinking_tokens",
    "pass_rate_1",
    "pass_rate_2",
    "pass_num_1",
    "pass_num_2",
    "failed_rate",
    "failed_num",
    "percent_cases_well_formed",
    "error_outputs",
    "num_malformed_responses",
    "num_with_malformed_responses",
    "user_asks",
    "lazy_comments",
    "syntax_errors",
    "indentation_errors",
    "exhausted_context_windows",
    "prompt_tokens",
    "completion_tokens",
    "test_timeouts",
    "total_tests",
    "command",
    "date",
    "versions",
    "seconds_per_case",
    "total_cost",
]


def find_benchmark_dirs(requested_dirs):
    if requested_dirs:
        return [Path(path) for path in requested_dirs]

    return sorted(
        [path for path in BENCHMARK_ROOT.iterdir() if path.is_dir()],
        key=lambda path: path.name,
    )


def load_result_files(dirname, stats_languages=None):
    if stats_languages:
        languages = [lang.strip().lower() for lang in stats_languages.split(",") if lang.strip()]
        patterns = [f"{lang}/exercises/practice/*/.aider.results.json" for lang in languages]
    else:
        patterns = ["*/exercises/practice/*/.aider.results.json"]

    results = []
    for pattern in patterns:
        for result_file in dirname.glob(pattern):
            try:
                results.append(json.loads(result_file.read_text()))
            except json.JSONDecodeError:
                continue
    return results


def summarize_dir(dirname, stats_languages=None):
    results = load_result_files(dirname, stats_languages=stats_languages)
    if not results:
        return None

    total_tests = len(list(dirname.glob("*/exercises/practice/*")))
    tries = max(max(len(item.get("tests_outcomes", [])), 2) for item in results)
    passed_by_attempt = [0] * tries
    solved_by_attempt = [0] * tries

    total_cost = 0.0
    total_duration = 0.0
    total_prompt_tokens = 0
    total_completion_tokens = 0
    total_error_outputs = 0
    malformed_cases = 0
    total_user_asks = 0
    total_lazy_comments = 0
    total_syntax_errors = 0
    total_indentation_errors = 0
    total_exhausted_context_windows = 0
    total_test_timeouts = 0

    variants = {
        "model": set(),
        "edit_format": set(),
        "command": set(),
        "commit_hash": set(),
        "editor_model": set(),
        "editor_edit_format": set(),
        "reasoning_effort": set(),
        "thinking_tokens": set(),
    }

    for item in results:
        outcomes = item.get("tests_outcomes", [])
        for try_index, passed in enumerate(outcomes):
            if passed:
                passed_by_attempt[try_index] += 1

        for try_index in range(tries):
            if try_index < len(outcomes) and outcomes[try_index]:
                solved_by_attempt[try_index] = solved_by_attempt[try_index] + 1
                break

        total_cost += item.get("cost", 0.0)
        total_duration += item.get("duration", 0.0)
        total_prompt_tokens += item.get("prompt_tokens", 0)
        total_completion_tokens += item.get("completion_tokens", 0)
        total_error_outputs += item.get("num_error_outputs", 0)
        total_user_asks += item.get("num_user_asks", 0)
        total_lazy_comments += item.get("lazy_comments", 0)
        total_syntax_errors += item.get("syntax_errors", 0)
        total_indentation_errors += item.get("indentation_errors", 0)
        total_exhausted_context_windows += item.get("num_exhausted_context_windows", 0)
        total_test_timeouts += item.get("test_timeouts", 0)
        malformed_cases += 1 if item.get("num_malformed_responses", 0) else 0

        for key in variants:
            value = item.get(key)
            if value:
                variants[key].add(str(value))

    completed_tests = len(results)
    pass_rates = {
        f"pass_rate_{index + 1}": round(100.0 * passed / completed_tests, 1)
        for index, passed in enumerate(solved_by_attempt)
    }

    percent_well_formed = round(100.0 * (1 - malformed_cases / completed_tests), 1)
    primary_pass_rate = pass_rates.get("pass_rate_1", 0.0)
    cost_per_case = total_cost / completed_tests if completed_tests else 0.0
    seconds_per_case = total_duration / completed_tests if completed_tests else 0.0
    failed_num = max(0, completed_tests - sum(solved_by_attempt[:2]))
    failed_rate = round(100.0 * failed_num / completed_tests, 1) if completed_tests else 0.0

    model_name = ", ".join(sorted(variants["model"]))
    edit_format = ", ".join(sorted(variants["edit_format"]))
    command = ", ".join(sorted(variants["command"]))
    if not command and model_name:
        command = f"aider --model {model_name}"
        if edit_format:
            command += f" --edit-format {edit_format}"

    row = {
        "dirname": dirname.name,
        "date": dirname.name[:10],
        "completed_tests": completed_tests,
        "test_cases": completed_tests,
        "total_tests": total_tests,
        "is_complete": completed_tests == total_tests,
        "model": model_name,
        "edit_format": edit_format,
        "command": command,
        "commit_hash": ", ".join(sorted(variants["commit_hash"])),
        "editor_model": ", ".join(sorted(variants["editor_model"])),
        "editor_edit_format": ", ".join(sorted(variants["editor_edit_format"])),
        "reasoning_effort": ", ".join(sorted(variants["reasoning_effort"])),
        "thinking_tokens": ", ".join(sorted(variants["thinking_tokens"])),
        "pass_percent": primary_pass_rate,
        "percent_well_formed": percent_well_formed,
        "failed_num": failed_num,
        "failed_rate": failed_rate,
        "error_outputs": total_error_outputs,
        "num_malformed_responses": malformed_cases,
        "num_with_malformed_responses": malformed_cases,
        "user_asks": total_user_asks,
        "lazy_comments": total_lazy_comments,
        "syntax_errors": total_syntax_errors,
        "indentation_errors": total_indentation_errors,
        "exhausted_context_windows": total_exhausted_context_windows,
        "total_cost": round(total_cost, 4),
        "cost_per_case": round(cost_per_case, 4),
        "seconds_per_case": round(seconds_per_case, 1),
        "prompt_tokens": total_prompt_tokens,
        "completion_tokens": total_completion_tokens,
        "test_timeouts": total_test_timeouts,
        "versions": "",
        **pass_rates,
    }
    for index, passed in enumerate(solved_by_attempt):
        row[f"pass_num_{index + 1}"] = passed

    last_pass_index = max(len(solved_by_attempt), 1)
    row["last_pass_index"] = last_pass_index
    row["last_pass_rate"] = row.get(f"pass_rate_{last_pass_index}", 0.0)
    row["last_pass_num"] = row.get(f"pass_num_{last_pass_index}", 0)
    row["failed_count"] = failed_num
    row["failure_rate"] = failed_rate
    row["failure_num"] = failed_num
    return row


def build_rows(directories, stats_languages=None, complete_only=False):
    rows = []
    for dirname in directories:
        row = summarize_dir(dirname, stats_languages=stats_languages)
        if not row:
            continue
        if complete_only and not row["is_complete"]:
            continue
        rows.append(row)

    return sorted(
        rows,
        key=lambda row: (-row["pass_percent"], row["total_cost"], row["model"], row["dirname"]),
    )


def parse_yaml_scalar(value):
    value = value.strip()
    if value == '""':
        return ""
    if value == "true":
        return True
    if value == "false":
        return False
    if value.startswith('"') and value.endswith('"'):
        return json.loads(value)
    if re.fullmatch(r"-?\d+", value):
        return int(value)
    if re.fullmatch(r"-?\d+\.\d+", value):
        return float(value)
    return value


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


def load_benchmark_report(dirname):
    report_path = dirname / "benchmark-report.yml"
    if not report_path.exists():
        return None

    report = {}
    for raw_line in report_path.read_text(encoding="utf-8").splitlines():
        if not raw_line.strip():
            continue
        line = raw_line[2:] if raw_line.startswith("- ") else raw_line.strip()
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        report[key.strip()] = parse_yaml_scalar(value)
    return report


def load_leaderboard_yaml(yaml_path):
    entries = []
    current = None

    for raw_line in yaml_path.read_text(encoding="utf-8").splitlines():
        if not raw_line.strip():
            continue

        if raw_line.startswith("- "):
            if current:
                entries.append(current)
            current = {}
            line = raw_line[2:]
        else:
            if current is None:
                continue
            line = raw_line.strip()

        if ":" not in line:
            continue

        key, value = line.split(":", 1)
        current[key.strip()] = parse_yaml_scalar(value)

    if current:
        entries.append(current)

    return entries


def row_from_yaml_entry(entry):
    pass_rate_1 = float(entry.get("pass_rate_1", 0) or 0)
    pass_num_1 = int(entry.get("pass_num_1", 0) or 0)
    indices = sorted(
        {
            int(key.split("_")[-1])
            for key in entry
            if key.startswith("pass_rate_") or key.startswith("pass_num_")
        }
    )
    last_pass_index = max(indices, default=1)
    test_cases = int(entry.get("test_cases", entry.get("total_tests", 0)) or 0)
    solved_total = sum(int(entry.get(f"pass_num_{index}", 0) or 0) for index in indices)
    if solved_total and test_cases:
        percent_correct = (solved_total / test_cases) * 100.0
    else:
        percent_correct = sum(float(entry.get(f"pass_rate_{index}", 0) or 0) for index in indices)
    total_tests = int(entry.get("total_tests", test_cases) or test_cases)
    failed_num = int(entry.get("failed_num", entry.get("failure_num", 0)) or 0)
    failed_rate = float(entry.get("failed_rate", entry.get("failure_rate", 0)) or 0)
    percent_well_formed = float(
        entry.get("percent_well_formed", entry.get("percent_cases_well_formed", 0)) or 0
    )

    row = {
        "dirname": str(entry.get("dirname", "") or ""),
        "date": str(entry.get("date", "") or ""),
        "completed_tests": test_cases,
        "test_cases": test_cases,
        "total_tests": total_tests,
        "is_complete": test_cases == total_tests,
        "model": str(entry.get("model", "") or ""),
        "edit_format": str(entry.get("edit_format", "") or ""),
        "command": str(entry.get("command", "") or ""),
        "commit_hash": str(entry.get("commit_hash", "") or ""),
        "editor_model": str(entry.get("editor_model", "") or ""),
        "editor_edit_format": str(entry.get("editor_edit_format", "") or ""),
        "reasoning_effort": str(entry.get("reasoning_effort", "") or ""),
        "thinking_tokens": str(entry.get("thinking_tokens", "") or ""),
        "pass_percent": min(percent_correct, 100.0),
        "percent_well_formed": percent_well_formed,
        "failed_num": failed_num,
        "failed_rate": failed_rate,
        "error_outputs": int(entry.get("error_outputs", 0) or 0),
        "num_malformed_responses": int(entry.get("num_malformed_responses", 0) or 0),
        "num_with_malformed_responses": int(entry.get("num_with_malformed_responses", 0) or 0),
        "user_asks": int(entry.get("user_asks", 0) or 0),
        "lazy_comments": int(entry.get("lazy_comments", 0) or 0),
        "syntax_errors": int(entry.get("syntax_errors", 0) or 0),
        "indentation_errors": int(entry.get("indentation_errors", 0) or 0),
        "exhausted_context_windows": int(entry.get("exhausted_context_windows", 0) or 0),
        "total_cost": float(entry.get("total_cost", 0) or 0),
        "cost_per_case": float(entry.get("total_cost", 0) or 0) / test_cases if test_cases else 0.0,
        "seconds_per_case": float(entry.get("seconds_per_case", 0) or 0),
        "prompt_tokens": int(entry.get("prompt_tokens", 0) or 0),
        "completion_tokens": int(entry.get("completion_tokens", 0) or 0),
        "test_timeouts": int(entry.get("test_timeouts", 0) or 0),
        "versions": str(entry.get("versions", "") or ""),
        "last_pass_index": last_pass_index,
        "last_pass_rate": float(entry.get(f"pass_rate_{last_pass_index}", 0) or 0),
        "last_pass_num": int(entry.get(f"pass_num_{last_pass_index}", 0) or 0),
        "failed_count": failed_num,
        "failure_rate": failed_rate,
        "failure_num": failed_num,
    }

    for index in indices:
        row[f"pass_rate_{index}"] = float(entry.get(f"pass_rate_{index}", 0) or 0)
        row[f"pass_num_{index}"] = int(entry.get(f"pass_num_{index}", 0) or 0)

    if "pass_rate_1" not in row:
        row["pass_rate_1"] = pass_rate_1
    if "pass_num_1" not in row:
        row["pass_num_1"] = pass_num_1

    return row


def build_yaml_entries(directories, stats_languages=None, complete_only=False):
    entries = []
    for dirname in directories:
        row = summarize_dir(dirname, stats_languages=stats_languages)
        if not row:
            continue
        if complete_only and not row["is_complete"]:
            continue

        report = load_benchmark_report(dirname) or {}
        if not report:
            report = {
                key: row[key]
                for key in row
                if key
                not in {"pass_percent", "cost_per_case", "completed_tests", "is_complete"}
            }
        entries.append(report)

    def sort_key(entry):
        return (
            -float(entry.get("pass_rate_2", 0) or 0),
            -float(entry.get("pass_rate_1", 0) or 0),
            -float(entry.get("test_cases", 0) or 0),
            float(entry.get("total_cost", 0) or 0),
            str(entry.get("dirname", "")),
        )

    deduped = {}
    for entry in sorted(entries, key=sort_key):
        model_name = str(entry.get("model", "")).strip()
        if not model_name:
            model_name = str(entry.get("dirname", "")).strip()
        if model_name not in deduped:
            deduped[model_name] = entry

    return sorted(
        deduped.values(),
        key=lambda entry: (
            -float(entry.get("pass_rate_2", 0) or 0),
            -float(entry.get("pass_rate_1", 0) or 0),
            str(entry.get("dirname", "")),
        ),
    )


def write_yaml(entries, output_path):
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if not entries:
        output_path.write_text("[]\n", encoding="utf-8")
        return

    lines = []
    for entry in entries:
        ordered_keys = [key for key in YAML_PREFERRED_FIELDS if key in entry]
        ordered_keys.extend(sorted(key for key in entry if key not in ordered_keys))

        lines.append(f"- dirname: {yaml_scalar(entry.get('dirname', ''))}")
        for key in ordered_keys:
            if key == "dirname":
                continue
            lines.append(f"  {key}: {yaml_scalar(entry.get(key))}")
        lines.append("")

    output_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def write_markdown(rows, output_path, title="LLM Coding Benchmark"):
    output_path.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"# {title}", ""]

    summary = build_summary(rows)
    lines.extend(
        [
            "## Summary",
            "",
            f"- Models: {summary['total_models']}",
            f"- Avg use cases per model: {summary['use_cases_per_model']:.1f} ({summary['use_cases_completion_rate']:.1f}% of available cases)",
            f"- Avg successes per model: {summary['avg_successes_per_model']:.1f} ({summary['avg_success_rate']:.1f}%)",
            f"- Avg failures per model: {summary['avg_failures_per_model']:.1f} ({summary['avg_failure_rate']:.1f}%)",
            f"- Avg first-try successes per model: {summary['avg_first_try_successes']:.1f} ({summary['avg_first_try_success_rate']:.1f}%)",
            f"- Avg second-try successes per model: {summary['avg_second_try_successes']:.1f} ({summary['avg_second_try_success_rate']:.1f}%)",
            "",
            "## Runs",
            "",
            "| Model | Run | % Correct | 1st Try | 2nd Try | Failure Rate | Test Cases | Total Cost | Avg Time |",
            "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
        ]
    )

    for row in rows:
        lines.append(
            "| {model} | {dirname} | {pass_percent:.1f}% | {pass_rate_1:.1f}% | {last_pass_rate:.1f}% | {failed_rate:.1f}% | {test_cases} | ${total_cost:.4f} | {seconds_per_case:.1f}s |".format(
                model=row.get("model") or "unknown",
                dirname=row.get("dirname", ""),
                pass_percent=float(row.get("pass_percent", 0) or 0),
                pass_rate_1=float(row.get("pass_rate_1", 0) or 0),
                last_pass_rate=float(row.get("last_pass_rate", 0) or 0),
                failed_rate=float(row.get("failed_rate", 0) or 0),
                test_cases=int(row.get("test_cases", 0) or 0),
                total_cost=float(row.get("total_cost", 0) or 0),
                seconds_per_case=float(row.get("seconds_per_case", 0) or 0),
            )
        )

    output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def percent_bar(value, css_class):
    width = max(0, min(100, float(value)))
    return (
        f'<div class="bar {css_class}"><span class="fill" style="width:{width:.1f}%"></span>'
        f'<span class="label">{width:.1f}%</span></div>'
    )


def money_bar(value, max_cost):
    width = 0 if max_cost <= 0 else (float(value) / max_cost) * 100
    width = max(0, min(100, width))
    return (
        f'<div class="bar cost"><span class="fill" style="width:{width:.1f}%"></span>'
        f'<span class="label">${float(value):.4f}</span></div>'
    )


def get_last_pass_index(row):
    if row.get("last_pass_index"):
        return int(row["last_pass_index"])

    indices = [
        int(key.split("_")[-1])
        for key in row
        if key.startswith("pass_rate_") and row.get(key) is not None
    ]
    return max(indices, default=1)


def format_duration(seconds_per_case):
    return f"{float(seconds_per_case):.1f}s"


def format_money(value):
    return f"${float(value):.4f}"


def build_detail_payload(row):
    last_pass_index = get_last_pass_index(row)
    failed_count = int(row.get("failed_count", 0) or 0)
    complete_label = "Yes" if row["is_complete"] else f"No ({row['completed_tests']}/{row['total_tests']})"
    return {
        "model": row.get("model") or "unknown",
        "run": row.get("dirname", ""),
        "details": [
            {"label": "Dirname", "value": str(row.get("dirname", ""))},
            {"label": "Test cases", "value": str(row.get("test_cases", 0))},
            {"label": "Model", "value": str(row.get("model") or "")},
            {"label": "Edit format", "value": str(row.get("edit_format") or "")},
            {"label": "Commit hash", "value": str(row.get("commit_hash") or "")},
            {"label": "Reasoning effort", "value": str(row.get("reasoning_effort") or "")},
            {"label": "Pass rate 1", "value": f"{float(row.get('pass_rate_1', 0) or 0):.1f}"},
            {"label": "Pass rate 2", "value": f"{float(row.get('pass_rate_2', 0) or 0):.1f}"},
            {"label": "Pass num 1", "value": str(int(row.get("pass_num_1", 0) or 0))},
            {"label": "Pass num 2", "value": str(int(row.get("pass_num_2", 0) or 0))},
            {"label": "Failed rate", "value": f"{float(row.get('failed_rate', 0) or 0):.1f}"},
            {"label": "Failed num", "value": str(int(row.get("failed_num", 0) or 0))},
            {"label": "Percent cases well formed", "value": f"{float(row.get('percent_well_formed', 0) or 0):.1f}"},
            {"label": "Error outputs", "value": str(row.get("error_outputs", 0) or 0)},
            {"label": "Num malformed responses", "value": str(row.get("num_malformed_responses", 0) or 0)},
            {"label": "Num with malformed responses", "value": str(row.get("num_with_malformed_responses", 0) or 0)},
            {"label": "User asks", "value": str(row.get("user_asks", 0) or 0)},
            {"label": "Lazy comments", "value": str(row.get("lazy_comments", 0) or 0)},
            {"label": "Syntax errors", "value": str(row.get("syntax_errors", 0) or 0)},
            {"label": "Indentation errors", "value": str(row.get("indentation_errors", 0) or 0)},
            {"label": "Exhausted context windows", "value": str(row.get("exhausted_context_windows", 0) or 0)},
            {"label": "Prompt tokens", "value": str(row.get("prompt_tokens", 0) or 0)},
            {"label": "Completion tokens", "value": str(row.get("completion_tokens", 0) or 0)},
            {"label": "Test timeouts", "value": str(row.get("test_timeouts", 0) or 0)},
            {"label": "Total tests", "value": str(row.get("total_tests", 0) or 0)},
            {"label": "Date", "value": str(row.get("date", ""))},
            {"label": "Versions", "value": str(row.get("versions") or "")},
            {"label": "Seconds per case", "value": f"{float(row.get('seconds_per_case', 0) or 0):.1f}"},
            {"label": "Total cost", "value": f"{float(row.get('total_cost', 0) or 0):.4f}"},
        ],
    }


def build_summary(rows):
    total_models = len(rows)
    total_test_cases = sum(int(row.get("test_cases", 0) or 0) for row in rows)
    total_available_cases = sum(int(row.get("total_tests", 0) or 0) for row in rows)
    use_cases_per_model = (total_test_cases / total_models) if total_models else 0.0
    available_cases_per_model = (total_available_cases / total_models) if total_available_cases and total_models else use_cases_per_model
    avg_successes_per_model = (
        sum(int(row.get("test_cases", 0) or 0) - int(row.get("failed_num", 0) or 0) for row in rows) / total_models
        if total_models
        else 0.0
    )
    avg_failures_per_model = (
        sum(int(row.get("failed_num", 0) or 0) for row in rows) / total_models if total_models else 0.0
    )
    avg_first_try_successes = (
        sum(int(row.get("pass_num_1", 0) or 0) for row in rows) / total_models if total_models else 0.0
    )
    avg_second_try_successes = (
        sum(int(row.get("pass_num_2", 0) or 0) for row in rows) / total_models if total_models else 0.0
    )

    rate_denominator = use_cases_per_model if use_cases_per_model else 1.0
    coverage_denominator = available_cases_per_model if available_cases_per_model else 1.0
    return {
        "total_models": total_models,
        "total_test_cases": total_test_cases,
        "use_cases_per_model": use_cases_per_model,
        "available_cases_per_model": available_cases_per_model,
        "use_cases_completion_rate": (use_cases_per_model / coverage_denominator) * 100 if total_models else 0.0,
        "avg_successes_per_model": avg_successes_per_model,
        "avg_success_rate": (avg_successes_per_model / rate_denominator) * 100 if total_models else 0.0,
        "avg_failures_per_model": avg_failures_per_model,
        "avg_failure_rate": (avg_failures_per_model / rate_denominator) * 100 if total_models else 0.0,
        "avg_first_try_successes": avg_first_try_successes,
        "avg_first_try_success_rate": (avg_first_try_successes / rate_denominator) * 100 if total_models else 0.0,
        "avg_second_try_successes": avg_second_try_successes,
        "avg_second_try_success_rate": (avg_second_try_successes / rate_denominator) * 100 if total_models else 0.0,
    }


def build_display_rows(rows):
    max_cost = max((max(row["total_cost"], row.get("cost_per_case", 0)) for row in rows), default=0)
    denominator = max_cost if max_cost > 0 else 1
    display_rows = []

    for row in rows:
        last_pass_index = get_last_pass_index(row)
        failed_count = int(row.get("failed_count", 0) or 0)
        display_rows.append(
            {
                "model": row.get("model") or "unknown",
                "run_name": row["dirname"],
                "percent_correct": float(row.get("pass_percent", 0) or 0),
                "command": row.get("command") or "",
                "edit_format": row.get("edit_format") or "",
                "pass_first_try": float(row.get("pass_rate_1", 0) or 0),
                "pass_after_retries": float(row.get(f"pass_rate_{last_pass_index}", 0) or 0),
                "failed_rate": float(row.get("failed_rate", 0) or 0),
                "exercises_run": int(row.get("test_cases", 0) or 0),
                "failed": failed_count,
                "failed_class": "bad" if failed_count else "good",
                "failure_rate": float(row.get("failed_rate", 0) or 0),
                "failure_num": int(row.get("failed_num", 0) or 0),
                "reliable_replies": float(row.get("percent_well_formed", 0) or 0),
                "avg_time": format_duration(row.get("seconds_per_case", 0)),
                "avg_time_sort": float(row.get("seconds_per_case", 0) or 0),
                "cost": format_money(row.get("cost_per_case", 0)),
                "cost_sort": float(row.get("cost_per_case", 0) or 0),
                "cost_width": max(
                    0,
                    min(100, (float(row.get("cost_per_case", 0) or 0) / denominator) * 100),
                ),
                "cost_per_exercise": format_money(row.get("cost_per_case", 0)),
                "cost_per_exercise_sort": float(row.get("cost_per_case", 0) or 0),
                "cost_per_exercise_width": max(
                    0,
                    min(100, (float(row.get("cost_per_case", 0) or 0) / denominator) * 100),
                ),
                "total_cost": format_money(row.get("total_cost", 0)),
                "total_cost_sort": float(row.get("total_cost", 0) or 0),
                "total_cost_width": max(
                    0,
                    min(100, (float(row.get("total_cost", 0) or 0) / denominator) * 100),
                ),
                "pass_percent": float(row.get("pass_percent", 0) or 0),
                "seconds_per_case": float(row.get("seconds_per_case", 0) or 0),
                "cost_per_case": float(row.get("cost_per_case", 0) or 0),
                "test_cases": int(row.get("test_cases", 0) or 0),
                "date": row.get("date", ""),
                "details": build_detail_payload(row)["details"],
                "details_json": json.dumps(build_detail_payload(row), separators=(",", ":")),
            }
        )

    return display_rows


def get_jinja_environment():
    return Environment(
        loader=FileSystemLoader(str(TEMPLATE_ROOT)),
        autoescape=select_autoescape(enabled_extensions=("html", "xml", "tpl"), default_for_string=True),
    )


def render_html(rows, output_path, title):
    output_path.parent.mkdir(parents=True, exist_ok=True)
    env = get_jinja_environment()
    template = env.get_template(LEADERBOARD_TEMPLATE)
    html_doc = template.render(
        title=title,
        rows=build_display_rows(rows),
        row_count=len(rows),
        summary=build_summary(rows),
    )
    output_path.write_text(html_doc, encoding="utf-8")


def parse_args():
    parser = argparse.ArgumentParser(
        description="Generate Markdown, HTML, and YAML reports from benchmark result directories."
    )
    parser.add_argument("dirs", nargs="*", help="Benchmark directories to include")
    parser.add_argument(
        "--stats-languages",
        help="Comma-separated languages to include, eg csharp",
    )
    parser.add_argument(
        "--complete-only",
        action="store_true",
        help="Include only runs that completed every exercise in the selected scope",
    )
    parser.add_argument(
        "--html",
        default=str(BENCHMARK_ROOT / "leaderboard.html"),
        help="HTML output path",
    )
    parser.add_argument(
        "--md",
        default=str(BENCHMARK_ROOT / "leaderboard.md"),
        help="Markdown output path",
    )
    parser.add_argument(
        "--yaml",
        default=str(BENCHMARK_ROOT / "polyglot_leaderboard.yml"),
        help="YAML output path",
    )
    parser.add_argument(
        "--from-yaml",
        help="Render outputs from an existing leaderboard YAML file instead of rebuilding from benchmark directories",
    )
    parser.add_argument(
        "--title",
        default="LLM Coding Benchmark",
        help="HTML page title",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    yaml_entries = None
    if args.from_yaml:
        yaml_path = Path(args.from_yaml)
        yaml_entries = load_leaderboard_yaml(yaml_path)
        rows = [row_from_yaml_entry(entry) for entry in yaml_entries]
    else:
        directories = find_benchmark_dirs(args.dirs)
        rows = build_rows(
            directories,
            stats_languages=args.stats_languages,
            complete_only=args.complete_only,
        )

    if not rows:
        raise SystemExit("No benchmark results found to export.")

    md_path = Path(args.md)
    html_path = Path(args.html)
    yaml_path = Path(args.yaml)
    if yaml_entries is None:
        yaml_entries = build_yaml_entries(
            directories,
            stats_languages=args.stats_languages,
            complete_only=args.complete_only,
        )
    write_markdown(rows, md_path, args.title)
    render_html(rows, html_path, args.title)
    write_yaml(yaml_entries, yaml_path)

    print(f"Wrote Markdown: {md_path}")
    print(f"Wrote HTML: {html_path}")
    print(f"Wrote YAML: {yaml_path}")
    print(f"Rows: {len(rows)}")


if __name__ == "__main__":
    main()