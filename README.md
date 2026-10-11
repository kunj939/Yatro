# TripAI

**Your trip. Your budget. Your choices.**

TripAI is a budget-aware travel planner. You give it a start point, destination, dates, time window, budget, group and
preferences; it collects real-world data, verifies it, proves the trip is feasible (or explains exactly why not), optimises
the route inside your time and money limits, ranks alternatives, and only then asks an LLM to explain the result.
The LLM is never the source of truth for prices, times, distances or opening hours.

With **no API keys** it runs in **demo mode**: a complete Gurugram → Jaipur day trip for a couple on ₹5,000
(vegetarian, train + cab), built by the real engine from sample data.

```
tripai/
├── backend/            FastAPI, SQLAlchemy 2, Alembic, planning engine, providers, tests
├── frontend/           Next.js 15 (App Router), TypeScript, Tailwind
├── docs/API.md         Endpoint reference
├── docs/ARCHITECTURE.md  Pipeline, providers, verification, quota
├── docker-compose.yml  PostgreSQL + backend + frontend
└── .env.example        Every setting, documented
```

## Quick start with Docker (recommended)

Requirements: Docker with Compose v2.

```bash
cp .env.example .env          # works as-is in demo mode
docker compose up --build
```

- App: http://localhost:3000 (sample trip at http://localhost:3000/trips/demo)
- API docs (Swagger): http://localhost:8000/api/docs
- Migrations run automatically when the backend container starts.

## Local development without Docker

Requirements: Python 3.12, Node 20+ (22 recommended), PostgreSQL 14+ (or SQLite for a quick look).

### 1. Database

```bash
# PostgreSQL
createuser -P tripai            # password: tripai
createdb -O tripai tripai
```

Or skip PostgreSQL and use SQLite by setting `DATABASE_URL=sqlite:///./tripai.db` in `.env`.

### 2. Backend

```bash
cp .env.example .env            # from the repo root; the backend also reads ../.env
cd backend
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
alembic upgrade head
uvicorn app.main:app --reload --port 8000
```

Check it: `curl http://localhost:8000/api/health` → `{"status":"ok"}`.

### 3. Frontend

```bash
cd frontend
npm install
npm run dev                     # http://localhost:3000
```

The frontend talks to `NEXT_PUBLIC_API_URL` (default `http://localhost:8000`). For a production build:
`npm run build && npm start`.

## Modes and keys

| Setting | Effect |
|---|---|
| `DEMO_MODE=true` | Sample data only, no network calls. |
| `DEMO_MODE=auto` (default) | Each provider uses its real integration when its key exists, sample data otherwise. |
| `GOOGLE_MAPS_SERVER_KEY` | Places (New), Routes and Geocoding for real places, hours, prices and travel times. |
| `WEB_SEARCH_PROVIDER` + `WEB_SEARCH_API_KEY` | Brave, Serper or Tavily, used to cross-check opening hours and closures. |
| `OPENROUTER_API_KEY` | LLM explanations and plain-language changes via OpenRouter. |
| `OPENAI_COMPAT_BASE_URL` | Any OpenAI-compatible server, e.g. self-hosted vLLM serving Qwen. No engine changes needed. |
| `NEXT_PUBLIC_GOOGLE_MAPS_BROWSER_KEY` | Google Maps in the Map tab. Without it, a route sketch is drawn from plan coordinates. |

Every item in a plan carries a verification state (Official, Verified, Estimated, Needs check, Not available, Sample data),
so users can always see where a number came from.

Switching to a self-hosted model:

```bash
vllm serve Qwen/Qwen2.5-32B-Instruct --port 8001
# .env
LLM_PROVIDER=openai_compatible
OPENAI_COMPAT_BASE_URL=http://localhost:8001/v1
OPENAI_COMPAT_MODEL=Qwen/Qwen2.5-32B-Instruct
```

## Plans and payments

- Free: **2 trip plans per account, lifetime** (not monthly). After that: "Your 2 free trip plans are used."
- **PLUS** ₹99/month: unlimited plans. **ONE TRIP** ₹49: one extra plan.
- Changing or re-planning a trip you already planned never uses a plan. A plan that turns out infeasible is refunded.
- `PAYMENTS_MODE=mock` runs a test checkout (nothing is charged). Connect a real payment provider in
  `backend/app/api/routes/subscription.py` (verify its webhook) before charging anyone.

## Tests

```bash
cd backend
pip install -r requirements-dev.txt
pytest                                   # SQLite by default
TEST_DATABASE_URL=postgresql+psycopg://tripai:tripai@localhost:5432/tripai_test pytest   # against PostgreSQL
ruff check .
```

```bash
cd frontend
npm run typecheck && npm run build
```

## Production checklist

- Set `ENVIRONMENT=production` and a strong `JWT_SECRET` (the app refuses to start with the dev secret).
- Set `CORS_ORIGINS` to your frontend origin and `NEXT_PUBLIC_API_URL` to your public API URL (rebuild the frontend after changing it).
- Restrict the Google browser key by HTTP referrer; keep `GOOGLE_MAPS_SERVER_KEY` server-side only.
- Replace mock payments before launch.
- Run behind HTTPS (a reverse proxy such as Caddy or nginx).

## Rebranding

The name, tagline and support email live in `frontend/lib/brand.ts` and `APP_NAME` in the backend settings.
Colours are design tokens in `frontend/tailwind.config.ts`.
