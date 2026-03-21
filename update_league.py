#!/usr/bin/env python3
"""Fantasy Cycling League — fetch PCS rankings and generate league table."""

import csv
import os
import re
from datetime import datetime, timezone

import cloudscraper

# ---------------------------------------------------------------------------
# Draft picks: manager → list of riders (UPPERCASE Lastname, Firstname)
# ---------------------------------------------------------------------------
TEAMS = {
    "Nick": [
        "AYUSO Juan", "VINGEGAARD Jonas", "VINE Jay", "NARVÁEZ Jhonatan",
        "TARLING Joshua", "WILLIAMS Stephen", "FORTUNATO Lorenzo", "O'CONNOR Ben",
    ],
    "Tim": [
        "PHILIPSEN Jasper", "KOOIJ Olav", "MERLIER Tim", "PEDERSEN Mads",
        "STRONG Corbin", "CHRISTEN Jan", "BLACKMORE Joseph", "HINDLEY Jai",
    ],
    "Cameron": [
        "HEALY Ben", "JORGENSON Matteo", "PIDCOCK Thomas", "ONLEY Oscar",
        "ROGLIČ Primož", "ARENSMAN Thymen", "GANNA Filippo", "ALAPHILIPPE Julian",
    ],
    "Andy": [
        "EVENEPOEL Remco", "BRENNAN Matthew", "MCNULTY Brandon", "ALMEIDA João",
        "GRÉGOIRE Romain", "MOSCHETTI Matteo", "YATES Adam", "UIJTDEBROEKS Cian",
    ],
    "Dave": [
        "VAUQUELIN Kévin", "CICCONE Giulio", "GALL Felix", "DEL TORO Isaac",
        "MARTINEZ Lenny", "POWLESS Neilson", "GIRMAY Biniam", "JEANNIÈRE Emilien",
    ],
    "Mike": [
        "DE LIE Arnaud", "SKJELMOSE Mattias", "MAGNIER Paul", "VAN DER POEL Mathieu",
        "CARAPAZ Richard", "NYS Thibau", "SEIXAS Paul", "GEE Derek",
    ],
    "Campbell": [
        "VAN AERT Wout", "LIPOWITZ Florian", "MILAN Jonathan", "SCARONI Christian",
        "WRIGHT Fred", "PELLIZZARI Giulio", "STORER Michael", "DEL GROSSO Tibor",
    ],
    "Joe": [
        "PIDCOCK Joseph", "CRAPS Lars", "JAKOBSEN Fabio", "SÖDERQVIST Jakob",
        "WIDAR Jarno", "HIRSCHI Marc", "WANG Gustav", "POGAČAR Tadej",
    ],
    "Geminiani's Hipsters": [
        "BISIAUX Léo", "NORDHAGEN Jørgen", "LAMPERTI Luke", "AUGUST Andrew",
        "AGOSTINACCHIO Filippo", "FINN Lorenzo", "TULETT Ben", "ABRAHAMSEN Jonas",
    ],
}

# Build reverse lookup: rider name → manager
RIDER_TO_MANAGER = {}
for manager, riders in TEAMS.items():
    for rider in riders:
        RIDER_TO_MANAGER[rider] = manager


# PCS sometimes uses different names than the draft spreadsheet.
# Map PCS name → draft name so lookups work.
PCS_NAME_ALIASES = {
    "GEE-WEST Derek": "GEE Derek",
}

BASE_URL = "https://www.procyclingstats.com"


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
            prev_rank = int(tds[1].strip())
        except ValueError:
            continue
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


def build_ranking_lookup(raw_rankings: list[dict]) -> dict[str, dict]:
    """Build a lookup dict keyed by rider name, applying aliases."""
    lookup = {}
    for entry in raw_rankings:
        name = entry["rider_name"]
        # Apply alias if one exists (PCS name → draft name)
        name = PCS_NAME_ALIASES.get(name, name)
        lookup[name] = {
            "rank": entry["rank"],
            "prev_rank": entry["prev_rank"],
            "team": entry["team_name"],
            "points": entry["points"],
        }
    return lookup


def compute_league_table(ranking: dict[str, dict]) -> list[dict]:
    """Compute league standings for each manager."""
    standings = []
    for manager, riders in TEAMS.items():
        total_points = 0
        null_count = 0
        for rider in riders:
            pts = ranking.get(rider, {}).get("points", 0)
            total_points += pts
            if pts == 0:
                null_count += 1
        standings.append({
            "manager": manager,
            "points": total_points,
            "null_count": null_count,
        })
    standings.sort(key=lambda x: x["points"], reverse=True)
    for i, entry in enumerate(standings, start=1):
        entry["rank"] = i
    return standings


def write_league_csv(standings: list[dict], path: str):
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["rank", "manager", "points", "null_count"])
        writer.writeheader()
        writer.writerows(standings)
    print(f"Written: {path}")


def write_detailed_csv(ranking: dict[str, dict], path: str):
    rows = []
    for manager, riders in TEAMS.items():
        for rider in riders:
            pts = ranking.get(rider, {}).get("points", 0)
            rows.append({"manager": manager, "rider": rider, "points": pts})
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["manager", "rider", "points"])
        writer.writeheader()
        writer.writerows(rows)
    print(f"Written: {path}")


def write_ranking_csv(ranking: dict[str, dict], path: str):
    rows = []
    for name, info in ranking.items():
        rows.append({
            "rank": info["rank"],
            "prev_rank": info["prev_rank"],
            "rider": name,
            "team": info["team"],
            "points": info["points"],
            "manager": RIDER_TO_MANAGER.get(name, ""),
        })
    rows.sort(key=lambda x: x["rank"])
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["rank", "prev_rank", "rider", "team", "points", "manager"])
        writer.writeheader()
        writer.writerows(rows)
    print(f"Written: {path}")


def generate_html(standings: list[dict], ranking: dict[str, dict], path: str):
    """Generate a self-contained HTML league table."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    # Build per-manager rider details sorted by points descending
    manager_details = {}
    for manager, riders in TEAMS.items():
        details = []
        for rider in riders:
            info = ranking.get(rider, {})
            details.append({
                "rider": rider,
                "points": info.get("points", 0),
                "rank": info.get("rank", "—"),
                "team": info.get("team", ""),
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
          <td class="null-count">{entry['null_count']}</td>
        </tr>
"""

    # Build detail sections
    detail_sections = ""
    for entry in standings:
        mgr = entry["manager"]
        riders_html = ""
        for r in manager_details[mgr]:
            rank_display = f"#{r['rank']}" if r["rank"] != "—" else "—"
            riders_html += f"""            <tr>
              <td>{r['rider']}</td>
              <td class="team">{r['team']}</td>
              <td class="points">{r['points']:,}</td>
              <td class="rider-rank">{rank_display}</td>
            </tr>
"""
        detail_sections += f"""      <details class="manager-detail">
        <summary>{mgr} — {entry['points']:,} pts</summary>
        <table class="rider-table">
          <thead><tr><th>Rider</th><th>Team</th><th>Points</th><th>PCS Rank</th></tr></thead>
          <tbody>
{riders_html}          </tbody>
        </table>
      </details>
"""

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>2026 Fantasy Cycling League</title>
<style>
  :root {{
    --bg: #0f1117;
    --card: #1a1d27;
    --border: #2a2d3a;
    --text: #e2e4ea;
    --text-dim: #8b8fa3;
    --accent: #6c63ff;
    --accent-glow: rgba(108, 99, 255, 0.15);
    --gold: #f5c842;
    --silver: #b0b8c8;
    --bronze: #cd7f32;
  }}
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{
    font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
    background: var(--bg);
    color: var(--text);
    line-height: 1.6;
    padding: 2rem 1rem;
    min-height: 100vh;
  }}
  .container {{ max-width: 800px; margin: 0 auto; }}
  h1 {{
    text-align: center;
    font-size: 1.8rem;
    font-weight: 700;
    margin-bottom: 0.25rem;
    letter-spacing: -0.02em;
  }}
  .subtitle {{
    text-align: center;
    color: var(--text-dim);
    font-size: 0.85rem;
    margin-bottom: 2rem;
  }}
  table.standings {{
    width: 100%;
    border-collapse: collapse;
    margin-bottom: 2rem;
    background: var(--card);
    border-radius: 12px;
    overflow: hidden;
    box-shadow: 0 4px 24px rgba(0,0,0,0.3);
  }}
  table.standings th {{
    background: var(--accent);
    color: #fff;
    font-weight: 600;
    font-size: 0.8rem;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    padding: 0.75rem 1rem;
    text-align: left;
  }}
  table.standings td {{
    padding: 0.75rem 1rem;
    border-bottom: 1px solid var(--border);
    font-size: 0.95rem;
  }}
  table.standings tr:last-child td {{ border-bottom: none; }}
  table.standings tr:hover {{ background: var(--accent-glow); }}
  td.rank {{ font-weight: 700; width: 3rem; text-align: center; }}
  td.points {{ font-weight: 600; font-variant-numeric: tabular-nums; }}
  td.null-count {{ color: var(--text-dim); text-align: center; }}
  tr:nth-child(1) td.rank {{ color: var(--gold); }}
  tr:nth-child(2) td.rank {{ color: var(--silver); }}
  tr:nth-child(3) td.rank {{ color: var(--bronze); }}

  h2 {{
    font-size: 1.2rem;
    font-weight: 600;
    margin-bottom: 1rem;
    color: var(--text-dim);
  }}
  .manager-detail {{
    background: var(--card);
    border: 1px solid var(--border);
    border-radius: 10px;
    margin-bottom: 0.5rem;
    overflow: hidden;
  }}
  .manager-detail summary {{
    padding: 0.75rem 1rem;
    cursor: pointer;
    font-weight: 600;
    font-size: 0.95rem;
    user-select: none;
    list-style: none;
  }}
  .manager-detail summary::-webkit-details-marker {{ display: none; }}
  .manager-detail summary::before {{
    content: '▸';
    display: inline-block;
    margin-right: 0.5rem;
    transition: transform 0.2s;
    color: var(--accent);
  }}
  .manager-detail[open] summary::before {{ transform: rotate(90deg); }}
  .rider-table {{
    width: 100%;
    border-collapse: collapse;
    font-size: 0.85rem;
  }}
  .rider-table th {{
    text-align: left;
    padding: 0.5rem 1rem;
    color: var(--text-dim);
    font-weight: 500;
    font-size: 0.75rem;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    border-bottom: 1px solid var(--border);
  }}
  .rider-table td {{
    padding: 0.5rem 1rem;
    border-bottom: 1px solid var(--border);
  }}
  .rider-table tr:last-child td {{ border-bottom: none; }}
  .rider-table td.points {{ font-weight: 600; font-variant-numeric: tabular-nums; }}
  .rider-table td.team {{ color: var(--text-dim); font-size: 0.8rem; }}
  .rider-table td.rider-rank {{ color: var(--text-dim); font-variant-numeric: tabular-nums; }}

  .updated {{
    text-align: center;
    color: var(--text-dim);
    font-size: 0.75rem;
    margin-top: 2rem;
    padding-top: 1rem;
    border-top: 1px solid var(--border);
  }}
  @media (max-width: 600px) {{
    body {{ padding: 1rem 0.5rem; }}
    h1 {{ font-size: 1.4rem; }}
    table.standings td, table.standings th {{ padding: 0.5rem 0.6rem; font-size: 0.85rem; }}
    .rider-table td, .rider-table th {{ padding: 0.4rem 0.6rem; }}
  }}
</style>
</head>
<body>
<div class="container">
  <h1>2026 Fantasy Cycling League</h1>
  <p class="subtitle">Points sourced from ProCyclingStats season ranking</p>

  <table class="standings">
    <thead>
      <tr><th>Rank</th><th>Manager</th><th>Points</th><th>Nulls</th></tr>
    </thead>
    <tbody>
{standings_rows}    </tbody>
  </table>

  <h2>Rider Breakdown</h2>
{detail_sections}
  <p class="updated">Last updated: {now}</p>
</div>
</body>
</html>"""

    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(html)
    print(f"Written: {path}")


def log_missing_riders(ranking: dict[str, dict]):
    """Log any drafted riders not found in the PCS ranking."""
    missing = []
    for manager, riders in TEAMS.items():
        for rider in riders:
            if rider not in ranking:
                missing.append((manager, rider))
    if missing:
        print(f"\n⚠ {len(missing)} drafted rider(s) NOT found in PCS ranking:")
        for manager, rider in missing:
            print(f"  {rider} ({manager})")
    else:
        print("\n✓ All 72 drafted riders found in PCS ranking")


def main():
    base_dir = os.path.dirname(os.path.abspath(__file__))

    # Step 1: Fetch rankings
    raw = fetch_rankings()

    # Step 2: Build lookup
    ranking = build_ranking_lookup(raw)

    # Step 3: Check for missing riders
    log_missing_riders(ranking)

    # Step 4: Compute league table
    standings = compute_league_table(ranking)

    # Step 5: Print summary
    print("\n=== League Standings ===")
    for entry in standings:
        print(f"  {entry['rank']}. {entry['manager']}: {entry['points']:,} pts (nulls: {entry['null_count']})")

    # Step 6: Write outputs
    write_league_csv(standings, os.path.join(base_dir, "league_table.csv"))
    write_detailed_csv(ranking, os.path.join(base_dir, "league_detailed.csv"))
    write_ranking_csv(ranking, os.path.join(base_dir, "ranking.csv"))
    generate_html(standings, ranking, os.path.join(base_dir, "docs", "index.html"))

    print("\nDone!")


if __name__ == "__main__":
    main()
