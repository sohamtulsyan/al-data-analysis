# Assumptions — Retention Pipeline PRD

This is the canonical list of assumptions made in drafting the Retention Pipeline PRD. It mirrors the **Assumptions** section at the top of `PRD Final.md`. Where an assumption also drives a computational rule, the full rule lives in the referenced PRD section and is not repeated here.

1. **Grid games only — `puzzleType` is ignored.** Every puzzle is assumed to be a grid game, so `puzzleType` is neither required nor persisted. This lets `isLoaded` be defined simply as `filledBoxes == 0` (see Variables — `isLoaded`).

   *Unresolved edge case:* two users can end in the identical recorded state `filledBoxes == 0`, `playState == inProgress` while meaning different things — (a) **entered then erased**: the user typed an answer (`filledBoxes > 0`) then cleared it back to `0`; (b) **never interacted**: the user opened the puzzle but never typed. The data cannot tell them apart, so both are treated as `loaded`.

2. **One series, one puzzle type per run.** The pipeline analyzes a single puzzle series at a time, assumed to contain a single homogeneous `puzzleType`. A series can in principle mix types, but since `puzzleType` is omitted (assumption 1), mixed-type series are out of scope.

3. **`getUserInfo` is omitted.** Lead-generation user fields are neither requested nor analyzed.

4. **`score` is ignored.** The `score` field is not analyzed or persisted.

5. **Activity = any timestamped event on a day.** A user is active on a day if a `startTimestamp` or `updatedTimestamp` falls on it; a day with no timestamped event is not counted, even if the user was plausibly mid-play. *Example:* start Monday, return Tuesday without finishing, finish Wednesday → active on **Monday and Wednesday only**, not Tuesday. (Formal rule: Variables — `isActiveOnDay`.)

6. **All times are UTC.** Day boundaries are evaluated in UTC. If a later analysis converts to another timezone, the observation window must be widened by one day on each side (`TL−1` and `TL+1`) so plays that cross the shifted boundary are captured.

7. **Engagement time is attributed only to a fully-contained play.** A play's `screenTimeSeconds` counts toward a bucket only when **both** its start and its last update fall inside that bucket — same day (Daily), same calendar week (Weekly), or same calendar month (Monthly); a play that straddles a boundary is excluded at that grain. This is **intentionally stricter than activity (assumption 5)**: engagement measures time spent completing a play within a single bucket, whereas activity only requires *some* event on the day. The two definitions are deliberately different. (Formal rule: Engagement — Play Eligibility.)
