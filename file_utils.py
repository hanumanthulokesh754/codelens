"""
Handles turning the three submission types (file upload, pasted code,
GitHub URL) into a common shape: a list of (filename, source_code) tuples
that the analysis stage can iterate over.
"""

import os
import shutil
import subprocess
import tempfile

ALLOWED_EXTENSIONS = {".py", ".js"}

IGNORED_DIRS = {
    "node_modules", "venv", ".venv", "__pycache__", ".git",
    "dist", "build", "env", ".idea", ".vscode", "site-packages",
}


def extract_from_paste(code: str, filename: str = "snippet.py"):
    """Pasted code becomes a single virtual file."""
    return [(filename, code)]


def extract_from_upload(file_storage):
    """
    file_storage: a Werkzeug FileStorage object from request.files.
    Only reads it if the extension is one we analyze.
    """
    filename = file_storage.filename
    ext = os.path.splitext(filename)[1].lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise ValueError(f"Unsupported file type: {ext}. Only .py and .js are supported.")
    content = file_storage.read().decode("utf-8", errors="replace")
    return [(filename, content)]


def extract_from_github(repo_url: str, branch: str | None = None, max_files: int = 40):
    """
    Shallow-clones a public GitHub repo into a temp directory, walks it,
    and reads every allowed source file, skipping dependency/build folders.
    The temp directory is always cleaned up afterward.
    """
    tmp_dir = tempfile.mkdtemp(prefix="codereview_")
    try:
        cmd = ["git", "clone", "--depth", "1"]
        if branch:
            cmd += ["--branch", branch]
        cmd += [repo_url, tmp_dir]

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        if result.returncode != 0:
            raise ValueError(f"Could not clone repository: {result.stderr.strip()}")

        files = []
        for root, dirs, filenames in os.walk(tmp_dir):
            dirs[:] = [d for d in dirs if d not in IGNORED_DIRS and not d.startswith(".")]
            for name in filenames:
                ext = os.path.splitext(name)[1].lower()
                if ext not in ALLOWED_EXTENSIONS:
                    continue
                full_path = os.path.join(root, name)
                rel_path = os.path.relpath(full_path, tmp_dir)
                try:
                    with open(full_path, "r", encoding="utf-8", errors="replace") as f:
                        files.append((rel_path, f.read()))
                except OSError:
                    continue
                if len(files) >= max_files:
                    return files
        return files
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def write_files_to_temp_dir(files):
    """
    Writes (filename, content) tuples to a fresh temp directory so
    subprocess-based tools (pylint, bandit) have real paths to point at.
    Returns (temp_dir_path, [(filename, full_path), ...]).
    Caller is responsible for cleaning up the temp dir.
    """
    tmp_dir = tempfile.mkdtemp(prefix="codereview_analysis_")
    written = []
    for filename, content in files:
        # Flatten nested paths so files from a cloned repo don't collide
        # with subdirectories that don't exist in tmp_dir.
        safe_name = filename.replace("/", "__").replace("\\", "__")
        full_path = os.path.join(tmp_dir, safe_name)
        with open(full_path, "w", encoding="utf-8") as f:
            f.write(content)
        written.append((filename, full_path))
    return tmp_dir, written
