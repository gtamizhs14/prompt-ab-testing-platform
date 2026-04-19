# Prompt A/B Testing Platform

A backend platform for versioning LLM prompts and running statistically rigorous A/B experiments. Built with FastAPI, PostgreSQL, and Streamlit.

## Features

- **Prompt versioning** — every change is tracked with version numbers, commit messages, and a full audit trail
- **A/B experiments** — run controlled experiments comparing prompt versions on real traffic
- **Consistent variant assignment** — MD5 hashing ensures the same user always gets the same variant
- **Statistical significance** — Welch's t-test, 95% confidence intervals, MDE, and sample size tracking
- **LLM-as-judge** — async quality scoring (1–5) using a second model call after each response
- **Auto-stop** — kills experiments automatically on high error rates (>10%) or clear statistical losers (p < 0.01)
- **Winner promotion** — one-click promotion of winning prompt version to production
- **Streamlit dashboard** — live metrics, time series charts, side-by-side version comparison

## Tech Stack

- **API** — FastAPI
- **Database** — PostgreSQL + SQLAlchemy + Alembic
- **LLM** — Groq (llama-3.1-8b-instant, free tier)
- **Stats** — scipy
- **Dashboard** — Streamlit

## Setup

```bash
git clone https://github.com/gtamizhs14/prompt-ab-testing-platform.git
cd prompt-ab-testing-platform

python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

Create a `.env` file:

```env
DATABASE_URL=postgresql://user:password@localhost:5432/prompt_db
LLM_PROVIDER=groq
GROQ_API_KEY=your_key_here
```

Run migrations and start:

```bash
alembic upgrade head
uvicorn app.main:app --reload --port 8000

# Dashboard (separate terminal)
streamlit run dashboard/app.py
```

## API Overview

### Prompts
| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/prompts` | Create prompt |
| `POST` | `/prompts/{id}/versions` | Add version |
| `GET` | `/prompts/{id}/versions` | List versions |
| `PUT` | `/prompts/{id}/activate/{version_id}` | Set active version |
| `GET` | `/prompts/{id}/diff/{v1}/{v2}` | Diff two versions |
| `POST` | `/prompts/{id}/compare` | Run both versions through LLM side by side |

### Experiments
| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/experiments` | Create experiment |
| `POST` | `/experiments/{id}/variants` | Add variant |
| `PUT` | `/experiments/{id}/start` | Start (validates traffic sums to 100%) |
| `PUT` | `/experiments/{id}/stop` | Stop |
| `GET` | `/experiments/{id}/results` | Live metrics + significance |
| `GET` | `/experiments/{id}/timeseries` | Metric over time per variant |
| `POST` | `/experiments/{id}/promote-winner` | Promote winning version |
| `POST` | `/experiments/v1/completions` | Serve a completion |

## Project Structure

```
app/
├── api/
│   ├── prompt_routes.py
│   └── experiment_routes.py
├── db/
│   ├── models.py
│   └── database.py
├── schemas/
│   └── prompt.py
└── services/
    ├── llm_service.py        # Groq / Ollama / mock
    ├── judge_service.py      # LLM-as-judge scoring
    ├── experiment_service.py # Auto-stop, winner logic
    ├── metrics_service.py    # Metric aggregation
    ├── stats_service.py      # Welch t-test, CI, MDE
    └── hash_service.py       # Consistent hashing
dashboard/
└── app.py                    # Streamlit UI
alembic/                      # DB migrations
```
