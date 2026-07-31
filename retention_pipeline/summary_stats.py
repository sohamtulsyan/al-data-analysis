"""Mean / median summaries of per-bucket metric series (CLI display only).

Aggregations exclude ``N/A`` from both numerator and denominator (PRD Global §).
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple


def coerce_number(value: Any) -> Optional[float]:
    if value is None or value == "N/A":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def mean_median(values: Sequence[float]) -> Tuple[Optional[float], Optional[float], int]:
    """Return (mean, median, n) for a non-empty numeric series; else (None, None, 0)."""
    nums = [float(v) for v in values]
    n = len(nums)
    if n == 0:
        return None, None, 0
    mean = sum(nums) / n
    ordered = sorted(nums)
    mid = n // 2
    if n % 2 == 1:
        median = ordered[mid]
    else:
        median = (ordered[mid - 1] + ordered[mid]) / 2.0
    return mean, median, n


def _fmt(value: Optional[float], *, percent: bool = False) -> str:
    if value is None:
        return "N/A"
    if percent:
        return f"{value:.2f}%"
    if abs(value - round(value)) < 1e-9:
        return f"{value:.0f}"
    return f"{value:.2f}"


def format_summary_line(
    label: str,
    values: Sequence[Any],
    *,
    percent: bool = False,
) -> str:
    nums = [v for v in (coerce_number(x) for x in values) if v is not None]
    mean, median, n = mean_median(nums)
    return (
        f"  {label:<42} n={n:<4} "
        f"mean={_fmt(mean, percent=percent):>10}  "
        f"median={_fmt(median, percent=percent):>10}"
    )


def _bucket_field(buckets: Iterable[Dict[str, Any]], field: str) -> List[Any]:
    return [b.get(field) for b in buckets]


def summarize_metric_block(name: str, metric: Dict[str, Any]) -> List[str]:
    """Build CLI lines for one analysis / retention metric object."""
    buckets = metric.get("buckets") or []
    if not buckets:
        return []

    lines: List[str] = []

    # Scalar per-bucket fields commonly plotted
    scalar_specs: List[Tuple[str, str, bool]] = [
        ("userGrowth", "userGrowth", False),
        ("medianScreenTimeSeconds", "medianScreenTimeSeconds", False),
        ("averageScreenTimeSeconds", "averageScreenTimeSeconds", False),
        ("qualifyingPlays", "qualifyingPlays", False),
        ("totalPlays", "totalPlays", False),
        ("loadedCount", "loadedCount", False),
        ("solvingCount", "solvingCount", False),
        ("completedCount", "completedCount", False),
        ("loadedFractionPercent", "loadedFractionPercent", True),
        ("solvingFractionPercent", "solvingFractionPercent", True),
        ("completedFractionPercent", "completedFractionPercent", True),
        ("signupFractionPercent", "signupFractionPercent", True),
        ("signups", "signups", False),
        ("activeUsers", "activeUsers", False),
        ("nuuCount", "nuuCount", False),
    ]
    present = set(buckets[0].keys())
    for field, suffix, is_pct in scalar_specs:
        if field not in present:
            continue
        lines.append(
            format_summary_line(
                f"{name}.{suffix}",
                _bucket_field(buckets, field),
                percent=is_pct,
            )
        )

    # Retention-style horizons
    if "horizons" in present:
        horizon_keys = sorted(
            buckets[0]["horizons"].keys(),
            key=lambda h: int(h[1:]) if h.startswith("D") and h[1:].isdigit() else h,
        )
        for h in horizon_keys:
            lines.append(
                format_summary_line(
                    f"{name}.{h}.retentionPercent",
                    [b["horizons"][h].get("retentionPercent") for b in buckets],
                    percent=True,
                )
            )
            # Cohort volume once per family (same across horizons for a bucket)
            vol_key = None
            sample = buckets[0]["horizons"][h]
            for candidate in (
                "totalLoggedInInBucket",
                "totalOuuInBucket",
                "totalNuuInBucket",
                "totalActiveInBucket",
                "totalCohort",
            ):
                if candidate in sample:
                    vol_key = candidate
                    break
            if vol_key and h == horizon_keys[0]:
                lines.append(
                    format_summary_line(
                        f"{name}.{vol_key}",
                        [b["horizons"][h].get(vol_key) for b in buckets],
                        percent=False,
                    )
                )

    return lines


def summarize_analysis_payload(payload: Dict[str, Any]) -> List[str]:
    """Summaries for ``analysis_results.json`` (and similar top-level metrics maps)."""
    metrics = payload.get("metrics") or {}
    lines: List[str] = []
    for key in (
        "userGrowth",
        "engagement",
        "strictRetention",
        "cumulativeRetention",
        "consecutiveRetention",
        "rollingRetention",
        "playState",
    ):
        block = metrics.get(key)
        if isinstance(block, dict):
            lines.extend(summarize_metric_block(key, block))
    return lines


def summarize_retention_payload(payload: Dict[str, Any], *, prefix: str = "") -> List[str]:
    """Summaries for NUU / OUU / logged-in retention JSON payloads."""
    metrics = payload.get("metrics") or {}
    lines: List[str] = []
    for key, block in metrics.items():
        if not isinstance(block, dict):
            continue
        label = f"{prefix}{key}" if prefix else key
        lines.extend(summarize_metric_block(label, block))
    return lines


def summarize_signup_payload(payload: Dict[str, Any]) -> List[str]:
    metric = payload.get("metric")
    if isinstance(metric, dict):
        return summarize_metric_block("signupFraction", metric)
    return []


def print_distribution_summaries(lines: List[str], *, title: str = "Distribution summaries") -> None:
    if not lines:
        return
    print(f"{title} (N/A excluded):")
    for line in lines:
        print(line)
