"""
Wraps Pylint, Bandit, and Radon and normalizes their very different
output formats into the shape our review_findings table expects:
severity, tool_source, issue, explanation, suggestion, file_name, line_number.

Only .py files are run through these tools — Pylint/Bandit/Radon are
Python-specific. .js files are skipped here and left entirely to the
AI review stage.
"""

import json
import subprocess

from radon.complexity import cc_visit
from radon.metrics import mi_visit
from radon.raw import analyze as radon_raw_analyze

# Pylint message types -> our severity scale
PYLINT_SEVERITY = {
    "fatal": "critical",
    "error": "high",
    "warning": "medium",
    "refactor": "low",
    "convention": "low",
}

# Bandit already uses LOW/MEDIUM/HIGH — just lowercase it. Bandit has no
# "critical", so we escalate HIGH+HIGH-confidence findings to critical.
def _bandit_severity(issue_severity: str, issue_confidence: str) -> str:
    severity = issue_severity.lower()
    if severity == "high" and issue_confidence.upper() == "HIGH":
        return "critical"
    return severity


def run_pylint(file_name: str, file_path: str):
    """Runs Pylint on a single .py file and returns a list of findings."""
    try:
        result = subprocess.run(
            ["pylint", "--output-format=json", file_path],
            capture_output=True, text=True, timeout=30,
        )
        # Pylint exits non-zero whenever it finds issues — that's expected,
        # not a crash. Only missing stdout means something actually broke.
        raw = result.stdout.strip()
        issues = json.loads(raw) if raw else []
    except (subprocess.TimeoutExpired, json.JSONDecodeError):
        return []

    findings = []
    for issue in issues:
        findings.append({
            "severity": PYLINT_SEVERITY.get(issue.get("type"), "low"),
            "tool_source": "pylint",
            "issue": issue.get("message", "Pylint issue"),
            "explanation": f"{issue.get('symbol', '')} ({issue.get('message-id', '')})",
            "suggestion": _pylint_suggestion(issue.get("symbol", "")),
            "file_name": file_name,
            "line_number": issue.get("line", 0),
        })
    return findings


def _pylint_suggestion(symbol: str) -> str:
    # A few common ones get a concrete nudge; otherwise defer to the message itself.
    known = {
        "unused-variable": "Remove the unused variable, or prefix it with an underscore if it's intentional.",
        "missing-function-docstring": "Add a short docstring describing what the function does and its parameters.",
        "missing-module-docstring": "Add a module-level docstring summarizing this file's purpose.",
        "eval-used": "Replace eval() with ast.literal_eval or restructure the logic to avoid dynamic execution.",
        "line-too-long": "Break the line up or shorten the expression to fit the configured line length.",
    }
    return known.get(symbol, "Review Pylint's message above and adjust the flagged code accordingly.")


def run_bandit(file_name: str, file_path: str):
    """Runs Bandit on a single .py file and returns a list of security findings."""
    try:
        result = subprocess.run(
            ["bandit", "-f", "json", "-q", file_path],
            capture_output=True, text=True, timeout=30,
        )
        raw = result.stdout.strip()
        data = json.loads(raw) if raw else {"results": []}
    except (subprocess.TimeoutExpired, json.JSONDecodeError):
        return []

    findings = []
    for issue in data.get("results", []):
        findings.append({
            "severity": _bandit_severity(issue.get("issue_severity", "low"), issue.get("issue_confidence", "low")),
            "tool_source": "bandit",
            "issue": issue.get("test_name", "Security issue").replace("_", " ").title(),
            "explanation": issue.get("issue_text", ""),
            "suggestion": f"See {issue.get('more_info', 'Bandit documentation')} for the recommended fix.",
            "file_name": file_name,
            "line_number": issue.get("line_number", 0),
        })
    return findings


def run_radon(file_name: str, code: str):
    """
    Returns (findings, metrics) for a single .py file using Radon's Python API
    directly (no subprocess needed — Radon is a library, not just a CLI).
    """
    findings = []
    num_functions = 0
    num_classes = 0
    total_complexity = 0

    try:
        blocks = cc_visit(code)
    except SyntaxError:
        blocks = []

    for block in blocks:
        kind = type(block).__name__  # "Function" or "Class"
        if kind == "Class":
            num_classes += 1
        else:
            num_functions += 1
        total_complexity += block.complexity

        if block.complexity > 10:
            findings.append({
                "severity": "high" if block.complexity > 20 else "medium",
                "tool_source": "radon",
                "issue": f"'{block.name}' has high cyclomatic complexity ({block.complexity})",
                "explanation": "Complexity above 10 makes a function harder to test, read, and safely modify.",
                "suggestion": "Split the branching logic into smaller, single-purpose functions.",
                "file_name": file_name,
                "line_number": block.lineno,
            })

    try:
        maintainability_index = round(mi_visit(code, multi=True), 1)
    except SyntaxError:
        maintainability_index = None

    try:
        raw = radon_raw_analyze(code)
        loc = raw.loc
    except SyntaxError:
        loc = code.count("\n") + 1

    metrics = {
        "loc": loc,
        "num_functions": num_functions,
        "num_classes": num_classes,
        "total_complexity": total_complexity,
        "maintainability_index": maintainability_index,
    }
    return findings, metrics


def run_full_static_analysis(files_with_paths):
    """
    files_with_paths: [(filename, full_path_on_disk), ...] for ALL submitted
    files (already written to disk by file_utils.write_files_to_temp_dir).

    Returns (all_findings, aggregate_metrics). Only .py files go through
    Pylint/Bandit/Radon; .js files are skipped (left for the AI stage).
    """
    all_findings = []
    total_loc = 0
    total_functions = 0
    total_classes = 0
    total_complexity = 0
    mi_scores = []

    for file_name, full_path in files_with_paths:
        if not file_name.endswith(".py"):
            continue

        all_findings += run_pylint(file_name, full_path)
        all_findings += run_bandit(file_name, full_path)

        with open(full_path, "r", encoding="utf-8", errors="replace") as f:
            code = f.read()
        radon_findings, metrics = run_radon(file_name, code)
        all_findings += radon_findings

        total_loc += metrics["loc"]
        total_functions += metrics["num_functions"]
        total_classes += metrics["num_classes"]
        total_complexity += metrics["total_complexity"]
        if metrics["maintainability_index"] is not None:
            mi_scores.append(metrics["maintainability_index"])

    aggregate_metrics = {
        "total_lines_of_code": total_loc,
        "num_functions": total_functions,
        "num_classes": total_classes,
        "cyclomatic_complexity": total_complexity,
        "maintainability_index": round(sum(mi_scores) / len(mi_scores), 1) if mi_scores else None,
    }
    return all_findings, aggregate_metrics
