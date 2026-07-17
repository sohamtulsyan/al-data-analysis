**RUN**
PYTHONPATH=. python cli.py run --skip-fetch \
  --timeline-start 2026-05-15T00:00:00 \
  --timeline-end 2026-07-15T23:59:59 \
  --grain Weekly --timezone UTC

**WEEKLY REGISTRATIONS (users CSV)**
PYTHONPATH=. python scripts/plot_weekly_registrations.py
# or: PYTHONPATH=. python scripts/plot_weekly_registrations.py path/to/users.csv

**SIGNUP FRACTION (signups / weekly active users)**
PYTHONPATH=. python cli.py signup-fraction \
  --timeline-end 2026-07-15T23:59:59 \
  --users-csv users_2026_07_16_12_34_42.csv

**DATA FETCH BACKFILL**
PYTHONPATH=. python cli.py backfill


**NUU Retention Analysis**
PYTHONPATH=. python scripts/plot_nuu_retention.py \
  --grain Weekly \
  --window-start 2026-07-01 \
  --window-end 2026-07-17

**API SERVER (FastAPI)**
PYTHONPATH=. uvicorn retention_api.main:app --reload --port 8000
# OpenAPI: http://localhost:8000/docs
# Guide: docs/API.md
# Dashboard PRD (for React agent): docs/Dashboard-PRD.md