#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
One-off script to backfill history.json from PCS rider results.

Fetches each rider's 2026 race results from ProCyclingStats, then
reconstructs weekly standings from the start of the season.

Requirements (same as update_league.py plus one extra):
    pip install cloudscraper pyyaml procyclingstats

Usage:
    python backfill_history.py              # writes history.json
    python backfill_history.py --dry-run    # preview without writing
"""

import argparse
import json
import os
import re
import sys
import time
import unicodedata
from collections import defaultdict
from datetime import datetime, timedelta, timezone

import cloudscraper
import yaml

# We use cloudscraper to fetch HTML, then feed it to the PCS library
# for parsing. This avoids Cloudflare blocks.
from procyclingstats import RiderResults


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

BASE_URL = "https://www.procyclingstats.com"
SEASON = 2026
# Generate a snapshot every Monday from early January
SNAPSHOT_DAY = 0  # Monday = 0
FIRST_SNAPSHOT = datetime(SEASON, 1, 13, tzinfo=timezone.utc)  # First Monday with possible results
REQUEST_DELAY = 2.0  # seconds between PCS requests -- be polite

# Riders whose PCS URL slug doesn't match the auto-generated one
SLUG_OVERRIDES = {
    "PIDCOCK Thomas": "tom-pidcock",
    "AYUSO Juan": "juan-ayuso-pesquera",
    "SKJELMOSE Mattias": "mattias-skjelmose-jensen",
    "O'CONNOR Ben": "ben-o-connor",
    "WRIGHT Fred": "alfred-wright",
}


def load_config(path):
    with open(path) as f:
        return yaml.safe_load(f)


# ---------------------------------------------------------------------------
# Name → PCS URL slug
# ---------------------------------------------------------------------------

CHAR_MAP = {
    "ø": "o", "Ø": "O", "æ": "ae", "Æ": "AE",
    "ð": "d", "Ð": "D", "ł": "l", "Ł": "L",
    "ß": "ss", "þ": "th", "Þ": "Th", "'": "", "'": "",
}


def name_to_slug(name):
    """Convert 'EVENEPOEL Remco' → 'remco-evenepoel'.

    Handles accented characters, multi-part surnames, etc.
    """
    # Replace special characters that NFD can't decompose
    for char, replacement in CHAR_MAP.items():
        name = name.replace(char, replacement)

    # Strip combining accents: é→e, č→c, ž→z, etc.
    normalized = unicodedata.normalize("NFD", name)
    ascii_name = "".join(c for c in normalized if unicodedata.category(c) != "Mn")

    # PCS format: "firstname-lastname" all lowercase
    parts = ascii_name.strip().split()
    if len(parts) < 2:
        return ascii_name.lower().replace(" ", "-")

    # First token(s) are surname (uppercase in our config), rest is first name
    # e.g. "VAN AERT Wout" → surname=["VAN","AERT"], first=["Wout"]
    # or  "EVENEPOEL Remco" → surname=["EVENEPOEL"], first=["Remco"]
    surname_parts = []
    first_parts = []
    hit_lowercase = False
    for p in parts:
        if not hit_lowercase and p == p.upper() and p.isalpha():
            surname_parts.append(p)
        else:
            hit_lowercase = True
            first_parts.append(p)

    # Some names may be all uppercase — fall back
    if not first_parts:
        # Assume last part is first name
        first_parts = [surname_parts.pop()]

    slug = "-".join(first_parts + surname_parts).lower()
    # Clean up any double hyphens or special chars
    slug = re.sub(r"[^a-z0-9-]", "", slug)
    slug = re.sub(r"-+", "-", slug).strip("-")
    return slug


# ---------------------------------------------------------------------------
# Fetch rider results
# ---------------------------------------------------------------------------

def fallback_parse_results(html):
    """Parse results directly from HTML when the PCS library can't handle the page.

    Looks for table rows containing dates (DD.MM) and PCS points.
    """
    parsed = []
    rows = re.findall(r'<tr[^>]*>(.*?)</tr>', html, re.DOTALL)
    for row in rows:
        tds = re.findall(r'<td[^>]*>(.*?)</td>', row, re.DOTALL)
        # Look for a date cell (DD.MM or YYYY-MM-DD)
        date_str = None
        points = 0
        stage_name = ""
        for td in tds:
            td_text = re.sub(r'<[^>]+>', '', td).strip()
            # Match DD.MM format
            dm = re.match(r'^(\d{2})\.(\d{2})$', td_text)
            if dm:
                date_str = f"{SEASON}-{dm.group(2)}-{dm.group(1)}"
                continue
            # Match YYYY-MM-DD format
            ymd = re.match(r'^(\d{4}-\d{2}-\d{2})$', td_text)
            if ymd:
                date_str = ymd.group(1)
                continue
        # Look for PCS points in the last few columns
        # PCS typically puts points in the last or second-to-last td
        for td in reversed(tds[-3:]):
            td_text = re.sub(r'<[^>]+>', '', td).strip()
            if td_text.isdigit() and int(td_text) > 0:
                points = int(td_text)
                break
        # Get stage name from links
        name_match = re.search(r'<a[^>]+>([^<]+)</a>', row)
        if name_match:
            stage_name = name_match.group(1).strip()

        if date_str and points and date_str.startswith(str(SEASON)):
            parsed.append({
                "date": date_str,
                "pcs_points": points,
                "stage_name": stage_name,
            })
    return parsed


def fetch_rider_results(session, rider_name, aliases, debug=False):
    """Fetch a rider's 2026 results from PCS.

    Returns list of {'date': 'YYYY-MM-DD', 'pcs_points': int, 'stage_name': str}
    """
    display_name = aliases.get(rider_name, rider_name)
    slug = SLUG_OVERRIDES.get(rider_name, name_to_slug(display_name))
    url = f"{BASE_URL}/rider/{slug}/results"

    try:
        r = session.get(url, timeout=30)
        if r.status_code != 200:
            if debug:
                print(f"\n    DEBUG: HTTP {r.status_code} for {url}")
            return []

        if debug:
            print(f"\n    DEBUG: Got {len(r.text)} bytes from {url}")

        # Try the PCS library first
        parsed = None
        results_url = f"rider/{slug}/results"
        try:
            rr = RiderResults(results_url, html=r.text, update_html=False)
            raw = rr.results("date", "pcs_points", "stage_name")
            if debug:
                print(f"    DEBUG: Library parsed {len(raw)} raw results")
            parsed = []
            for row in raw:
                pts = row.get("pcs_points", 0)
                date = row.get("date", "")
                if pts and date and date.startswith(str(SEASON)):
                    parsed.append({
                        "date": date,
                        "pcs_points": int(pts),
                        "stage_name": row.get("stage_name", ""),
                    })
        except Exception as e:
            if debug:
                print(f"    DEBUG: Library failed ({e}), trying fallback parser")

        # If the library failed or returned nothing, try fallback regex parsing
        if parsed is None:
            parsed = fallback_parse_results(r.text)
            if debug:
                print(f"    DEBUG: Fallback parsed {len(parsed)} results")

        return parsed

    except Exception as e:
        print(f"\n    Error fetching {slug}: {e}")
        return []


# ---------------------------------------------------------------------------
# Build timeline
# ---------------------------------------------------------------------------

def build_weekly_history(teams, rider_results):
    """Build weekly history snapshots from rider race results."""

    # Determine snapshot dates: every Monday from FIRST_SNAPSHOT to now
    now = datetime.now(timezone.utc)
    snapshot_dates = []
    d = FIRST_SNAPSHOT
    while d <= now:
        snapshot_dates.append(d)
        d += timedelta(days=7)

    # Also add today if it's not a Monday (to capture the latest state)
    if snapshot_dates and snapshot_dates[-1].date() != now.date():
        snapshot_dates.append(now)

    print(f"\nGenerating {len(snapshot_dates)} weekly snapshots "
          f"({snapshot_dates[0].strftime('%Y-%m-%d')} → {snapshot_dates[-1].strftime('%Y-%m-%d')})")

    history = []
    for snap_date in snapshot_dates:
        snap_str = snap_date.strftime("%Y-%m-%d")

        teams_snapshot = {}
        all_totals = []

        for manager, riders in teams.items():
            rider_details = []
            team_total = 0

            for rider in riders:
                # Sum PCS points for this rider up to snap_date
                cumulative = 0
                for result in rider_results.get(rider, []):
                    if result["date"] <= snap_str:
                        cumulative += result["pcs_points"]

                rider_details.append({"rider": rider, "points": cumulative})
                team_total += cumulative

            teams_snapshot[manager] = {
                "total": team_total,
                "rank": 0,  # filled in below
                "banked": 0,
                "riders": rider_details,
            }
            all_totals.append((manager, team_total))

        # Assign ranks
        all_totals.sort(key=lambda x: x[1], reverse=True)
        for rank, (manager, _) in enumerate(all_totals, start=1):
            teams_snapshot[manager]["rank"] = rank

        # Skip snapshots where everyone is still at zero
        if all(t["total"] == 0 for t in teams_snapshot.values()):
            continue

        history.append({"date": snap_str, "teams": teams_snapshot})

    return history


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Backfill fantasy cycling history from PCS")
    parser.add_argument("--dry-run", action="store_true", help="Preview without writing history.json")
    args = parser.parse_args()

    base_dir = os.path.dirname(os.path.abspath(__file__))
    config_path = os.path.join(base_dir, "league_config.yaml")
    history_path = os.path.join(base_dir, "history.json")

    # Load config
    config = load_config(config_path)
    aliases = config.get("aliases", {})
    teams = config["first_half"]  # Backfill uses first-half rosters

    # Collect all unique riders
    all_riders = []
    for manager, riders in teams.items():
        for rider in riders:
            if rider not in all_riders:
                all_riders.append(rider)

    print(f"League has {len(teams)} teams, {len(all_riders)} unique riders")
    print(f"Fetching 2026 results from ProCyclingStats...\n")

    # Fetch results for each rider
    session = cloudscraper.create_scraper()
    rider_results = {}
    errors = []

    for i, rider in enumerate(all_riders, 1):
        slug = SLUG_OVERRIDES.get(rider, name_to_slug(aliases.get(rider, rider)))
        print(f"  [{i}/{len(all_riders)}] {rider} ({slug})...", end=" ", flush=True)

        results = fetch_rider_results(session, rider, aliases)

        if results:
            total_pts = sum(r["pcs_points"] for r in results)
            print(f"✓ {len(results)} results, {total_pts:,} pts")
            rider_results[rider] = results
        else:
            print("— no results found")
            errors.append(rider)

        # Be polite to PCS
        if i < len(all_riders):
            time.sleep(REQUEST_DELAY)

    print(f"\nFetched results for {len(rider_results)}/{len(all_riders)} riders")
    if errors:
        print(f"No results for: {', '.join(errors)}")

    # Build weekly history
    history = build_weekly_history(teams, rider_results)

    if not history:
        print("No history generated (all zeros). Something may be wrong.")
        return

    # Print summary
    print(f"\n{'='*60}")
    print(f"Generated {len(history)} snapshots\n")
    print(f"{'Date':<14} {'Leader':<22} {'Pts':>7}  {'2nd':>18} {'3rd':>18}")
    print("-" * 80)
    for h in history:
        top3 = sorted(h["teams"].items(), key=lambda x: x[1]["total"], reverse=True)[:3]
        row = f"{h['date']:<14} {top3[0][0]:<22} {top3[0][1]['total']:>7,}"
        if len(top3) > 1:
            row += f"  {top3[1][0]:>10} {top3[1][1]['total']:>6,}"
        if len(top3) > 2:
            row += f"  {top3[2][0]:>10} {top3[2][1]['total']:>6,}"
        print(row)

    if args.dry_run:
        print(f"\n[DRY RUN] Would write {len(history)} snapshots to {history_path}")
        return

    # Write history.json
    with open(history_path, "w") as f:
        json.dump(history, f, indent=2, ensure_ascii=False)
    print(f"\nWritten: {history_path} ({len(history)} snapshots)")
    print("\nNext steps:")
    print("  1. Review the output above to check it looks right")
    print("  2. Run 'python update_league.py' to regenerate index.html with charts")
    print("  3. Commit history.json and docs/index.html")


if __name__ == "__main__":
    main()
