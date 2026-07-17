"""Visualization of analysis_results.json — matplotlib PNGs + Plotly HTML."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import plotly.graph_objects as go  # noqa: E402
from plotly.subplots import make_subplots  # noqa: E402

from retention_pipeline.config import OUTPUT_DIR


def _num(v: Any) -> Optional[float]:
    if v is None or v == "N/A":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _bucket_labels(buckets: List[Dict[str, Any]]) -> List[str]:
    labels = []
    for b in buckets:
        label = b["bucket"]
        if b.get("partialBucket"):
            side = b.get("censorSide") or "partial"
            label = f"{label} ({side})"
        labels.append(label)
    return labels


def render_visualizations(
    results: Dict[str, Any],
    output_dir: Path = OUTPUT_DIR,
) -> Dict[str, Path]:
    """Write chart PNGs and an interactive HTML dashboard from analysis results."""
    charts_dir = output_dir / "charts"
    charts_dir.mkdir(parents=True, exist_ok=True)
    metrics = results.get("metrics") or {}
    paths: Dict[str, Path] = {}

    # --- User Growth ---
    ug = metrics.get("userGrowth")
    if ug and ug.get("buckets"):
        labels = _bucket_labels(ug["buckets"])
        values = [b["userGrowth"] for b in ug["buckets"]]
        fig, ax = plt.subplots(figsize=(10, 4))
        ax.plot(labels, values, marker="o", color="#1f4e79")
        ax.set_title("User Growth — distinct active users per bucket")
        ax.set_xlabel("Bucket")
        ax.set_ylabel("Distinct users")
        ax.tick_params(axis="x", rotation=45)
        fig.tight_layout()
        p = charts_dir / "user_growth.png"
        fig.savefig(p, dpi=140)
        plt.close(fig)
        paths["user_growth"] = p

    # --- Engagement ---
    eng = metrics.get("engagement")
    if eng and eng.get("buckets"):
        labels = _bucket_labels(eng["buckets"])
        values = [_num(b["medianScreenTimeSeconds"]) for b in eng["buckets"]]
        plot_x = [l for l, v in zip(labels, values) if v is not None]
        plot_y = [v for v in values if v is not None]
        fig, ax = plt.subplots(figsize=(10, 4))
        ax.plot(plot_x, plot_y, marker="o", color="#2e7d32")
        ax.set_title("Engagement — median screenTimeSeconds (fully-contained plays)")
        ax.set_xlabel("Bucket")
        ax.set_ylabel("Median screen time (seconds)")
        ax.tick_params(axis="x", rotation=45)
        fig.tight_layout()
        p = charts_dir / "engagement.png"
        fig.savefig(p, dpi=140)
        plt.close(fig)
        paths["engagement"] = p

    # --- Retention families ---
    for key, title in (
        ("strictRetention", "Strict Retention (%)"),
        ("cumulativeRetention", "Cumulative Retention (%)"),
        ("consecutiveRetention", "Consecutive Retention (%)"),
        ("rollingRetention", "Rolling Retention (%)"),
    ):
        ret = metrics.get(key)
        if not ret or not ret.get("buckets"):
            continue
        labels = _bucket_labels(ret["buckets"])
        horizons = sorted(
            ret["buckets"][0]["horizons"].keys(),
            key=lambda h: int(h[1:]),
        )
        fig, ax = plt.subplots(figsize=(10, 4))
        for h in horizons:
            ys = [_num(b["horizons"][h]["retentionPercent"]) for b in ret["buckets"]]
            xs = [l for l, y in zip(labels, ys) if y is not None]
            yy = [y for y in ys if y is not None]
            if yy:
                ax.plot(xs, yy, marker="o", label=h)
        ax.set_title(title)
        ax.set_xlabel("Bucket")
        ax.set_ylabel("Retention (%)")
        ax.legend()
        ax.tick_params(axis="x", rotation=45)
        fig.tight_layout()
        p = charts_dir / f"{key}.png"
        fig.savefig(p, dpi=140)
        plt.close(fig)
        paths[key] = p

    # --- Play State ---
    ps = metrics.get("playState")
    if ps and ps.get("buckets"):
        labels = _bucket_labels(ps["buckets"])
        loaded = [b["loadedCount"] for b in ps["buckets"]]
        solving = [b["solvingCount"] for b in ps["buckets"]]
        completed = [b["completedCount"] for b in ps["buckets"]]
        fig, ax = plt.subplots(figsize=(10, 4))
        x = range(len(labels))
        ax.bar(x, loaded, label="loaded", color="#90a4ae")
        ax.bar(x, solving, bottom=loaded, label="solving", color="#fb8c00")
        bottom2 = [a + b for a, b in zip(loaded, solving)]
        ax.bar(x, completed, bottom=bottom2, label="completed", color="#43a047")
        ax.set_xticks(list(x))
        ax.set_xticklabels(labels, rotation=45, ha="right")
        ax.set_title("Play State — counts by outcome (updatedTimestamp attribution)")
        ax.set_xlabel("Bucket")
        ax.set_ylabel("Play count")
        ax.legend()
        fig.tight_layout()
        p = charts_dir / "play_state.png"
        fig.savefig(p, dpi=140)
        plt.close(fig)
        paths["play_state"] = p

    # --- Interactive Plotly dashboard ---
    html_path = _write_plotly_dashboard(results, charts_dir / "dashboard.html")
    paths["dashboard"] = html_path
    return paths


def _write_plotly_dashboard(results: Dict[str, Any], path: Path) -> Path:
    metrics = results.get("metrics") or {}
    fig = make_subplots(
        rows=3,
        cols=2,
        subplot_titles=(
            "User Growth (distinct users)",
            "Engagement median screenTimeSeconds",
            "Strict Retention %",
            "Cumulative Retention %",
            "Consecutive vs Rolling Retention %",
            "Play State counts",
        ),
        vertical_spacing=0.12,
        horizontal_spacing=0.08,
    )

    ug = metrics.get("userGrowth")
    if ug and ug.get("buckets"):
        fig.add_trace(
            go.Scatter(
                x=_bucket_labels(ug["buckets"]),
                y=[b["userGrowth"] for b in ug["buckets"]],
                name="userGrowth",
                mode="lines+markers",
            ),
            row=1,
            col=1,
        )

    eng = metrics.get("engagement")
    if eng and eng.get("buckets"):
        ys = [_num(b["medianScreenTimeSeconds"]) for b in eng["buckets"]]
        xs = _bucket_labels(eng["buckets"])
        fig.add_trace(
            go.Scatter(
                x=[x for x, y in zip(xs, ys) if y is not None],
                y=[y for y in ys if y is not None],
                name="medianScreenTime",
                mode="lines+markers",
            ),
            row=1,
            col=2,
        )

    for col, key in ((1, "strictRetention"), (2, "cumulativeRetention")):
        ret = metrics.get(key)
        if not ret or not ret.get("buckets"):
            continue
        labels = _bucket_labels(ret["buckets"])
        for h in sorted(ret["buckets"][0]["horizons"].keys(), key=lambda s: int(s[1:])):
            ys = [_num(b["horizons"][h]["retentionPercent"]) for b in ret["buckets"]]
            fig.add_trace(
                go.Scatter(
                    x=[x for x, y in zip(labels, ys) if y is not None],
                    y=[y for y in ys if y is not None],
                    name=f"{key}:{h}",
                    mode="lines+markers",
                ),
                row=2,
                col=col,
            )

    for key, dash in (("consecutiveRetention", "solid"), ("rollingRetention", "dot")):
        ret = metrics.get(key)
        if not ret or not ret.get("buckets"):
            continue
        labels = _bucket_labels(ret["buckets"])
        for h in sorted(ret["buckets"][0]["horizons"].keys(), key=lambda s: int(s[1:])):
            ys = [_num(b["horizons"][h]["retentionPercent"]) for b in ret["buckets"]]
            fig.add_trace(
                go.Scatter(
                    x=[x for x, y in zip(labels, ys) if y is not None],
                    y=[y for y in ys if y is not None],
                    name=f"{key}:{h}",
                    mode="lines+markers",
                    line=dict(dash=dash),
                ),
                row=3,
                col=1,
            )

    ps = metrics.get("playState")
    if ps and ps.get("buckets"):
        labels = _bucket_labels(ps["buckets"])
        fig.add_trace(
            go.Bar(x=labels, y=[b["loadedCount"] for b in ps["buckets"]], name="loaded"),
            row=3,
            col=2,
        )
        fig.add_trace(
            go.Bar(
                x=labels, y=[b["solvingCount"] for b in ps["buckets"]], name="solving"
            ),
            row=3,
            col=2,
        )
        fig.add_trace(
            go.Bar(
                x=labels,
                y=[b["completedCount"] for b in ps["buckets"]],
                name="completed",
            ),
            row=3,
            col=2,
        )
        fig.update_layout(barmode="stack")

    fig.update_layout(
        height=1100,
        title_text=(
            f"Retention Pipeline — {results.get('grain')} grain · "
            f"{results.get('timelineStart')} → {results.get('timelineEnd')} "
            f"({results.get('timezone')})"
        ),
        legend=dict(orientation="h", yanchor="bottom", y=1.02),
    )
    fig.write_html(str(path), include_plotlyjs="cdn")
    return path


def load_and_render(results_path: Path, output_dir: Path = OUTPUT_DIR) -> Dict[str, Path]:
    results = json.loads(results_path.read_text(encoding="utf-8"))
    return render_visualizations(results, output_dir)
