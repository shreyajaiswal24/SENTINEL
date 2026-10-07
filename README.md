# 🛡️ SENTINEL — Self-Healing Pipeline Monitor

An agentic system that monitors data pipelines, detects failures, diagnoses them
with an LLM, gates remediation by risk (auto-fix / voice approval / stop), applies
and validates fixes, and reports — orchestrated as a LangGraph state machine.

```
pipeline run → monitor → diagnose → [risk gate] → hitl → fixer → validator → reporter
                  │ (LLM)                │ (voice)            ↑ retry ≤ 2 ┘
                  └ detect anomaly       └ yes/no | approve/reject
```

## Architecture

| Layer | Path | What |
|-------|------|------|
| Pipelines | `pipelines/` | 3 Faker pipelines with 6 injectable failures |
| State / DB | `utils/` | `SentinelState`, SQLite (`pipeline_runs`, `pipeline_data`, `incidents`) |
| Agents | `agents/` | monitor, diagnose (LLM: OpenAI→Groq), fixer, validator, hitl, reporter + `graph.py` |
| Voice | `voice/` | ElevenLabs TTS + STT (modes: off / text / voice) |
| Refine | `refine/` | Bring-your-own-data: verify + clean an uploaded sales CSV/Excel |
| API | `backend/` | FastAPI exposing `dashboard/data.py` as JSON + the refine endpoint |
| UI | `frontend/` | React + Vite dashboard (metrics, incidents, refine panel) |

## Setup

```bash
pip install -r requirements.txt
cp .env.example .env          # add OPENAI_API_KEY (preferred) or GROQ_API_KEY, + ELEVENLABS_API_KEY
```

**Diagnose LLM:** OpenAI and Groq (`openai/gpt-oss-120b`), each the other's automatic
fallback; `LLM_PROVIDER` picks which is tried first. Setting either key alone works; with both, a failed OpenAI
call falls through to Groq.

## Run

**Pipelines + phase tests** (no keys needed):
```bash
python main.py --failures     # generate runs (clean + injected)
python phase2.py              # Monitor   (21/21 vs ground truth)
python phase4.py              # full-graph routing
python phase5.py              # real Fixer + Validator
python phase6.py              # voice HITL (mocked I/O)
python phase7_9.py            # Reporter + API
python phase10_approvals.py   # dashboard click-to-approve (offline, temp DB)
python phase3.py [--all]      # Diagnose (live LLM) — needs OPENAI_API_KEY or GROQ_API_KEY
```

**Dashboard** (two terminals):
```bash
uvicorn backend.main:app --reload --port 8000     # API → http://localhost:8000
cd frontend && npm install && npm run dev          # UI  → http://localhost:5173
# port 8000 busy? run the API on 8001 and start the UI with API_PORT=8001
```
The Vite dev server proxies `/api` to the backend. For voice HITL, set
`VOICE_MODE=text` (stdin) or `VOICE_MODE=voice` (ElevenLabs + mic) in `.env`.
`ELEVENLABS_VOICE_ID` defaults to "Sarah" (a stock free-plan voice); library
voices like Rachel need a paid ElevenLabs plan.

## Approve risky fixes from the dashboard

Medium/high-risk incidents **pause** the LangGraph run at the HITL node
(`interrupt()`, state saved by a SQLite checkpointer) and appear in the
**"Waiting for your approval"** panel with the LLM's cause, risk and confidence.

* **Approve fix** resumes the run (`Command(resume=True)`) → fixer → validator → reporter
* **Reject** resumes it straight to the reporter, with no fix applied
* **Approve all** clears the queue, and **Simulate failures** injects a batch of failures
* Low risk auto-fixes, critical escalates and never shows a button
* Decisions are claimed atomically, so a double-click can't run a fix twice

API: `GET /api/approvals`, `POST /api/approvals/{id}/approve|reject`,
`POST /api/approvals/approve-all`, `POST /api/simulate` (see `backend/approvals.py`).

## Refine your data (bring-your-own-data)

Upload a real sales file on the dashboard and get a cleaned one back. SENTINEL
verifies four things — **duplicates, null/missing values, column/schema issues,
row-count/volume** — removes exact dupes, drops rows missing a critical value
(`order_id`/`amount`), renames drifted columns (`total_amount`→`amount`), and
flags (never fabricates) missing columns or low volume.

* Engine: `refine/engine.py` (`refine_file` / `refine_dataframe`)
* API: `POST /api/refine` (CSV/Excel upload) → report + `download_token`;
  `GET /api/refine/download/{token}` → the refined file
* UI: the **"Refine your data"** panel in the dashboard
* Multiple files can be uploaded at once; each gets its own report + download
* Try it: upload `sample_messy_sales.csv`, or the 7 scenario files in `test_uploads/`
  (regenerate with `python test_uploads/make_samples.py`)

It **cleans, never fabricates** — missing real data is flagged, not invented.

## Status

Phases 1–9 implemented and **verified live** (OpenAI-first diagnosis with Groq
fallback; ElevenLabs voice via TTS→STT round-trip; full graph end-to-end).
Plus the bring-your-own-data refine feature (CSV & Excel). Real-audio voice
playback needs a machine with speakers/mic; this repo was validated headless.
