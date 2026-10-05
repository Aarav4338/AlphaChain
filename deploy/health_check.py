#!/usr/bin/env python3
"""
AlphaChain Daemon Heartbeat Monitor & Health Checker.
Inspects persistent heartbeat state, validates timestamp freshness,
and verifies 24/7 daemon vitality.
"""

import sys
import os
import time
import json
import argparse
from pathlib import Path
from datetime import datetime, timezone

DEFAULT_PRODUCTION_PATH = Path("/opt/alphachain/logs/heartbeat.json")
REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_LOCAL_PATH = REPO_ROOT / "logs" / "heartbeat.json"


def resolve_heartbeat_path(arg_path: str = None) -> Path:
    """Resolve heartbeat path prioritizing CLI argument, then production path, then local repo path."""
    if arg_path:
        return Path(arg_path)
    if DEFAULT_PRODUCTION_PATH.exists():
        return DEFAULT_PRODUCTION_PATH
    if DEFAULT_LOCAL_PATH.exists():
        return DEFAULT_LOCAL_PATH
    if Path("logs/heartbeat.json").exists():
        return Path("logs/heartbeat.json")
    return DEFAULT_PRODUCTION_PATH


def parse_args():
    parser = argparse.ArgumentParser(description="AlphaChain Heartbeat Health Checker")
    parser.add_argument(
        "--file",
        type=str,
        default=None,
        help="Path to heartbeat.json (defaults to /opt/alphachain/logs/heartbeat.json or ./logs/heartbeat.json)",
    )
    parser.add_argument(
        "--max-lag",
        type=int,
        default=180,
        help="Maximum allowable heartbeat age in seconds before alerting (default: 180s / 3 minutes)",
    )
    parser.add_argument(
        "--json-output",
        action="store_true",
        help="Output health results in JSON format for automated monitoring ingest",
    )
    return parser.parse_args()


def check_health(heartbeat_path: Path, max_lag_seconds: int = 180) -> dict:
    now_epoch = time.time()
    result = {
        "status": "UNKNOWN",
        "current_time_utc": datetime.now(timezone.utc).isoformat(),
        "heartbeat_file": str(heartbeat_path),
        "file_exists": False,
        "lag_seconds": None,
        "details": {},
        "message": "",
        "exit_code": 0,
    }

    if not heartbeat_path.exists():
        result["status"] = "MISSING"
        result["message"] = f"Heartbeat file does not exist at {heartbeat_path}."
        result["exit_code"] = 2
        return result

    result["file_exists"] = True

    try:
        with open(heartbeat_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        result["status"] = "CRITICAL"
        result["message"] = f"Failed to parse heartbeat JSON: {e}"
        result["exit_code"] = 2
        return result

    result["details"] = data
    epoch_timestamp = data.get("epoch_seconds") or data.get("timestamp_epoch")

    if epoch_timestamp is None:
        # Fallback to parsing timestamp_utc if present
        iso_str = data.get("timestamp_utc")
        if iso_str:
            try:
                dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
                epoch_timestamp = dt.timestamp()
            except Exception:
                pass

    if epoch_timestamp is None:
        # Final fallback to file modification time
        epoch_timestamp = os.path.getmtime(heartbeat_path)

    lag = max(0.0, now_epoch - float(epoch_timestamp))
    result["lag_seconds"] = round(lag, 1)

    cb_status = data.get("circuit_breaker", "UNKNOWN")
    regime = data.get("regime", "UNKNOWN")
    equity = data.get("equity", 0.0)
    free_usd = data.get("free_usd", 0.0)
    positions_count = data.get("positions_count", 0)

    result["circuit_breaker"] = cb_status

    if lag > max_lag_seconds:
        result["status"] = "STALE"
        result["message"] = (
            f"Heartbeat is STALE! Lag is {lag:.1f}s, exceeding maximum allowed threshold of {max_lag_seconds}s."
        )
        result["exit_code"] = 2
    elif lag > (max_lag_seconds / 2):
        result["status"] = "WARNING"
        result["message"] = f"Heartbeat is delayed. Lag is {lag:.1f}s (threshold: {max_lag_seconds}s)."
        result["exit_code"] = 1
    elif cb_status == "TRIPPED":
        result["status"] = "WARNING"
        result["message"] = f"Daemon is active (Lag: {lag:.1f}s), but Circuit Breaker is TRIPPED!"
        result["exit_code"] = 1
    else:
        result["status"] = "HEALTHY"
        result["message"] = (
            f"Daemon is healthy. Lag: {lag:.1f}s. Regime: {regime}, "
            f"Equity: ${equity:,.2f}, Free USD: ${free_usd:,.2f}, Positions: {positions_count}, CB: {cb_status}"
        )
        result["exit_code"] = 0

    return result


def main():
    args = parse_args()
    heartbeat_path = resolve_heartbeat_path(args.file)
    report = check_health(heartbeat_path, args.max_lag)

    if args.json_output:
        print(json.dumps(report, indent=2))
    else:
        status = report["status"]
        if status == "HEALTHY":
            print(f"✅ [{status}] {report['message']}")
        elif status == "WARNING":
            print(f"⚠️ [{status}] {report['message']}")
        else:
            print(f"❌ [{status}] {report['message']}")

    sys.exit(report["exit_code"])


if __name__ == "__main__":
    main()
