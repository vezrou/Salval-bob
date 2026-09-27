# SALVAL

AI writes frontend fast. We make sure it still looks like your codebase.

SALVAL is a React/Vite chat frontend and FastAPI backend that analyzes a GitHub repository before offering project-aware implementation advice.

## Run locally

Backend (Python 3.10+):

```powershell
cd backend
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements.txt
# Copy .env.example to .env and set OPENAI_API_KEY.
.venv/Scripts/python -m uvicorn main:app --reload
```

Frontend (in a second terminal):

```powershell
cd frontend
npm ci
npm run dev
```

Open the Vite URL and choose **Try the demo**. Vite proxies `/ping`, `/analyze`, `/build`, and `/assist` to port 8000. For a separately hosted backend, set `VITE_API_URL` in `frontend/.env` before building.

## Demo flow

1. Enter a public repository root URL such as `https://github.com/owner/repo`.
2. Choose **Analyze repo**, or send your first message to scan automatically.
3. Wait for the context-loaded summary. Expand it to see the stack, file count and project summary.
4. Ask for a change. Agents receive the saved snapshot and follow Analyze → Reuse → Plan → Generate → Review → Improve.
5. Follow-up messages reuse the same session. A new scan starts a fresh chat. Failed requests preserve the message draft.

The scan focuses on frontend. In mixed repositories it samples up to 48 frontend files (140 KB) and 12 supporting backend files (40 KB); frontend-only repositories can use all 60 files / 180 KB. Backend source informs API contracts, authentication and UI integration rather than a standalone backend review. File roles are inferred from paths/extensions and checked by the analyzer. It identifies components, hooks, utilities, tokens and patterns. The sampled source is retained in the backend session and included with follow-up requests for concrete reviews. It is not exhaustive source retrieval. Code is returned in chat; this version does not write files, execute generated code, run tests on it, or perform a separate automated review pass.

Sessions live in one backend process and disappear on restart. Refreshing the frontend starts a new chat. Use one persistent backend worker for this demo; shared durable sessions are needed for multiple workers or serverless deployments. Root Vercel rewrites include the API routes; a frontend-only deployment needs `VITE_API_URL` pointing to its backend.

The UI supports public repositories. The API also accepts an optional GitHub token; `GITHUB_TOKEN` can be configured server-side. Repository source is sent to the configured model provider for analysis. Authentication, usage limits and persistent storage are still production follow-ups.

## Checks

```powershell
cd backend
.venv/Scripts/python -m unittest discover -s tests -v
cd ../frontend
npm test
npm run build
```

Tests mock GitHub/model responses and cover repository context, agent routing, failed analysis, session reuse and UI request ordering. They do not validate live model output.

## Screenshots

See [screenshots.md](screenshots.md) for screenshots and visual notes for the project.
