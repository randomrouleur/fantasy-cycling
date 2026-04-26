#!/usr/bin/env python3
"""Fantasy Cycling League — fetch PCS rankings and generate league table."""

import argparse
import csv
import json
import os
import re
from datetime import datetime, timedelta, timezone

import cloudscraper
import yaml

BASE_URL = "https://www.procyclingstats.com"


# ---------------------------------------------------------------------------
# Config loading
# ---------------------------------------------------------------------------

def load_config(path: str) -> dict:
    """Load league configuration from YAML."""
    with open(path) as f:
        return yaml.safe_load(f)


def get_active_teams(config: dict) -> dict[str, list[str]]:
    """Return the currently active rosters based on transfer status."""
    if config["transfers_done"] and config.get("second_half"):
        return config["second_half"]
    return config["first_half"]


def build_rider_to_manager(teams: dict[str, list[str]]) -> dict[str, str]:
    """Build reverse lookup: rider name → manager."""
    lookup = {}
    for manager, riders in teams.items():
        for rider in riders:
            lookup[rider] = manager
    return lookup


# ---------------------------------------------------------------------------
# Snapshot (mid-season baseline)
# ---------------------------------------------------------------------------

def load_snapshot(path: str) -> dict[str, int]:
    """Load mid-season snapshot: rider → points at snapshot time."""
    snapshot = {}
    with open(path, newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            snapshot[row["rider"]] = int(row["points"])
    return snapshot


def write_snapshot(ranking: dict[str, dict], path: str):
    """Write current PCS rankings as a snapshot CSV."""
    rows = []
    for name, info in ranking.items():
        rows.append({"rider": name, "points": info["points"]})
    rows.sort(key=lambda x: x["rider"])
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["rider", "points"])
        writer.writeheader()
        writer.writerows(rows)
    print(f"Written snapshot: {path}")


# ---------------------------------------------------------------------------
# PCS scraping
# ---------------------------------------------------------------------------

def parse_ranking_page(html: str) -> list[dict]:
    """Parse rider rows from a PCS season ranking HTML page.

    Season ranking rows have 6 columns:
      0: rank, 1: prev_rank, 2: delta, 3: rider, 4: team, 5: points
    """
    riders = []
    rows = re.findall(r'<tr class="[^"]*">(.*?)</tr>', html, re.DOTALL)
    for row in rows:
        tds = re.findall(r'<td[^>]*>(.*?)</td>', row, re.DOTALL)
        if len(tds) < 6:
            continue
        try:
            rank = int(tds[0].strip())
        except ValueError:
            continue
        try:
            prev_rank = int(tds[1].strip())
        except ValueError:
            prev_rank = 0
        name_match = re.search(r'href="rider/[^"]+">([^<]+)</a>', tds[3])
        team_match = re.search(r'href="team/[^"]+">([^<]+)</a>', tds[4])
        points_match = re.search(r'>(\d+)</a>', tds[5])
        if not name_match or not points_match:
            continue
        riders.append({
            "rider_name": name_match.group(1),
            "rank": rank,
            "prev_rank": prev_rank,
            "team_name": team_match.group(1) if team_match else "",
            "points": int(points_match.group(1)),
        })
    return riders


def fetch_rankings() -> list[dict]:
    """Fetch all pages of PCS season individual ranking."""
    print("Fetching PCS season individual ranking...")
    session = cloudscraper.create_scraper()

    # First page (establishes session)
    r = session.get(f"{BASE_URL}/rankings/me/season-individual")
    all_riders = parse_ranking_page(r.text)
    print(f"  Page 1: {len(all_riders)} riders")

    # Discover page offsets from the offset <select>
    offset_select = re.search(
        r'<select[^>]*name="offset"[^>]*>(.*?)</select>', r.text, re.DOTALL
    )
    if not offset_select:
        print("  Warning: could not find pagination, using first page only")
        return all_riders

    offsets = re.findall(r'<option[^>]*value="(\d+)"', offset_select.group(1))

    # Fetch remaining pages (skip offset=0 which is page 1)
    for i, offset in enumerate(offsets[1:], start=2):
        r = session.get(
            f"{BASE_URL}/rankings.php",
            params={"p": "me", "s": "season-individual", "offset": offset},
        )
        riders = parse_ranking_page(r.text)
        print(f"  Page {i}: {len(riders)} riders")
        all_riders.extend(riders)

    print(f"Total riders fetched: {len(all_riders)}")
    return all_riders


def build_ranking_lookup(raw_rankings: list[dict], aliases: dict[str, str]) -> dict[str, dict]:
    """Build a lookup dict keyed by rider name, applying aliases."""
    lookup = {}
    for entry in raw_rankings:
        name = entry["rider_name"]
        name = aliases.get(name, name)
        lookup[name] = {
            "rank": entry["rank"],
            "prev_rank": entry["prev_rank"],
            "team": entry["team_name"],
            "points": entry["points"],
        }
    return lookup


# ---------------------------------------------------------------------------
# League computation
# ---------------------------------------------------------------------------

def compute_league_table(
    active_teams: dict[str, list[str]],
    ranking: dict[str, dict],
    transfers_done: bool = False,
    first_half_teams: dict[str, list[str]] | None = None,
    snapshot: dict[str, int] | None = None,
) -> list[dict]:
    """Compute league standings for each manager.

    When transfers_done:
      banked = sum of 1st-half riders' snapshot points
      delta  = sum of (current - baseline) for each 2nd-half rider
      total  = banked + delta

    Otherwise:
      total = sum of current rider points
    """
    standings = []
    for manager, riders in active_teams.items():
        if transfers_done and snapshot is not None and first_half_teams is not None:
            banked = sum(snapshot.get(r, 0) for r in first_half_teams[manager])
            delta = 0
            for rider in riders:
                baseline = snapshot.get(rider, 0)
                current = ranking.get(rider, {}).get("points", 0)
                delta += max(0, current - baseline)
            total_points = banked + delta
        else:
            banked = 0
            delta = 0
            total_points = 0
            for rider in riders:
                total_points += ranking.get(rider, {}).get("points", 0)

        standings.append({
            "manager": manager,
            "points": total_points,
            "banked": banked,
        })

    standings.sort(key=lambda x: x["points"], reverse=True)
    for i, entry in enumerate(standings, start=1):
        entry["rank"] = i
    return standings


# ---------------------------------------------------------------------------
# Output writers
# ---------------------------------------------------------------------------

def write_league_csv(standings: list[dict], path: str):
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["rank", "manager", "points", "banked"])
        writer.writeheader()
        writer.writerows(standings)
    print(f"Written: {path}")


def write_detailed_csv(
    active_teams: dict[str, list[str]],
    ranking: dict[str, dict],
    path: str,
    transfers_done: bool = False,
    snapshot: dict[str, int] | None = None,
):
    rows = []
    for manager, riders in active_teams.items():
        for rider in riders:
            current = ranking.get(rider, {}).get("points", 0)
            if transfers_done and snapshot is not None:
                baseline = snapshot.get(rider, 0)
                pts = max(0, current - baseline)
            else:
                pts = current
            rows.append({"manager": manager, "rider": rider, "points": pts})
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["manager", "rider", "points"])
        writer.writeheader()
        writer.writerows(rows)
    print(f"Written: {path}")


def write_ranking_csv(ranking: dict[str, dict], rider_to_manager: dict[str, str], path: str):
    rows = []
    for name, info in ranking.items():
        rows.append({
            "rank": info["rank"],
            "prev_rank": info["prev_rank"],
            "rider": name,
            "team": info["team"],
            "points": info["points"],
            "manager": rider_to_manager.get(name, ""),
        })
    rows.sort(key=lambda x: x["rank"])
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["rank", "prev_rank", "rider", "team", "points", "manager"])
        writer.writeheader()
        writer.writerows(rows)
    print(f"Written: {path}")


def generate_html(
    standings: list[dict],
    active_teams: dict[str, list[str]],
    ranking: dict[str, dict],
    path: str,
    transfers_done: bool = False,
    snapshot: dict[str, int] | None = None,
    history: list[dict] | None = None,
    auction_costs: dict[str, int] | None = None,
):
    """Generate a self-contained HTML league table."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    # Build per-manager rider details sorted by points descending
    manager_details = {}
    for manager, riders in active_teams.items():
        details = []
        for rider in riders:
            info = ranking.get(rider, {})
            current = info.get("points", 0)
            if transfers_done and snapshot is not None:
                baseline = snapshot.get(rider, 0)
                display_points = max(0, current - baseline)
            else:
                display_points = current
            cost = (auction_costs or {}).get(rider)
            if cost and cost > 0 and display_points > 0:
                value = round(display_points / cost, 1)
            else:
                value = None
            details.append({
                "rider": rider,
                "points": display_points,
                "rank": info.get("rank", "—"),
                "team": info.get("team", ""),
                "cost": cost,
                "value": value,
            })
        details.sort(key=lambda x: x["points"], reverse=True)
        manager_details[manager] = details

    # Build standings rows
    standings_rows = ""
    for entry in standings:
        standings_rows += f"""        <tr>
          <td class="rank">{entry['rank']}</td>
          <td class="manager">{entry['manager']}</td>
          <td class="points">{entry['points']:,}</td>
        </tr>
"""

    # Build detail sections
    detail_sections = ""
    for entry in standings:
        mgr = entry["manager"]
        riders_html = ""

        has_costs = auction_costs is not None

        # Show banked points row if transfers have happened
        if transfers_done and entry["banked"] > 0:
            banked_extra = "<td></td>" if has_costs else ""
            riders_html += f"""            <tr class="banked-row">
              <td><em>1st Half (banked)</em></td>
              <td class="team"></td>
              <td class="points">{entry['banked']:,}</td>
              {banked_extra}
              <td class="rider-rank"></td>
            </tr>
"""

        for r in manager_details[mgr]:
            rank_display = f"#{r['rank']}" if r["rank"] != "—" else "—"
            if has_costs:
                if r["cost"] is not None and r["cost"] > 0:
                    cost_display = f"${r['cost']}"
                    value_display = f"{r['value']:.1f}" if r["value"] else "—"
                elif r["cost"] == 0:
                    cost_display = "free"
                    value_display = "—"
                else:
                    cost_display = "—"
                    value_display = "—"
                value_td = f'<td class="value">{value_display}</td>'
            else:
                value_td = ""
            riders_html += f"""            <tr>
              <td>{r['rider']}</td>
              <td class="team">{r['team']}</td>
              <td class="points">{r['points']:,}</td>
              {value_td}
              <td class="rider-rank">{rank_display}</td>
            </tr>
"""
        value_th = "<th>Pts/$</th>" if has_costs else ""
        detail_sections += f"""      <details class="manager-detail">
        <summary>{mgr} — {entry['points']:,} pts</summary>
        <table class="rider-table">
          <thead><tr><th>Rider</th><th>Team</th><th>Points</th>{value_th}<th>PCS Rank</th></tr></thead>
          <tbody>
{riders_html}          </tbody>
        </table>
      </details>
"""

    # Build top 10 hot riders (most points gained in the last month)
    hot_riders_html = ""
    if history and len(history) >= 2:
        latest = history[-1]
        # Pick the snapshot whose date is closest to ~28 days before latest.
        # Snapshot cadence is mixed (weekly backfill + 2x/week live), so an
        # index-based offset would misrepresent the window.
        latest_dt = datetime.strptime(latest["date"], "%Y-%m-%d")
        target_dt = latest_dt - timedelta(days=28)
        baseline = min(
            history[:-1],
            key=lambda h: abs((datetime.strptime(h["date"], "%Y-%m-%d") - target_dt).days),
        )
        baseline_date = baseline["date"]
        latest_date = latest["date"]

        def _rider_points(snapshot: dict, rider_name: str) -> int:
            # Search across every manager's roster: a rider may have been on
            # a different team at baseline if a transfer happened in-window.
            for mgr_data in snapshot["teams"].values():
                for rr in mgr_data.get("riders", []):
                    if rr["rider"] == rider_name:
                        return rr["points"]
            return 0

        gains = []
        for mgr, details in manager_details.items():
            for r in details:
                rider_name = r["rider"]
                current_pts = _rider_points(latest, rider_name)
                baseline_pts = _rider_points(baseline, rider_name)
                gained = current_pts - baseline_pts
                if gained > 0:
                    gains.append({
                        "rider": rider_name,
                        "manager": mgr,
                        "gained": gained,
                        "total": current_pts,
                    })

        gains.sort(key=lambda x: x["gained"], reverse=True)

        hot_rows = ""
        for i, g in enumerate(gains[:10], 1):
            hot_rows += f"""          <tr>
            <td class="rank">{i}</td>
            <td>{g['rider']}</td>
            <td class="team">{g['manager']}</td>
            <td class="points">+{g['gained']:,}</td>
            <td class="points">{g['total']:,}</td>
          </tr>
"""
        # Format the date range
        b_parts = baseline_date.split("-")
        l_parts = latest_date.split("-")
        date_range = f"{b_parts[2]}/{b_parts[1]} — {l_parts[2]}/{l_parts[1]}"

        hot_riders_html = f"""
  <h2>Hot Riders</h2>
  <p class="value-note">Most points gained in the last month ({date_range})</p>
  <table class="standings value-table">
    <thead>
      <tr><th>#</th><th>Rider</th><th>Manager</th><th>Gained</th><th>Total</th></tr>
    </thead>
    <tbody>
{hot_rows}    </tbody>
  </table>
"""

    # Build top 10 best value table (riders that cost > $0)
    best_value_html = ""
    if auction_costs:
        all_riders_value = []
        for mgr, details in manager_details.items():
            for r in details:
                if r["cost"] and r["cost"] > 0 and r["points"] > 0:
                    all_riders_value.append({
                        "rider": r["rider"],
                        "manager": mgr,
                        "points": r["points"],
                        "cost": r["cost"],
                        "value": r["value"],
                    })
        all_riders_value.sort(key=lambda x: x["value"], reverse=True)

        value_rows = ""
        for i, rv in enumerate(all_riders_value[:10], 1):
            value_rows += f"""          <tr>
            <td class="rank">{i}</td>
            <td>{rv['rider']}</td>
            <td class="team">{rv['manager']}</td>
            <td class="points">{rv['points']:,}</td>
            <td class="cost">${rv['cost']}</td>
            <td class="value">{rv['value']:.1f}</td>
          </tr>
"""
        best_value_html = f"""
  <h2>Best Value Picks</h2>
  <p class="value-note">Top 10 riders by points per dollar spent (excludes free picks)</p>
  <table class="standings value-table">
    <thead>
      <tr><th>#</th><th>Rider</th><th>Manager</th><th>Points</th><th>Cost</th><th>Pts/$</th></tr>
    </thead>
    <tbody>
{value_rows}    </tbody>
  </table>
"""

    # Build history JSON for embedding in HTML
    history_json = json.dumps(history or [], ensure_ascii=False)

    # Decide at render time whether to emit the banked-segment JS block.
    # The block is only meaningful once transfers have happened and at least
    # one manager has nonzero banked points in the latest snapshot. Emitting
    # it unconditionally would put the literal "Banked (1st half)" string in
    # the HTML even pre-transfer, defeating runtime suppression.
    banked_segment_js = ""
    if history:
        latest_teams = (history[-1] or {}).get("teams", {}) or {}
        if any((t or {}).get("banked", 0) > 0 for t in latest_teams.values()):
            banked_segment_js = """
    const bankedValues = managers.map(m => (latestSnap.teams[m] && latestSnap.teams[m].banked) || 0);
    riderDatasets.unshift({
      label: 'Banked (1st half)',
      data: bankedValues,
      backgroundColor: '#a8a8a8',
      borderColor: '#ffffff',
      borderWidth: 0.5,
    });
"""

    # Build manager list in standings order for consistent chart colours
    manager_order = [e["manager"] for e in standings]
    manager_order_json = json.dumps(manager_order, ensure_ascii=False)

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>2026 Fantasy Cycling League</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Hanken+Grotesk:wght@400;500;600;700;800&display=swap" rel="stylesheet">
<script src="https://cdn.jsdelivr.net/npm/chart.js@4"></script>
<style>
  :root {{
    --bg: #ffffff;
    --surface: #f8f8f8;
    --text: #1a1a1a;
    --text-secondary: #6b6b6b;
    --accent: #00a67e;
    --accent-light: rgba(0, 166, 126, 0.06);
    --border: #e5e5e5;
    --border-strong: #d0d0d0;
    --gold: #c5960c;
    --silver: #6b7280;
    --bronze: #a0622d;
  }}

  * {{ margin: 0; padding: 0; box-sizing: border-box; }}

  body {{
    font-family: 'Hanken Grotesk', -apple-system, BlinkMacSystemFont, sans-serif;
    background: var(--bg);
    color: var(--text);
    line-height: 1.55;
    padding: 3rem 1.25rem;
    min-height: 100vh;
    -webkit-font-smoothing: antialiased;
  }}

  .container {{ max-width: 840px; margin: 0 auto; }}

  /* --- Header --- */
  header {{
    margin-bottom: 2.75rem;
    padding-bottom: 1.5rem;
    border-bottom: 2px solid var(--text);
  }}
  h1 {{
    font-size: 2rem;
    font-weight: 800;
    letter-spacing: -0.035em;
    line-height: 1.15;
    margin-bottom: 0.35rem;
  }}
  .subtitle {{
    color: var(--text-secondary);
    font-size: 0.85rem;
    font-weight: 500;
    letter-spacing: 0.01em;
  }}

  /* --- Standings table --- */
  table.standings {{
    width: 100%;
    border-collapse: collapse;
    margin-bottom: 3rem;
  }}
  table.standings thead {{
    border-bottom: 2px solid var(--text);
  }}
  table.standings th {{
    font-weight: 700;
    font-size: 0.7rem;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    padding: 0.6rem 0.75rem;
    text-align: left;
    color: var(--text);
  }}
  table.standings td {{
    padding: 0.65rem 0.75rem;
    border-bottom: 1px solid var(--border);
    font-size: 0.92rem;
    transition: background 0.12s ease;
  }}
  table.standings tr:last-child td {{ border-bottom: 1px solid var(--border); }}
  table.standings tbody tr:hover {{ background: var(--accent-light); }}
  td.rank {{
    font-weight: 700;
    width: 3rem;
    text-align: center;
    font-variant-numeric: tabular-nums;
  }}
  td.manager {{ font-weight: 600; }}
  td.points {{
    font-weight: 700;
    font-variant-numeric: tabular-nums;
    letter-spacing: -0.01em;
  }}
  tbody tr:nth-child(1) td.rank {{ color: var(--gold); }}
  tbody tr:nth-child(2) td.rank {{ color: var(--silver); }}
  tbody tr:nth-child(3) td.rank {{ color: var(--bronze); }}

  /* --- Breakdown --- */
  h2 {{
    font-size: 1.15rem;
    font-weight: 800;
    letter-spacing: -0.02em;
    text-transform: uppercase;
    margin-bottom: 0.65rem;
    color: var(--text);
  }}
  .manager-detail {{
    border-top: 1px solid var(--border);
    transition: background 0.15s ease;
  }}
  .manager-detail:last-of-type {{
    border-bottom: 1px solid var(--border);
  }}
  .manager-detail summary {{
    padding: 0.6rem 0.25rem;
    cursor: pointer;
    font-weight: 600;
    font-size: 0.9rem;
    user-select: none;
    list-style: none;
    display: flex;
    align-items: center;
    gap: 0.5rem;
    transition: color 0.15s ease;
  }}
  .manager-detail summary:hover {{
    color: var(--accent);
  }}
  .manager-detail summary::-webkit-details-marker {{ display: none; }}
  .manager-detail summary::before {{
    content: '';
    display: inline-block;
    width: 6px;
    height: 6px;
    border-right: 2px solid var(--accent);
    border-bottom: 2px solid var(--accent);
    transform: rotate(-45deg);
    transition: transform 0.2s ease;
    flex-shrink: 0;
  }}
  .manager-detail[open] summary::before {{
    transform: rotate(45deg);
  }}
  .rider-table {{
    width: 100%;
    border-collapse: collapse;
    font-size: 0.82rem;
    margin-bottom: 0.5rem;
  }}
  .rider-table th {{
    text-align: left;
    padding: 0.35rem 0.75rem;
    color: var(--text-secondary);
    font-weight: 600;
    font-size: 0.68rem;
    text-transform: uppercase;
    letter-spacing: 0.06em;
    background: var(--surface);
    border-bottom: 1px solid var(--border);
  }}
  .rider-table td {{
    padding: 0.4rem 0.75rem;
    border-bottom: 1px solid var(--border);
  }}
  .rider-table tr:last-child td {{ border-bottom: none; }}
  .rider-table td.points {{ font-weight: 600; font-variant-numeric: tabular-nums; }}
  .rider-table td.team {{ color: var(--text-secondary); font-size: 0.78rem; }}
  .rider-table td.rider-rank {{ color: var(--text-secondary); font-variant-numeric: tabular-nums; }}
  .rider-table td.value {{ color: var(--accent); font-weight: 600; font-variant-numeric: tabular-nums; }}
  .banked-row td {{ background: var(--surface); }}
  .value-note {{
    color: var(--text-secondary);
    font-size: 0.78rem;
    margin-bottom: 0.75rem;
    margin-top: -0.4rem;
  }}
  .value-table td.cost {{ font-variant-numeric: tabular-nums; }}
  .value-table td.value {{ color: var(--accent); font-weight: 700; font-variant-numeric: tabular-nums; }}

  /* --- Charts --- */
  .charts-section {{
    margin-top: 3rem;
  }}
  .chart-container {{
    position: relative;
    margin-bottom: 2.5rem;
    padding: 1.25rem;
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 6px;
  }}
  .chart-container h3 {{
    font-size: 0.85rem;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.04em;
    margin-bottom: 1rem;
    color: var(--text);
  }}
  .chart-container canvas {{
    width: 100% !important;
  }}
  .chart-note {{
    font-size: 0.7rem;
    color: var(--text-secondary);
    text-align: center;
    margin-top: 0.5rem;
  }}

  /* --- Footer --- */
  .updated {{
    text-align: center;
    color: var(--text-secondary);
    font-size: 0.72rem;
    font-weight: 500;
    margin-top: 2.75rem;
    padding-top: 1rem;
    border-top: 1px solid var(--border);
    letter-spacing: 0.01em;
  }}

  /* --- Chart zoom toggle --- */
  .chart-zoom {{
    display: none;
    text-align: right;
    margin-bottom: 0.5rem;
  }}
  .chart-zoom button {{
    font-family: inherit;
    font-size: 0.68rem;
    font-weight: 600;
    padding: 0.25rem 0.6rem;
    border: 1px solid var(--border-strong);
    border-radius: 3px;
    background: var(--bg);
    color: var(--text-secondary);
    cursor: pointer;
    margin-left: 0.3rem;
  }}
  .chart-zoom button.active {{
    background: var(--text);
    color: var(--bg);
    border-color: var(--text);
  }}

  /* --- Responsive --- */
  @media (max-width: 600px) {{
    body {{ padding: 1.5rem 0.75rem; }}
    h1 {{ font-size: 1.5rem; }}
    table.standings td, table.standings th {{ padding: 0.5rem 0.5rem; font-size: 0.82rem; }}
    .rider-table td, .rider-table th {{ padding: 0.3rem 0.5rem; }}
    .chart-container canvas {{ max-height: 350px; }}
    .chart-zoom {{ display: block; }}
  }}
</style>
</head>
<body>
<div class="container">
  <header>
    <h1>2026 Fantasy Cycling League</h1>
    <p class="subtitle">Points sourced from ProCyclingStats season ranking</p>
  </header>

  <table class="standings">
    <thead>
      <tr><th>Rank</th><th>Manager</th><th>Points</th></tr>
    </thead>
    <tbody>
{standings_rows}    </tbody>
  </table>

  <h2>Rider Breakdown</h2>
{detail_sections}
  <div class="charts-section">
    <h2>Season Progress</h2>

    <div class="chart-container">
      <h3>Team Points Over Time</h3>
      <div class="chart-zoom" id="zoomPoints">
        <button data-range="month" class="active">Last month</button>
        <button data-range="all">Full season</button>
      </div>
      <canvas id="pointsOverTime"></canvas>
    </div>

    <div class="chart-container">
      <h3>League Position Over Time</h3>
      <div class="chart-zoom" id="zoomPosition">
        <button data-range="month" class="active">Last month</button>
        <button data-range="all">Full season</button>
      </div>
      <canvas id="positionOverTime"></canvas>
    </div>

    <div class="chart-container">
      <h3>Points Gained Per Update</h3>
      <canvas id="pointsGained"></canvas>
    </div>

    <div class="chart-container">
      <h3>Rider Contributions</h3>
      <canvas id="riderContribution"></canvas>
    </div>
  </div>
{hot_riders_html}
{best_value_html}
  <p class="updated">Updated every Monday &amp; Thursday<br>Last updated: {now}</p>
</div>

<script>
(function() {{
  const history = {history_json};
  const managers = {manager_order_json};

  // Colour palette — distinct, accessible colours for up to 9 teams
  const COLOURS = [
    '#e6194b', '#3cb44b', '#4363d8', '#f58231', '#911eb4',
    '#42d4f4', '#f032e6', '#bfef45', '#469990'
  ];

  const colourMap = {{}};
  managers.forEach((m, i) => colourMap[m] = COLOURS[i % COLOURS.length]);

  // --- Helpers ---
  const isMobile = window.innerWidth <= 600;
  const lineWidth = isMobile ? 1.5 : 2;
  const lineWidthBump = isMobile ? 1.5 : 2.5;
  const ptRadius = isMobile ? 0 : (history.length > 20 ? 0 : 3);
  const ptRadiusBump = isMobile ? 0 : (history.length > 20 ? 0 : 4);

  const dates = history.map(h => h.date);
  const shortDates = dates.map(d => {{
    const parts = d.split('-');
    return parts[2] + '/' + parts[1];
  }});
  const MONTHS = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
  // Month labels: show month name on first occurrence, blank otherwise
  const monthLabels = dates.map((d, i) => {{
    const month = parseInt(d.split('-')[1], 10);
    const prevMonth = i > 0 ? parseInt(dates[i-1].split('-')[1], 10) : -1;
    return month !== prevMonth ? MONTHS[month - 1] : '';
  }});

  // Zoom: find index for ~1 month ago
  const lastMonthIdx = Math.max(0, dates.length - 5);  // ~4-5 weekly entries = 1 month

  function setupZoom(containerId, chart, allDateLabels, allMonthLabels, allDatasets) {{
    const container = document.getElementById(containerId);
    if (!container) return;
    const buttons = container.querySelectorAll('button');
    // Default to last month on mobile
    if (isMobile && dates.length > 5) {{
      applyZoom(chart, allDateLabels, allDatasets, lastMonthIdx);
    }} else {{
      // Desktop: show full season with month labels
      applyZoom(chart, allMonthLabels, allDatasets, 0);
    }}
    buttons.forEach(btn => {{
      btn.addEventListener('click', function() {{
        buttons.forEach(b => b.classList.remove('active'));
        btn.classList.add('active');
        const range = btn.dataset.range;
        if (range === 'month') {{
          applyZoom(chart, allDateLabels, allDatasets, lastMonthIdx);
        }} else {{
          applyZoom(chart, allMonthLabels, allDatasets, 0);
        }}
      }});
    }});
  }}

  function applyZoom(chart, labels, allDatasets, startIdx) {{
    chart.data.labels = labels.slice(startIdx);
    chart.data.datasets.forEach((ds, i) => {{
      ds.data = allDatasets[i].slice(startIdx);
    }});
    // Update stored originals for hover highlight
    chart._origColours = chart.data.datasets.map(d => d.borderColor);
    chart._origWidths = chart.data.datasets.map(d => d.borderWidth);
    chart.update();
  }}

  // Chart.js defaults
  Chart.defaults.font.family = "'Hanken Grotesk', sans-serif";
  Chart.defaults.font.size = 11;
  Chart.defaults.color = '#6b6b6b';

  // Line highlight on hover: dims non-hovered datasets
  function lineHighlightOpts(baseOpts) {{
    baseOpts.onHover = function(evt, elements, chart) {{
      const ds = chart.data.datasets;
      if (!elements || !elements.length) {{
        // Reset all
        ds.forEach((d, i) => {{
          d.borderColor = chart._origColours[i];
          d.borderWidth = chart._origWidths[i];
        }});
        chart.update('none');
        return;
      }}
      // Use the first active element's dataset
      const activeIdx = elements[0].datasetIndex;
      ds.forEach((d, i) => {{
        d.borderColor = (i === activeIdx) ? chart._origColours[i] : chart._origColours[i] + '40';
        d.borderWidth = (i === activeIdx) ? chart._origWidths[i] + 1 : chart._origWidths[i];
      }});
      chart.update('none');
    }};
    // Change interaction to nearest dataset for better line targeting
    baseOpts.interaction = {{ mode: 'nearest', intersect: false, axis: 'xy' }};
    return baseOpts;
  }}
  function storeOriginals(chart) {{
    chart._origColours = chart.data.datasets.map(d => d.borderColor);
    chart._origWidths = chart.data.datasets.map(d => d.borderWidth);
  }}

  // --- 1. Team Points Over Time (line chart) ---
  if (history.length >= 1) {{
    const allPointsData = managers.map(m => history.map(h => h.teams[m] ? h.teams[m].total : null));
    const c1 = new Chart(document.getElementById('pointsOverTime'), {{
      type: 'line',
      data: {{
        labels: monthLabels.slice(),
        datasets: managers.map((m, i) => ({{
          label: m,
          data: allPointsData[i].slice(),
          borderColor: colourMap[m],
          backgroundColor: colourMap[m] + '18',
          borderWidth: lineWidth,
          pointRadius: ptRadius,
          pointHoverRadius: 5,
          tension: 0.25,
          fill: false,
        }})),
      }},
      options: lineHighlightOpts({{
        responsive: true,
        aspectRatio: isMobile ? 1.2 : 2,
        plugins: {{
          legend: {{ position: 'bottom', labels: {{ boxWidth: 10, padding: 8, font: {{ size: isMobile ? 9 : 11 }} }} }},
          tooltip: {{ mode: 'index', intersect: false, callbacks: {{ label: ctx => ctx.dataset.label + ': ' + (ctx.parsed.y ?? 0).toLocaleString() + ' pts' }} }},
        }},
        scales: {{
          y: {{ beginAtZero: true, grid: {{ color: '#e5e5e5' }}, ticks: {{ callback: v => v.toLocaleString() }} }},
          x: {{ grid: {{ display: false }} }},
        }},
      }}),
    }});
    storeOriginals(c1);
    setupZoom('zoomPoints', c1, shortDates.slice(), monthLabels.slice(), allPointsData);
  }}

  // --- 2. League Position Over Time (bump chart) ---
  if (history.length >= 2) {{
    const allPosData = managers.map(m => history.map(h => h.teams[m] ? h.teams[m].rank : null));
    const c2 = new Chart(document.getElementById('positionOverTime'), {{
      type: 'line',
      data: {{
        labels: monthLabels.slice(),
        datasets: managers.map((m, i) => ({{
          label: m,
          data: allPosData[i].slice(),
          borderColor: colourMap[m],
          backgroundColor: colourMap[m],
          borderWidth: lineWidthBump,
          pointRadius: ptRadiusBump,
          pointHoverRadius: 6,
          tension: 0.25,
          fill: false,
        }})),
      }},
      options: lineHighlightOpts({{
        responsive: true,
        aspectRatio: isMobile ? 1.2 : 2,
        plugins: {{
          legend: {{ position: 'bottom', labels: {{ boxWidth: 10, padding: 8, font: {{ size: isMobile ? 9 : 11 }} }} }},
          tooltip: {{ mode: 'index', intersect: false, callbacks: {{ label: ctx => ctx.dataset.label + ': #' + ctx.parsed.y }} }},
        }},
        scales: {{
          y: {{
            reverse: true,
            min: 1,
            max: managers.length,
            ticks: {{ stepSize: 1, callback: v => '#' + v }},
            grid: {{ color: '#e5e5e5' }},
          }},
          x: {{ grid: {{ display: false }} }},
        }},
      }}),
    }});
    storeOriginals(c2);
    setupZoom('zoomPosition', c2, shortDates.slice(), monthLabels.slice(), allPosData);
  }} else {{
    document.getElementById('positionOverTime').parentElement.querySelector('h3').textContent += ' (needs 2+ updates)';
  }}

  // --- 3. Points Gained Per Update (grouped bar chart) ---
  if (history.length >= 2) {{
    const gainMonthLabels = monthLabels.slice(1);
    const gainDatasets = managers.map(m => ({{
      label: m,
      data: history.slice(1).map((h, i) => {{
        const prev = history[i].teams[m] ? history[i].teams[m].total : 0;
        const curr = h.teams[m] ? h.teams[m].total : 0;
        return Math.max(0, curr - prev);
      }}),
      backgroundColor: colourMap[m] + 'cc',
      borderColor: colourMap[m],
      borderWidth: 1,
    }}));

    new Chart(document.getElementById('pointsGained'), {{
      type: 'bar',
      data: {{ labels: gainMonthLabels, datasets: gainDatasets }},
      options: {{
        responsive: true,
        plugins: {{
          legend: {{ position: 'bottom', labels: {{ boxWidth: 12, padding: 12 }} }},
          tooltip: {{ callbacks: {{ label: ctx => ctx.dataset.label + ': +' + ctx.parsed.y.toLocaleString() }} }},
        }},
        scales: {{
          y: {{ beginAtZero: true, grid: {{ color: '#e5e5e5' }}, ticks: {{ callback: v => '+' + v.toLocaleString() }} }},
          x: {{ grid: {{ display: false }} }},
        }},
      }},
    }});
  }} else {{
    document.getElementById('pointsGained').parentElement.querySelector('h3').textContent += ' (needs 2+ updates)';
  }}

  // --- 4. Rider Contribution (horizontal stacked bar) ---
  const latestSnap = history.length > 0 ? history[history.length - 1] : null;
  if (latestSnap) {{
    // Build rider datasets — each unique rider becomes a segment
    const riderMap = {{}};  // rider → array of values per manager
    managers.forEach((m, mi) => {{
      const riders = latestSnap.teams[m] ? latestSnap.teams[m].riders : [];
      riders.sort((a, b) => b.points - a.points);
      riders.forEach((r, ri) => {{
        if (!riderMap[r.rider]) riderMap[r.rider] = new Array(managers.length).fill(0);
        riderMap[r.rider][mi] = r.points;
      }});
    }});

    // Generate colours for riders
    const riderNames = Object.keys(riderMap);
    const riderColours = [
      '#264653', '#2a9d8f', '#e9c46a', '#f4a261', '#e76f51',
      '#606c38', '#283618', '#dda15e', '#bc6c25', '#023047',
      '#219ebc', '#8ecae6', '#ffb703', '#fb8500', '#457b9d',
      '#1d3557', '#a8dadc', '#e63946', '#f1faee', '#6d6875',
      '#b5838d', '#e5989b', '#ffcdb2', '#ffb4a2', '#6930c3',
      '#5390d9', '#48bfe3', '#56cfe1', '#64dfdf', '#72efdd',
      '#80ffdb', '#7400b8', '#5e60ce', '#4ea8de', '#06d6a0',
      '#118ab2', '#073b4c', '#ef476f', '#ffd166', '#8338ec',
      '#3a86ff', '#ff006e', '#8ac926', '#1982c4', '#6a4c93',
      '#f72585', '#7209b7', '#3f37c9', '#4361ee', '#4cc9f0',
      '#c9184a', '#ff4d6d', '#ff758f', '#ff8fa3', '#ffb3c1',
      '#d9ed92', '#b5e48c', '#99d98c', '#76c893', '#52b69a',
      '#34a0a4', '#168aad', '#1a759f', '#1e6091', '#184e77',
      '#9b2226', '#ae2012', '#bb3e03', '#ca6702', '#ee9b00',
      '#e9d8a6', '#94d2bd', '#0a9396', '#005f73', '#001219',
    ];

    const riderDatasets = riderNames.map((name, i) => ({{
      label: name,
      data: riderMap[name],
      backgroundColor: riderColours[i % riderColours.length] + 'dd',
      borderColor: '#ffffff',
      borderWidth: 0.5,
    }}));
{banked_segment_js}

    // Wrap long manager names into multi-line labels for Chart.js
    const wrappedLabels = managers.map(name => {{
      if (name.length > 12) {{
        const words = name.split(' ');
        const mid = Math.ceil(words.length / 2);
        return [words.slice(0, mid).join(' '), words.slice(mid).join(' ')];
      }}
      return name;
    }});

    new Chart(document.getElementById('riderContribution'), {{
      type: 'bar',
      data: {{ labels: wrappedLabels, datasets: riderDatasets }},
      options: {{
        indexAxis: 'y',
        responsive: true,
        aspectRatio: isMobile ? 0.9 : 1.5,
        plugins: {{
          legend: {{ display: false }},
          tooltip: {{
            callbacks: {{
              label: ctx => {{
                if (ctx.parsed.x === 0) return null;
                return ctx.dataset.label + ': ' + ctx.parsed.x.toLocaleString() + ' pts';
              }},
            }},
          }},
        }},
        scales: {{
          x: {{
            stacked: true,
            beginAtZero: true,
            grid: {{ color: '#e5e5e5' }},
            ticks: {{ callback: v => v.toLocaleString() }},
          }},
          y: {{
            stacked: true,
            grid: {{ display: false }},
            ticks: {{
              autoSkip: false,
              font: {{ size: isMobile ? 9 : 11 }},
            }},
          }},
        }},
      }},
    }});
  }}
}})();
</script>
</body>
</html>"""

    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(html)
    print(f"Written: {path}")


# ---------------------------------------------------------------------------
# History tracking
# ---------------------------------------------------------------------------

def load_history(path: str) -> list[dict]:
    """Load existing history from JSON file, or return empty list."""
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return []


def append_history(
    history: list[dict],
    standings: list[dict],
    active_teams: dict[str, list[str]],
    ranking: dict[str, dict],
    path: str,
    transfers_done: bool = False,
    snapshot: dict[str, int] | None = None,
):
    """Append a new snapshot to the history file.

    Each entry records the date and each manager's total points plus
    per-rider breakdown, giving us everything we need for charts.
    """
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # Don't add a duplicate entry for the same date
    if history and history[-1]["date"] == today:
        history.pop()

    teams_snapshot = {}
    for entry in standings:
        mgr = entry["manager"]
        riders = []
        for rider in active_teams[mgr]:
            info = ranking.get(rider, {})
            current = info.get("points", 0)
            if transfers_done and snapshot is not None:
                baseline = snapshot.get(rider, 0)
                display_points = max(0, current - baseline)
            else:
                display_points = current
            riders.append({"rider": rider, "points": display_points})
        teams_snapshot[mgr] = {
            "total": entry["points"],
            "rank": entry["rank"],
            "banked": entry["banked"],
            "riders": riders,
        }

    history.append({
        "date": today,
        "teams": teams_snapshot,
    })

    with open(path, "w") as f:
        json.dump(history, f, indent=2, ensure_ascii=False)
    print(f"Written: {path} ({len(history)} snapshots)")


def log_missing_riders(active_teams: dict[str, list[str]], ranking: dict[str, dict]):
    """Log any drafted riders not found in the PCS ranking."""
    all_riders = [r for riders in active_teams.values() for r in riders]
    missing = []
    for manager, riders in active_teams.items():
        for rider in riders:
            if rider not in ranking:
                missing.append((manager, rider))
    if missing:
        print(f"\n⚠ {len(missing)} drafted rider(s) NOT found in PCS ranking:")
        for manager, rider in missing:
            print(f"  {rider} ({manager})")
    else:
        print(f"\n✓ All {len(all_riders)} drafted riders found in PCS ranking")


def main():
    parser = argparse.ArgumentParser(description="Fantasy Cycling League updater")
    parser.add_argument(
        "--snapshot", action="store_true",
        help="Save current PCS rankings as mid-season snapshot and exit",
    )
    args = parser.parse_args()

    base_dir = os.path.dirname(os.path.abspath(__file__))
    config_path = os.path.join(base_dir, "league_config.yaml")
    snapshot_path = os.path.join(base_dir, "mid_season_snapshot.csv")
    history_path = os.path.join(base_dir, "history.json")

    # Load config
    config = load_config(config_path)
    aliases = config.get("aliases", {})
    transfers_done = config.get("transfers_done", False)
    first_half_teams = config["first_half"]
    active_teams = get_active_teams(config)
    rider_to_manager = build_rider_to_manager(active_teams)
    auction_costs = config.get("auction_costs", {})

    # Step 1: Fetch rankings
    raw = fetch_rankings()

    # Step 2: Build lookup
    ranking = build_ranking_lookup(raw, aliases)

    # Handle snapshot mode
    if args.snapshot:
        write_snapshot(ranking, snapshot_path)
        print("Snapshot saved. Update league_config.yaml with second_half rosters")
        print("and set transfers_done: true, then run without --snapshot.")
        return

    # Step 3: Check for missing riders
    log_missing_riders(active_teams, ranking)

    # Step 4: Load snapshot if transfers are active
    snapshot = None
    if transfers_done:
        if not os.path.exists(snapshot_path):
            print(f"ERROR: transfers_done is true but {snapshot_path} not found.")
            print("Run with --snapshot first to create the baseline.")
            return
        snapshot = load_snapshot(snapshot_path)
        print(f"Loaded mid-season snapshot ({len(snapshot)} riders)")

    # Step 5: Compute league table
    standings = compute_league_table(
        active_teams, ranking, transfers_done, first_half_teams, snapshot
    )

    # Step 6: Update history
    history = load_history(history_path)
    append_history(
        history, standings, active_teams, ranking, history_path,
        transfers_done, snapshot,
    )

    # Step 7: Print summary
    print("\n=== League Standings ===")
    for entry in standings:
        banked_info = f" (banked: {entry['banked']:,})" if entry["banked"] > 0 else ""
        print(f"  {entry['rank']}. {entry['manager']}: {entry['points']:,} pts{banked_info}")

    # Step 8: Write outputs
    write_league_csv(standings, os.path.join(base_dir, "league_table.csv"))
    write_detailed_csv(
        active_teams, ranking, os.path.join(base_dir, "league_detailed.csv"),
        transfers_done, snapshot,
    )
    write_ranking_csv(ranking, rider_to_manager, os.path.join(base_dir, "ranking.csv"))
    generate_html(
        standings, active_teams, ranking,
        os.path.join(base_dir, "docs", "index.html"),
        transfers_done, snapshot,
        history=history,
        auction_costs=auction_costs,
    )

    print("\nDone!")


if __name__ == "__main__":
    main()
