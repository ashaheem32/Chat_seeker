# Chatseeker


> AI-powered chat analysis dashboard. Upload any chat export, get a deep
> read on emotion, vocabulary, conflicts, love languages, and the
> overall communication health of the conversation.

Chatseeker parses platform-native exports (WhatsApp, Telegram, Instagram,
Facebook, CSV, raw JSON) into a Universal Chat JSON (UCJ) format, then
runs a multi-stage analysis pipeline that classifies emotion, detects
language, translates non-English messages to English, embeds every
message into a pgvector index, and surfaces the patterns through a
seven-module dashboard plus a streaming natural-language search.

## What you get

| Module | What it shows |
| ------ | ------------- |
| **Stats Overview** | Hero metrics, behavioral cards (response time gauge, streak, who-texts-first donut), participant comparison with relative airtime, six fun-facts tiles |
| **Emotion Timeline** | Sentiment line chart with day/week/month granularity + annotated peaks, per-participant emotion donuts with sample messages, GitHub-style mood calendar, top-5 happiest / hardest weeks |
| **Words & Emojis** | Custom CSS-flow word cloud, top-20 stacked bar chart, emoji grid with sentiment correlation, vocabulary richness with TF-IDF distinctive-word lists, common phrases + inside phrases, late-night messages panel |
| **Natural Language Search** | Streaming Claude Q&A with cited messages, filterable raw retrieval mode, suggestion pills across 4 categories, search history in localStorage, slide-in context drawer |
| **Conflict Analysis** | Difficult-moment windows with sentiment arcs, Claude-clustered themes, expandable timeline with blurred-by-default trigger messages, language patterns + hour/DOW distributions |
| **Love Languages** | Per-participant Recharts radar charts (5 axes), comparison view, Claude-classified with keyword-pre-pass cost saver, Claude-written compatibility insight |
| **Health Score** | 0–100 composite gauge across 7 weighted factors (communication balance / response consistency / sentiment trend / conflict recovery / affection frequency / engagement depth / shared activities), Claude-narrated insights |

Plus a **Layer-4 language normalization pipeline** that detects 18+
language categories (Manglish / Hinglish / Tanglish / Tenglish / Bengali
Roman / Arabic / Devanagari / Tamil / Telugu / Kannada / Bengali / Han /
Hangul / Cyrillic / French / Spanish / German / mixed code-switched),
translates non-English messages to English via Claude, and stores both
forms — every downstream stage reads `content_english` so cross-language
chats analyze cleanly.

## Tech stack

| Layer | Tech |
| ----- | ---- |
| Frontend | Next.js 14 App Router · TypeScript · Tailwind · Shadcn/UI · Recharts · Radix Dialog/Tooltip · Zustand · DM Sans + Syne |
| Backend | FastAPI · async SQLAlchemy 2.0 · asyncpg · Pydantic v2 · pgvector · tenacity |
| AI / NLP | Anthropic Claude (sonnet 4.6 / 4 dated) · OpenAI text-embedding-3-small · cardiffnlp/twitter-roberta · j-hartmann emotion-distilroberta · KeyBERT + BERTopic · spaCy en_core_web_sm · sentence-transformers (fallback) · VADER (fallback) |
| Workers | Celery + Redis |
| Database | PostgreSQL 16 · pgvector · pg_trgm · citext |
| Cache | Redis 7 (analysis cache + Celery broker + 30-day translation cache) |
| Pkg mgr | pnpm (frontend), Poetry (backend) |

## Repository layout

```
Chatseeker/
├── frontend/                           Next.js 14 app
│   ├── app/
│   │   ├── page.tsx                    Landing + drop-zone
│   │   └── dashboard/[chatId]/         Dashboard shell + modules
│   ├── components/
│   │   ├── dashboard/                  Seven dashboard modules
│   │   ├── ui/                         Shadcn primitives + StatusBadge + Toaster
│   │   └── upload/                     DropZone, progress, results
│   └── lib/                            api.ts, types.ts, store.ts, hooks
├── backend/
│   ├── alembic/                        Migrations (0001 schema, 0002 translation cols)
│   └── app/
│       ├── core/                       config / database / cache (Redis) / language
│       ├── models/                     User, ChatUpload, Message, AnalysisCache
│       ├── schemas/                    Pydantic schemas (one per module)
│       ├── services/                   The brain
│       │   ├── language/               Layer 4: detector, translator, normalizer
│       │   ├── nlp/                    sentiment, emotion, topics, entities, pipeline
│       │   ├── embeddings/             OpenAI + local fallback, indexer
│       │   ├── search/                 SemanticSearch + NaturalLanguageSearch (streaming)
│       │   ├── parser/                 Platform parsers (WhatsApp/Telegram/IG/FB/CSV)
│       │   ├── stats_service.py
│       │   ├── emotion_service.py
│       │   ├── word_service.py
│       │   ├── conflict_service.py
│       │   ├── love_language_service.py
│       │   ├── health_score_service.py
│       │   └── analysis_triage.py      Local pre-pass (saves API tokens)
│       ├── routers/                    FastAPI routes
│       ├── tasks/                      Celery tasks (language normalization)
│       └── workers/                    Celery app + NLP/embedding tasks
├── docker-compose.yml                  Production stack
├── docker-compose.dev.yml              Dev override (hot reload)
├── .env.example                        Copy to .env and fill in
└── README.md
```

## Quick start (Docker — recommended)

Requirements: Docker Desktop 4.30+ (or Docker Engine 24+) and Compose v2.

```bash
# 1. Clone + configure
git clone <your-fork>.git Chatseeker && cd Chatseeker
cp .env.example .env
# Edit .env — see "Environment variables" below for what's required

# 2. Boot the dev stack with hot reload
docker compose -f docker-compose.yml -f docker-compose.dev.yml up --build

# 3. Run migrations (in a second terminal, while the stack is up)
docker compose exec backend alembic upgrade head

# 4. Open the app
open http://localhost:3000
```

That brings up:

| Service  | URL / port            | Purpose |
| -------- | --------------------- | ------- |
| Frontend | http://localhost:3000 | Next.js UI |
| Backend  | http://localhost:8000 | FastAPI — `/docs` for Swagger |
| Postgres | localhost:5432        | DB with pgvector / pg_trgm / citext |
| Redis    | localhost:6379        | Cache + Celery broker + translation cache |
| Worker   | (background)          | Celery worker (NLP + embedding + language tasks) |

### Production-mode local boot

```bash
docker compose up -d --build
docker compose logs -f
```

Builds optimized images (Next.js standalone output, Python venv copied
into a slim base) and runs detached.

## Local development without Docker

Useful for fast iteration with native debuggers. You can run frontend +
backend natively while still using Docker for Postgres + Redis only.

### 1. Data services

```bash
docker compose up -d postgres redis
```

### 2. Backend

Requires Python 3.12 and Poetry 1.8+.

```bash
cd backend
poetry install                              # base deps
poetry install --with nlp                   # add transformers + sentence-transformers + spacy
poetry run python -m spacy download en_core_web_sm

poetry run alembic upgrade head             # apply migrations 0001 + 0002

# API
poetry run uvicorn app.main:app --reload --port 8000

# In a second terminal — Celery worker (NLP / embedding / language tasks)
poetry run celery -A app.workers.celery_app worker --loglevel=info
```

API docs: http://localhost:8000/docs

### 3. Frontend

Requires Node 20+ and pnpm 9+ (`corepack enable && corepack prepare pnpm@latest --activate`).

```bash
cd frontend
pnpm install
pnpm dev
```

App: http://localhost:3000

## Pipeline at a glance

After upload, processing runs as a chain of Celery tasks. Status is
visible via `GET /api/v1/conversations/{id}/status`.

```
   ┌──────────┐   ┌────────────────────┐   ┌─────────┐   ┌───────────┐   ┌──────┐
   │  upload  │──▶│ language normalize │──▶│   NLP   │──▶│ embedding │──▶│ done │
   └──────────┘   └────────────────────┘   └─────────┘   └───────────┘   └──────┘
   parse + persist   detect → translate    sentiment +     pgvector
   to messages       → write content_       emotion +      via OpenAI or
                     english                 KeyBERT +     local fallback
                                             BERTopic +
                                             spaCy NER
```

Every stage is **idempotent** — re-running a task only touches rows
where the relevant column is still NULL. Crashes are safe; just
re-trigger the task.

Every stage **fails open** — when Anthropic / OpenAI aren't reachable,
the dashboard still renders with deterministic fallbacks and the UI
surfaces a "less precise without AI" hint via the `used_llm` flag.

## API surface

All routes are mounted under `/api/v1`.

| Group | Endpoints |
| ----- | --------- |
| Upload | `POST /upload`, `GET /upload/{id}`, `GET /upload/{id}/ucj`, `WS /upload/ws/{id}` |
| Conversations | `GET /conversations`, `GET /conversations/{id}`, `GET /conversations/{id}/status` |
| Stats | `/stats/{id}/overview`, `/participant/{name}` |
| Emotion | `/stats/{id}/sentiment-timeline`, `/emotion-distribution`, `/emotion-by-participant`, `/emotional-peaks`, `/mood-calendar` |
| Words | `/stats/{id}/word-frequency`, `/emoji-frequency`, `/bigrams`, `/unique-words`, `/word-trend`, `/late-night` |
| Conflict | `/stats/{id}/conflicts`, `/conflict-themes` |
| Love | `/stats/{id}/love-language` |
| Health | `/stats/{id}/health-score` |
| Search | `POST /search/{id}`, `POST /search/{id}/stream` (SSE), `GET /search/{id}/suggestions`, `GET /search/{id}/similar/{msg_id}`, `GET /search/{id}/context/{msg_id}` |

Every read endpoint accepts `?refresh=true` to bypass the Redis cache.

## Database migrations

```bash
cd backend
poetry run alembic upgrade head            # apply both migrations
poetry run alembic revision --autogenerate -m "describe the change"
```

Migrations to date:
- **0001 initial** — `users`, `chat_uploads`, `messages`, `analysis_cache`,
  enum types, HNSW vector index, all the dashboard-friendly composite indexes.
- **0002 translation columns** — adds `content_english` / `original_language` /
  `was_translated` to `messages`. All three are nullable / default-false so
  the migration is online-safe with no backfill required.

## Environment variables

See [`.env.example`](.env.example) for the full annotated list.

**Critical for full functionality:**

- `ANTHROPIC_API_KEY` — without this you lose: love-language Claude
  classification, conflict theme clustering, NL search synthesis,
  health-score narrative + per-factor insights, Layer-4 language
  translation. Fallbacks exist but quality drops noticeably.
- `OPENAI_API_KEY` — used for embeddings (`text-embedding-3-small`).
  Without it, Chatseeker falls back to the local `all-MiniLM-L6-v2` model
  (requires `poetry install --with nlp`); vectors are zero-padded to
  match the DB column dim.
- `SECRET_KEY` — generate with
  `python -c "import secrets; print(secrets.token_urlsafe(32))"`.
- `POSTGRES_PASSWORD` — change for any non-local deployment.

**Hostnames change between docker-compose and native dev:**

| Variable | Native dev | Inside docker-compose |
| -------- | ---------- | --------------------- |
| `POSTGRES_HOST` | `localhost` | `postgres` |
| `DATABASE_URL` host | `localhost` | `postgres` |
| `REDIS_URL` host | `localhost` | `redis` |
| `UPLOAD_DIR` | `./uploads` | `/app/uploads` (default) |

## Universal Chat JSON (UCJ)

Platform-agnostic schema. Parsers normalize WhatsApp .txt, Telegram
JSON, Instagram JSON, Facebook JSON, CSV, and raw UCJ exports into a
single shape before persistence. Schema lives in two places that must
stay in sync:

- Backend (canonical): [`backend/app/schemas/ucj.py`](backend/app/schemas/ucj.py)
- Frontend types:      [`frontend/lib/types.ts`](frontend/lib/types.ts)

After persistence, the language normalizer adds `content_english` per
message — every downstream module reads that field with `content` as
the fallback, so non-English chats analyze in semantically-comparable
English without losing the original.

## Cost & API token efficiency

Chatseeker runs a **local pre-pass before every Claude call** to keep
costs under control. The pattern, surfaced through
[`backend/app/services/analysis_triage.py`](backend/app/services/analysis_triage.py):

- **Love-language classifier** — keyword fallback runs first on every
  candidate; only the residual gets shipped to Claude. Real chats
  produce 30–70% keyword hits, cutting tokens proportionally.
- **NL search rephrase** — keyword-rich queries (≥5 distinct
  non-stopword tokens) skip the rephrase Claude call entirely.
- **Translation cache** — Layer-4 translator caches per-message in
  Redis with 30-day TTL keyed by `MD5(original_text)`. Re-uploading
  the same chat with one new line only pays for the new line.

Estimated cost for a fully-analyzed 50k-message chat: **~$0.12–$0.25**
(language translation is the heaviest path; everything else fits in a
few cents thanks to caching + pre-passes).

## Project scripts

### Backend

```bash
poetry run pytest                          # unit tests
poetry run ruff check app                  # lint
poetry run ruff format app                 # auto-format
poetry run mypy app                        # type-check
poetry run alembic upgrade head            # apply migrations
poetry run celery -A app.workers.celery_app worker --loglevel=info
```

### Frontend

```bash
pnpm dev                                   # dev server with hot reload
pnpm build                                 # production build
pnpm lint                                  # next lint
pnpm type-check                            # tsc --noEmit
pnpm format                                # prettier
```

## Architecture notes

- **Async everywhere.** FastAPI + asyncpg + async SQLAlchemy means
  request handlers don't block the event loop on DB I/O. Heavy
  LLM/embedding work runs in Celery; the API just enqueues.
- **pgvector + HNSW.** Each message gets a 1536-dim embedding indexed
  with `(m=16, ef_construction=64)`. Sub-100ms semantic search on
  100k-message chats.
- **Streaming SSE.** Natural-language search uses POST+ReadableStream
  (not EventSource — POST bodies aren't supported there). Events:
  `meta` → `delta` chunks → `done`. Frontend renders evidence cards
  as soon as `meta` arrives, so the UI feels alive while Claude types.
- **Two-tier cache.** Redis for hot reads (analysis services, 1h
  default TTL; translation, 30d TTL). Postgres `analysis_cache` table
  for durable per-upload aggregates.
- **Idempotent pipeline.** Every stage uses "WHERE column IS NULL" to
  pick up where it left off. No checkpoint tables.
- **Dual-fallback design.** Every Claude / OpenAI call has a
  deterministic fallback; the frontend reads a `used_llm` boolean so
  the UI honestly reflects whether AI was involved.

## License

TBD.
