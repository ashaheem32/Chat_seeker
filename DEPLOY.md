# Deploying ChatLens for free

Hosting costs $0 on this layout. The only metered spend is the AI API usage
(Claude for narration and search, OpenAI for embeddings), which runs to a few
cents per analysed chat.

| Piece | Service | Free tier limits that matter |
| ----- | ------- | ----------------------------- |
| Frontend | Vercel Hobby | none for this app |
| API + Celery worker | Render free web service | 512 MB RAM, sleeps after 15 min idle, ~30-60 s cold start, disk is ephemeral |
| Postgres + pgvector | Neon free | 0.5 GB storage |
| Redis | Render Key Value free | 25 MB, internal only |

## 1. Neon (database)

1. Create a project at https://neon.tech. Pick the same region as Render
   (US West / Oregon by default).
2. Copy the connection string and convert it to the asyncpg form:

   ```
   postgresql+asyncpg://USER:PASSWORD@HOST/DB?ssl=require
   ```

   Keep this for step 2. Migrations run on every boot, and the first
   migration creates the `vector`, `pg_trgm` and `citext` extensions, so no
   manual SQL is needed.

## 2. Render (backend)

1. Push the repo to GitHub (it already points at `ashaheem32/Chat_seeker`).
2. In Render: **New → Blueprint**, pick the repo. It reads `render.yaml` and
   creates `chatlens-api` (web, free) and `chatlens-redis` (key value, free).
3. When prompted, fill the secrets:

   | Key | Value |
   | --- | ----- |
   | `DATABASE_URL` | the asyncpg string from Neon |
   | `CORS_ORIGINS` | your Vercel URL, e.g. `https://chatlens.vercel.app` |
   | `ANTHROPIC_API_KEY` | from console.anthropic.com |
   | `OPENAI_API_KEY` | from platform.openai.com (needed for embeddings / search) |

4. Deploy. The first build takes a few minutes. When the logs show
   `[start] starting api on :10000`, open
   `https://chatlens-api.onrender.com/health` and expect `{"status":"ok"}`.

What `backend/scripts/start.sh` does on each boot: `alembic upgrade head`,
then a Celery worker in the background, then uvicorn in the foreground.
Both share the container's disk, which the upload flow relies on.

## 3. Vercel (frontend)

1. Import the repo. Set **Root Directory** to `frontend`. Framework preset
   Next.js is detected automatically (pnpm is picked up from the lockfile).
2. Environment variables (Production, and Preview if you want previews to
   hit the same backend):

   | Key | Value |
   | --- | ----- |
   | `NEXT_PUBLIC_API_URL` | `https://chatlens-api.onrender.com` |
   | `BACKEND_URL` | `https://chatlens-api.onrender.com` |

   `NEXT_PUBLIC_API_URL` must be the public Render URL, not the `/api/backend`
   proxy: Vercel rewrites do not carry WebSocket upgrades and the upload
   progress feed uses one. `BACKEND_URL` keeps the server-side proxy working
   for anything that still goes through it.
3. Deploy. Then go back to Render and make sure `CORS_ORIGINS` matches the
   final Vercel domain exactly (scheme + host, no trailing slash).

## 4. Smoke test

1. Open the Vercel URL. The landing page should load instantly.
2. Click **Analyze a chat**, upload `sample_data/*`. The first request after
   idle takes up to a minute while Render wakes the service.
3. Watch the progress bar reach 100% and the dashboard open.

## Known limits on the free tier

- **Cold starts.** Render stops the service after 15 idle minutes. The first
  request wakes it. A free uptime pinger (e.g. cron-job.org hitting
  `/health` every 10 minutes) keeps it warm but uses your 750 free hours
  faster; one always-on service fits within the monthly allowance.
- **Ephemeral disk.** Raw uploads under `/tmp/chatlens_uploads` vanish on
  redeploy. Parsed messages live in Neon, so dashboards survive.
- **Memory.** The Docker image skips the heavy `nlp` dependency group, so
  sentiment uses VADER and emotion donuts stay empty. That is what keeps
  the service inside 512 MB. Installing the `nlp` group needs a paid
  instance or a VM.
- **Neon idle suspend.** Free Neon branches pause after inactivity and
  resume on the first query (a second or two). Nothing to do.

## Alternative: one free VM

If you ever want zero cold starts and the full NLP stack, an Oracle Cloud
"Always Free" ARM VM (4 cores, 24 GB) runs `docker compose up -d` unchanged.
Add Caddy for HTTPS and point Vercel at it.
