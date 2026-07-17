# **Assumptions**

1. **Grid games only — `puzzleType` is ignored.** Every puzzle is assumed to be a grid game, so `puzzleType` is neither required nor persisted. This lets `isLoaded` be defined simply as `filledBoxes == 0` (see Variables — `isLoaded`).
   *Unresolved edge case:* two users can end in the identical recorded state `filledBoxes == 0`, `playState == inProgress` while meaning different things — (a) **entered then erased**: the user typed an answer (`filledBoxes > 0`) then cleared it back to `0`; (b) **never interacted**: the user opened the puzzle but never typed. The data cannot tell them apart, so both are treated as `loaded`.

2. **One series, one puzzle type per run.** The pipeline analyzes a single puzzle series at a time, assumed to contain a single homogeneous `puzzleType`. A series can in principle mix types, but since `puzzleType` is omitted (assumption 1), mixed-type series are out of scope.

3. **`getUserInfo` is omitted.**

4. **`score` is ignored.**

5. **Activity = any timestamped event on a day.** A user is active on a day if a `startTimestamp` or `updatedTimestamp` falls on it; a day with no timestamped event is not counted, even if the user was plausibly mid-play. *Example:* start Monday, return Tuesday without finishing, finish Wednesday → active on **Monday and Wednesday only**, not Tuesday. (Formal rule: Variables — `isActiveOnDay`.)

6. **All times are UTC.** A `TIMEZONE` field exists, but for the current scope it is always assumed to be UTC.

7. **Engagement time is attributed only to a fully-contained play.** A play's `screenTimeSeconds` counts toward a bucket only when **both** its start and its last update fall inside that bucket — same day (Daily), same calendar week (Weekly), or same calendar month (Monthly); a play that straddles a boundary is excluded at that grain. This is **intentionally stricter than activity (assumption 5)**: engagement measures time spent completing a play within a single bucket, whereas activity only requires *some* event on the day. The two definitions are deliberately different. (Formal rule: Engagement — Play Eligibility.)

---

# **API Fetch Library**

Reference API documentation:

* Auth (token generation): `https://amuselabs.github.io/api-doc/generate-api-token`  
* Bulk plays: `https://amuselabs.github.io/api-doc/get-bulk-plays-for-a-series-or-puzzle`

---

## **1\. Configuration Inputs**

All inputs below are supplied to the service through environment variables or a configuration store. None of them are hardcoded in source. Every value is treated as immutable for the duration of a single run unless explicitly stated otherwise.

| Name | Type | Format / Constraints | Description |
| ----- | ----- | ----- | ----- |
| `API_BASE_URL` | string (URL) | Fixed value `https://puzzleme.amuselabs.com/pmm/api/v2` | Base URL for the v2 API surface.  |
| `CLIENT_ID` | string | Non-empty | Client identifier used for authentication. Sourced from a secret. |
| `CLIENT_SECRET` | string | Non-empty | Client secret used for authentication. Sourced from a secret; never logged. |
| `TIMEZONE` | string | IANA/ISO 8601-compatible timezone identifier. Default: `UTC` | Timezone against which all date boundaries, day-grain loops, and the daily cron are computed. Every timestamp derived by this service is interpreted in `TIMEZONE` unless a field is explicitly defined as an API-returned value. |
| `SERIES_ID` | string | Non-empty | Series to pull plays for. Sent as the `series` query parameter. |
| `PUZZLE_ID` | string | Optional; non-empty when present | Specific puzzle identifier. When present, retrieval is scoped to a single puzzle; when absent, retrieval covers every puzzle in the series. Sent as the `puzzleId` query parameter. |
| `FROM` | datetime string | ISO 8601, interpreted in `TIMEZONE` | Inclusive start-of-day boundary for a single day's fetch (§3). |
| `TO` | datetime string | ISO 8601, interpreted in `TIMEZONE` | Inclusive end-of-day boundary. |
| `LIMIT` | integer | 1–1000; default and maximum `1000` (per API docs) | Maximum plays returned per page.  |
| `OFFSET` | integer | ≥ 0; starts at `0` | Number of plays to skip before returning results. Advanced during pagination per §3.3. |
| `TOTAL_DATA_TIME_START` | datetime string | ISO 8601, interpreted in `TIMEZONE` | The earliest date for which historical data must be backfilled. Defines the inclusive lower bound of the historical loop (§4). |
| `RATE_LIMIT_RPS` | integer | ≥ 1; default `20` | Global cap on outbound requests per second across the **entire** service (all threads combined), enforced by a single shared rate limiter (§6). |
| `MAX_WORKERS` | integer | ≥ 1; default `8` | Maximum number of concurrent day-fetch workers used by the historical backfill (§6). Set to `1` for fully single-threaded, deterministic operation. |

**Terminology.** A *play* is a single user's interaction record with a single puzzle. A *page* is one API response containing up to `LIMIT` plays. A *day-grain fetch* is the complete set of paginated calls required to retrieve all plays for one calendar day in `TIMEZONE`.

---

## **2\. Authentication (Token Acquisition)**

### **2.1 Token endpoint**

Authentication is performed by issuing a `POST` request to the {API\_BASE\_URL}/token endpoint.

**Request.** The body is `application/x-www-form-urlencoded` (not JSON) with fields:

| Field | Source | Notes |
| --- | --- | --- |
| `client_id` | `CLIENT_ID` | Required. |
| `client_secret` | `CLIENT_SECRET` | Required. |

Headers: `Content-Type: application/x-www-form-urlencoded`, `Accept: application/json`.

Example:

```
POST {API_BASE_URL}/token
Content-Type: application/x-www-form-urlencoded
Accept: application/json

client_id=<CLIENT_ID>&client_secret=<CLIENT_SECRET>
```

Sending the same fields as JSON (`Content-Type: application/json`) is rejected by the API with `errorCode` 400 / `errorMessage` "Invalid parameter."

### **2.2 Success response**

A successful token request returns HTTP `200` with the following body:

{  
  "status": 0,  
  "access\_token": "string",  
  "token\_type": "string",  
  "expires\_at\_seconds": 0  
}

* `status` — `0` indicates success (same convention as other PuzzleMe API responses).  
* `access_token` — the bearer token (JWT) presented on all subsequent API calls via the `Authorization: Bearer <access_token>` header.  
* `token_type` — the token scheme (expected `Bearer`).  
* `expires_at_seconds` — absolute Unix epoch seconds at which the token expires (not a relative TTL). Observed usable lifetime is ~**45 minutes**.

Treat the response as success only when HTTP status is `200`, body `status` is `0`, and `access_token` is a non-empty string. The token is held only in memory for the run — never written to disk, logged, or committed.

### **2.3 Error response and retry policy**

A failed token request returns an error payload of the form:

{  
  "status": 1,  
  "errorCode": 0,  
  "errorMessage": "string",  
  "errorDetails": {}  
}

* `status` — non-zero indicates failure.  
* `errorCode` — numeric error code (may also appear as the HTTP status when the body is sparse).  
* `errorMessage` — human-readable error description.  
* `errorDetails` — an object containing additional structured context (may be empty).

**Retry rule.** On a failed token request, retry the /token endpoint. Attempt the request **up to 3 times total**. If all 3 attempts fail, abort the run and surface an error whose message is descriptive and actionable (it must include the failing `errorCode` and `errorMessage` returned by the final attempt). 

---

## **3\. Single-Day Fetch (Step 2\)**

This procedure retrieves all plays for exactly one calendar day and writes them to a single CSV file. It is the unit of work invoked by both the historical loop (§4) and the daily cron (§5).

### **3.1 Request**

Issue a `GET` request to:

{API\_BASE\_URL}/analytics/plays

with the following query parameters:

| Parameter | Value | Notes |
| ----- | ----- | ----- |
| `series` | `SERIES_ID` | Always sent. |
| `puzzleId` | `PUZZLE_ID` | Sent only when `PUZZLE_ID` is configured. Required only when single-puzzle completion analysis for a specific day is desired; omitted otherwise to retrieve all puzzles in the series. |
| `from` | `FROM` | Inclusive start-of-day boundary for the target day, in ISO 8601, in `TIMEZONE`. |
| `to` | `FROM` \+ 23:59:59 | Inclusive end-of-day boundary, expressed as `FROM` plus 23 hours, 59 minutes, 59 seconds, in ISO 8601\. |
| `limit` | 1000 | API applies its default of `1000`. |
| `offset` | `OFFSET` | Starts at `0`; advanced per §3.3. |
| `getUserInfo` | `false` | Lead-generation user fields are not requested. |

The request carries the `Authorization: Bearer <access_token>` header from §2 and `Accept: application/json`.

### **3.2 Success response shape**

A successful call returns:

{  
  "status": 0,  
  "plays": \[  
    {  
      "userId": "string",  
      "puzzleId": "string",  
      "puzzleType": "crossword",  
      "startedAt": "ISO-8601 timestamp",  
      "updatedAt": "ISO-8601 timestamp",  
      "playProgress": {  
        "playState": "inProgress",  
        "totalBoxes": 0,  
        "filledBoxes": 0  
      },  
      "score": 0,  
      "screenTimeSeconds": 0  
    }  
  \],  
  "hasMore": false  
}

Field definitions:

* `status` — `0` indicates a successful request.  
* `plays` — array of play objects (may be empty).  
* `plays[].userId` — string identifier of the user associated with the play.  
* `plays[].puzzleId` — equals `PUZZLE_ID` when `PUZZLE_ID` is supplied; present for disambiguation when retrieving an entire series.  
* `plays[].puzzleType` — enum. Permitted values: `crossword`, `codeword`, `krisskross`, `sudoku`, `quiz`, `wordsearch`, `wordflower`, `jigsaw`.  
* `plays[].startedAt` — ISO 8601 timestamp of when the play began.  
* `plays[].updatedAt` — ISO 8601 timestamp of the play's last update.  
* `plays[].playProgress.playState` — enum. Permitted values: `inProgress`, `completed`.  
* `plays[].playProgress.totalBoxes` — integer; total number of boxes in the puzzle.  
* `plays[].playProgress.filledBoxes` — integer; number of boxes filled by the user.  
* `plays[].score` — integer.  
* `plays[].screenTimeSeconds` — integer; seconds of active screen time on the play.  
* `hasMore` — boolean; `true` indicates additional pages remain.

### **3.3 Pagination**

1. Call the endpoint with the current `offset` (initially `0`).  
2. Process and persist the returned plays (§3.4).  
3. If `hasMore` is `true`, advance `offset` and repeat from step 1\.  
4. Continue until a response returns `hasMore = false`.

**Offset increment.** Advance `offset` by the page size actually in effect. Because `limit` is omitted and the API default is `1000`, `offset` is advanced by `1000` per page. 

### **3.4 Expired-token handling during a fetch**

If any call to `/analytics/plays` returns HTTP `401` with `errorCode 98`, the token has expired. In that case:

1. Pause the fetch loop.  
2. Re-run §2 to acquire a fresh token.  
3. Resume the fetch from the exact `offset` at which the `401` occurred (the failed page is re-requested; no plays are skipped).

### **3.5 Persistence — CSV schema**

Each play object is written as one row of a UTF-8 encoded CSV. The column order and mapping are fixed and defined below. This schema is identical for every daily CSV produced by the service.

| Column | Source field | Type |
| ----- | ----- | ----- |
| `uid` | `plays[].userId` | string |
| `seriesId` | `SERIES_ID` (constant for the run) | string |
| `puzzleId` | `plays[].puzzleId` | string |
| `startTimestamp` | `plays[].startedAt` | ISO 8601 string |
| `updatedTimestamp` | `plays[].updatedAt` | ISO 8601 string |
| `playState` | `plays[].playProgress.playState` | enum string |
| `totalBoxes` | `plays[].playProgress.totalBoxes` | integer |
| `filledBoxes` | `plays[].playProgress.filledBoxes` | integer |
| `screenTimeSeconds` | `plays[].screenTimeSeconds` | integer |

Notes:

* The CSV includes a header row with the column names exactly as written above, in the order shown.  
* `score` and `puzzleType` are intentionally **not** persisted in this schema.
* **Atomic commit.** A day's CSV is first written to a temporary file (e.g. `DD-MM-YYYY.csv.partial`) and **atomically renamed** to its final `DD-MM-YYYY.csv` name only after that day's full paginated fetch has completed successfully. The final filename therefore exists **if and only if** that day is complete; a partial or interrupted fetch leaves at most a temporary file, never a valid final CSV. This atomicity is the foundation of the day-level resumability defined in §7.

---

## **4\. Historical Backfill Loop (Step 3a)**

Purpose: populate the dataset for every past day from the earliest required date up to (and including) yesterday.

Define:

* `startTime` \= `TOTAL_DATA_TIME_START` — the inclusive lower bound, at `00:00:00` (start of day) in `TIMEZONE`.  
* `endTime` \= **yesterday**, at `23:59:59` (end of day) in `TIMEZONE`.

Procedure:

1. **Enumerate required days.** Build the set of calendar days from `startTime` (inclusive) through `endTime` (inclusive), in `TIMEZONE`.
2. **Skip completed days (resume).** For each required day, if its completed CSV already exists in `Daily Play Data`, that day is done — skip it (§7). Only not-yet-completed days remain, so an interrupted run resumes at the first incomplete day instead of restarting the whole loop.
3. **Dispatch to workers.** Distribute the remaining days across a pool of up to `MAX_WORKERS` concurrent workers (§6). Each worker sets `FROM` to its day's `00:00:00` and executes the single-day fetch (§3) — which derives `to = from + 23:59:59` and handles pagination and token refresh — independently of the other workers. All workers share one global rate limiter (§6.1).
4. **Persist (atomic).** Each worker persists its day's plays as a **separate** CSV using the §3.5 schema, written to a temporary file and atomically renamed to its final name only on success (§3.5, §7).
5. **File naming.** Each CSV is named for the calendar day it represents, in the format `DD-MM-YYYY.csv` (zero-padded day and month, four-digit year). Example: `07-03-2026.csv` for 7 March 2026.
6. **Directory.** All daily CSVs are written to a directory named `Daily Play Data`.
7. When every required day has a completed CSV, halt the historical loop.

---

## **5\. Daily Scheduled Fetch (Step 3b)**

Let `D` represent the calendar day on which the historical backfill is run.

The historical backfill retrieves data from `TOTAL_DATA_TIME_START` through `D-1`, meaning the previous completed calendar day.

The first scheduled cron job runs at 00:00 on `D+1`. At this point, day `D` has been completed, so the job retrieves all play data for `D` using the following boundaries:

* `startTime` \= `D` at 00:00:00  
* `endTime` \= `D` at 23:59:59

The job executes the single-day fetch described in Section 3 and saves the resulting CSV in the `Daily Play Data` directory using the `DD-MM-YYYY.csv` naming format.

The cron job then runs every day at 00:00 in `TIMEZONE`, retrieving data for the calendar day that has just ended.

---

## **6\. Rate Limiting and Concurrency**

Outbound API traffic is bounded by a configurable global rate limit and, optionally, parallelized across multiple day-fetch workers. Both are governed so the service never exceeds the rate cap regardless of how many workers run.

### **6.1 Global rate limiter (token-bucket)**

* A single, process-wide, thread-safe **token-bucket** limiter governs **every** outbound HTTP request the service makes — both `/token` (§2) and `/analytics/plays` (§3) calls.
* The bucket holds at most `RATE_LIMIT_RPS` tokens (default `20`) and refills continuously at `RATE_LIMIT_RPS` tokens per second. Each request must acquire one token before it is sent; if the bucket is empty, the caller blocks until a token becomes available.
* Because the limiter is **shared** across all threads (one instance for the whole process), the **aggregate** request rate across all workers never exceeds `RATE_LIMIT_RPS`. The limit is global, not per-thread: with `RATE_LIMIT_RPS = 20`, four workers together still emit at most 20 requests per second, not 80.
* Lowering `RATE_LIMIT_RPS` throttles the whole service uniformly and requires no other change.

### **6.2 Multithreaded day workers**

* The historical backfill (§4) may fetch multiple days **in parallel** using a worker pool of up to `MAX_WORKERS` threads (default `8`; set `MAX_WORKERS = 1` for fully sequential, deterministic operation).
* **The unit of parallelism is one calendar day.** Each worker runs the complete single-day fetch (§3) for its assigned day and writes that day's own CSV. Because every day maps to a distinct output file (§3.5), workers never write to the same file and need no write coordination.
* The only state shared between workers is (a) the global rate limiter (§6.1), (b) the in-memory bearer token, and (c) the set of completed days (§7); all three are accessed in a thread-safe manner.
* **Ordering.** Under concurrency, days may complete in any order. This is acceptable because each day is self-contained (its CSV depends only on that day's data). If a strict chronological processing order is required, set `MAX_WORKERS = 1`.

### **6.3 Shared-token refresh under concurrency (single-flight)**

The bearer token (§2) is shared by all workers. When a worker receives `401 / errorCode 98` (§3.4), token refresh is performed under a **single-flight** guard: the first worker to observe expiry acquires a lock and refreshes the token exactly once; other workers that also hit `401` wait for that single refresh and then reuse the new token, rather than each triggering its own refresh. After refresh, every affected worker resumes its fetch from the exact `offset` at which its `401` occurred (§3.4), so no plays are skipped.

### **6.4 Transient server errors (429 / 5xx)**

If a request returns HTTP `429` (rate limited) or a `5xx` server error, the worker retries the **same** page after an exponential backoff (doubling delay, capped, with jitter), up to a small fixed number of attempts, before surfacing an error. Correctly honoring `RATE_LIMIT_RPS` should keep `429` responses rare; this backoff is a safety net, not the primary throttle.

---

## **7\. Statefulness and Resumability (Day-Level)**

The pipeline is **fully stateful and resumable at day granularity**: if a run is interrupted (crash, deploy, manual stop), the next run resumes from the first not-yet-completed day and never re-processes days that already finished. The entire loop is never restarted.

### **7.1 The filesystem is the checkpoint**

The set of completed day-CSVs already present in `Daily Play Data` **is** the pipeline's state — no separate database, manifest, or external checkpoint store is required. A day is **complete** if and only if its final `DD-MM-YYYY.csv` exists (per the atomic-commit rule in §3.5). This single invariant makes completion unambiguous and crash-safe.

### **7.2 Atomic commit (recap)**

Each day's plays are written to a temporary file (e.g. `DD-MM-YYYY.csv.partial`) and **atomically renamed** to `DD-MM-YYYY.csv` only after that day's full paginated fetch has succeeded (§3.5). Consequences:

* A final filename can never represent a half-written day; the rename is atomic, so readers see either the complete file or no file.
* An interruption mid-day leaves at most a `.partial` temp file, never a valid final CSV. Such temp files are ignored for completion purposes and are safely deleted before the day is retried.

### **7.3 Resume procedure on restart**

1. Enumerate the required days for the run (historical: `TOTAL_DATA_TIME_START … yesterday`; daily cron: the just-ended day).
2. Partition them into **completed** (final CSV exists) and **pending** (no final CSV).
3. Discard any stale `.partial` temp files belonging to pending days.
4. Fetch **only** the pending days (§4 dispatch, §6 concurrency), each starting from `offset = 0`.
5. The run is finished when every required day has a final CSV.

Because completed days are identified by the mere existence of their CSV, resuming after an interruption processes only the remainder — the loop is never restarted from the beginning.

### **7.4 In-day granularity (chosen scope)**

Resumption is at **day** granularity: a day interrupted mid-pagination has no committed CSV, so it is re-fetched from `offset = 0` on the next run. Within a single live run, §3.4 still resumes mid-day at the exact `offset` after a token refresh; §7 governs resumption **across** runs (process-level interruption). Sub-day (page-level) checkpointing across runs is intentionally out of scope for this design.

### **7.5 Idempotency**

Re-running the pipeline is safe and idempotent. Completed days are skipped; a re-fetched day is written to a fresh temp file and atomically replaces any prior state on rename, so re-runs never produce duplicate rows, partial merges, or double-counted plays. The daily cron (§5) obeys the same rule: if the target day's CSV already exists, the invocation is a no-op for that day.

---

# **Variables**

**Scope.** Single source of truth for every variable: **Input Variables** (caller-supplied), **Derived Variables** (computed from play data), and **Play-by-Play Variables** (the per-play CSV schema from the API Fetch Library).

**Conventions used throughout this section:**

* All timestamps are ISO 8601 strings.  
* The timezone against which all calendar-day boundaries are evaluated is `TIMEZONE` (defined in the API Fetch Library section; default `UTC`). Every phrase such as "on a given date," "that day," or "belongs to day D" is evaluated in `TIMEZONE`.  
* A **calendar day `D`** spans the **closed** interval `[D 00:00:00, D 23:59:59]`, inclusive on both ends, in `TIMEZONE`. (This matches the `from` / `to` day-boundary derivation used by the API Fetch Library.)  
* "UID" refers to the `uid` column of the Play-by-Play schema (i.e., the user identifier).  
* "A play" or "a play row" refers to one row of a daily CSV as defined in §3.

---

## **1\. Input Variables**

Input Variables are provided by the user or calling context and parameterize a computation run. They are not derived from play data.

| Name | Symbol | Type | Format / Constraints | Definition |
| ----- | ----- | ----- | ----- | ----- |
| Timeline Start | `timelineStart` | datetime string | ISO 8601, in `TIMEZONE` | Inclusive start of the analysis time window. |
| Timeline End | `timelineEnd` | datetime string | ISO 8601, in `TIMEZONE` | Inclusive end of the analysis time window. |
| Grain | `grain` | enum | One of: `Daily`, `Weekly`, `Monthly` | The bucketing granularity at which metrics are aggregated over the timeline. |

### **1.1 Timeline (`TL`)**

The **Timeline** is the user-specified analysis window, represented by the pair (`timelineStart`, `timelineEnd`).

* The window is **inclusive** on both ends: it covers every calendar day `D` such that `timelineStart ≤ D ≤ timelineEnd`, evaluated in `TIMEZONE`.  
* The window must span **at least one (1) day**. There is **no upper bound** on its length.  
* `timelineStart` and `timelineEnd` are stored separately (rather than as a single composite value) and both use ISO 8601 for consistency with the rest of the system.  
* Constraint: `timelineStart ≤ timelineEnd`. A window where `timelineStart` and `timelineEnd` fall on the same calendar day is valid and represents a one-day timeline.

### **1.2 Grain (`G`)**

The **Grain** is an enum controlling the size of the aggregation buckets into which the Timeline is divided:

* `Daily` — each bucket is one calendar day.  
* `Weekly` — each bucket is one week.  
* `Monthly` — each bucket is one calendar month.

Note: the exact week-boundary and partial-bucket rules (e.g., which weekday starts a week, and how buckets that are only partially covered by the Timeline are handled) are defined in the Aggregation section, not here. This section only enumerates the permitted values.

---

## **2\. Derived Variables**

Derived Variables are computed deterministically from Play-by-Play data (§3) and/or Input Variables (§1). This list is **expanding and non-exhaustive**; additional derived variables will be appended over time. Each entry specifies its inputs, its exact computation, and its type so the value is reproducible without further interpretation.

### **2.1 `serverTimeSeconds`**

* **Type:** integer (seconds).

* **Scope:** per play (one value per play row).

* **Inputs:** `startTimestamp` (`startedAt`), `updatedTimestamp` (`updatedAt`) from the play row.

**Definition:** the total elapsed wall-clock time between the start of the play and its last update, expressed in whole seconds:

 serverTimeSeconds \= floor( (updatedTimestamp − startTimestamp) in seconds )

*  Both operands are parsed as absolute instants (ISO 8601). The result is the non-negative difference in seconds. If `updatedTimestamp < startTimestamp` (which should not occur in valid data), the value is undefined and the row is treated as a data-quality anomaly rather than assigned a negative duration.

### **2.2 `isLoaded`**

* **Type:** boolean.

* **Scope:** per play (one value per play row).

* **Inputs:** `filledBoxes` from the play row.

**Definition:** indicates whether the user's play is in the "loaded" state — the puzzle was opened but no boxes were filled.

 isLoaded \= ( filledBoxes \== 0 )

*  `isLoaded` is `true` when `filledBoxes` equals exactly `0`, and `false` for any `filledBoxes ≥ 1`.

### **2.3 `isActiveOnDay`**

* **Type:** boolean.

* **Scope:** per (UID, calendar day `D`) pair.

* **Inputs:** the play rows for the given UID across day `D`'s CSV and all earlier days' CSVs, the calendar day `D`, and `TIMEZONE`.

* **Definition:** a UID is **active on day `D`** if and only if **at least one** of the following holds:

  * **Started on `D`.** There exists a row for that UID in **day `D`'s own CSV** whose `startTimestamp` falls within `[D 00:00:00, D 23:59:59]` in `TIMEZONE`.  
  * **Updated on `D` from an earlier start (foresight).** There exists a row for that UID in **some earlier day's CSV** (a day `x` with `x < D`) whose `updatedTimestamp` falls within `[D 00:00:00, D 23:59:59]` in `TIMEZONE`.

Formally, for a fixed UID and day `D`, with `D_start = D 00:00:00` and `D_end = D 23:59:59` in `TIMEZONE`:

 isActiveOnDay(UID, D) \=  
    TRUE  if  ∃ a row r with r.uid \== UID in day D's CSV  
                such that  D\_start ≤ r.startTimestamp ≤ D\_end  
          OR  
              ∃ a row r with r.uid \== UID in the CSV of some day x \< D  
                such that  D\_start ≤ r.updatedTimestamp ≤ D\_end  
    FALSE otherwise

* **Rationale for the asymmetry.** Daily CSVs are bucketed by `startTimestamp` (the API Fetch Library filters on `startedAt`), so a play always resides in the CSV of the day it *started*. Condition 1 therefore captures every play that started on `D` directly from `D`'s own file. Condition 2 provides *foresight*: a play that started on an earlier day `x` but was last updated on `D` records that later activity on `D`. Because a play can only be updated at or after it started, a play's `updatedTimestamp` for day `D` can only ever appear in the CSV of a day `x ≤ D` — never a future file. Consequently, computing activity for `D` requires scanning `D`'s CSV plus earlier CSVs, and **never** any file dated after `D`.

   Clarifications that make this definition exhaustive:

  * **Boundary handling:** both interval ends are inclusive. A timestamp of exactly `D 00:00:00` or exactly `D 23:59:59` counts as belonging to `D`.  
  * **Same-day updates are covered by Condition 1's row:** a play that both started and was updated on `D` lives in `D`'s CSV and is caught by Condition 1; it does not additionally require Condition 2\.  
  * **Existence, not count:** a single qualifying row (under either condition) makes the UID active on `D`. Multiple qualifying rows do not change the boolean result.  
  * **In-between days are not counted.** A play that started on day `x` and was next updated on a later day `z` is counted as active on `x` and on `z`, but **not** on the intervening days `x < D < z`, because no timestamp for that play falls within those days. Counting intervening days would require assuming continuous activity that the data does not evidence; this definition deliberately does not make that assumption.

* **Design note — Foresight (forward-pass) population, not retrospection.** The intended implementation does **not**, for each day `D`, look backward over all earlier CSVs (retrospection). Instead it makes a **single forward pass** over the daily CSVs in chronological order, and marks activity *ahead* of the current file (foresight): while reading a row in day `x`'s CSV, the implementation marks the UID active on `x` via the row's `startTimestamp`, and if the same row's `updatedTimestamp` falls on a later day `z > x`, it **immediately marks the UID active on day `z`** as well. Because every row is read exactly once and each `updatedTimestamp` can only point to a day at or after its own start day, the entire activity signal is produced in one pass with no repeated backward scans. This is functionally equivalent to the retrospective definition above but is the compute-efficient realization of it, and it is the prescribed approach.

* **Output target — Activity Matrix.** The per-(UID, day) activity signal produced by this variable is written into the **Activity Matrix** (defined in the next section): a structure whose cell `(UID, D)` records whether that UID was active on day `D`. The foresight pass populates this matrix by writing into cell `(UID, z)` at the moment a qualifying `updatedTimestamp` for future day `z` is encountered, rather than computing each cell on demand. The matrix's exact shape, defaults (unmarked cells \= inactive), and semantics are specified in the next section; this variable defines the rule by which its cells are set to active.

### **2.4 `dailyActiveUsers`**

* **Type:** integer (count).

* **Scope:** per calendar day `D`.

* **Inputs:** `isActiveOnDay(UID, D)` for every UID present in the data, for the given day `D`.

**Definition:** the **distinct (deduplicated) count of UIDs** for which `isActiveOnDay(UID, D) == TRUE` on day `D`.

 dailyActiveUsers(D) \= COUNT( DISTINCT UID  where  isActiveOnDay(UID, D) \== TRUE )

*  Deduplication is mandatory and explicit: each UID contributes **at most one** to the count for a given day, regardless of how many qualifying play rows it has on that day. A UID that appears in multiple rows on `D` is counted exactly once.

---

## **3\. Play-by-Play Variables (CSV Schema)**

The Play-by-Play Variables are the columns of the per-day CSV produced by the API Fetch Library. Each row represents one play. The schema — columns, order, and types — is defined **canonically in the API Fetch Library, §3.5 (Persistence — CSV schema)** and is not restated here, to prevent divergence. Downstream Derived Variables (§2) reference these columns by name: `uid`, `seriesId`, `puzzleId`, `startTimestamp`, `updatedTimestamp`, `playState`, `totalBoxes`, `filledBoxes`, `screenTimeSeconds`.

Notes:

* Every CSV is UTF-8 encoded and includes a header row with the column names exactly as written in §3.5, in the order shown.
* This schema is authoritative for downstream variable definitions: any change to it must be reflected in every dependent Derived Variable definition.

# **Cleaning and Intermediate Steps**

**Scope.** Data-cleaning rules applied to raw Play-by-Play CSVs before any metric — parsing/formatting, deduplication, null handling, right-censoring (`N/A` for out-of-window data) — and construction of the transient **Activity Matrix**.

**Dependencies:** consumes the Play-by-Play CSV schema and the `isActiveOnDay` rule defined in the Variables section; consumes `timelineStart`, `timelineEnd`, `grain`, and `TIMEZONE` as inputs.

---

## **1\. Cleaning Steps**

Cleaning is applied to the union of daily CSVs relevant to a computation before derived variables and metrics are evaluated. Each rule below is deterministic and order-defined (see §1.6 for the canonical order).

### **1.1 CSV Parsing and Formatting**

* **Encoding.** Every daily CSV is read as UTF-8. A byte-order mark (BOM), if present, is stripped before parsing.  
* **Header.** The first row is the header and must match the Play-by-Play schema column names exactly, in the defined order (`uid`, `seriesId`, `puzzleId`, `startTimestamp`, `updatedTimestamp`, `playState`, `totalBoxes`, `filledBoxes`, `screenTimeSeconds`). A file whose header does not match is rejected as malformed and reported; it is not silently reordered.  
* **Delimiter and quoting.** Standard RFC 4180 CSV: comma-delimited, fields containing commas/quotes/newlines are double-quoted, embedded double-quotes are escaped by doubling. Parsing must honor quoted fields rather than naive comma-splitting.  
* **Type coercion.** After parsing, each column is coerced to its schema type: `totalBoxes`, `filledBoxes`, `screenTimeSeconds` → integer; `startTimestamp`, `updatedTimestamp` → ISO 8601 instant; `playState` → enum (`inProgress` | `completed`); the rest → string. A value that cannot be coerced to its declared type is treated as null for that cell and handled per §1.3.  
* **Whitespace.** Leading/trailing whitespace on string and timestamp fields is trimmed before coercion.  
* **Timestamp normalization.** All timestamps are parsed as absolute instants and evaluated in `TIMEZONE` for all day-boundary comparisons, consistent with the Variables section.

### **1.2 Deduplication (metric-scoped)**

Deduplication is **not** applied globally to the raw rows; it is applied **per metric, according to what that metric counts**. This preserves multiple plays per user where a metric legitimately needs them, while preventing double-counting where a metric is user-scoped.

* **User-scoped metrics (deduplicate by UID).** Any metric that counts *distinct users* (for example, `dailyActiveUsers`) must count each `uid` **at most once** within the relevant bucket, regardless of how many play rows that `uid` contributes. Deduplication key: `uid` within the bucket (e.g., within a day for daily active users).  
* **Play-scoped metrics (no UID deduplication).** Any metric that counts or aggregates *plays* (for example, total plays, completion counts per play, screen-time distributions) retains every qualifying row and does not deduplicate by `uid`.  
* **Exact duplicate rows.** If two rows are byte-for-byte identical across all schema columns, they are treated as a single row (true duplicates are collapsed) before any metric runs. This is distinct from the legitimate case of one `uid` having multiple *different* plays.

Each metric's definition must state explicitly whether it is user-scoped or play-scoped so the correct deduplication rule is unambiguous.

### **1.3 Null Handling**

A cell is **null** if it is empty, whitespace-only, or failed type coercion (§1.1).

* **Key fields — `uid`, `startTimestamp`.** A row with a null `uid` or a null `startTimestamp` cannot be attributed to a user or a day and is **excluded** from all computation, and the exclusion is counted and reported as a data-quality metric (it is not silently dropped).  
* **`updatedTimestamp` null.** If `updatedTimestamp` is null, it is treated as equal to `startTimestamp` for that row (the play has no recorded update beyond its start). Derived values that depend on it (e.g., `serverTimeSeconds`) therefore evaluate to `0` rather than null.  
* **Numeric fields — `totalBoxes`, `filledBoxes`, `screenTimeSeconds`.** A null numeric field is **not** coerced to `0` (which would silently distort sums and rates). It is retained as null; any metric that consumes it must either exclude that row from that specific metric or handle the null explicitly per its own definition. `isLoaded` (which tests `filledBoxes == 0`) is **undefined** when `filledBoxes` is null and the row is excluded from `isLoaded`\-dependent metrics.  
* **`playState` null.** A row with a null/unrecognized `playState` is excluded from completion/state-dependent metrics but may still participate in user-activity metrics if its `uid` and timestamps are valid.  
* **Reporting.** Every category of null-driven exclusion is tallied and surfaced (counts per field) so data quality is observable rather than hidden.

### **1.4 Right-Censoring (Out-of-Window Data → smart `N/A`)**

Some metrics require observation of data *outside* the requested Timeline to be computed correctly (for example, a metric measuring whether a user who was active near `timelineEnd` returns within N days would need data from after `timelineEnd`; a metric anchored to activity before `timelineStart` would need data from before it). When the data needed to compute a metric for a given bucket lies outside the available/requested window, that metric value is **right-censored** and returned as **`N/A`**, not as `0` and not as a partial (misleading) number.

Rules:

* **Definition of censoring.** A metric value for a bucket is censored if its correct computation depends on plays whose timestamps fall outside `[timelineStart, timelineEnd]` (on either side) and that data is therefore unavailable for this run.  
* **Right-censoring (near `timelineEnd`).** For any metric with a forward-looking horizon of `h` days (e.g., "returned within `h` days"), buckets whose evaluation window extends past `timelineEnd` are returned as `N/A`. Concretely, a bucket dated later than `timelineEnd − h` cannot be fully observed and is `N/A`.  
* **Left-censoring (near `timelineStart`).** Symmetrically, metrics requiring a backward-looking lookback of `b` days are `N/A` for buckets earlier than `timelineStart + b`, because the required prior data is not in-window.  
* **`N/A` is distinct from `0`.** `N/A` means "not determinable from available data," whereas `0` means "determinable and equal to zero." These must never be conflated in storage, computation, or display. Aggregations (averages, totals) must exclude `N/A` cells from both numerator and denominator rather than treating them as zero.  
* **Smart application.** Censoring is applied **only** to the specific metric-and-bucket combinations that actually require out-of-window data. Metrics that are fully computable within the window for a given bucket are returned normally; the same bucket may have a real value for one metric and `N/A` for another.

The per-metric horizon (`h`) and lookback (`b`) values are declared in each metric's own definition. This section defines the mechanism; the metric definitions supply the parameters.

### **1.5 Bucketing Prerequisite**

Before metrics run, in-window days are grouped into buckets per `grain` (`Daily` / `Weekly` / `Monthly`). The exact week-start convention and partial-bucket treatment are defined in the Aggregation section; this section only requires that cleaning is complete before bucketing and metric evaluation.

### **1.6 Canonical Order of Operations**

To guarantee reproducibility, cleaning is applied in this fixed order:

1. Parse and format each CSV (§1.1).  
2. Collapse byte-identical duplicate rows (§1.2).  
3. Apply null handling and record exclusions (§1.3).  
4. Build the transient Activity Matrix (§2).  
5. Apply metric-scoped deduplication and right-censoring at metric evaluation time (§1.2, §1.4).

---

## **2\. Transient Activity Matrix**

The **Activity Matrix** is a transient (in-memory only) intermediate structure that materializes the `isActiveOnDay` signal in tabular form so downstream metrics can read activity in O(1) per (user, day) instead of recomputing it. It is **not** persisted and **not** downloadable; it exists only for the duration of a computation run.

### **2.1 Shape and Semantics**

* **Rows:** one per distinct `uid` observed in the relevant data.  
* **Columns:** one per calendar date `D` in the range required by the computation (see §2.4 for range), in `TIMEZONE`.  
* **Cell `(UID, D)`:** the activity flag for that user on that day.  
  * Value `1` (or boolean `true`) ⇒ `isActiveOnDay(UID, D) == TRUE`.  
  * Value `0` (or boolean `false`, the **default** for any cell never set) ⇒ not active on `D`.  
* **Default.** Every cell is initialized to `0`/`false`. A cell becomes `1`/`true` only when the foresight pass (Variables §2.3, Foresight design note\) marks it.

### **2.2 Population (Foresight Forward-Pass)**

The matrix is populated in a **single chronological forward pass** over the daily CSVs, exactly as prescribed by the `isActiveOnDay` foresight rule:

1. Initialize all cells to `0`/`false`.  
2. Iterate CSVs in ascending date order. For each row (with valid `uid`, per §1.3):  
   * Mark cell `(uid, startDay)` \= `1`, where `startDay` is the `TIMEZONE` calendar day of `startTimestamp`.  
   * Compute `updateDay` \= the `TIMEZONE` calendar day of `updatedTimestamp`. If `updateDay > startDay`, mark cell `(uid, updateDay)` \= `1` (this is the foresight write into a later column).  
3. Because each row is read once and `updateDay ≥ startDay` always holds, no backward re-scan is required and no future file is ever read to resolve an earlier day.

Marking is idempotent: setting an already-`1` cell to `1` is a no-op, so multiple qualifying rows for the same (UID, day) do not corrupt the flag.

### **2.3 Deriving `dailyActiveUsers` from the Matrix**

For any in-window day `D`, `dailyActiveUsers(D)` is the number of rows whose cell in column `D` equals `1` — i.e., the column-sum of column `D`. Because each UID is a single row and each cell is 0/1, the column-sum is inherently deduplicated (no UID can contribute more than 1), satisfying the user-scoped deduplication requirement of §1.2 by construction.

### **2.4 Column Range**

The matrix must span every day that any in-window metric needs to read, which is at least `[timelineStart_day, timelineEnd_day]` inclusive. Where a metric's lookback/horizon (or a foresight update landing just past `timelineEnd`) requires reading activity slightly outside the timeline, the affected out-of-window columns are represented as needed but the corresponding metric values remain governed by the right-censoring rules of §1.4. Columns that fall outside available data are treated as all-zero/censored per §1.4, never fabricated.

# **User Growth**

**Scope.** The **User Growth** metric: the distinct count of users (`uid`) per bucket at a user-specified grain over a timeline — the counting rule (with mandatory deduplication), grain gating by timeline length, calendar bucketing, and disclosure of partially-covered edge buckets.

**Dependencies:** consumes the cleaned Play-by-Play data and the transient **Activity Matrix** produced in the Cleaning section; consumes `timelineStart`, `timelineEnd`, `grain`, and `TIMEZONE`. All activity determinations in this section are read **directly from the Activity Matrix** (counting its rows and columns), not recomputed from raw plays.

---

## **1\. Inputs**

| Name | Symbol | Type | Definition |
| ----- | ----- | ----- | ----- |
| Timeline Start | `timelineStart` | datetime string (ISO 8601, `TIMEZONE`) | Inclusive start of the observation window. Defined in the Variables section. |
| Timeline End | `timelineEnd` | datetime string (ISO 8601, `TIMEZONE`) | Inclusive end of the observation window. Defined in the Variables section. |
| Grain | `grain` | enum (`Daily` | `Weekly` | `Monthly`) | The bucketing granularity at which the distinct-user count is reported. |

### **1.1 Definition of "Timeline" (terminology)**

Throughout this section and those that follow, **"timeline"** refers to the **number of days between `timelineStart` and `timelineEnd`** (the length of the user-set observation window). This is **not** the same as the total data time frame (the full span of data the system has ingested, governed by `TOTAL_DATA_TIME_START` in the API Fetch Library section). The timeline is a user-chosen window they wish to observe, and its representation via `timelineStart` / `timelineEnd` was defined in a previous section.

For the constraint rules below, define:

* timelineLengthDays \= (number of calendar days in the inclusive range \[timelineStart\_day, timelineEnd\_day\], in TIMEZONE)

A single-day timeline (`timelineStart` and `timelineEnd` on the same calendar day) has `timelineLengthDays = 1`.

---

## **2\. Objective**

Track user growth as the **distinct-`uid` count** per bucket at the given grain over the timeline; its trend across buckets expresses growth.

---

## **3\. Core Metric and Deduplication**

The Activity Matrix is the single source of truth for activity. Its rows are distinct `uid`s, its columns are calendar dates in `TIMEZONE`, and cell `(UID, D)` is `1` when the user was active on day `D` and `0` otherwise. Every value below is obtained by counting rows and columns of this matrix; no activity is recomputed from raw plays here.

### **3.1 Metric definition**

For a given bucket `B` (a day, a calendar week, or a calendar month, per grain), the User Growth value is the number of distinct users active on **any** day in `B`, read from the matrix:

* userGrowth(B) \= COUNT( matrix rows (uids) that have at least one cell \== 1 among the columns whose dates belong to bucket B )  
* For a **Daily** bucket (a single day `D`), this reduces to the **column sum** of column `D`: the number of rows with a `1` in column `D`.  
* For a **Weekly** or **Monthly** bucket, it is the count of rows that have a `1` in **at least one** of the columns belonging to that bucket (a row-wise OR operation across the bucket's columns, then a count of `true` rows).

### **3.2 Deduplication is mandatory (satisfied by matrix structure)**

This is a **user-scoped** metric. Each `uid` must be counted **at most once per bucket**, regardless of how many plays or active days it has inside that bucket. The matrix satisfies this by construction: each `uid` is exactly one row, and a row contributes at most `1` to a bucket no matter how many of the bucket's columns it has set. Counting distinct rows (not summing cells) is therefore inherently deduplicated. Deduplication is not optional; summing cells instead of counting rows would double-count multi-day users and turn the value into a play/active-day count rather than a user count.

### **3.3 Weekly / Monthly are pooled-then-deduped, never summed**

For `Weekly` and `Monthly` grains, the bucket value is **not** the sum of the daily distinct counts (i.e., not the sum of the column sums) within that period. Summing daily counts multi-counts any user active on more than one day. Instead, the value is a **row-wise OR across the bucket's columns, then a count of rows that are `true`**:

For a weekly or monthly bucket B, over the matrix:

1. Take the set of columns whose dates fall within B.  
2. A uid (row) qualifies if it has a 1 in ANY of those columns (logical OR across the row's cells in B).  
3. userGrowth(B) \= the number of qualifying rows.

This pools every user active on any day in `B` and counts each such user once.

---

## **4\. Constraints and Edge Cases**

### **4.1 Timeline length lower bound**

The timeline must satisfy `timelineLengthDays ≥ 1`. This is a property of the timeline variable itself (established in a previous section), so a zero- or negative-length timeline cannot occur as valid input. Because at least one day is always present, the **Daily grain is always permitted**.

### **4.2 Grain permission matrix (gated by timeline length)**

Which grains are allowed depends on `timelineLengthDays`:

| `timelineLengthDays` | Daily | Weekly | Monthly |
| ----- | ----- | ----- | ----- |
| `1` to `6` (≥ 1 and \< 7\) | Allowed | **Not allowed** | **Not allowed** |
| `7` to `29` (≥ 7 and \< 30\) | Allowed | Allowed | **Not allowed** |
| `≥ 30` | Allowed | Allowed | Allowed |

Rules stated explicitly:

1. **`timelineLengthDays ≥ 1`:** Daily is always allowed.  
2. **`1 ≤ timelineLengthDays < 7`:** only Daily is allowed. If `Weekly` or `Monthly` is passed, the pipeline returns an error (no data is computed) — see §4.3.  
3. **`7 ≤ timelineLengthDays < 30`:** Daily and Weekly are allowed. If `Monthly` is passed, the pipeline returns an error — see §4.3.  
4. **`timelineLengthDays ≥ 30`:** Daily, Weekly, and Monthly are all allowed and aggregated per the bucketing rules in §5.

### **4.3 Error handling for disallowed grain**

When a `grain` is passed that is not permitted for the current `timelineLengthDays`, the pipeline performs **no computation** and returns a structured error. The error must state the requested grain, the timeline length, and the reason the grain is unavailable.

Recommended error response shape:

* {  
*   "status": "error",  
*   "errorType": "GRAIN\_NOT\_ALLOWED\_FOR\_TIMELINE",  
*   "message": "Grain 'Monthly' requires a timeline of at least 30 days, but the requested timeline is 12 days. Allowed grains for this timeline: Daily, Weekly.",  
*   "requestedGrain": "Monthly",  
*   "timelineLengthDays": 12,  
*   "allowedGrains": \["Daily", "Weekly"\]  
* }

The `message` is human-readable and descriptive; the machine-readable fields (`errorType`, `requestedGrain`, `timelineLengthDays`, `allowedGrains`) allow programmatic handling. No partial or best-effort result is returned in the error case.

---

## **5\. Bucketing Rules (Calendar-based)**

### **5.1 Daily**

Each bucket is one calendar day in `TIMEZONE`, spanning `[D 00:00:00, D 23:59:59]`, consistent with the day definition used throughout the document. Its value is the column sum of that day's matrix column.

### **5.2 Weekly — calendar weeks, Monday-start**

Weekly grain uses **calendar weeks, not rolling 7-day windows**. Weeks begin on **Monday** and end on **Sunday** (ISO-8601 week convention).

* For any given day, convert it to its ISO timestamp and determine the ISO calendar week (Monday–Sunday) that contains it.  
* A weekly bucket pools all days that belong to the same ISO calendar week and fall within the timeline.  
* The metric for the week is the number of matrix rows with a `1` in at least one of that week's columns (pooled-then-deduped distinct `uid` count, §3.3).

### **5.3 Monthly — calendar months**

Monthly grain uses **calendar months**, not rolling 30-day windows.

* For any given day, determine the calendar month (year \+ month, in `TIMEZONE`) that contains it.  
* A monthly bucket pools all days belonging to the same calendar month that fall within the timeline.  
* The metric for the month is the number of matrix rows with a `1` in at least one of that month's columns (pooled-then-deduped distinct `uid` count, §3.3).  
  ---

## **6\. Edge Censoring (Partially-Covered First/Last Buckets)**

Because weekly/monthly buckets are calendar-aligned while the timeline is an arbitrary user-chosen range, the **first and last** buckets may be only partially covered. Such a bucket is **left-censored** (first) or **right-censored** (last) and must be flagged with its side and covered sub-range. Example: a timeline starting on a Wednesday has a first ISO week covering only Wed–Sun (left-censored); ending on a Tuesday has a last week covering only Mon–Tue (right-censored); the same applies to partial calendar months. A censored bucket still reports its distinct-user count over the days actually present, but the flag marks the value as **not comparable** to fully-covered buckets — consumers must not read the lower count as a real decline. This is coverage disclosure, not suppression.

# **Engagement by Timeline**

**Scope.** The **Engagement** metric: the median `screenTimeSeconds` of qualifying plays per bucket at a user-specified grain. **Play-scoped** (summarizes plays, not users), so it does **not** use the Activity Matrix.

**Dependencies:** consumes the cleaned Play-by-Play daily CSVs (the `screenTimeSeconds`, `startTimestamp`, and `updatedTimestamp` columns) and `timelineStart`, `timelineEnd`, `grain`, `TIMEZONE`. It does not depend on the Activity Matrix or on `isActiveOnDay`.

---

## **1\. Inputs**

| Name | Symbol | Type | Definition |
| ----- | ----- | ----- | ----- |
| Timeline Start | `timelineStart` | datetime string (ISO 8601, `TIMEZONE`) | Inclusive start of the observation window. |
| Timeline End | `timelineEnd` | datetime string (ISO 8601, `TIMEZONE`) | Inclusive end of the observation window. |
| Grain | `grain` | enum (`Daily` | `Weekly` | `Monthly`) | Bucketing granularity at which the median is reported. |

`timelineLengthDays` is defined as in the User Growth section (inclusive day count of `[timelineStart, timelineEnd]` in `TIMEZONE`).

---

## **2\. Objective**

To track **engagement time**,i.e, how long users spend across all puzzles in a series within a given timeframe. The metric is the **median screen time** of qualifying plays in each bucket. The median (not the mean) is used because screen-time distributions are typically right-skewed (a few very long sessions), and the median is robust to those outliers.

---

## **3\. Metric Field**

The metric is computed from the **`screenTimeSeconds`** column of the Play-by-Play schema (integer seconds of active screen time on a play). This is the authoritative field; any earlier informal reference to "time taken" or "screenTimeTaken" refers to this same `screenTimeSeconds` column.

---

## **4\. Play Eligibility (both timestamps must fall inside the bucket)**

This is the critical rule of this section and applies at **every** grain.

A play is **counted toward a bucket `B`** if and only if **both** its `startTimestamp` **and** its `updatedTimestamp` fall within `B`'s date range (inclusive of `B`'s boundaries), in `TIMEZONE`:

1) playQualifiesForBucket(play, B) \= ( B\_start ≤ play.startTimestamp   ≤ B\_end ) AND  
   ( B\_start ≤ play.updatedTimestamp ≤ B\_end )  
   

where `B_start` is `00:00:00` of the first day of bucket `B` and `B_end` is `23:59:59` of the last day of bucket `B`, both in `TIMEZONE`.

If either timestamp falls outside `B`, the play is **ignored** for bucket `B`. This "fully contained" rule is assumption 7 in the Assumptions section, which records its rationale and its intentional contrast with the activity definition.

### **4.1 Consequence: grain-dependent eligibility**

Because the eligibility test is evaluated against the bucket being computed, **the same play can qualify at a coarser grain and be dropped at a finer one.** For example, a play that starts on Monday and is last updated on Wednesday of the same calendar week:

* is **excluded** from all three daily buckets (Mon, Tue, Wed), because its start and update are not within a single day;  
* is **included** in that week's weekly bucket, because both timestamps lie within the one calendar week;  
* is **included** in that month's monthly bucket, provided the week does not cross a month boundary.

Eligibility is therefore not a fixed property of a play; it is recomputed per bucket at the grain being reported.

---

## **5\. Grain Validation**

Grain permission is gated by `timelineLengthDays` using the **same rules as the User Growth section** (§4.2 permission matrix). If a disallowed grain is passed, the pipeline performs **no computation** and returns the same structured error defined there (`errorType: "GRAIN_NOT_ALLOWED_FOR_TIMELINE"`, with `requestedGrain`, `timelineLengthDays`, and `allowedGrains`).

---

## **6\. Computation by Grain**

The bucketing (calendar days; ISO Monday-start calendar weeks; calendar months) is identical to the User Growth section. The value in each bucket is a **median**, computed as follows.

### **6.1 Median definition (standard)**

For a bucket's set of qualifying `screenTimeSeconds` values, sorted ascending:

* If the count of values is **odd**, the median is the single middle value.  
* If the count is **even**, the median is the **arithmetic mean of the two middle values** (standard median convention).  
* If the bucket has **zero** qualifying plays, the median is `N/A` (not `0`); `0` would falsely imply plays with zero screen time. A zero-play bucket is reported as `N/A` with a zero qualifying-play count.

### **6.2 Daily grain**

1. Load the day's input CSV into memory.  
2. Keep only plays where **both** `startTimestamp` and `updatedTimestamp` fall on that day (§4).  
3. Take the `screenTimeSeconds` distribution of the kept plays.  
4. Report the **median** `screenTimeSeconds` for that day.

This metric is over **plays, not distinct users** — a user with multiple qualifying plays on a day contributes each qualifying play to the distribution. This is precisely why the Activity Matrix (a user-scoped, deduplicated structure) is **not** used here.

### **6.3 Weekly grain**

1. Determine the calendar week (ISO, Monday-start) each in-window day belongs to.  
2. For the week's bucket, **pool every qualifying play's `screenTimeSeconds`** across all days in that week — where "qualifying" means both timestamps fall within that **calendar week** (§4).  
3. Compute the **median over the entire pooled set of data points** and report it.

**Do not** compute a median (or mean) of the per-day medians. The weekly value is the median of the full pooled distribution of individual `screenTimeSeconds` data points across the week. A "median of medians" or "mean of medians" is explicitly incorrect and must not be used.

### **6.4 Monthly grain**

Identical to Weekly, but pooling across the **calendar month**: qualifying plays are those whose start and update both fall within that calendar month; the reported value is the median over the entire pooled month-level distribution of `screenTimeSeconds`. Again, never a median/mean of sub-period medians.

---

## **7\. Censoring and Partial-Bucket Acknowledgement**

Two coverage limitations apply, both disclosed:

* **Partial calendar buckets (edge censoring).** First/last weekly/monthly buckets partially covered by the timeline are flagged left/right censored with their covered sub-range — exactly as defined in User Growth §6. The median is still computed over the plays present but is not comparable to a full bucket.
* **Eligibility-driven exclusions.** Independently, boundary-spanning plays are excluded (§4); the per-bucket count of excluded plays is reported so consumers know how much play volume the median rests on.

`N/A` (zero qualifying plays) is always distinct from `0` and is excluded from any downstream aggregation.

## **8\. Computational Efficiency and Data Structures**

Computing a median conventionally implies holding the whole distribution in memory and sorting it. That is only necessary when the values are unbounded or arbitrary-precision. Here, `screenTimeSeconds` is a **non-negative bounded integer**, which permits an **exact** median in memory proportional to the number of *distinct* values rather than the number of plays. The structures below preserve the exact median semantics of §6; any chosen approach must yield medians identical to the naïve "collect all, sort, pick middle" method.

### **8.1 Recommended data structures (in priority order)**

* **Frequency histogram / count-array (primary, exact).** Maintain a map or array `count[v]` \= number of qualifying plays with `screenTimeSeconds == v`, per bucket. Build it in one streaming pass over the day's rows. To read the median: let `n` be the total count; walk the histogram in ascending value order accumulating counts until reaching position `⌈n/2⌉` (odd `n`, that value is the median) or positions `n/2` and `n/2 + 1` (even `n`, average the two values found).

* **Sorted dynamic array / sorted list (fallback, exact).** If a histogram is undesirable (e.g., values are effectively continuous or a general-purpose path is preferred), collect the qualifying `screenTimeSeconds` into a contiguous dynamic array and use a linear-time selection algorithm (quickselect / nth\_element) to find the middle element(s) in expected O(n) without a full sort, or sort once in O(n log n). This holds all data points in memory (O(n)) and is the structure implied by "load the whole distribution," retained here only as a fallback.

### **8.2 Pass and lifetime optimizations**

* **Stream per-day, retain only needed columns.** Load each daily CSV once and extract only `screenTimeSeconds`, `startTimestamp`, `updatedTimestamp`; discard other columns to minimize memory.  
* **Filter at ingest.** Apply the both-timestamps-in-bucket eligibility test (§4) as rows are read, so ineligible plays are never inserted into any histogram.  
* **Single pass, no second traversal.** Eligibility filtering, histogram accumulation, and bucket assignment all happen during the same per-day read; nothing is traversed twice and nothing is persisted beyond the transient per-bucket histograms.  
* **Integer keys.** Use integer day/week/month indices as accumulator keys to avoid repeated date parsing in hot loops; parse each date once at ingest.  
  ---

# **Retention — Common Framework**

**Scope.** The four retention metrics — **Strict**, **Cumulative**, **Consecutive**, **Rolling** — share inputs, per-user anchoring, cohort construction, aggregation, and censoring. This section defines that shared machinery once; each metric section then defines only its **horizon set**, return **window**, and **predicate**. All activity is read directly from the Activity Matrix.

**Dependencies:** consumes the transient **Activity Matrix** (rows = `uid`, columns = calendar dates in `TIMEZONE`, cell = `1` if active), plus `timelineStart`, `timelineEnd`, `grain`, `TIMEZONE`.

---

## **1. Inputs (shared)**

| Name | Type | Definition |
| ----- | ----- | ----- |
| Activity Matrix | matrix | Transient structure from the Cleaning section: one row per `uid`, one column per calendar date in `TIMEZONE`; cell `(UID, D) = 1` iff the user was active on day `D`, else `0`. |
| Timeline (`timelineStart`, `timelineEnd`) | datetime pair (ISO 8601, `TIMEZONE`) | Inclusive observation window. |
| Grain | enum (`Daily` \| `Weekly` \| `Monthly`) | Bucketing granularity, gated by `timelineLengthDays` exactly as in the User Growth section (§4.2 permission matrix), with the same `GRAIN_NOT_ALLOWED_FOR_TIMELINE` error (§4.3) for a disallowed grain. |

Bucketing is calendar-based and identical to the earlier sections: **Daily** = one calendar day; **Weekly** = ISO calendar week (Monday-start); **Monthly** = calendar month, all in `TIMEZONE`.

---

## **2. The Anchor Day `D0` (per user, per bucket)**

`D0` is the user's **first active day within the bucket** — a per-user anchor, not a fixed calendar date:

* **Daily grain:** every day is its own bucket, so every active day is that user's `D0` for that daily bucket.
* **Weekly grain:** the **earliest day the user is active** within the ISO calendar week (Monday-start) — the first `1` in that user's row among the week's columns.
* **Monthly grain:** the **earliest day the user is active** within the calendar month.

`D0` is defined **independently per user**. Two users in the same bucket may have different `D0` dates (one anchored Monday, another Wednesday); the bucket determines *which users are anchored to it* (those whose first-in-bucket activity falls in it), not a shared calendar anchor. A user with no activity in a bucket has no `D0` there and does not participate in that bucket's retention at all. `D0` is guaranteed active by construction (its cell is `1`) and is the cohort-entry day — it is **not** re-checked as part of any return window.

---

## **3. Target Day and Window**

Each metric anchors its return check on the **Nth calendar day strictly after `D0`** (excluding `D0` itself), evaluated on the calendar in `TIMEZONE`:

DN = D0 + N calendar days

Worked example (anchor `D0 = 15 July`): `D1 = 16 July`, `D3 = 18 July`, `D7 = 22 July`, `D30 = 14 August`. Time of day is irrelevant — the matrix cell is a whole-day flag.

Each retention section defines its own **window** over which the return predicate is evaluated (a single day, a closing window `D1 … DN`, or an opening window `DN … timelineEnd`) and its **predicate** (exact match, logical OR, or logical AND across the window's matrix cells).

---

## **4. Eligibility and Cohorts (per horizon, per bucket)**

Each horizon is evaluated separately and has its own cohort within a bucket.

### **4.1 Total (eligible) cohort — denominator**

For a bucket `B` and horizon `DN`, the total cohort is the set of users whose `D0` falls in `B` **and** whose target day is observable within the timeline:

totalCohort(B, DN) = { UID : D0(UID) ∈ B  AND  (D0(UID) + N days) ≤ timelineEnd_day }

* Membership is a **distinct count of users**, each `uid` once (per §2 a user has exactly one `D0` per bucket, so no user is double-counted). It is read from the matrix as the count of users with a `D0` in `B` (their first `1` in the bucket) that also satisfy the observability condition; it is **not** a single day's DAU.
* **The eligibility gate `D0 + N ≤ timelineEnd` is identical across all four retention metrics.** They differ only in their numerator predicate/window, never in how the denominator is formed. Their denominators therefore coincide horizon-for-horizon and can be asserted as an integration test.

### **4.2 Retained cohort — numerator**

The subset of the total cohort satisfying that metric's return predicate over its window:

retainedCohort(B, DN) = { UID ∈ totalCohort(B, DN) : <metric predicate>(UID) == TRUE }

Each retained user is counted **exactly once** per bucket per horizon.

### **4.3 Retention fraction**

retention(B, DN) = | retainedCohort(B, DN) |  /  | totalCohort(B, DN) |   (× 100 for percentage)

If `| totalCohort(B, DN) | == 0`, the value is `N/A` (no eligible users to measure), **never `0`**.

---

## **5. Aggregation Across a Grain (never a mean/median of per-cohort fractions)**

When a bucket spans multiple days (Weekly, Monthly), or whenever multiple cohorts are combined, the reported value is computed from **pooled distinct-user cohorts**, not by averaging per-day or per-cohort percentages. Averaging weights small and large cohorts equally and is **explicitly incorrect**.

1. Pool `totalCohort(B, DN)` across the bucket — every distinct user whose `D0` is in the bucket and whose target is observable, counted once.
2. Pool `retainedCohort(B, DN)` — every distinct user in that cohort satisfying the metric's predicate against **their own** `D0`-anchored window, counted once.
3. `retention(B, DN) = |pooled retainedCohort| / |pooled totalCohort| × 100`.

Each user contributes to the bucket according to **their own personal `D0`** and is counted once. A user whose `D0` is Monday and one whose `D0` is Wednesday are both in the same weekly cohort, each measured against their own target/window.

---

## **6. Right-Censoring (timeline-bounded, not grain-bounded)**

* **Window lookups may cross bucket boundaries.** A window is read from the matrix by absolute calendar date and may extend beyond the `D0` bucket (e.g., a `D0` late in a week whose window runs into the next week). The bucket only anchors `D0`; it does not constrain where the window may land. The matrix column for such a day is read normally.
* **The timeline is the hard bound.** If a user's target day `D0 + N` falls **after `timelineEnd`**, that user is **right-censored** for that horizon — excluded from **both** numerator and denominator (equivalently, only users with `D0 + N ≤ timelineEnd` are eligible; §4.1). An unobservable user is **never** counted as "not retained," since absence of observation is not evidence of non-return.
* **Per-horizon censoring.** Evaluated independently per horizon: a user near `timelineEnd` may be observable/eligible for D3 yet censored for D30.

**Limitation (acknowledged).** Buckets close to `timelineEnd` rest on smaller, partially-censored cohorts, especially for longer horizons (D7, D30); those values are less stable and not directly comparable to fully-observed buckets. Censored users are excluded rather than assumed lapsed, so the reported fraction stays unbiased even though its cohort is smaller. This censoring is distinct from, and applies on top of, the partial first/last calendar-bucket coverage disclosed in earlier sections.

---

# **Strict Retention Analysis**

**Scope.** **Strict Retention**: the fraction of users, anchored at `D0` in a bucket, active **exactly** on the Nth calendar day after `D0`, for N ∈ {1, 3, 7, 30}. Inputs, `D0`, cohorts, aggregation, and censoring follow the **Retention — Common Framework** (§1–§6); this section defines only the strict window and predicate.

---

## **1. Objective**

Compute **Strict D1, D3, D7, and D30 retention** per bucket. "Strict" means retention is checked **only** on the exact target day (`D0 + N`); activity on the intervening days is irrelevant and is never used as a substitute.

---

## **2. Window and Predicate**

* **Window:** the single day `DN = D0 + N` (Common Framework §3). Intervening days (`D0+1 … D0+N−1`) are **not** examined. `D3` is the 3rd day after `D0`, **not** "active on any of days 1–3."
* **Predicate (single matrix cell lookup):**

retainedAtDN(UID) = ( ActivityMatrix[UID][ D0(UID) + N days ] == 1 )

`D0`'s own cell is `1` by construction, so only the `DN` cell needs to be checked.

The total cohort (denominator), retained cohort (numerator), retention fraction, pooled aggregation, and right-censoring are exactly as in the Common Framework §4–§6.

---

# **Cumulative Retention Analysis**

**Scope.** **Cumulative Retention**: the fraction of users, anchored at `D0` in a bucket, who returned **at least once** during `D1 … DN` inclusive, for N ∈ {3, 7, 30}. Follows the **Retention — Common Framework** (§1–§6); the return check is an **OR across a closing window**.

---

## **1. Objective**

Compute **Cumulative D3, D7, and D30 retention** per bucket. "Cumulative" means a user counts as retained if they returned on **any** day within the horizon window, not only on the exact terminal day.

**D1 cumulative retention does not exist** and is not computed. The D1 window would be a single day (`D1` only), which is identical to Strict D1 retention; there is no cumulative window to OR over, so D1 cumulative is undefined by construction and must not be reported.

---

## **2. Window and Predicate**

**Window** (closing; `D0` is excluded — it is active by construction, so including it would make every user trivially retained):

window(N) = [ D0 + 1 day , D0 + 2 days , … , D0 + N days ]

* **D3 cumulative:** window `D1, D2, D3` (`D0+1 … D0+3`).
* **D7 cumulative:** window `D1 … D7` (`D0+1 … D0+7`).
* **D30 cumulative:** window `D1 … D30` (`D0+1 … D0+30`).

Worked example (`D0 = 15 July`): D3 window = 16–18 July; D7 window = 16–22 July; D30 window = 16 July–14 August.

**Predicate (row-wise OR):**

retainedCumulativeAtDN(UID) = OR over d in window(N) of ( ActivityMatrix[UID][ D0(UID) + d ] == 1 )   // TRUE if any one (or more) of the window's cells is 1

A single `1` anywhere in `D1 … DN` makes the user retained; the number of active days in the window does not matter (boolean OR, not a count).

Cohorts, fraction, pooled aggregation, and right-censoring follow the Common Framework §4–§6. Note that the eligibility gate `D0 + N ≤ timelineEnd` is exactly the cohort-maturity rule "only include cohorts whose complete Day N has passed": a user whose window extends past `timelineEnd` has not had the full opportunity to return and is right-censored, so newer, less-mature cohorts do not deflate the metric.

---

## **3. Relationship to Strict Retention**

For the same `D0` and horizon N, cumulative retention is **always ≥ strict retention**, because the strict terminal day `DN` is one of the days inside the cumulative window `D1 … DN`. Any user counted as strictly retained at `DN` is necessarily cumulatively retained at `DN`; the reverse does not hold. The two metrics answer different questions: strict = "active on exactly that day"; cumulative = "returned at all by that day."

---

# **Consecutive Retention Analysis**

**Scope.** **Consecutive Retention**: the fraction of users, anchored at `D0` in a bucket, active on **every** day from `D1` through `DN` (an unbroken streak), for N ∈ {3, 7, 30}. Follows the **Retention — Common Framework** (§1–§6); the return check is a **logical AND across the window**.

---

## **1. Objective**

Compute **Consecutive D3, D7, and D30 retention** per bucket. "Consecutive" means a user counts as retained only if they returned on **every** day of the horizon window with no gaps — an unbroken streak from `D1` through `DN`. Informally: if a user has an unbroken `N`-day streak starting the day after `D0`, they are `N`-day consecutively retained.

**Reported horizons.** Only **D3, D7, and D30** are produced. Intermediate horizons (D1, D2, D4, …) are calculable by the same rule but are **not** reported; they appear here only to explain the monotonic relationship in §3.

---

## **2. Window and Predicate**

**Window** (`D0` is excluded — it is the cohort-entry day, guaranteed active by construction; Common Framework §2):

window(N) = [ D0 + 1 day , D0 + 2 days , … , D0 + N days ]

* **D3 consecutive:** window `D1 … D3` (`D0+1 … D0+3`) — 3 days, all required.
* **D7 consecutive:** window `D1 … D7` (`D0+1 … D0+7`) — 7 days, all required.
* **D30 consecutive:** window `D1 … D30` (`D0+1 … D0+30`) — 30 days, all required.

Worked example (`D0 = 15 July`): D3 window = 16–18 July; D7 window = 16–22 July; D30 window = 16 July–14 August. Every day listed must be active for the user to qualify.

**Predicate (row-wise AND):**

retainedConsecutiveAtDN(UID) = AND over d in window(N) of ( ActivityMatrix[UID][ D0(UID) + d ] == 1 )   // TRUE only if EVERY one of the window's cells is 1

A single `0` (missed day) anywhere in `D1 … DN` makes the flag **false**; the streak must be unbroken across the entire window.

Cohorts, fraction, pooled aggregation, and right-censoring follow the Common Framework §4–§6 (the eligibility gate `D0 + N ≤ timelineEnd` requires the entire streak window to have elapsed within the timeline).

---

## **3. Monotonicity (built-in consistency check)**

Consecutive retention can **only decrease or stay equal** as the horizon grows, because a longer streak strictly contains every shorter one. For any fixed `D0`:

D1 ≥ D2 ≥ D3 ≥ … ≥ D7 ≥ … ≥ D30   (consecutive retention, non-increasing in N)

Every user who satisfies the D7 streak necessarily satisfied D1 through D6; but a user who satisfies D3 may break the streak before D7. Restricting to reported horizons, the invariant is:

D3 consecutive  ≥  D7 consecutive  ≥  D30 consecutive

evaluated on the **same cohort basis**. This inequality is a useful implementation sanity check: a violation indicates a bug in windowing, anchoring, or cohort construction. (The check is exact only when the horizons are compared on identical, fully-observed cohorts; near `timelineEnd`, differing per-horizon censorship changes the denominators, so the inequality is guaranteed strictly only among users eligible for all three horizons.)

---

## **4. Relationship to Strict and Cumulative Retention**

For the same `D0`, horizon N, and cohort:

* **Consecutive ≤ Cumulative** at the same horizon: requiring activity on *every* day of `D1 … DN` (AND) is strictly harder than requiring activity on *at least one* day (OR). Every consecutively-retained user is also cumulatively retained; the reverse does not hold.
* **Consecutive ≤ Strict at `DN`:** consecutive requires the terminal day `DN` to be active (it is inside the streak), so consecutive implies strict at `DN`. The full ordering at a fixed horizon and cohort is therefore **cumulative ≥ strict ≥ consecutive**.

---

# **Rolling Retention Analysis**

**Scope.** **Rolling Retention**: the fraction of users, anchored at `D0` in a bucket, who returned on day `D0 + N` **or any later day** through `timelineEnd`, for N ∈ {3, 7, 30}. The open-ended counterpart to Cumulative. Follows the **Retention — Common Framework** (§1–§6); differs only in its numerator window.

---

## **1. Objective**

Compute **Rolling D3, D7, and D30 retention** per bucket. "Rolling" means a user counts as retained if they returned on the target day **or any later in-timeline day**, not only on that exact day and not only within a closing window. Only **D3, D7, D30** are produced.

* **Rolling D3:** retained if active on day `D0 + 3` **or any day after** (through `timelineEnd`).
* **Rolling D7:** retained if active on day `D0 + 7` **or any day after** (through `timelineEnd`).
* **Rolling D30:** retained if active on day `D0 + 30` **or any day after** (through `timelineEnd`).

---

## **2. Window and Predicate**

**Window** (opening, right-bounded by the timeline):

window(N) = [ D0 + N days , D0 + N + 1 days , … , timelineEnd_day ]

* The window **starts** at the exact target day `DN = D0 + N` (inclusive).
* The window **ends** at `timelineEnd` (inclusive). This is the only right bound. **Nothing beyond the timeline is ever considered**, even if matrix columns for later dates happen to exist (e.g., foresight-written columns just past `timelineEnd`); those are out of scope for this metric.
* Because the right edge is `timelineEnd` rather than a fixed offset, the window has **no fixed length** — it is longer for users anchored earlier in the timeline and shorter for those anchored later. This opportunity asymmetry is inherent to an open-ended rolling metric and is acknowledged (§4).

Contrast with cumulative retention: cumulative scans `[D1 … DN]` (a closing window ending at the horizon); rolling scans `[DN … timelineEnd]` (an opening window starting at the horizon). The two overlap only on the single day `DN`.

**Predicate (row-wise OR over the tail):**

retainedRollingAtDN(UID) = OR over d from (D0(UID) + N) to timelineEnd_day of ( ActivityMatrix[UID][d] == 1 )   // TRUE if the user is active on the target day or any later in-timeline day

A single `1` anywhere from `DN` to `timelineEnd` makes the user retained; days strictly before `DN` (`D1 … D(N−1)`) are **not** examined by this metric.

---

## **3. Cohorts (fixed base minus horizon-dependent censoring)**

The denominator is the Common Framework §4.1 cohort. In plain terms, take a **fixed base** and subtract the users you cannot yet observe:

totalActive(B)  = number of DISTINCT users whose D0 falls in bucket B (the bucket's distinct active-user count)   ← same for every horizon
censored(B, DN) = those users whose target day D0 + N falls after timelineEnd                                     ← grows as N grows

eligibleCohort(B, DN) = totalActive(B) − censored(B, DN) = { UID : D0(UID) ∈ B  AND  (D0(UID) + N days) ≤ timelineEnd_day }

Step by step:

* Start with every distinct user first active in the bucket — `totalActive(B)`. This is the bucket's distinct active-user count (the DAU / WAU / MAU base from the User Growth section), and it is the **same** number for D3, D7, and D30.
* Drop a user for horizon N only if their target day `D0 + N` already falls past `timelineEnd` — meaning we cannot observe even the first day of their window, so they cannot be measured for that horizon.
* Whoever remains is the eligible cohort: users first active in the bucket whose target day still lands inside the timeline.

This is the **same eligibility test** (`D0 + N ≤ timelineEnd`) used by the strict, cumulative, and consecutive sections; rolling changes only its numerator window (§2), not the denominator. The numerator (`retainedRollingAtDN == TRUE` over this cohort), the fraction, pooled aggregation, and the `N/A`-vs-`0` rule all follow the Common Framework §4.2–§6.

---

## **4. Opportunity Asymmetry and Nested Denominators**

* **Fixed base, growing censored set.** Since a larger N pushes more users' `D0 + N` past `timelineEnd`, the censored set grows with the horizon while the base stays fixed:

censored(B, D3) ≤ censored(B, D7) ≤ censored(B, D30)  ⇒  eligibleCohort(B, D3) ≥ eligibleCohort(B, D7) ≥ eligibleCohort(B, D30)   (nested denominators)

This nested-denominator relationship must hold within any single bucket and is a useful cross-horizon sanity check.

* **Inherent opportunity asymmetry (acknowledged).** Unlike the closing-window metrics, rolling's window length is not constant: a user anchored early in the timeline has many days in which to satisfy the "on or after" condition, while one anchored near `timelineEnd` has few. Consequently, buckets and cohorts closer to `timelineEnd` have **less data and less opportunity**, and their rolling retention is **imperfect and biased downward** relative to earlier cohorts. This is disclosed rather than corrected; the metric should be read with this limitation in mind, especially for longer horizons near the end of the timeline.

---

## **5. Relationship to Other Retention Metrics**

At a fixed `D0`, horizon N, and cohort:

* **Rolling vs Cumulative are not generally orderable.** Cumulative scans `[D1 … DN]`; rolling scans `[DN … timelineEnd]`. They share only day `DN`, so neither contains the other. A user active only on `D1` is cumulative-retained but not rolling-retained; a user active only on `D(N+5)` is rolling-retained but not cumulative-retained.
* **Rolling ≥ Strict at the same horizon.** Strict checks the single day `DN`; rolling checks `DN` plus every later in-timeline day. The strict target day is inside the rolling window, so every strictly-retained user is rolling-retained (the reverse does not hold): `rolling(DN) ≥ strict(DN)` on the same eligible cohort — a useful cross-metric sanity check.

---

# **Play State Analysis**

**Scope.** **Play State Analysis**: the distribution of play outcomes (`loaded`, `solving`, `completed`) across dates at a user-specified grain, via a play-level **Completion Matrix** — per-state counts and fractions. **Play-scoped**, so it does **not** use the Activity Matrix.

**Dependencies:** consumes the cleaned Play-by-Play CSVs (`uid`, `puzzleId`, `updatedTimestamp`, `playState`, `filledBoxes`) and the `isLoaded` derived variable, plus `timelineStart`, `timelineEnd`, `grain`, `TIMEZONE`.

---

## **1\. Inputs**

| Name | Type | Definition |
| ----- | ----- | ----- |
| Timeline (`timelineStart`, `timelineEnd`) | datetime pair (ISO 8601, `TIMEZONE`) | Inclusive observation window. |
| Grain | enum (`Daily` | `Weekly` | `Monthly`) | Bucketing granularity, gated by `timelineLengthDays` exactly as in the User Growth section (same `GRAIN_NOT_ALLOWED_FOR_TIMELINE` error for a disallowed grain). |

---

## **2\. Objective**

To analyse **play progress across dates** for all users: for any bucket, how many plays were left in each state, and what fraction of plays ended in each state. This reveals where plays fall off (opened but untouched, started but unfinished, or completed).

---

## **3\. Play State Enum**

Every play, on every date, has exactly one of four states:

| State | Meaning | Storage code |
| ----- | ----- | ----- |
| `absent` | The play is not attributed to this date (its resolving date is a different day, or outside the timeline). | `0` |
| `loaded` | The play was opened but no boxes were filled (`isLoaded == true`). | `1` |
| `solving` | The play was started with at least one box filled but is not complete (`isLoaded == false` and source `playState == inProgress`). | `2` |
| `completed` | The play is finished (source `playState == completed`). | `3` |

The integer codes `0..3` are a **compact storage encoding only**. They are **not** aggregated by summing (a column sum cannot be decomposed back into per-state counts). Aggregation is always **count-by-state** (§6). The codes exist purely to store each cell in a single small integer and to allow fast equality filtering.

### **3.1 State derivation (per play)**

For a given play row, the non-absent state is derived as follows, in this order:

1. If `isLoaded == true` → **`loaded`** (`1`). (`isLoaded` is `filledBoxes == 0`, per the Variables section.)  
2. Else if source `playState == inProgress` → **`solving`** (`2`). (Not loaded, so `filledBoxes ≥ 1`, and still in progress.)  
3. Else if source `playState == completed` → **`completed`** (`3`).

These three branches are exhaustive for a play that resolves on the date in question: `isLoaded` splits off the zero-filled case first, then the remaining plays are partitioned by the source `playState` enum (`inProgress` vs `completed`). The `absent` state (`0`) is **not** derived from a play's own fields; it is the default for any (play, date) pair where the play does not resolve to that date (§4).

Precedence note: `isLoaded` is checked **before** the source `playState`. A play with `filledBoxes == 0` is `loaded` even if its source `playState` were `inProgress`; this is intended, since "loaded" is the more specific zero-input state. A `completed` play cannot have `filledBoxes == 0` in valid data, so the `loaded`\-first ordering does not misclassify completions.

---

## **4\. The Completion Matrix**

The **Completion Matrix** is a play-level structure used to place each play's state on a single date.

* **Row key:** the tuple **(`puzzleId`, `uid`)** — one row per play. This key is unique: PuzzleMe creates exactly one play row per (user, puzzle); subsequent updates modify that same play rather than creating a new one, so a (`puzzleId`, `uid`) pair maps to exactly one play with one `updatedTimestamp`.  
* **Column key:** a single **calendar date** in `TIMEZONE`.  
* **Cell (row, date):** the play's state code (`0..3`) on that date.

### **4.1 Single-date placement rule**

Each play resolves to **exactly one** date column — the calendar day (in `TIMEZONE`) of its **`updatedTimestamp`** (the play's last-update date, which reflects the date on which the play was left in its recorded state).

resolveDate(play) \= calendar day of play.updatedTimestamp, in TIMEZONE

* On the column `resolveDate(play)`, the cell holds the play's derived non-absent state (`loaded` / `solving` / `completed`, per §3.1).  
* On **every other** date column, the play's cell is `absent` (`0`). Consequently each row has **at most one** non-absent cell.

### **4.2 Attribution note (`updatedTimestamp`, not `startedTimestamp`)**

Play State Analysis attributes a play to the date of its **last update** (`updatedTimestamp`), which is deliberately different from the Engagement section (which attributes plays by the day whose CSV they reside in, i.e., `startedTimestamp`). This is intentional: Play State Analysis asks "what state was this play left in, and on what date," so the last-update date is the correct anchor. A play started on one day and last updated on another is placed on the **update** day here.

### **4.3 Out-of-timeline plays are dropped**

If `resolveDate(play)` falls **outside** `[timelineStart, timelineEnd]`, the play has no non-absent cell in any in-window column and therefore contributes to **no** in-window metric. Such plays are dropped from all aggregation in this section. Nothing outside the timeline is ever considered.

### **4.4 Efficient representation**

The matrix is logically dense but practically sparse (each row has one non-absent cell). Recommended implementations, all preserving identical results:

* **Do not materialize a dense (plays × dates) grid.** Instead, for each play store a single pair `(resolveDate, stateCode)`; the "matrix" is the collection of these pairs. Absent cells are implicit (any (play, date) not equal to the stored `resolveDate` is `absent`).  
* **Aggregate with per-date, per-state counters.** Maintain, for each date column, a length-4 tally `counts[date][0..3]`. In one pass over plays, for each in-timeline play increment `counts[resolveDate][stateCode]`. This yields every per-state count directly, in O(number of plays) time and O(number of in-window dates × 4\) space, with no sorting and no ambiguous sum decomposition.  
* **Integer date keys.** Key columns by an integer day index to avoid repeated date parsing in hot loops.

---

## **5\. Bucketing**

Bucketing is identical to the other sections: **Daily** \= one calendar day; **Weekly** \= ISO calendar week (Monday-start); **Monthly** \= calendar month, all in `TIMEZONE`. A bucket's columns are the date columns whose dates fall within it and within the timeline. Grain permission and errors follow the User Growth section.

---

## **6\. Metrics**

All metrics below are simple aggregations over the completion matrix's columns. Because each play occupies exactly one non-absent cell, no deduplication is required — each play is counted at most once by construction.

### **6.1 Total plays in a bucket (denominator)**

The **total number of plays** in a bucket is the count of **non-absent** cells (state code `≠ 0`) across all date columns belonging to that bucket:

totalPlays(B) \= count of cells with stateCode ∈ {1, 2, 3}  
                over all date columns in bucket B  
             \= Σ over dates d in B of ( counts\[d\]\[1\] \+ counts\[d\]\[2\] \+ counts\[d\]\[3\] )

For example, for a weekly bucket, `totalPlays` is the number of non-absent entries across all seven of that week's in-window date columns. This is the **denominator** for all fraction metrics in this section.

### **6.2 Per-state absolute counts**

For each reported state `s ∈ {loaded, solving, completed}`, the absolute count in a bucket is the number of cells equal to that state across the bucket's columns:

stateCount(B, s) \= Σ over dates d in B of counts\[d\]\[ code(s) \]

* `loadedCount(B) = Σ_d counts[d][1]`  
* `solvingCount(B) = Σ_d counts[d][2]`  
* `completedCount(B) = Σ_d counts[d][3]`

All three are computed and reported for every bucket. (`absent` is never counted or reported — absent plays do not exist on that date; §6.4.)

By construction: `loadedCount(B) + solvingCount(B) + completedCount(B) = totalPlays(B)`.

### **6.3 Per-state fractions**

For each reported state `s`, the fraction of plays left in that state is:

stateFraction(B, s) \= stateCount(B, s) / totalPlays(B)     (× 100 for percentage)

* `loadedFraction(B) = loadedCount(B) / totalPlays(B)`  
* `solvingFraction(B) = solvingCount(B) / totalPlays(B)`  
* `completedFraction(B) = completedCount(B) / totalPlays(B)`

All three fractions are reported. They sum to `1` (100%) by construction, since the three states partition the non-absent plays.

If `totalPlays(B) == 0`, every fraction is `N/A` (not `0`), and the per-state absolute counts are all `0`. A zero-play bucket is reported with `N/A` fractions and a zero play count.

### **6.4 `absent` is excluded**

The `absent` state is never a numerator and never reported as a fraction. An absent (play, date) pair means the play simply does not exist on that date, so it is neither part of the denominator (§6.1 counts only non-absent) nor a reported outcome.

---

## **7\. Censoring and Edge Cases**

* **Partial first/last buckets.** As in earlier sections, the first and last weekly/monthly buckets may be only partially covered by the timeline; they are flagged as left/right censored and their counts reflect only the in-window dates present. Values are reported but marked not directly comparable to fully-covered buckets.  
* **Out-of-timeline plays.** Dropped entirely (§4.3); they contribute to no bucket.  
* **Zero-play buckets.** Fractions are `N/A`, counts are `0` (§6.3).  
* **Data-quality nulls.** Plays excluded during cleaning (null `uid`, null `startTimestamp`, or a `playState`/`filledBoxes` that cannot resolve to a state) are not placed in the matrix; their exclusion is already tallied by the Cleaning section and they do not appear in any count here.

---
