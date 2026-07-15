"""
AI Code Review Assistant — backend.

This backend has exactly one job the frontend can't do on its own:
running the actual analysis pipeline (Pylint/Bandit/Radon + the AI review).
Auth, and reading/listing/deleting reviews, are all handled by the frontend
talking to Supabase directly — this service only writes analysis RESULTS
back into Supabase using the service role key, which bypasses RLS.

Because that means this backend can write to ANY user's rows, every request
is checked manually: the caller's Supabase JWT is verified, then we confirm
the project being analyzed actually belongs to that same user before
touching the database.
"""

import os

from dotenv import load_dotenv
from flask import Flask, jsonify, request
from flask_cors import CORS
from supabase import create_client

from analysis import ai_review, file_utils, static_analysis

load_dotenv()

app = Flask(__name__)
CORS(app)  # tighten this to your frontend's origin before deploying

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_SERVICE_ROLE_KEY = os.environ["SUPABASE_SERVICE_ROLE_KEY"]
supabase = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)

SEVERITY_PENALTY = {"critical": 15, "high": 8, "medium": 3, "low": 1}


def get_authenticated_user():
    """Verifies the Authorization: Bearer <token> header against Supabase Auth."""
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        return None
    token = auth_header.removeprefix("Bearer ").strip()
    try:
        response = supabase.auth.get_user(token)
        return response.user
    except Exception:
        return None


def compute_review_score(findings):
    penalty = sum(SEVERITY_PENALTY.get(f["severity"], 0) for f in findings)
    return max(0, 100 - penalty)


@app.route("/api/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"})


@app.route("/api/analyze", methods=["POST"])
def analyze():
    user = get_authenticated_user()
    if not user:
        return jsonify({"error": "Missing or invalid authorization token."}), 401

    # multipart/form-data for file uploads, JSON for paste/github.
    is_multipart = request.content_type and "multipart/form-data" in request.content_type
    payload = request.form if is_multipart else (request.get_json(silent=True) or {})

    project_id = payload.get("project_id")
    review_id = payload.get("review_id")
    upload_type = payload.get("upload_type")

    if not project_id or not review_id or upload_type not in ("file_upload", "code_paste", "github"):
        return jsonify({"error": "project_id, review_id, and a valid upload_type are required."}), 400

    # Ownership check: this project must belong to the authenticated user.
    project = supabase.table("projects").select("id, user_id").eq("id", project_id).single().execute()
    if not project.data or project.data["user_id"] != user.id:
        return jsonify({"error": "Project not found or not owned by this user."}), 403

    # --- Stage 1: extract source files for the given submission type ---
    try:
        if upload_type == "file_upload":
            uploaded = request.files.get("file")
            if not uploaded:
                return jsonify({"error": "No file provided."}), 400
            files = file_utils.extract_from_upload(uploaded)
        elif upload_type == "code_paste":
            code = payload.get("code", "")
            filename = payload.get("filename", "snippet.py")
            if not code.strip():
                return jsonify({"error": "No code provided."}), 400
            files = file_utils.extract_from_paste(code, filename)
        else:  # github
            repo_url = payload.get("github_url")
            branch = payload.get("branch") or None
            if not repo_url:
                return jsonify({"error": "No GitHub URL provided."}), 400
            files = file_utils.extract_from_github(repo_url, branch)
    except ValueError as e:
        supabase.table("reviews").update({"status": "failed"}).eq("id", review_id).execute()
        return jsonify({"error": str(e)}), 400

    if not files:
        supabase.table("reviews").update({"status": "failed"}).eq("id", review_id).execute()
        return jsonify({"error": "No supported .py or .js files found."}), 400

    try:
        # --- Stage 2: static analysis (Pylint, Bandit, Radon) ---
        tmp_dir, files_with_paths = file_utils.write_files_to_temp_dir(files)
        try:
            static_findings, metrics = static_analysis.run_full_static_analysis(files_with_paths)
        finally:
            import shutil
            shutil.rmtree(tmp_dir, ignore_errors=True)

        # --- Stage 3: AI review ---
        ai_findings = ai_review.run_ai_review(files, static_findings)

        all_findings = static_findings + ai_findings
        review_score = compute_review_score(all_findings)

        # --- Save results ---
        supabase.table("reviews").update({
            "status": "completed",
            "review_score": review_score,
            "maintainability_index": metrics["maintainability_index"],
            "cyclomatic_complexity": metrics["cyclomatic_complexity"],
            "total_lines_of_code": metrics["total_lines_of_code"],
            "num_functions": metrics["num_functions"],
            "num_classes": metrics["num_classes"],
        }).eq("id", review_id).execute()

        if all_findings:
            rows = [{**f, "review_id": review_id} for f in all_findings]
            supabase.table("review_findings").insert(rows).execute()

        return jsonify({
            "status": "completed",
            "review_score": review_score,
            "metrics": metrics,
            "findings_count": len(all_findings),
        })

    except Exception as e:
        supabase.table("reviews").update({"status": "failed"}).eq("id", review_id).execute()
        return jsonify({"error": f"Analysis failed: {str(e)}"}), 500


if __name__ == "__main__":
    app.run(debug=True, port=5000)
