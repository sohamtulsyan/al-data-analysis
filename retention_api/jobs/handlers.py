"""Thin job handlers — call existing pipeline and script entry points."""

from __future__ import annotations

import json
import os
from datetime import date
from pathlib import Path
from typing import Any, Dict, List
from zoneinfo import ZoneInfo

from retention_pipeline.analysis import run_analysis
from retention_pipeline.api.backfill import run_daily_cron, run_historical_backfill
from retention_pipeline.cleaning.pipeline import discover_csv_days, prepare_analysis
from retention_pipeline.metrics.errors import GrainNotAllowedError
from retention_pipeline.signup_analysis import run_signup_fraction
from retention_pipeline.visualize import load_and_render

from retention_api.jobs.script_loader import load_script_module
from retention_api.schemas.jobs import JobType
from retention_api.services.config_merge import (
    load_fetch_config,
    resolve_analysis_params,
    resolve_users_csv,
    resolve_window_params,
)
from retention_api.services.paths import data_dir, output_dir


def api_should_generate_charts() -> bool:
    """PNG generation uses matplotlib; disabled by default on API (use JSON + Recharts)."""
    return os.environ.get("RETENTION_API_GENERATE_CHARTS", "0").strip().lower() in (
        "1",
        "true",
        "yes",
    )


def _artifacts(
    paths: Dict[str, str],
    charts: List[str] | None = None,
) -> Dict[str, Any]:
    return {
        "paths": paths,
        "charts": charts or [],
    }


def run_job_handler(job_type: JobType, params: Dict[str, Any]) -> Dict[str, Any]:
    ddir = Path(params.get("dataDir") or data_dir())
    odir = Path(params.get("outputDir") or output_dir())

    if job_type == JobType.backfill:
        if not _credentials_ok():
            raise ValueError("CLIENT_ID and CLIENT_SECRET must be set in environment.")
        config = load_fetch_config()
        paths = run_historical_backfill(config, directory=ddir)
        return _artifacts({"fetchedDays": str(len(paths)), "dataDir": str(ddir)})

    if job_type == JobType.daily:
        if not _credentials_ok():
            raise ValueError("CLIENT_ID and CLIENT_SECRET must be set in environment.")
        config = load_fetch_config()
        path = run_daily_cron(config, directory=ddir)
        return _artifacts(
            {
                "dataDir": str(ddir),
                "dailyCsv": str(path) if path else "",
            }
        )

    if job_type == JobType.analyze:
        start, end, grain, tz_name, strict_h, window_h = resolve_analysis_params(params)
        try:
            result = run_analysis(
                timeline_start=start.date(),
                timeline_end=end.date(),
                grain=grain,
                timezone=tz_name,
                data_dir=ddir,
                output_dir=odir,
                strict_horizons=strict_h,
                window_horizons=window_h,
            )
        except GrainNotAllowedError as exc:
            raise ValueError(json.dumps(exc.to_dict())) from exc
        results_path = odir / "analysis_results.json"
        return _artifacts(
            {
                "analysisResults": str(results_path),
                "distinctUsers": str(result.get("distinctUsersInMatrix", "")),
            }
        )

    if job_type == JobType.pipeline:
        skip_fetch = bool(params.get("skipFetch", False))
        if not skip_fetch:
            if not _credentials_ok():
                raise ValueError("CLIENT_ID and CLIENT_SECRET must be set in environment.")
            config = load_fetch_config()
            run_historical_backfill(config, directory=ddir)
        start, end, grain, tz_name, strict_h, window_h = resolve_analysis_params(params)
        try:
            run_analysis(
                timeline_start=start.date(),
                timeline_end=end.date(),
                grain=grain,
                timezone=tz_name,
                data_dir=ddir,
                output_dir=odir,
                strict_horizons=strict_h,
                window_horizons=window_h,
            )
        except GrainNotAllowedError as exc:
            raise ValueError(json.dumps(exc.to_dict())) from exc
        results_path = odir / "analysis_results.json"
        chart_paths: Dict[str, Path] = {}
        charts_list: List[str] = []
        if api_should_generate_charts():
            chart_paths = load_and_render(results_path, odir)
            charts_list = [str(v) for v in chart_paths.values()]
        return _artifacts(
            {
                "analysisResults": str(results_path),
                **{k: str(v) for k, v in chart_paths.items()},
            },
            charts=charts_list,
        )

    if job_type == JobType.signup_fraction:
        start, end, _grain, tz_name, _, _ = resolve_analysis_params(params)
        users_csv = resolve_users_csv(params)
        if not users_csv.is_file():
            raise FileNotFoundError(f"Users CSV not found: {users_csv}")
        try:
            result = run_signup_fraction(
                users_csv=users_csv,
                timeline_start=start.date(),
                timeline_end=end.date(),
                timezone=tz_name,
                data_dir=ddir,
                output_dir=odir,
                generate_chart=api_should_generate_charts(),
            )
        except GrainNotAllowedError as exc:
            raise ValueError(json.dumps(exc.to_dict())) from exc
        return _artifacts(
            {
                "resultsPath": result.get("resultsPath", ""),
                "chartPath": result.get("chartPath", ""),
            },
            charts=[result.get("chartPath", "")] if result.get("chartPath") else [],
        )

    if job_type == JobType.nuu_counts:
        mod = load_script_module("compute_nuu.py")
        ws = params.get("windowStart")
        we = params.get("windowEnd")
        tz_name = params.get("timezone")
        if not ws or not we:
            ws, we, tz_name, _grain, _, _ = resolve_window_params(params)
        argv = [
            "--data-dir",
            str(ddir),
            "--output-dir",
            str(odir),
            "--window-start",
            ws,
            "--window-end",
            we,
        ]
        if tz_name:
            argv.extend(["--timezone", tz_name])
        rc = mod.main(argv)
        if rc != 0:
            raise RuntimeError(f"compute_nuu.py exited with code {rc}")
        return _artifacts(
            {
                "nuuDaily": str(odir / "nuu_daily.csv"),
                "nuuWeekly": str(odir / "nuu_weekly.csv"),
                "nuuMonthly": str(odir / "nuu_monthly.csv"),
            }
        )

    if job_type == JobType.nuu_retention:
        return _run_nuu_retention(ddir, odir, params)

    if job_type == JobType.nuu_signup_fraction:
        mod = load_script_module("plot_nuu_signup_fraction.py")
        users_csv = resolve_users_csv(params)
        ws, we, tz_name, _grain, _, _ = resolve_window_params(params)
        argv = [
            "--users-csv",
            str(users_csv),
            "--nuu-weekly",
            str(odir / "nuu_weekly.csv"),
            "--window-start",
            ws,
            "--window-end",
            we,
            "--output-dir",
            str(odir),
        ]
        if not api_should_generate_charts():
            argv.append("--skip-chart")
        rc = mod.main(argv)
        if rc != 0:
            raise RuntimeError(f"plot_nuu_signup_fraction.py exited with code {rc}")
        chart = odir / "charts" / "nuu_signup_fraction.png"
        return _artifacts(
            {
                "csv": str(odir / "nuu_signup_fraction.csv"),
                "chart": str(chart),
            },
            charts=[str(chart)] if chart.is_file() else [],
        )

    raise ValueError(f"Unknown job type: {job_type}")


def _credentials_ok() -> bool:
    from retention_api.services.config_merge import client_credentials_configured

    return client_credentials_configured()


def _run_nuu_retention(
    ddir: Path,
    odir: Path,
    params: Dict[str, Any],
) -> Dict[str, Any]:
    """Mirror scripts/plot_nuu_retention.main() with configurable horizons."""
    mod = load_script_module("plot_nuu_retention.py")
    ws, we, tz_name, grain, strict_h, window_h = resolve_window_params(params)
    window_start = date.fromisoformat(ws)
    window_end = date.fromisoformat(we)

    available = discover_csv_days(ddir)
    if not available:
        raise FileNotFoundError(f"No Daily Play Data CSVs in {ddir}")

    timeframe_start = available[0]
    timeframe_end = max(available[-1], window_end)
    tz = ZoneInfo(tz_name)

    _plays, matrix, _dq = prepare_analysis(ddir, timeframe_start, timeframe_end, tz)
    try:
        mod.validate_grain(window_start, window_end, grain)
    except Exception as exc:
        to_dict = getattr(exc, "to_dict", None)
        if callable(to_dict):
            raise ValueError(json.dumps(to_dict())) from exc
        raise

    nuu_d0 = mod._all_nuu_d0(matrix, window_start, window_end)
    strict = mod._compute_family(
        name="nuuStrictRetention",
        matrix=matrix,
        nuu_d0=nuu_d0,
        window_start=window_start,
        window_end=window_end,
        grain=grain,
        horizons=strict_h,
        predicate=mod._strict,
    )
    cumulative = mod._compute_family(
        name="nuuCumulativeRetention",
        matrix=matrix,
        nuu_d0=nuu_d0,
        window_start=window_start,
        window_end=window_end,
        grain=grain,
        horizons=window_h,
        predicate=mod._cumulative,
    )
    consecutive = mod._compute_family(
        name="nuuConsecutiveRetention",
        matrix=matrix,
        nuu_d0=nuu_d0,
        window_start=window_start,
        window_end=window_end,
        grain=grain,
        horizons=window_h,
        predicate=mod._consecutive,
    )

    payload = {
        "windowStart": window_start.isoformat(),
        "windowEnd": window_end.isoformat(),
        "grain": grain,
        "timezone": tz_name,
        "cohort": "newUniqueUsers",
        "metrics": {
            "strictRetention": strict,
            "cumulativeRetention": cumulative,
            "consecutiveRetention": consecutive,
        },
    }
    charts_dir = odir / "charts"
    json_path = odir / "nuu_retention.json"
    odir.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")

    chart_paths: Dict[str, Path] = {}
    charts_list: List[str] = []
    if api_should_generate_charts():
        chart_paths = {
            "strict": mod._plot_retention(
                strict,
                "NUU Strict Retention (%)",
                charts_dir / "nuu_strictRetention.png",
            ),
            "cumulative": mod._plot_retention(
                cumulative,
                "NUU Cumulative Retention (%)",
                charts_dir / "nuu_cumulativeRetention.png",
            ),
            "consecutive": mod._plot_retention(
                consecutive,
                "NUU Consecutive Retention (%)",
                charts_dir / "nuu_consecutiveRetention.png",
            ),
        }
        charts_list = [str(v) for v in chart_paths.values()]

    return _artifacts(
        {
            "nuuRetentionJson": str(json_path),
            **{k: str(v) for k, v in chart_paths.items()},
        },
        charts=charts_list,
    )
