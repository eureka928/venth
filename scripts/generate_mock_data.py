#!/usr/bin/env python3
"""
Generate mock data from the Synth API.

Run this script ONCE with your real API key to populate the mock_data/ directory
with real API responses that contributors can develop against locally.

Usage:
    python scripts/generate_mock_data.py --api-key YOUR_KEY
    python scripts/generate_mock_data.py --api-key YOUR_KEY --dry-run
    python scripts/generate_mock_data.py --api-key YOUR_KEY --force
"""

import argparse
import json
import os
import sys
import time

try:
    import requests
except ImportError:
    print("Error: 'requests' package is required. Install with: pip install requests")
    sys.exit(1)

BASE_URL = "https://api.synthdata.co"

ASSETS = ["BTC", "ETH", "SOL", "XAU", "SPY", "NVDA", "TSLA", "AAPL", "GOOGL"]
HORIZONS = ["24h", "1h"]

# Endpoint definitions: (directory_name, url_path, takes_asset, takes_horizon)
ENDPOINTS = [
    ("prediction_percentiles", "/insights/prediction-percentiles", True, True),
    ("volatility", "/insights/volatility", True, True),
    ("option_pricing", "/insights/option-pricing", True, False),
    ("liquidation", "/insights/liquidation", True, False),
    ("lp_bounds", "/insights/lp-bounds", True, False),
    ("lp_probabilities", "/insights/lp-probabilities", True, False),
]

# Polymarket endpoints don't take an asset parameter
POLYMARKET_ENDPOINTS = [
    ("polymarket", "up_down_daily", "/insights/polymarket/up-down/daily"),
    ("polymarket", "up_down_hourly", "/insights/polymarket/up-down/hourly"),
    ("polymarket", "range", "/insights/polymarket/range"),
]


def fetch(api_key: str, path: str, params: dict | None = None) -> dict | list | None:
    """Make an authenticated GET request to the Synth API."""
    headers = {"Authorization": f"Apikey {api_key}"}
    try:
        resp = requests.get(f"{BASE_URL}{path}", headers=headers, params=params, timeout=30)
        resp.raise_for_status()
        return resp.json()
    except requests.exceptions.RequestException as e:
        print(f"  ✗ Error: {e}")
        return None


def save_json(data: dict | list, filepath: str) -> None:
    """Save data as formatted JSON."""
    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    with open(filepath, "w") as f:
        json.dump(data, f, indent=2)
    print(f"  ✓ Saved {filepath}")


def plan_jobs(mock_data_dir: str, force: bool) -> list[dict]:
    """
    Build a list of all API calls to make.
    Each job is a dict with: dir, filename, path, params, description.
    """
    jobs = []

    # Per-asset endpoints
    for dir_name, url_path, takes_asset, takes_horizon in ENDPOINTS:
        if takes_asset and takes_horizon:
            for asset in ASSETS:
                for horizon in HORIZONS:
                    filename = f"{asset}_{horizon}.json"
                    filepath = os.path.join(mock_data_dir, dir_name, filename)
                    if not force and os.path.exists(filepath):
                        continue
                    jobs.append({
                        "dir": dir_name,
                        "filename": filename,
                        "filepath": filepath,
                        "path": url_path,
                        "params": {"asset": asset, "horizon": horizon},
                        "description": f"{dir_name}/{filename}",
                    })
        elif takes_asset:
            for asset in ASSETS:
                filename = f"{asset}.json"
                filepath = os.path.join(mock_data_dir, dir_name, filename)
                if not force and os.path.exists(filepath):
                    continue
                jobs.append({
                    "dir": dir_name,
                    "filename": filename,
                    "filepath": filepath,
                    "path": url_path,
                    "params": {"asset": asset},
                    "description": f"{dir_name}/{filename}",
                })

    # Polymarket endpoints (no asset param)
    for dir_name, file_stem, url_path in POLYMARKET_ENDPOINTS:
        filename = f"{file_stem}.json"
        filepath = os.path.join(mock_data_dir, dir_name, filename)
        if not force and os.path.exists(filepath):
            continue
        jobs.append({
            "dir": dir_name,
            "filename": filename,
            "filepath": filepath,
            "path": url_path,
            "params": {},
            "description": f"{dir_name}/{filename}",
        })

    # Leaderboard (per asset)
    for asset in ASSETS:
        filename = f"latest_{asset}.json"
        filepath = os.path.join(mock_data_dir, "leaderboard", filename)
        if not force and os.path.exists(filepath):
            continue
        jobs.append({
            "dir": "leaderboard",
            "filename": filename,
            "filepath": filepath,
            "path": "/v2/leaderboard/latest",
            "params": {"asset": asset, "days": 14, "limit": 10},
            "description": f"leaderboard/{filename}",
        })

    return jobs


def main():
    parser = argparse.ArgumentParser(description="Generate Synth API mock data")
    parser.add_argument("--api-key", required=True, help="Your Synth API key")
    parser.add_argument("--dry-run", action="store_true", help="Preview what would be fetched without making any API calls")
    parser.add_argument("--force", action="store_true", help="Re-fetch even if files already exist")
    parser.add_argument("--delay", type=float, default=1.0, help="Seconds to wait between API calls (default: 1.0)")
    args = parser.parse_args()

    # Resolve mock_data dir relative to repo root
    script_dir = os.path.dirname(os.path.abspath(__file__))
    repo_root = os.path.dirname(script_dir)
    mock_data_dir = os.path.join(repo_root, "mock_data")

    jobs = plan_jobs(mock_data_dir, args.force)

    if not jobs:
        print("All mock data files already exist. Use --force to re-fetch.")
        return

    print(f"\n{'=' * 60}")
    print(f"  Synth API Mock Data Generator")
    print(f"{'=' * 60}")
    print(f"  API calls to make: {len(jobs)}")
    print(f"  Output directory:  {mock_data_dir}")
    print(f"  Delay between calls: {args.delay}s")
    if args.force:
        print(f"  Mode: FORCE (overwriting existing files)")
    print(f"{'=' * 60}\n")

    if args.dry_run:
        print("DRY RUN — no API calls will be made.\n")
        for job in jobs:
            print(f"  Would fetch: {job['description']}")
            print(f"    URL: {BASE_URL}{job['path']}")
            print(f"    Params: {job['params']}")
            print()
        print(f"Total: {len(jobs)} API calls would be made.")
        return

    # Execute
    success = 0
    failed = 0

    for i, job in enumerate(jobs, 1):
        print(f"[{i}/{len(jobs)}] Fetching {job['description']}...")
        data = fetch(args.api_key, job["path"], job["params"])
        if data is not None:
            save_json(data, job["filepath"])
            success += 1
        else:
            failed += 1

        # Rate limit (skip delay on last job)
        if i < len(jobs):
            time.sleep(args.delay)

    print(f"\n{'=' * 60}")
    print(f"  Done! {success} succeeded, {failed} failed out of {len(jobs)} total.")
    print(f"{'=' * 60}\n")


if __name__ == "__main__":
    main()
