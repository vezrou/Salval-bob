"""
GitHub repository fetcher.

Fetches the file tree and contents of frontend-relevant files from any
public GitHub repository. Supports private repos when a personal access
token is provided.

Usage:
    from agents.github_fetcher import fetch_repo
    files = fetch_repo("https://github.com/owner/repo", token="ghp_...")
    # returns: [{"path": "src/Button.tsx", "content": "..."}, ...]
"""

from concurrent.futures import ThreadPoolExecutor
import base64
import os
from urllib.parse import urlsplit, quote
import httpx
from .repository_scope import BACKEND_EXTENSIONS, file_scope

# File extensions worth analyzing for frontend context
_FRONTEND_EXTENSIONS = {
    ".tsx", ".jsx", ".ts", ".js", ".vue", ".svelte",
    ".css", ".scss", ".sass", ".less",
    ".html",
}

# Paths to skip — build output, dependencies, generated files
_SKIP_DIRS = {
    "node_modules", "dist", "build", ".next", ".nuxt", "out",
    ".git", ".vite", "coverage", "__pycache__",
}

_MAX_FILES = 60       # cap to stay within token budget
_MAX_FILE_BYTES = 50_000  # skip files larger than ~50 KB


def _parse_repo_url(url: str) -> tuple[str, str]:
    """
    Extract owner and repo name from a GitHub URL.
    Accepts:
        https://github.com/owner/repo
        https://github.com/owner/repo.git
    Returns (owner, repo).
    """
    parsed = urlsplit(url.strip())
    parts = parsed.path.strip("/").split("/")
    if parsed.scheme != "https" or parsed.netloc.lower() != "github.com" or len(parts) != 2:
        raise ValueError("Use a repository root URL: https://github.com/owner/repo")
    owner, repo = parts
    repo = repo.removesuffix(".git")
    if not owner or not repo or any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_." for c in owner + repo):
        raise ValueError("Invalid GitHub owner or repository name.")
    return owner, repo


def _default_branch(owner: str, repo: str, headers: dict) -> str:
    """Return the default branch name for the repo."""
    url = f"https://api.github.com/repos/{owner}/{repo}"
    response = httpx.get(url, headers=headers, timeout=15)
    response.raise_for_status()
    return response.json().get("default_branch", "main")


def _file_tree(owner: str, repo: str, branch: str, headers: dict) -> list[dict]:
    """
    Return the recursive file tree for the repo.
    Each entry: {"path": "...", "type": "blob"|"tree", "size": int, "sha": "..."}
    """
    url = f"https://api.github.com/repos/{owner}/{repo}/git/trees/{quote(branch, safe='')}?recursive=1"
    response = httpx.get(url, headers=headers, timeout=20)
    response.raise_for_status()
    data = response.json()
    if data.get("truncated"):
        # Repo is very large — tree was truncated, we still work with what we got
        pass
    return data.get("tree", [])


def _should_fetch(entry: dict) -> bool:
    """Return True if this tree entry is a frontend file worth fetching."""
    if entry.get("type") != "blob":
        return False
    path: str = entry.get("path", "")
    # Skip files inside ignored directories
    parts = path.split("/")
    if any(part in _SKIP_DIRS for part in parts):
        return False
    # Skip by extension
    ext = os.path.splitext(path)[1].lower()
    if ext not in _FRONTEND_EXTENSIONS | BACKEND_EXTENSIONS and parts[-1] != "package.json":
        return False
    # Skip oversized files
    if entry.get("size", 0) > _MAX_FILE_BYTES:
        return False
    return True


def _fetch_file(owner: str, repo: str, path: str, headers: dict, branch: str) -> str | None:
    """
    Fetch and decode a single file's content.
    Returns the decoded text, or None if it cannot be read.
    """
    url = f"https://api.github.com/repos/{owner}/{repo}/contents/{quote(path, safe='/')}"
    try:
        response = httpx.get(url, headers=headers, params={"ref": branch}, timeout=15)
        response.raise_for_status()
        data = response.json()
        encoded = data.get("content", "")
        return base64.b64decode(encoded).decode("utf-8", errors="replace")
    except httpx.HTTPStatusError as e:
        # Do not silently turn quota/auth failures into a successful partial scan.
        if e.response.status_code in (401, 403, 429):
            raise
        return None
    except Exception:
        return None


def fetch_repo(repo_url: str, token: str = "") -> list[dict]:
    """
    Fetch all frontend-relevant files from a GitHub repository.

    Args:
        repo_url: Full GitHub URL, e.g. "https://github.com/owner/repo"
        token:    Optional personal access token for private repos or
                  higher rate limits. Falls back to GITHUB_TOKEN env var.

    Returns:
        List of dicts: [{"path": "src/Button.tsx", "content": "..."}, ...]

    Raises:
        ValueError:  If the URL cannot be parsed.
        httpx.HTTPStatusError: If the GitHub API returns an error (e.g. 404, 401).
        RuntimeError: If no frontend files are found in the repo.
    """
    resolved_token = token or os.environ.get("GITHUB_TOKEN", "")

    headers = {"Accept": "application/vnd.github+json"}
    if resolved_token:
        headers["Authorization"] = f"Bearer {resolved_token}"

    owner, repo = _parse_repo_url(repo_url)
    branch = _default_branch(owner, repo, headers)
    tree = _file_tree(owner, repo, branch, headers)

    candidates = [entry for entry in tree if _should_fetch(entry)]

    # Prioritise files closer to the root and shorter paths (more likely core code)
    candidates.sort(key=lambda e: (e["path"].count("/"), e["path"]))
    frontend = [entry for entry in candidates if file_scope(entry["path"]) == "frontend"]
    backend = [entry for entry in candidates if file_scope(entry["path"]) == "backend"]
    # Reserve source budget for UI; server context must not displace it.
    candidates = frontend[:48 if backend else _MAX_FILES] + backend[:12]

    if not frontend:
        raise RuntimeError(
            "No frontend files found in this repository. "
            "Make sure the repo contains .tsx, .jsx, .vue, .css, or similar files."
        )

    files = []
    remaining_bytes = {"frontend": 140_000 if backend else 180_000, "backend": 40_000}
    with ThreadPoolExecutor(max_workers=6) as pool:
        contents = pool.map(lambda entry: _fetch_file(owner, repo, entry["path"], headers, branch), candidates)
        for entry, content in zip(candidates, contents):
            scope = file_scope(entry["path"])
            if content and len(content.encode("utf-8")) <= remaining_bytes[scope]:
                remaining_bytes[scope] -= len(content.encode("utf-8"))
                files.append({"path": entry["path"], "content": content, "scope": scope})

    if not any(file["scope"] == "frontend" for file in files):
        raise RuntimeError("No repository files could be read. Check access and retry.")
    return files
