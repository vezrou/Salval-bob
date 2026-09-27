# Connecting everything
from dotenv import load_dotenv
load_dotenv()

import uuid
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import httpx

from agents.main_agent import main_agent
from agents.assistant import check_code
from agents.github_fetcher import fetch_repo
from agents.subagents.analyzer import analyze

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# In-memory session store
# Each session holds:
#   "history" — list of OpenAI-compatible message dicts
#   "context" — project context snapshot extracted by the analyzer (or None)
# Sessions are kept for the lifetime of the server process.
# ---------------------------------------------------------------------------
_sessions: dict[str, dict] = {}
_MAX_HISTORY = 20  # keep last 20 turns (10 exchanges) to stay within token limits


def _get_session(sid: str) -> dict:
    if sid not in _sessions:
        _sessions[sid] = {"history": [], "context": None}
    return _sessions[sid]


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------

class BuildRequest(BaseModel):
    command: str
    code: str = ""
    session_id: str = ""   # optional; server creates one if blank


class AnalyzeRequest(BaseModel):
    repo_url: str
    token: str = ""        # optional GitHub personal access token
    session_id: str = ""   # optional; server creates one if blank


class CodeCheck(BaseModel):
    code: str
    language: str = "python"


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.post("/analyze")
def analyze_repo(req: AnalyzeRequest):
    """
    Fetch a GitHub repo, analyze its frontend files, and store the resulting
    project context snapshot in the session for all subsequent /build calls.
    """
    sid = req.session_id.strip() or str(uuid.uuid4())
    try:
        files = fetch_repo(req.repo_url, token=req.token)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except httpx.HTTPStatusError as e:
        status_code = e.response.status_code
        if status_code == 429 or (status_code == 403 and (
            e.response.headers.get("x-ratelimit-remaining") == "0"
            or "retry-after" in e.response.headers
            or "rate limit" in e.response.text.lower()
        )):
            raise HTTPException(status_code=429, detail=(
                "GitHub's request limit has been reached. Configure GITHUB_TOKEN in the backend "
                "environment (Railway Variables or backend/.env), then restart/redeploy. "
                "If a token is already configured, wait for GitHub's limit to reset before retrying."
            ))
        if status_code == 403:
            raise HTTPException(status_code=403, detail="GitHub denied access. Check the token's repository permissions.")
        if status_code == 404:
            raise HTTPException(status_code=404, detail="Repository not found. Check the URL and make sure it is public.")
        if status_code == 401:
            raise HTTPException(status_code=401, detail="GitHub rejected the token. Check that GITHUB_TOKEN is valid and has not expired.")
        raise HTTPException(status_code=502, detail=f"GitHub API error: {e}")
    except httpx.RequestError:
        raise HTTPException(status_code=502, detail="GitHub could not be reached. Please retry.")
    except RuntimeError as e:
        raise HTTPException(status_code=422, detail=str(e))

    try:
        context = analyze(files)
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))

    session = _get_session(sid)
    # Keep the actual sampled source for reviews, not just its generated summary.
    # The API response below stays small; source is retained only server-side.
    session["context"] = {**context, "source_files": files}
    session["history"] = []

    return {
        "session_id": sid,
        "files_analyzed": len(files),
        "context": context,
    }


@app.post("/build")
def build(req: BuildRequest):
    # Resolve or create a session
    sid = req.session_id.strip() or str(uuid.uuid4())
    if req.session_id and sid not in _sessions:
        raise HTTPException(status_code=409, detail="Your session expired. Analyze the repository again or start a new chat.")
    session = _get_session(sid)
    history = session["history"]
    context = session["context"]

    try:
        response = main_agent(req.command, req.code, history, context)
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))

    # Append this exchange to the session history (OpenAI format)
    history.append({"role": "user",      "content": req.command})
    history.append({"role": "assistant", "content": response["result"]})

    # Trim to keep only the most recent _MAX_HISTORY entries
    session["history"] = history[-_MAX_HISTORY:]

    return {**response, "session_id": sid}


@app.get("/ping")
def ping():
    return {"status": "ok"}


@app.post("/assist")
def assist(req: CodeCheck):
    return check_code(req.code, req.language)
