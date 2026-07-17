# Dashboard PRD — Retention Pipeline (React + Recharts)

**Audience:** Implementation agent (frontend).  
**Backend contract:** [API.md](./API.md) and OpenAPI at `{API_BASE}/docs`.  
**Goal:** A modular, production-quality dashboard that **replicates the meaning, layout, and chart semantics** of the existing Python visualizations in [`retention_pipeline/visualize.py`](../retention_pipeline/visualize.py), NUU scripts, and signup charts—using **JSON from the API** (not PNG fallbacks), rendered with **Recharts**.

---

## 1. Product summary

### 1.1 Problem

Operators need to configure the retention pipeline (series, timelines, grain, DN horizons), trigger long-running jobs, and **interpret metrics** the same way the matplotlib/Plotly exports already do—without opening static PNGs or HTML files.

### 1.2 Solution

A single-page application (SPA) with:

1. **Settings** — persist non-secret config via `PATCH /api/v1/config`.
2. **Runs** — start async jobs, poll status, surface errors.
3. **Overview** — one screen mirroring the Plotly `dashboard.html` grid (6 panels + header).
4. **Deep-dive routes** — full-width charts + metric definitions for pipeline retention, NUU, and signup analyses.
5. **Data health** — CSV coverage and summary stats from analysis payloads.

### 1.3 Out of scope (v1)

- Storing `CLIENT_ID` / `CLIENT_SECRET` in the browser (server env only; show `clientCredentialsConfigured`).
- Reimplementing metric math in the frontend (read-only visualization of API JSON).
- User authentication (private deployment; optional API base URL env only).

---

## 2. Technical requirements

| Area | Requirement |
|------|-------------|
| Framework | React 18+ (Vite recommended) |
| Charts | **Recharts** only for primary charts |
| HTTP | `fetch` or lightweight client; base URL from `VITE_API_BASE_URL` (default `http://localhost:8000`) |
| State | Modular: React Query (TanStack Query) recommended for config, results, jobs, polling |
| Styling | CSS modules or Tailwind; consistent with chart color tokens below |
| CORS | Backend must list dashboard origin in `CORS_ORIGINS` |

### 2.1 Repository layout (recommended)

```
dashboard/
  src/
    api/           # typed wrappers: config, jobs, results, data, artifacts
    hooks/         # useConfig, useJobPoll, useAnalysisResults, ...
    lib/           # parseRetentionPercent, bucketLabel, chartColors
    components/
      charts/      # one file per chart type (Recharts only)
      layout/      # AppShell, Nav, PageHeader, MetricExplainer
      jobs/        # JobLauncher, JobStatusBadge, JobHistoryList
      settings/    # ConfigForm sections
    pages/
      OverviewPage.tsx
      PipelineMetricsPage.tsx
      RetentionPage.tsx
      NuuPage.tsx
      SignupPage.tsx
      SettingsPage.tsx
      RunsPage.tsx
    types/         # mirrors API JSON shapes
  .env.example     # VITE_API_BASE_URL=
```

Each **page** composes **chart components** + **MetricExplainer** text; no monolithic 2000-line chart file.

---

## 3. API integration

### 3.1 Endpoints the dashboard must use

| Purpose | Method | Path |
|---------|--------|------|
| Load settings | GET | `/api/v1/config` |
| Save settings | PATCH | `/api/v1/config` |
| Data coverage | GET | `/api/v1/data/coverage` |
| Upload users CSV | POST | `/api/v1/data/users-csv` (multipart) |
| Start job | POST | `/api/v1/jobs` |
| Poll job | GET | `/api/v1/jobs/{id}` |
| Job history | GET | `/api/v1/jobs?limit=20` |
| Analysis JSON | GET | `/api/v1/results/analysis` |
| Signup JSON | GET | `/api/v1/results/signup-fraction` |
| NUU retention JSON | GET | `/api/v1/results/nuu-retention` |
| Health | GET | `/health` |

**Optional fallback:** `GET /api/v1/artifacts/charts/{name}` only for “Download PNG” actions—not for primary UI.

### 3.2 Job types to expose in UI

| UI label | `type` | Preconditions |
|----------|--------|----------------|
| Historical backfill | `backfill` | `clientCredentialsConfigured` |
| Daily fetch | `daily` | credentials |
| Analyze only | `analyze` | `timelineStart` / `timelineEnd` set |
| Full pipeline | `pipeline` | credentials unless `skipFetch: true` |
| NUU counts | `nuu_counts` | `windowStart` / `windowEnd` |
| NUU retention | `nuu_retention` | window + grain |
| Signup fraction (active users) | `signup_fraction` | users CSV path + timeline |
| Signup vs NUU | `nuu_signup_fraction` | run `nuu_counts` first |

**Polling:** After `POST /jobs`, poll `GET /jobs/{id}` every 1–2s until `succeeded` | `failed`. On success, invalidate result queries and navigate user to relevant page.

**Per-run overrides:** Job `params` may include any field also in config (`timelineStart`, `grain`, `strictHorizons`, etc.) without saving settings.

---

## 4. Shared data rules (must match Python)

Implement these once in `lib/` and reuse everywhere.

### 4.1 Bucket axis labels

Match [`_bucket_labels`](../retention_pipeline/visualize.py):

```ts
function bucketLabel(bucket: {
  bucket: string;
  partialBucket?: boolean;
  censorSide?: string | null;
}): string {
  if (bucket.partialBucket) {
    const side = bucket.censorSide ?? "partial";
    return `${bucket.bucket} (${side})`;
  }
  return bucket.bucket;
}
```

Use this string as the Recharts **X axis** category for all bucketed metrics.

### 4.2 Retention percent parsing

`retentionPercent` and `signupFractionPercent` may be a **number** or the string **`"N/A"`**.  
For line charts: **omit** points where value is `"N/A"` or non-numeric (same as Python filtering with `_num()`).  
Do not plot zero unless the API returned `0`.

### 4.3 Horizon keys

Retention buckets use `horizons: { D1: {...}, D3: {...}, ... }`.  
Sort series by numeric suffix: `D1`, `D3`, `D7`, `D30` (or whatever `strictHorizons` / `windowHorizons` produced).

### 4.4 Chart color tokens (match matplotlib defaults)

| Metric | Color | Hex |
|--------|--------|-----|
| User growth line | blue | `#1f4e79` |
| Engagement line | green | `#2e7d32` |
| Play state: loaded | grey | `#90a4ae` |
| Play state: solving | orange | `#fb8c00` |
| Play state: completed | green | `#43a047` |
| Signup / NUU signup fraction line | blue | `#1f4e79` |
| Retention multi-series | distinct hues | use Recharts default palette or assign per `Dn` consistently |

Retention: **multiple `Line` components** (one per horizon), `dot` + `type="monotone"`, legend showing `D1`, `D3`, etc.

Rolling vs consecutive on overview: Python uses **solid** vs **dotted** stroke for rolling; in Recharts use `strokeDasharray="5 5"` for rolling series only.

### 4.5 Page titles (global header)

When analysis results exist, show:

```text
Retention Pipeline — {grain} grain · {timelineStart} → {timelineEnd} ({timezone})
```

Same pattern for NUU:

```text
NUU Retention — {grain} grain · {windowStart} → {windowEnd} ({timezone})
```

Subtitles should include **distinct users in matrix**, **play row count**, and **timeline length days** from analysis root fields when present.

---

## 5. Metric definitions (in-UI copy)

Every chart section includes a collapsible **“What is this?”** block using the text below (can shorten for tooltips).

### 5.1 User growth

- **Chart title:** `User Growth — distinct active users per bucket`
- **Y-axis:** `Distinct users`
- **X-axis:** `Bucket`
- **Definition:** Count of distinct users with at least one qualifying play in the bucket (per PRD user growth).
- **JSON:** `GET /results/analysis` → `metrics.userGrowth.buckets[].userGrowth`

### 5.2 Engagement

- **Chart title:** `Engagement — median screenTimeSeconds (fully-contained plays)`
- **Y-axis:** `Median screen time (seconds)`
- **Definition:** Median `screenTimeSeconds` among fully-contained plays in the bucket.
- **JSON:** `metrics.engagement.buckets[].medianScreenTimeSeconds`

### 5.3 Pipeline retention (four families)

| Key | Chart title | Y-axis | Cohort definition (UI copy) |
|-----|-------------|--------|-----------------------------|
| `strictRetention` | Strict Retention (%) | Retention (%) | D0 = first active day **in bucket**; retained if active **exactly** on D0+N |
| `cumulativeRetention` | Cumulative Retention (%) | Retention (%) | Active on **any** day in D1…DN |
| `consecutiveRetention` | Consecutive Retention (%) | Retention (%) | Active on **every** day D1…DN |
| `rollingRetention` | Rolling Retention (%) | Retention (%) | Active on **any** day from DN through timeline end |

- **JSON path:** `metrics.{key}.buckets[].horizons.D{n}.retentionPercent`
- **Tooltip (optional):** show `retainedCohort`, `totalCohort`, `censored`, `totalActiveInBucket` from the same horizon object.

**Horizons:** Strict uses `strictHorizons` (default 1,3,7,30); others use `windowHorizons` (default 3,7,30).

### 5.4 Play state

- **Chart title:** `Play State — counts by outcome (updatedTimestamp attribution)`
- **Y-axis:** `Play count`
- **Chart type:** **Stacked bar** (`loadedCount`, `solvingCount`, `completedCount`)
- **JSON:** `metrics.playState.buckets[]`

### 5.5 Signup fraction (pipeline)

- **Chart title:** `Signup fraction — new accounts / active users (%) · {timelineStart} → {timelineEnd}`
- **Y-axis:** `Signup fraction (%)`
- **Definition:** Weekly (fixed grain in API): `signups / activeUsers × 100` from users CSV registrations vs activity matrix.
- **JSON:** `GET /results/signup-fraction` → `metric.buckets[].signupFractionPercent`
- Also show `totalSignups` and per-bucket `signups`, `activeUsers`.

### 5.6 NUU counts

Not in `/results/*` until you add an endpoint; v1 options:

1. **Preferred:** After `nuu_counts` job, parse artifact paths from job response and fetch CSV via a future API—or document that v1 reads weekly totals from a new `GET /api/v1/results/nuu-counts` if added.
2. **Interim:** Display job artifacts + link; chart from client-side CSV parse after job success if CSV is exposed.

**Agent note:** If no JSON endpoint exists, implement **NUU daily/weekly line chart** by extending backend with `GET /api/v1/results/nuu-counts` OR parse `nuu_weekly.csv` from a static file URL—**confirm with repo** before shipping. For this PRD, specify UI as:

- **Chart title:** `New Unique Users (NUU) — daily count in window`
- **Definition:** NUU on day D iff global first active day in matrix equals D; weekly/monthly = sum of daily NUU.

### 5.7 NUU retention

Same chart shapes as pipeline retention but:

- **Cohort:** New Unique Users (global first active day = D0).
- **Titles:** `NUU Strict Retention (%)`, `NUU Cumulative Retention (%)`, `NUU Consecutive Retention (%)`
- **JSON:** `GET /results/nuu-retention` → `metrics.strictRetention` | `cumulativeRetention` | `consecutiveRetention`
- Horizon objects use `totalNuuInBucket` instead of `totalActiveInBucket`.

### 5.8 Signup fraction vs NUU

- **Chart title:** `Signup fraction vs NUU — signups / new unique users (%) · {windowStart} → {windowEnd}`
- **Y-axis:** `Signup fraction (%)`
- **Definition:** Per ISO week: `signups(week) / nuu(week) × 100`
- **Data:** Currently CSV output (`nuu_signup_fraction.csv`) after job; v1 dashboard should table + line chart from parsed CSV post-job, or request a JSON results endpoint mirroring signup-fraction shape.

---

## 6. Pages and layout

### 6.1 Navigation

| Route | Page | Primary data source |
|-------|------|---------------------|
| `/` | Overview | `results/analysis` |
| `/pipeline` | Pipeline metrics | analysis (all non-retention + data quality) |
| `/retention` | Retention deep dive | analysis retention metrics |
| `/nuu` | NUU | nuu-retention + nuu counts job |
| `/signup` | Signup analyses | signup-fraction + nuu signup job |
| `/settings` | Settings | GET/PATCH config + users upload |
| `/runs` | Job history | `jobs?limit=20` + launch pad |

### 6.2 Overview page (Plotly parity)

**Layout:** CSS grid, 3 rows × 2 columns (responsive: stack on mobile).

| Cell | Recharts component | Source metric |
|------|-------------------|---------------|
| R1C1 | `LineChart` — user growth | `userGrowth` |
| R1C2 | `LineChart` — engagement | `engagement` |
| R2C1 | `LineChart` — multi-series strict retention | `strictRetention` |
| R2C2 | `LineChart` — multi-series cumulative | `cumulativeRetention` |
| R3C1 | `LineChart` — consecutive **and** rolling (same plot, dashed rolling) | both metrics |
| R3C2 | `BarChart` stacked — play state | `playState` |

**Empty state:** If `GET /results/analysis` → 404, show CTA: “Configure timeline in Settings and run **Analyze** or **Pipeline**.”

### 6.3 Settings page

Form sections:

1. **Fetch & API** — `apiBaseUrl`, `seriesId`, `timezone`, `totalDataTimeStart`, `puzzleId`, `limit`, `offset`, `rateLimitRps`, `maxWorkers`  
   - Banner if `!clientCredentialsConfigured`: “Backfill and daily fetch require server credentials (not configurable here).”

2. **Analysis timeline** — `timelineStart`, `timelineEnd`, `grain` (select: Daily | Weekly | Monthly)

3. **NUU window** — `windowStart`, `windowEnd` (date inputs → ISO date strings)

4. **Retention horizons** — `strictHorizons`, `windowHorizons` (tag input or comma-separated integers)

5. **Users data** — file upload → `POST /data/users-csv`; display `usersCsvPath`

Actions: **Save** (PATCH), **Reset form** from last GET.

### 6.4 Runs page

- Buttons for each job type (disabled when prerequisites missing).
- Checkbox: “Skip fetch” for pipeline.
- Table: last 20 jobs — id, type, status, created, error snippet.
- Click row → poll detail + artifact paths.

### 6.5 Data quality panel (Pipeline page)

Render `dataQuality` object from analysis as a definition list (counts + malformed file list). Matches JSON in `analysis_results.json`.

---

## 7. Recharts implementation patterns

### 7.1 Multi-series retention line chart

```tsx
// Pseudocode — implement in components/charts/RetentionLineChart.tsx
<LineChart data={rows} margin={{ top: 8, right: 24, left: 0, bottom: 64 }}>
  <CartesianGrid strokeDasharray="3 3" />
  <XAxis dataKey="label" angle={-45} textAnchor="end" interval={0} height={80} />
  <YAxis label={{ value: "Retention (%)", angle: -90, position: "insideLeft" }} domain={[0, "auto"]} />
  <Tooltip />
  <Legend />
  {horizons.map((h) => (
    <Line key={h} dataKey={h} type="monotone" dot />
  ))}
</LineChart>
```

Transform buckets → wide rows: `{ label: bucketLabel(b), D1: 7.39, D3: null, ... }`.

### 7.2 Stacked play state

Use `BarChart` + multiple `Bar stackId="a"` with colors from §4.4.

### 7.3 Sparse X-axis labels

When `labels.length > 24`, show every Nth tick (Python uses ~16 labels max)—mirror via `interval={Math.ceil(labels.length / 16)}` or custom tick formatter.

### 7.4 Accessibility

- Chart titles as `<h2>`; axes labeled.
- Tooltip readable on keyboard focus where Recharts allows.

---

## 8. TypeScript types (minimal)

Define interfaces aligned with API JSON (extend as needed):

```ts
interface AnalysisResults {
  timelineStart: string;
  timelineEnd: string;
  grain: string;
  timezone: string;
  timelineLengthDays: number;
  playRowCount: number;
  distinctUsersInMatrix: number;
  dataQuality: Record<string, unknown>;
  metrics: {
    userGrowth?: BucketMetric<number, "userGrowth">;
    engagement?: BucketMetric<number | null, "medianScreenTimeSeconds">;
    strictRetention?: RetentionMetric;
    cumulativeRetention?: RetentionMetric;
    consecutiveRetention?: RetentionMetric;
    rollingRetention?: RetentionMetric;
    playState?: BucketMetric<number, "loadedCount" | "solvingCount" | "completedCount">;
  };
}

interface RetentionHorizon {
  retentionPercent: number | "N/A";
  retainedCohort: number;
  totalCohort: number;
  censored: number;
  totalActiveInBucket?: number;
  totalNuuInBucket?: number;
}
```

---

## 9. Error and edge cases

| Scenario | UX |
|----------|-----|
| Job `failed` | Show `error` text; if JSON string from grain validation, pretty-print |
| Partial buckets | Label shows `(left)` / `(right)` / `(partial)` per API |
| All retention N/A | Line chart empty + note “Insufficient timeline for selected horizons” |
| CORS error | Inline help: set `CORS_ORIGINS` on API |
| API down | `/health` check on load; banner with retry |
| 404 on results | Empty state + link to Runs |

---

## 10. Acceptance criteria

1. **Overview** visually matches the six-panel Python dashboard (same titles, axes, series grouping).
2. All primary charts render from **`GET /results/analysis`** JSON without PNGs.
3. Settings **PATCH** persists and reloads via **GET**; secrets never appear in UI or network payloads.
4. User can run **pipeline** with `skipFetch: true`, poll to completion, and see updated charts.
5. Retention charts respect **N/A** and **partial bucket** labeling identically to Python.
6. NUU retention page renders three families from **`GET /results/nuu-retention`**.
7. Signup fraction page renders from **`GET /results/signup-fraction`**.
8. Codebase follows **modular** layout in §2.1 (pages + chart components + api layer).
9. **Recharts** is the only chart library for v1.

---

## 11. Suggested implementation order

1. API client + types + config hook  
2. Settings page + coverage widget  
3. Job launcher + polling hook  
4. Shared `lib/` transforms + `RetentionLineChart`, `PlayStateStackedBar`, `SimpleMetricLine`  
5. Overview page  
6. Retention + Pipeline + Signup + NUU pages  
7. Polish: empty states, export tooltip data, optional “Download PNG” via artifacts API  

---

## 12. References

- Backend API: [API.md](./API.md)
- Python chart reference: [`retention_pipeline/visualize.py`](../retention_pipeline/visualize.py)
- NUU retention charts: [`scripts/plot_nuu_retention.py`](../scripts/plot_nuu_retention.py)
- Signup charts: [`retention_pipeline/signup_analysis.py`](../retention_pipeline/signup_analysis.py), [`scripts/plot_nuu_signup_fraction.py`](../scripts/plot_nuu_signup_fraction.py)
- Example payloads: [`output/analysis_results.json`](../output/analysis_results.json), [`output/nuu_retention.json`](../output/nuu_retention.json), [`output/signup_fraction.json`](../output/signup_fraction.json)

---

## 13. Optional backend follow-ups (for full NUU/signup CSV parity)

If the implementing agent controls the API repo, add for cleaner dashboard data:

- `GET /api/v1/results/nuu-counts` — JSON `{ daily: [], weekly: [], monthly: [] }`
- `GET /api/v1/results/nuu-signup-fraction` — JSON with `buckets[]` like signup-fraction

Until then, dashboard may parse CSV paths from job `artifacts.paths` after successful jobs.
