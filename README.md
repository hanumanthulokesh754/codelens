# Codelens — AI Code Review Assistant

An AI-powered code review tool: submit a file, pasted snippet, or a public
GitHub repo, and get back static analysis (Pylint, Bandit, Radon) plus an
AI-generated review (bugs, code smells, performance, refactoring advice) —
all in one dashboard.

## Structure

```
codelens/
├── frontend/
│   └── index.html        Single-file frontend (HTML + Tailwind CDN + vanilla JS)
├── backend/
│   ├── app.py             Flask API — the one real endpoint: POST /api/analyze
│   ├── analysis/
│   │   ├── file_utils.py       Handles upload / paste / GitHub clone
│   │   ├── static_analysis.py  Pylint + Bandit + Radon wrappers
│   │   └── ai_review.py        Claude API call for higher-level review
│   ├── requirements.txt
│   ├── .env.example
│   └── README.md          Backend-specific setup details
└── database/
    └── schema.sql         Supabase Postgres schema + RLS policies
```

## How the pieces fit together

- **Frontend → Supabase directly**: auth (register/login/logout), and all
  reading/listing/deleting of reviews, go straight from the browser to
  Supabase using the anon key. Row Level Security (`database/schema.sql`)
  is what actually keeps users' data isolated from each other — not the
  frontend code.
- **Frontend → Backend**: only for the one thing Supabase can't do —
  actually running Pylint/Bandit/Radon and calling the AI model. The
  frontend calls `POST /api/analyze` on the Flask backend, which then
  writes the results back into Supabase using a service-role key.

## Setup order

1. **Database first**: create a Supabase project, then run
   `database/schema.sql` in the Supabase SQL Editor.
2. **Backend**: see `backend/README.md` — copy `.env.example` to `.env`,
   fill in your Supabase and Anthropic API keys, `pip install -r
   requirements.txt`, then `python app.py`.
3. **Frontend**: open `frontend/index.html` and fill in `SUPABASE_URL`,
   `SUPABASE_ANON_KEY`, and `BACKEND_URL` near the top of the
   `<script type="module">` block. No build step — just open the file in
   a browser, or serve it with any static file server.

## Deployment notes

- Frontend → Vercel (or any static host).
- Backend → Render or Railway, **not** Vercel serverless — static analysis
  and LLM calls can take longer than a serverless function's timeout allows.
- Database → Supabase (already hosted).

## Tech stack

| Layer | Technology |
|---|---|
| Frontend | HTML, Tailwind CSS (CDN), vanilla JS |
| Backend | Flask |
| Database / Auth | Supabase (Postgres + Row Level Security) |
| Static analysis | Pylint, Bandit, Radon |
| AI review | Claude API (Anthropic) |
