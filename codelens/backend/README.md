# AI Code Review Assistant — Backend

Runs the analysis pipeline the frontend can't do on its own: Pylint, Bandit,
and Radon on submitted code, plus an AI review via Claude. Auth and reading
reviews are handled entirely by the frontend talking to Supabase directly —
this service only *writes analysis results* into Supabase.

## Setup

```bash
cd backend
pip install -r requirements.txt
cp .env.example .env
# then fill in .env with your real values
python app.py
```

Runs on `http://localhost:5000` by default.

## Required `.env` values

| Variable | Where to find it |
|---|---|
| `SUPABASE_URL` | Supabase dashboard → Project Settings → API |
| `SUPABASE_SERVICE_ROLE_KEY` | Same page — **never expose this in frontend code**, only here on the backend |
| `ANTHROPIC_API_KEY` | console.anthropic.com → API Keys |
| `ANTHROPIC_MODEL` | Defaults to `claude-sonnet-5` — check docs.claude.com if this stops working, model names change over time |

If `ANTHROPIC_API_KEY` is left blank, the AI review stage is skipped
automatically and only static analysis results are saved — the endpoint
won't fail, it just returns fewer findings.

## The one endpoint that matters: `POST /api/analyze`

This is what the frontend's `setTimeout` mock in `startReview()` should be
replaced with. Call it right after creating the `projects` and `reviews`
rows in Supabase:

```js
const { data: { session } } = await supabase.auth.getSession();

const formData = new FormData();
formData.append('project_id', project.id);
formData.append('review_id', review.id);
formData.append('upload_type', uploadType); // 'file_upload' | 'code_paste' | 'github'

// then depending on uploadType, also append one of:
formData.append('file', fileObject);              // file_upload
formData.append('code', pastedCode);               // code_paste
formData.append('github_url', repoUrl);            // github
formData.append('branch', branchName);             // github, optional

await fetch('http://localhost:5000/api/analyze', {
  method: 'POST',
  headers: { Authorization: `Bearer ${session.access_token}` },
  body: formData,
});
```

The endpoint verifies the token, confirms the project belongs to that user,
runs the full pipeline, and writes the completed review + findings straight
into Supabase — so once it returns (or even before, since your frontend
already re-fetches from Supabase), the dashboard will show the real results.

## Why this isn't on Vercel

Pylint/Bandit/Radon + an LLM call can easily take several seconds on a real
file or repo, which risks tripping serverless function timeouts. Deploy this
on **Render** or **Railway** instead (as planned earlier) — a normal
long-running process, not a serverless function. Keep Vercel for the
frontend only.

## Notes / things to harden before a public deployment

- `CORS(app)` currently allows any origin — restrict it to your deployed
  frontend's URL before going live.
- GitHub repo cloning has no size limit beyond `max_files` in
  `file_utils.py` — add a repo size check if you expect people to submit
  very large projects.
- The endpoint runs synchronously, so a slow repo blocks that request. For
  a portfolio project this is fine; for real usage you'd move analysis to
  a background job (Celery/RQ) and poll `status` instead.
