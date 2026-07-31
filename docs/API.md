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
| `JOB_MAX_WORKERS` | `2` | Background pipeline-run thread pool size |
| `USERS_CSV_PATH` | `users_*.csv` in project root | Default users export |
| `RETENTION_API_GENERATE_CHARTS` | `0` | Set `1` to generate matplotlib PNGs on the server (not needed for Recharts dashboard). **Keep `0` on Python 3.14** — matplotlib PNG rendering can hit a `RecursionError` in `Path.__deepcopy__`. |

Use **`runtime.txt`** (`python-3.11.9`) or Render `PYTHON_VERSION=3.11.9` instead of 3.14 for full PNG support.

## Configuration API

### `GET /api/v1/config`

Returns effective dashboard settings plus `dataDir`, `outputDir`, and `clientCredentialsConfigured`.

### `PATCH /api/v1/config`

Partial update. Body fields (all optional):

- **Fetch:** `apiBaseUrl`, `seriesId`, `timezone`, `totalDataTimeStart`, `puzzleId`, `limit`, `offset`, `rateLimitRps`, `maxWorkers`
- **Analysis timeline:** `timelineStart`, `timelineEnd`, `grain` (`Daily` | `Weekly` | `Monthly`) — used for all analysis jobs including NUU
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
    "strictHorizons": [1, 3, 7, 30],
    "windowHorizons": [3, 7, 30]
  }'
```

## Pipeline Runs (async)

The pipeline runs in the background. Poll until `status` is `succeeded` or `failed`.

### `POST /api/v1/jobs`

```json
{ "type": "pipeline", "params": { } }
```

The only supported job type is `pipeline`.

`pipeline` runs the full computation in one pass:

- optional historical fetch (`params.skipFetch: true` to skip it)
- core analysis (`analysis_results.json`)
- NUU retention
- OUU retention
- logged-in retention (`uid` prefix `imgl`)
- signup fraction
- NUU counts
- NUU signup fraction
- chart generation when `RETENTION_API_GENERATE_CHARTS=1`

`params` can override timeline, grain, timezone, or horizon fields for a single pipeline run.

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
| `GET /api/v1/results/ouu-retention` | `ouu_retention.json` |
| `GET /api/v1/results/logged-in-retention` | `logged_in_retention.json` (uids starting with `imgl`) |
| `GET /api/v1/results/nuu-signup-fraction` | `nuu_signup_fraction.json` (falls back to CSV) |
| `GET /api/v1/artifacts/charts` | List chart filenames |
| `GET /api/v1/artifacts/charts/{name}` | PNG or HTML file |

## Render deployment

1. Create a **Web Service** from this repo; use `render.yaml` or set:
   - **Build:** `pip install -r requirements.txt`
   - **Start:** `uvicorn retention_api.main:app --host 0.0.0.0 --port $PORT`
2. **Attach a persistent disk before using `/var/data` paths** (Dashboard → Disks → New Disk):
   - Mount path: `/var/data`
   - Size: ≥ 1 GB (Blueprint uses 10 GB)
   - Then set (or keep from `render.yaml`):
     - `DATA_DIR=/var/data/Daily Play Data`
     - `OUTPUT_DIR=/var/data/output`
     - `RUNTIME_CONFIG_PATH=/var/data/runtime_config.json`
   - If the disk is **not** mounted, `PATCH /api/v1/config` fails with `Permission denied: '/var/data'`. Either add the disk, or **delete** those three env vars so the API writes under the repo (ephemeral — wiped on every deploy).
3. Set `CLIENT_ID`, `CLIENT_SECRET` in Render env. Set Python to **3.11.9** (`runtime.txt` / `PYTHON_VERSION`).
4. Optional **Cron** job: POST `pipeline` to your service URL (see `render.yaml`), or keep using a separate fetch-only cron outside this API.

## Error handling

- Pipeline failures: `GET /jobs/{id}` → `status: failed`, `error` message.
- Grain not allowed: pipeline responses surface PRD grain errors in `error`.
- Missing timeline: set `timelineStart` / `timelineEnd` via PATCH before `pipeline`.

## Relationship to CLI

The CLI ([`cli.py`](../cli.py)) is unchanged. The API calls the same library functions and scripts; metric logic lives only in `retention_pipeline/`.

## Dashboard (React)

Product requirements for a Recharts-based frontend: **[Dashboard-PRD.md](./Dashboard-PRD.md)**.


https://al-retentino-analysis-dashboard.onrender.com