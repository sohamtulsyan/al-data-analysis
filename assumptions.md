# Assumptions — Retention Pipeline PRD

This is the canonical list of assumptions made in drafting the Retention Pipeline PRD. It mirrors the **Assumptions** section at the top of `PRD Final.md`. Where an assumption also drives a computational rule, the full rule lives in the referenced PRD section and is not repeated here.

1. **Grid games only — `puzzleType` is ignored.** Every puzzle is assumed to be a grid game, so `puzzleType` is neither required nor persisted. This lets `isLoaded` be defined simply as `filledBoxes == 0` (see Variables — `isLoaded`).

   *Unresolved edge case:* two users can end in the identical recorded state `filledBoxes == 0`, `playState == inProgress` while meaning different things — (a) **entered then erased**: the user typed an answer (`filledBoxes > 0`) then cleared it back to `0`; (b) **never interacted**: the user opened the puzzle but never typed. The data cannot tell them apart, so both are treated as `loaded`.

2. **One series, one puzzle type per run.** The pipeline analyzes a single puzzle series at a time, assumed to contain a single homogeneous `puzzleType`. A series can in principle mix types, but since `puzzleType` is omitted (assumption 1), mixed-type series are out of scope.

3. **`getUserInfo` is omitted.**

4. **`score` is ignored.**

5. **Activity = any timestamped event on a day.** A user is active on a day if a `startTimestamp` or `updatedTimestamp` falls on it; a day with no timestamped event is not counted, even if the user was plausibly mid-play. *Example:* start Monday, return Tuesday without finishing, finish Wednesday → active on **Monday and Wednesday only**, not Tuesday. (Formal rule: Variables — `isActiveOnDay`.)

6. **All times are UTC.** A `TIMEZONE` field exists, but for the current scope it is always assumed to be UTC.

7. **Engagement time is attributed only to a fully-contained play.** A play's `screenTimeSeconds` counts toward a bucket only when **both** its start and its last update fall inside that bucket — same day (Daily), same calendar week (Weekly), or same calendar month (Monthly); a play that straddles a boundary is excluded at that grain. This is **intentionally stricter than activity (assumption 5)**: engagement measures time spent completing a play within a single bucket, whereas activity only requires *some* event on the day. The two definitions are deliberately different. (Formal rule: Engagement — Play Eligibility.)

8. **The daily cron cannot be fully correct — play state keeps changing after the day ends.** A play's `playState` and `updatedTimestamp` are mutated **in place on the same row** as the user returns to it, so the values recorded depend on *when* the fetch runs. Fetching day `n` immediately after it ends captures a play that is still in progress; if that user resumes and completes it on day `n+2`, the same row now holds a different `updatedTimestamp` and `playState` than what was persisted. Multi-day plays are therefore stored with a stale state. The pipeline currently assumes **a play started on day `n` also ends on day `n` for most users** — true for the majority, but it understates completions and last-update times for genuinely multi-day plays.

9. **First and last buckets are censored, and their dips/peaks are artifacts, not behaviour.** Because buckets are calendar-aligned and retention horizons look forward, the edges of the timeline are computed on a *subset* of the data a full bucket would contain:

   * **Left-censored (start).** If the timeline begins mid-week or mid-month, the first bucket spans fewer days, so it counts fewer users and plays.
   * **Right-censored (end).** The last bucket is likewise short, and forward-looking retention additionally drops every user whose target day `D0 + N` falls past `timelineEnd`, shrinking that bucket's cohort.
   * **Why this produces dips and peaks.** Fewer days means mechanically lower absolute counts — a **dip** that reflects less coverage, not less usage. Smaller cohorts mean a small denominator, so a handful of users swings the percentage sharply — **spiky peaks and dips that are variance, not signal**.

   Edge values are flagged left/right censored and must not be read as a real decline or surge. (Formal rules: User Growth — Edge Censoring; Retention — Common Framework §6.)

10. **OUU vs NUU partition within a bucket.** An **NUU** is a uid whose global first-ever active day (in the Activity Matrix) falls in the bucket. An **OUU** is a uid active in the bucket whose global first-ever day is *strictly before* their in-bucket D0 (first activity in that bucket). The same uid is never both NUU and OUU in the same bucket. Across buckets a former NUU can become an OUU once they return in a later bucket.

    *Left-lookback limit:* users whose true first activity predates the earliest Daily Play Data CSV look like NUUs on first appearance in-matrix. That mislabels some true OUUs as NUUs near the start of history — same limitation as NUU itself.
