# Retention Pipeline API

Human-oriented guide for the FastAPI service. Interactive OpenAPI docs live at `/docs` when the server is running.

## Security

| Variable | Where set | Exposed via API |
|----------|-----------|-----------------|
| `CLIENT_ID` | Server environment only (Render env, `.env` locally) | Never |
| `CLIENT_SECRET` | Server environment only | Never |
| All other settings | `PATCH /api/v1/config` → `runtime_config.json` | `GET /api/v1/config` (no secrets) |

`GET /api/v1/config` returns `clientCredentialsConfigured: true|false` so the dashboard knows whether fetch jobs can run.

## Local development

```bash
cd "/path/to/Retention Pipeline PRD"
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # fill CLIENT_ID, CLIENT_SECRET, SERIES_ID, TOTAL_DATA_TIME_START

export PYTHONPATH=.
uvicorn retention_api.main:app --reload --port 8000
```

Open http://localhost:8000/docs

### Environment variables

| Variable | Default | Purpose |
|----------|---------|---------|
| `CLIENT_ID` / `CLIENT_SECRET` | (required for fetch) | PuzzleMe API credentials |
| `DATA_DIR` | `Daily Play Data` | Daily play CSV directory |
| `OUTPUT_DIR` | `output` | JSON, charts, jobs |
| `RUNTIME_CONFIG_PATH` | `data/runtime_config.json` | Dashboard settings file |
| `CORS_ORIGINS` | (empty) | Comma-separated origins for React |
| `JOB_MAX_WORKERS` | `2` | Background job thread pool size |
| `USERS_CSV_PATH` | `users_*.csv` in project root | Default users export |

## Configuration API

### `GET /api/v1/config`

Returns effective dashboard settings plus `dataDir`, `outputDir`, and `clientCredentialsConfigured`.

### `PATCH /api/v1/config`

Partial update. Body fields (all optional):

- **Fetch:** `apiBaseUrl`, `seriesId`, `timezone`, `totalDataTimeStart`, `puzzleId`, `limit`, `offset`, `rateLimitRps`, `maxWorkers`
- **Analysis timeline:** `timelineStart`, `timelineEnd`, `grain` (`Daily` | `Weekly` | `Monthly`)
- **NUU window:** `windowStart`, `windowEnd` (ISO dates)
- **Retention horizons:** `strictHorizons` (e.g. `[1,3,7,30]`), `windowHorizons` (e.g. `[3,7,30]`)
- **Signup:** `usersCsvPath` (or upload via `POST /api/v1/data/users-csv`)

Example:

```bash
curl -s -X PATCH http://localhost:8000/api/v1/config \
  -H "Content-Type: application/json" \
  -d '{
    "seriesId": "your-series",
    "timezone": "UTC",
    "timelineStart": "2026-07-01T00:00:00",
    "timelineEnd": "2026-07-16T23:59:59",
    "grain": "Daily",
    "windowStart": "2026-07-01",
    "windowEnd": "2026-07-16",
    "strictHorizons": [1, 3, 7, 30],
    "windowHorizons": [3, 7, 30]
  }'
```

## Jobs (async)

Long work runs in the background. Poll until `status` is `succeeded` or `failed`.

### `POST /api/v1/jobs`

```json
{ "type": "<jobType>", "params": { } }
```

| `type` | Wraps | Notes |
|--------|--------|-------|
| `backfill` | `run_historical_backfill` | Needs credentials |
| `daily` | `run_daily_cron` | Needs credentials |
| `analyze` | `run_analysis` | Uses config + optional `params` overrides |
| `pipeline` | backfill + analyze + charts | `params.skipFetch: true` to skip fetch |
| `nuu_counts` | `scripts/compute_nuu.py` | Window from config or `params` |
| `nuu_retention` | NUU retention script logic | Configurable horizons |
| `signup_fraction` | `run_signup_fraction` | Weekly signups / active users |
| `nuu_signup_fraction` | `plot_nuu_signup_fraction.py` | Run `nuu_counts` first |

`params` can override any timeline/window/grain/timezone/horizon field for a single job.

Example — analyze existing CSVs without fetch:

```bash
curl -s -X POST http://localhost:8000/api/v1/jobs \
  -H "Content-Type: application/json" \
  -d '{"type":"pipeline","params":{"skipFetch":true}}'
```

Response (`202`):

```json
{ "id": "...", "type": "pipeline", "status": "pending", ... }
```

### `GET /api/v1/jobs/{id}`

### `GET /api/v1/jobs?limit=20`

## Read APIs (dashboard)

| Endpoint | Description |
|----------|-------------|
| `GET /api/v1/data/coverage` | Dates with CSV files in `DATA_DIR` |
| `POST /api/v1/data/users-csv` | Multipart upload; updates `usersCsvPath` |
| `GET /api/v1/results/analysis` | `analysis_results.json` |
| `GET /api/v1/results/signup-fraction` | `signup_fraction.json` |
| `GET /api/v1/results/nuu-retention` | `nuu_retention.json` |
| `GET /api/v1/artifacts/charts` | List chart filenames |
| `GET /api/v1/artifacts/charts/{name}` | PNG or HTML file |

## Render deployment

1. Create a **Web Service** from this repo; use `render.yaml` or set:
   - **Build:** `pip install -r requirements.txt`
   - **Start:** `uvicorn retention_api.main:app --host 0.0.0.0 --port $PORT`
2. Attach a **persistent disk** (e.g. 10GB) at `/var/data`; set `DATA_DIR`, `OUTPUT_DIR`, `RUNTIME_CONFIG_PATH` under that mount.
3. Set `CLIENT_ID`, `CLIENT_SECRET`, and initial `SERIES_ID` / `TOTAL_DATA_TIME_START` in Render env (or PATCH config after deploy).
4. Optional **Cron** job: POST `daily` to your service URL (see `render.yaml`).

## Error handling

- Job failures: `GET /jobs/{id}` → `status: failed`, `error` message.
- Grain not allowed: analyze/signup jobs surface PRD grain errors in `error`.
- Missing timeline: set `timelineStart` / `timelineEnd` via PATCH before `analyze` or `pipeline`.

## Relationship to CLI

The CLI ([`cli.py`](../cli.py)) is unchanged. The API calls the same library functions and scripts; metric logic lives only in `retention_pipeline/`.

## Dashboard (React)

Product requirements for a Recharts-based frontend: **[Dashboard-PRD.md](./Dashboard-PRD.md)**.
