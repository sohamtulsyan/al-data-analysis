"""Thin job handlers — call existing pipeline and script entry points."""

from __future__ import annotations

import json
import os
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
        return _run_analysis(ddir, odir, params)

    if job_type == JobType.pipeline:
        skip_fetch = bool(params.get("skipFetch", False))
        if not skip_fetch:
            if not _credentials_ok():
                raise ValueError("CLIENT_ID and CLIENT_SECRET must be set in environment.")
            config = load_fetch_config()
            run_historical_backfill(config, directory=ddir)

        analysis = _run_analysis(ddir, odir, params)
        derived_paths: Dict[str, str] = dict(analysis.get("paths", {}))
        derived_charts: List[str] = list(analysis.get("charts", []))

        for derived in (
            _run_nuu_retention(ddir, odir, params),
            _run_ouu_retention(ddir, odir, params),
            _run_logged_in_retention(ddir, odir, params),
            _run_signup_fraction(ddir, odir, params),
            _run_nuu_counts(ddir, odir, params),
            _run_nuu_signup_fraction(odir, params),
        ):
            derived_paths.update(derived.get("paths", {}))
            derived_charts.extend(derived.get("charts", []))

        return _artifacts(derived_paths, charts=derived_charts)

    if job_type == JobType.signup_fraction:
        return _run_signup_fraction(ddir, odir, params)

    if job_type == JobType.nuu_counts:
        return _run_nuu_counts(ddir, odir, params)

    if job_type == JobType.nuu_retention:
        return _run_nuu_retention(ddir, odir, params)

    if job_type == JobType.ouu_retention:
        return _run_ouu_retention(ddir, odir, params)

    if job_type == JobType.logged_in_retention:
        return _run_logged_in_retention(ddir, odir, params)

    if job_type == JobType.nuu_signup_fraction:
        return _run_nuu_signup_fraction(odir, params)

    raise ValueError(f"Unknown job type: {job_type}")


def _credentials_ok() -> bool:
    from retention_api.services.config_merge import client_credentials_configured

    return client_credentials_configured()


def _require_play_data_csvs(ddir: Path) -> None:
    """Fail fast with a actionable message when Daily Play Data is empty."""
    days = discover_csv_days(ddir)
    if days:
        return
    raise FileNotFoundError(
        f"No Daily Play Data CSVs in {ddir}. "
        "On Render, set DATA_DIR to your persistent disk (e.g. "
        "'/var/data/Daily Play Data') and run the pipeline (or fetch data first) "
        "before analysis. Locally, use cli.py backfill or --skip-fetch "
        "with CSVs under 'Daily Play Data/'."
    )


def _run_analysis(
    ddir: Path,
    odir: Path,
    params: Dict[str, Any],
) -> Dict[str, Any]:
    _require_play_data_csvs(ddir)
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
    chart_paths: Dict[str, Path] = {}
    charts_list: List[str] = []
    if api_should_generate_charts():
        chart_paths = load_and_render(results_path, odir)
        charts_list = [str(v) for v in chart_paths.values()]
    return _artifacts(
        {
            "analysisResults": str(results_path),
            "distinctUsers": str(result.get("distinctUsersInMatrix", "")),
            **{k: str(v) for k, v in chart_paths.items()},
        },
        charts=charts_list,
    )


def _run_signup_fraction(
    ddir: Path,
    odir: Path,
    params: Dict[str, Any],
) -> Dict[str, Any]:
    _require_play_data_csvs(ddir)
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


def _run_nuu_counts(
    ddir: Path,
    odir: Path,
    params: Dict[str, Any],
) -> Dict[str, Any]:
    _require_play_data_csvs(ddir)
    mod = load_script_module("compute_nuu.py")
    start, end, _grain, tz_name, _, _ = resolve_analysis_params(params)
    argv = [
        "--data-dir",
        str(ddir),
        "--output-dir",
        str(odir),
        "--window-start",
        start.date().isoformat(),
        "--window-end",
        end.date().isoformat(),
    ]
    if tz_name:
        argv.extend(["--timezone", tz_name])
    rc = mod.main(argv)
    if rc != 0:
        raise RuntimeError(f"compute_nuu.py exited with code {rc} (data dir: {ddir})")
    return _artifacts(
        {
            "nuuDaily": str(odir / "nuu_daily.csv"),
            "nuuWeekly": str(odir / "nuu_weekly.csv"),
            "nuuMonthly": str(odir / "nuu_monthly.csv"),
        }
    )


def _run_nuu_signup_fraction(
    odir: Path,
    params: Dict[str, Any],
) -> Dict[str, Any]:
    mod = load_script_module("plot_nuu_signup_fraction.py")
    users_csv = resolve_users_csv(params)
    start, end, _grain, _tz_name, _, _ = resolve_analysis_params(params)
    argv = [
        "--users-csv",
        str(users_csv),
        "--nuu-weekly",
        str(odir / "nuu_weekly.csv"),
        "--window-start",
        start.date().isoformat(),
        "--window-end",
        end.date().isoformat(),
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


def _run_nuu_retention(
    ddir: Path,
    odir: Path,
    params: Dict[str, Any],
) -> Dict[str, Any]:
    """Mirror scripts/plot_nuu_retention.main() with configurable horizons."""
    mod = load_script_module("plot_nuu_retention.py")
    start, end, grain, tz_name, strict_h, window_h = resolve_analysis_params(params)
    timeline_start = start.date()
    timeline_end = end.date()

    available = discover_csv_days(ddir)
    if not available:
        _require_play_data_csvs(ddir)  # raises with same message as other jobs

    timeframe_start = available[0]
    timeframe_end = max(available[-1], timeline_end)
    tz = ZoneInfo(tz_name)

    _plays, matrix, _dq = prepare_analysis(ddir, timeframe_start, timeframe_end, tz)
    try:
        mod.validate_grain(timeline_start, timeline_end, grain)
    except Exception as exc:
        to_dict = getattr(exc, "to_dict", None)
        if callable(to_dict):
            raise ValueError(json.dumps(to_dict())) from exc
        raise

    nuu_d0 = mod._all_nuu_d0(matrix, timeline_start, timeline_end)
    strict = mod._compute_family(
        name="nuuStrictRetention",
        matrix=matrix,
        nuu_d0=nuu_d0,
        window_start=timeline_start,
        window_end=timeline_end,
        grain=grain,
        horizons=strict_h,
        predicate=mod._strict,
    )
    cumulative = mod._compute_family(
        name="nuuCumulativeRetention",
        matrix=matrix,
        nuu_d0=nuu_d0,
        window_start=timeline_start,
        window_end=timeline_end,
        grain=grain,
        horizons=window_h,
        predicate=mod._cumulative,
    )
    consecutive = mod._compute_family(
        name="nuuConsecutiveRetention",
        matrix=matrix,
        nuu_d0=nuu_d0,
        window_start=timeline_start,
        window_end=timeline_end,
        grain=grain,
        horizons=window_h,
        predicate=mod._consecutive,
    )

    payload = {
        "timelineStart": timeline_start.isoformat(),
        "timelineEnd": timeline_end.isoformat(),
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


def _run_ouu_retention(
    ddir: Path,
    odir: Path,
    params: Dict[str, Any],
) -> Dict[str, Any]:
    """Mirror scripts/plot_ouu_retention with configurable horizons."""
    mod = load_script_module("plot_ouu_retention.py")
    start, end, grain, tz_name, strict_h, window_h = resolve_analysis_params(params)
    timeline_start = start.date()
    timeline_end = end.date()

    available = discover_csv_days(ddir)
    if not available:
        _require_play_data_csvs(ddir)

    timeframe_start = available[0]
    timeframe_end = max(available[-1], timeline_end)
    tz = ZoneInfo(tz_name)

    _plays, matrix, _dq = prepare_analysis(ddir, timeframe_start, timeframe_end, tz)
    try:
        mod.validate_grain(timeline_start, timeline_end, grain)
    except Exception as exc:
        to_dict = getattr(exc, "to_dict", None)
        if callable(to_dict):
            raise ValueError(json.dumps(to_dict())) from exc
        raise

    payload = mod.build_ouu_retention_payload(
        matrix,
        window_start=timeline_start,
        window_end=timeline_end,
        grain=grain,
        timezone=tz_name,
        strict_horizons=tuple(strict_h),
        window_horizons=tuple(window_h),
    )

    charts_dir = odir / "charts"
    json_path = odir / "ouu_retention.json"
    odir.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")

    chart_paths: Dict[str, Path] = {}
    charts_list: List[str] = []
    if api_should_generate_charts():
        metrics = payload["metrics"]
        chart_paths = {
            "strict": mod._plot_retention(
                metrics["strictRetention"],
                "OUU Strict Retention (%)",
                charts_dir / "ouu_strictRetention.png",
            ),
            "cumulative": mod._plot_retention(
                metrics["cumulativeRetention"],
                "OUU Cumulative Retention (%)",
                charts_dir / "ouu_cumulativeRetention.png",
            ),
            "consecutive": mod._plot_retention(
                metrics["consecutiveRetention"],
                "OUU Consecutive Retention (%)",
                charts_dir / "ouu_consecutiveRetention.png",
            ),
        }
        charts_list = [str(v) for v in chart_paths.values()]

    return _artifacts(
        {
            "ouuRetentionJson": str(json_path),
            **{k: str(v) for k, v in chart_paths.items()},
        },
        charts=charts_list,
    )


def _run_logged_in_retention(
    ddir: Path,
    odir: Path,
    params: Dict[str, Any],
) -> Dict[str, Any]:
    """Mirror scripts/plot_logged_in_retention with configurable horizons."""
    mod = load_script_module("plot_logged_in_retention.py")
    start, end, grain, tz_name, strict_h, window_h = resolve_analysis_params(params)
    timeline_start = start.date()
    timeline_end = end.date()

    available = discover_csv_days(ddir)
    if not available:
        _require_play_data_csvs(ddir)

    timeframe_start = available[0]
    timeframe_end = max(available[-1], timeline_end)
    tz = ZoneInfo(tz_name)

    _plays, matrix, _dq = prepare_analysis(ddir, timeframe_start, timeframe_end, tz)
    try:
        mod.validate_grain(timeline_start, timeline_end, grain)
    except Exception as exc:
        to_dict = getattr(exc, "to_dict", None)
        if callable(to_dict):
            raise ValueError(json.dumps(to_dict())) from exc
        raise

    payload = mod.build_logged_in_retention_payload(
        matrix,
        window_start=timeline_start,
        window_end=timeline_end,
        grain=grain,
        timezone=tz_name,
        strict_horizons=tuple(strict_h),
        window_horizons=tuple(window_h),
    )

    charts_dir = odir / "charts"
    json_path = odir / "logged_in_retention.json"
    odir.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")

    chart_paths: Dict[str, Path] = {}
    charts_list: List[str] = []
    if api_should_generate_charts():
        metrics = payload["metrics"]
        chart_paths = {
            "strict": mod._plot_retention(
                metrics["strictRetention"],
                "Logged-in Strict Retention (%)",
                charts_dir / "logged_in_strictRetention.png",
            ),
            "cumulative": mod._plot_retention(
                metrics["cumulativeRetention"],
                "Logged-in Cumulative Retention (%)",
                charts_dir / "logged_in_cumulativeRetention.png",
            ),
            "consecutive": mod._plot_retention(
                metrics["consecutiveRetention"],
                "Logged-in Consecutive Retention (%)",
                charts_dir / "logged_in_consecutiveRetention.png",
            ),
        }
        charts_list = [str(v) for v in chart_paths.values()]

    return _artifacts(
        {
            "loggedInRetentionJson": str(json_path),
            **{k: str(v) for k, v in chart_paths.items()},
        },
        charts=charts_list,
    )
