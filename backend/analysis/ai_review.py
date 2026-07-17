"""
Sends the submitted code (plus a summary of what static analysis already
found) to Claude and asks for higher-level findings that Pylint/Bandit/Radon
can't produce on their own: bug spotting, code smells, performance
suggestions, refactoring advice, and naming/readability feedback.

Model name: as of this writing, 'claude-sonnet-5' is Anthropic's current
mid-tier model. Model names do change over time -- if this stops working,
check https://docs.claude.com/en/docs/about-claude/models for the current
list before assuming the integration itself is broken.
"""

import json
import os

import anthropic

MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-5")
MAX_CODE_CHARS = 12000  # keep prompts bounded; truncate very large submissions

VALID_SEVERITIES = {"low", "medium", "high", "critical"}

SYSTEM_PROMPT = """You are a senior software engineer performing a code review.
You will be given source code and a summary of static analysis results that
already ran (Pylint, Bandit, Radon), so do NOT repeat issues already listed
there -- focus on things static analysis tools structurally cannot catch:
logic bugs, code smells, performance problems, unclear naming, missing edge
case handling, and refactoring opportunities.

Respond with ONLY a JSON array (no markdown fences, no prose before or after).
Each element must have exactly these keys:
  "severity": one of "low", "medium", "high", "critical"
  "issue": short title of the problem
  "explanation": 1-2 sentences on why it matters
  "suggestion": a concrete fix or improvement
  "file_name": the file this applies to
  "line_number": best-guess integer line number (0 if not applicable)

Return at most 8 findings. Return an empty array [] if the code looks solid."""


def run_ai_review(files_with_content, static_findings):
    """
    files_with_content: [(filename, source_code), ...]
    static_findings: list of finding dicts already produced by static_analysis.py
    Returns a list of finding dicts with tool_source = "ai".
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        # No key configured -- skip the AI stage rather than failing the
        # whole review. Static analysis results are still saved.
        return []

    client = anthropic.Anthropic(api_key=api_key)

    code_blob = "\n\n".join(
        f"# --- {name} ---\n{content[:MAX_CODE_CHARS]}"
        for name, content in files_with_content
    )
    static_summary = "\n".join(
        f"- [{f['severity']}] ({f['tool_source']}) {f['file_name']}:{f['line_number']} — {f['issue']}"
        for f in static_findings
    ) or "(no static analysis findings)"

    user_message = (
        f"Static analysis already found:\n{static_summary}\n\n"
        f"Source code:\n{code_blob}"
    )

    try:
        response = client.messages.create(
            model=MODEL,
            max_tokens=2000,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_message}],
        )
        text = "".join(block.text for block in response.content if block.type == "text").strip()
        # Strip accidental markdown fences even though the prompt says not to use them.
        text = text.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        raw_findings = json.loads(text)
    except (anthropic.APIError, json.JSONDecodeError, ValueError):
        return []

    findings = []
    for item in raw_findings:
        severity = str(item.get("severity", "low")).lower()
        if severity not in VALID_SEVERITIES:
            severity = "low"
        findings.append({
            "severity": severity,
            "tool_source": "ai",
            "issue": item.get("issue", "AI review finding"),
            "explanation": item.get("explanation", ""),
            "suggestion": item.get("suggestion", ""),
            "file_name": item.get("file_name") or (files_with_content[0][0] if files_with_content else ""),
            "line_number": int(item.get("line_number") or 0),
        })
    return findings
