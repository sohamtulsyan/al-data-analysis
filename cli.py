#!/usr/bin/env python3
"""CLI for the Retention Pipeline (PRD Final.md).

Commands:
  backfill          — historical fetch TOTAL_DATA_TIME_START … yesterday (§4)
  daily             — fetch the just-ended calendar day (§5)
  analyze           — clean + compute all metrics over Daily Play Data
  visualize         — render charts from analysis_results.json
  signup-fraction   — weekly signups / active users (%) from users CSV + plays
  run               — backfill (optional) + analyze + visualize
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from retention_pipeline.analysis import run_analysis
from retention_pipeline.api.backfill import run_daily_cron, run_historical_backfill
from retention_pipeline.config import (
    DAILY_PLAY_DATA_DIR,
    OUTPUT_DIR,
    PROJECT_ROOT,
    load_analysis_config,
    load_config,
)
from retention_pipeline.metrics.errors import GrainNotAllowedError
from retention_pipeline.signup_analysis import run_signup_fraction
from retention_pipeline.summary_stats import (
    print_distribution_summaries,
    summarize_analysis_payload,
    summarize_signup_payload,
)
from retention_pipeline.visualize import load_and_render


def _setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def _add_io_args(sp: argparse.ArgumentParser) -> None:
    sp.add_argument(
        "--data-dir",
        default=str(DAILY_PLAY_DATA_DIR),
        help="Daily Play Data directory",
    )
    sp.add_argument(
        "--output-dir",
        default=str(OUTPUT_DIR),
        help="Analysis / chart output directory",
    )
    sp.add_argument("--env", default=None, help="Path to .env file")


def _add_timeline_args(sp: argparse.ArgumentParser) -> None:
    sp.add_argument("--timeline-start", required=True, help="ISO 8601 timelineStart")
    sp.add_argument("--timeline-end", required=True, help="ISO 8601 timelineEnd")
    sp.add_argument(
        "--grain",
        required=True,
        choices=["Daily", "Weekly", "Monthly"],
    )
    sp.add_argument("--timezone", default="UTC")


def cmd_backfill(args: argparse.Namespace) -> int:
    config = load_config(Path(args.env) if args.env else None)
    paths = run_historical_backfill(config, directory=Path(args.data_dir))
    print(f"Fetched {len(paths)} day(s) into {args.data_dir}")
    return 0


def cmd_daily(args: argparse.Namespace) -> int:
    config = load_config(Path(args.env) if args.env else None)
    path = run_daily_cron(config, directory=Path(args.data_dir))
    if path is None:
        print("Daily cron: target day already complete (no-op).")
    else:
        print(f"Daily cron wrote {path}")
    return 0


def cmd_analyze(args: argparse.Namespace) -> int:
    start, end, grain, _tz = load_analysis_config(
        args.timeline_start,
        args.timeline_end,
        args.grain,
        args.timezone,
    )
    try:
        result = run_analysis(
            timeline_start=start.date(),
            timeline_end=end.date(),
            grain=grain,
            timezone=args.timezone,
            data_dir=Path(args.data_dir),
            output_dir=Path(args.output_dir),
        )
    except GrainNotAllowedError as exc:
        print(json.dumps(exc.to_dict(), indent=2))
        return 2
    print(f"Analysis complete. Distinct users: {result['distinctUsersInMatrix']}")
    print(f"Results: {Path(args.output_dir) / 'analysis_results.json'}")
    print_distribution_summaries(summarize_analysis_payload(result))
    return 0


def cmd_visualize(args: argparse.Namespace) -> int:
    results_path = Path(args.results)
    paths = load_and_render(results_path, Path(args.output_dir))
    for name, path in paths.items():
        print(f"{name}: {path}")
    return 0


def cmd_signup_fraction(args: argparse.Namespace) -> int:
    start, end, _grain, _tz = load_analysis_config(
        args.timeline_start,
        args.timeline_end,
        "Weekly",
        args.timezone,
    )
    users_csv = Path(args.users_csv)
    if not users_csv.is_file():
        print(f"Users CSV not found: {users_csv}", file=sys.stderr)
        return 1
    try:
        result = run_signup_fraction(
            users_csv=users_csv,
            timeline_start=start.date(),
            timeline_end=end.date(),
            timezone=args.timezone,
            data_dir=Path(args.data_dir),
            output_dir=Path(args.output_dir),
        )
    except GrainNotAllowedError as exc:
        print(json.dumps(exc.to_dict(), indent=2))
        return 2
    metric = result["metric"]
    print(
        f"Signup fraction: {metric['totalSignups']} signups across "
        f"{len(metric['buckets'])} weeks"
    )
    print(f"Results: {result['resultsPath']}")
    print(f"Chart:   {result['chartPath']}")
    print_distribution_summaries(summarize_signup_payload(result))
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    if not args.skip_fetch:
        config = load_config(Path(args.env) if args.env else None)
        run_historical_backfill(config, directory=Path(args.data_dir))
    rc = cmd_analyze(args)
    if rc != 0:
        return rc
    results_path = Path(args.output_dir) / "analysis_results.json"
    paths = load_and_render(results_path, Path(args.output_dir))
    print("Visualizations:")
    for name, path in paths.items():
        print(f"  {name}: {path}")
    # Distribution summaries already printed by cmd_analyze
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Retention Pipeline (PRD Final.md)")
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="command", required=True)

    b = sub.add_parser("backfill", help="Historical backfill (§4)")
    _add_io_args(b)
    b.set_defaults(func=cmd_backfill)

    d = sub.add_parser("daily", help="Daily cron fetch (§5)")
    _add_io_args(d)
    d.set_defaults(func=cmd_daily)

    a = sub.add_parser("analyze", help="Compute all metrics")
    _add_io_args(a)
    _add_timeline_args(a)
    a.set_defaults(func=cmd_analyze)

    v = sub.add_parser("visualize", help="Render charts from results JSON")
    _add_io_args(v)
    v.add_argument(
        "--results",
        default=str(OUTPUT_DIR / "analysis_results.json"),
    )
    v.set_defaults(func=cmd_visualize)

    s = sub.add_parser(
        "signup-fraction",
        help="Weekly signup fraction = new accounts / active users (%)",
    )
    _add_io_args(s)
    s.add_argument("--timeline-start", default="2026-03-01T00:00:00")
    s.add_argument("--timeline-end", required=True, help="ISO 8601 timelineEnd")
    s.add_argument("--timezone", default="UTC")
    s.add_argument(
        "--users-csv",
        default=str(PROJECT_ROOT / "users_2026_07_16_12_34_42.csv"),
        help="Users export CSV (Email ID is never read)",
    )
    s.set_defaults(func=cmd_signup_fraction)

    r = sub.add_parser("run", help="backfill + analyze + visualize")
    _add_io_args(r)
    _add_timeline_args(r)
    r.add_argument(
        "--skip-fetch",
        action="store_true",
        help="Skip API backfill; analyze existing Daily Play Data only",
    )
    r.set_defaults(func=cmd_run)

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    _setup_logging(args.verbose)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
