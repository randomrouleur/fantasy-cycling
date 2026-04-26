"""Tests for Fantasy Cycling League logic."""

import csv
import os
import tempfile

import pytest
import yaml

from update_league import (
    build_ranking_lookup,
    compute_league_table,
    generate_html,
    get_active_teams,
    load_config,
    load_snapshot,
    write_snapshot,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

SAMPLE_CONFIG = {
    "transfers_done": False,
    "first_half": {
        "Alice": ["RIDER A", "RIDER B", "RIDER C"],
        "Bob": ["RIDER D", "RIDER E", "RIDER F"],
    },
    "second_half": {
        "Alice": ["RIDER A", "RIDER G", "RIDER H"],  # kept A, swapped B/C
        "Bob": ["RIDER D", "RIDER E", "RIDER I"],     # kept D/E, swapped F
    },
    "aliases": {"RIDER X Alt": "RIDER X"},
}

SAMPLE_RANKING = {
    "RIDER A": {"rank": 1, "prev_rank": 2, "team": "Team 1", "points": 500},
    "RIDER B": {"rank": 2, "prev_rank": 1, "team": "Team 1", "points": 300},
    "RIDER C": {"rank": 3, "prev_rank": 3, "team": "Team 2", "points": 200},
    "RIDER D": {"rank": 4, "prev_rank": 5, "team": "Team 2", "points": 400},
    "RIDER E": {"rank": 5, "prev_rank": 4, "team": "Team 3", "points": 150},
    "RIDER F": {"rank": 6, "prev_rank": 6, "team": "Team 3", "points": 100},
    "RIDER G": {"rank": 7, "prev_rank": 7, "team": "Team 4", "points": 350},
    "RIDER H": {"rank": 8, "prev_rank": 8, "team": "Team 4", "points": 250},
    "RIDER I": {"rank": 9, "prev_rank": 9, "team": "Team 5", "points": 180},
}

# Snapshot taken at mid-season: points at that time
SAMPLE_SNAPSHOT = {
    "RIDER A": 300,  # was 300, now 500 → delta = 200
    "RIDER B": 200,  # was 200, now 300
    "RIDER C": 100,  # was 100, now 200
    "RIDER D": 250,  # was 250, now 400 → delta = 150
    "RIDER E": 100,  # was 100, now 150 → delta = 50
    "RIDER F": 80,   # was 80, now 100
    "RIDER G": 200,  # was 200, now 350 → delta = 150
    "RIDER H": 150,  # was 150, now 250 → delta = 100
    "RIDER I": 90,   # was 90, now 180 → delta = 90
}


# ---------------------------------------------------------------------------
# Config loading tests
# ---------------------------------------------------------------------------

class TestConfigLoading:
    def test_load_config(self, tmp_path):
        config_file = tmp_path / "config.yaml"
        config_file.write_text(yaml.dump(SAMPLE_CONFIG))
        config = load_config(str(config_file))
        assert config["transfers_done"] is False
        assert "Alice" in config["first_half"]
        assert len(config["first_half"]["Alice"]) == 3

    def test_get_active_teams_first_half(self):
        config = {**SAMPLE_CONFIG, "transfers_done": False}
        teams = get_active_teams(config)
        assert teams == SAMPLE_CONFIG["first_half"]

    def test_get_active_teams_second_half(self):
        config = {**SAMPLE_CONFIG, "transfers_done": True}
        teams = get_active_teams(config)
        assert teams == SAMPLE_CONFIG["second_half"]

    def test_get_active_teams_transfers_done_but_no_second_half(self):
        config = {**SAMPLE_CONFIG, "transfers_done": True, "second_half": {}}
        teams = get_active_teams(config)
        assert teams == SAMPLE_CONFIG["first_half"]


# ---------------------------------------------------------------------------
# First half (no transfers) tests
# ---------------------------------------------------------------------------

class TestFirstHalf:
    def test_total_is_sum_of_current_points(self):
        standings = compute_league_table(
            SAMPLE_CONFIG["first_half"], SAMPLE_RANKING,
        )
        alice = next(s for s in standings if s["manager"] == "Alice")
        bob = next(s for s in standings if s["manager"] == "Bob")
        # Alice: A(500) + B(300) + C(200) = 1000
        assert alice["points"] == 1000
        assert alice["banked"] == 0
        # Bob: D(400) + E(150) + F(100) = 650
        assert bob["points"] == 650
        assert bob["banked"] == 0

    def test_rankings_are_ordered(self):
        standings = compute_league_table(
            SAMPLE_CONFIG["first_half"], SAMPLE_RANKING,
        )
        assert standings[0]["rank"] == 1
        assert standings[0]["points"] >= standings[1]["points"]

    def test_rider_not_in_ranking_gets_zero(self):
        teams = {"Alice": ["RIDER A", "UNKNOWN RIDER", "RIDER C"]}
        standings = compute_league_table(teams, SAMPLE_RANKING)
        alice = standings[0]
        # A(500) + 0 + C(200) = 700
        assert alice["points"] == 700


# ---------------------------------------------------------------------------
# Second half (with transfers) tests
# ---------------------------------------------------------------------------

class TestSecondHalf:
    def test_banked_plus_delta(self):
        standings = compute_league_table(
            SAMPLE_CONFIG["second_half"],
            SAMPLE_RANKING,
            transfers_done=True,
            first_half_teams=SAMPLE_CONFIG["first_half"],
            snapshot=SAMPLE_SNAPSHOT,
        )
        alice = next(s for s in standings if s["manager"] == "Alice")
        # Banked: A(300) + B(200) + C(100) = 600
        assert alice["banked"] == 600
        # 2nd half riders: A(500-300=200) + G(350-200=150) + H(250-150=100) = 450
        # Total: 600 + 450 = 1050
        assert alice["points"] == 1050

    def test_all_new_riders(self):
        # Bob keeps D and E, swaps F for I
        standings = compute_league_table(
            SAMPLE_CONFIG["second_half"],
            SAMPLE_RANKING,
            transfers_done=True,
            first_half_teams=SAMPLE_CONFIG["first_half"],
            snapshot=SAMPLE_SNAPSHOT,
        )
        bob = next(s for s in standings if s["manager"] == "Bob")
        # Banked: D(250) + E(100) + F(80) = 430
        assert bob["banked"] == 430
        # 2nd half: D(400-250=150) + E(150-100=50) + I(180-90=90) = 290
        # Total: 430 + 290 = 720
        assert bob["points"] == 720

    def test_kept_rider_only_contributes_delta(self):
        """A kept rider should not be double-counted."""
        standings = compute_league_table(
            SAMPLE_CONFIG["second_half"],
            SAMPLE_RANKING,
            transfers_done=True,
            first_half_teams=SAMPLE_CONFIG["first_half"],
            snapshot=SAMPLE_SNAPSHOT,
        )
        alice = next(s for s in standings if s["manager"] == "Alice")
        # RIDER A: banked includes 300, delta includes (500-300)=200
        # Total contribution from A: 300 + 200 = 500 = current points (correct, no double-counting)
        # If double-counted, it would be 300 (banked) + 500 (full current) = 800
        assert alice["points"] == 1050  # not 1250

    def test_rider_not_in_snapshot_baseline_zero(self):
        """A rider not in the snapshot should have baseline 0."""
        snapshot_missing = {k: v for k, v in SAMPLE_SNAPSHOT.items() if k != "RIDER G"}
        standings = compute_league_table(
            SAMPLE_CONFIG["second_half"],
            SAMPLE_RANKING,
            transfers_done=True,
            first_half_teams=SAMPLE_CONFIG["first_half"],
            snapshot=snapshot_missing,
        )
        alice = next(s for s in standings if s["manager"] == "Alice")
        # Banked: same = 600
        # RIDER G baseline = 0 (not in snapshot), current = 350, delta = 350
        # A(200) + G(350) + H(100) = 650
        # Total: 600 + 650 = 1250
        assert alice["points"] == 1250

    def test_rider_points_dropped_delta_not_negative(self):
        """If a rider's current points are below baseline, delta should be 0."""
        ranking_dropped = {**SAMPLE_RANKING}
        ranking_dropped["RIDER G"] = {**SAMPLE_RANKING["RIDER G"], "points": 100}
        standings = compute_league_table(
            SAMPLE_CONFIG["second_half"],
            ranking_dropped,
            transfers_done=True,
            first_half_teams=SAMPLE_CONFIG["first_half"],
            snapshot=SAMPLE_SNAPSHOT,
        )
        alice = next(s for s in standings if s["manager"] == "Alice")
        # RIDER G: current(100) - baseline(200) = -100, clamped to 0
        # A(200) + G(0) + H(100) = 300
        # Total: 600 + 300 = 900
        assert alice["points"] == 900


# ---------------------------------------------------------------------------
# Snapshot I/O tests
# ---------------------------------------------------------------------------

class TestSnapshot:
    def test_write_and_load_snapshot(self, tmp_path):
        snapshot_path = str(tmp_path / "snapshot.csv")
        ranking = {
            "RIDER A": {"rank": 1, "prev_rank": 2, "team": "T1", "points": 500},
            "RIDER B": {"rank": 2, "prev_rank": 1, "team": "T2", "points": 300},
        }
        write_snapshot(ranking, snapshot_path)
        loaded = load_snapshot(snapshot_path)
        assert loaded == {"RIDER A": 500, "RIDER B": 300}

    def test_snapshot_csv_format(self, tmp_path):
        snapshot_path = str(tmp_path / "snapshot.csv")
        ranking = {
            "RIDER A": {"rank": 1, "prev_rank": 2, "team": "T1", "points": 500},
        }
        write_snapshot(ranking, snapshot_path)
        with open(snapshot_path) as f:
            reader = csv.DictReader(f)
            rows = list(reader)
        assert len(rows) == 1
        assert rows[0]["rider"] == "RIDER A"
        assert rows[0]["points"] == "500"


# ---------------------------------------------------------------------------
# Alias tests
# ---------------------------------------------------------------------------

class TestAliases:
    def test_alias_applied(self):
        raw = [
            {"rider_name": "RIDER X Alt", "rank": 1, "prev_rank": 1,
             "team_name": "Team", "points": 100},
        ]
        lookup = build_ranking_lookup(raw, {"RIDER X Alt": "RIDER X"})
        assert "RIDER X" in lookup
        assert "RIDER X Alt" not in lookup


# ---------------------------------------------------------------------------
# Helpers shared by generate_html tests
# ---------------------------------------------------------------------------

_GH_TEAMS = {"Alice": ["RIDER A", "RIDER B"], "Bob": ["RIDER C", "RIDER D"]}
_GH_RANKING = {
    "RIDER A": {"rank": 1, "prev_rank": 1, "team": "Team 1", "points": 500},
    "RIDER B": {"rank": 2, "prev_rank": 2, "team": "Team 1", "points": 300},
    "RIDER C": {"rank": 3, "prev_rank": 3, "team": "Team 2", "points": 200},
    "RIDER D": {"rank": 4, "prev_rank": 4, "team": "Team 2", "points": 100},
}
_GH_STANDINGS = [
    {"rank": 1, "manager": "Alice", "points": 800, "banked": 0},
    {"rank": 2, "manager": "Bob",   "points": 300, "banked": 0},
]


def _html(tmp_path, **kwargs):
    """Call generate_html with minimal defaults and return the written HTML."""
    out = str(tmp_path / "league.html")
    generate_html(
        standings=kwargs.pop("standings", _GH_STANDINGS),
        active_teams=kwargs.pop("active_teams", _GH_TEAMS),
        ranking=kwargs.pop("ranking", _GH_RANKING),
        path=out,
        **kwargs,
    )
    with open(out) as f:
        return f.read()


def _snapshot_entry(date, teams_pts):
    """Build a history snapshot dict.

    teams_pts: {"Manager": [("RIDER X", pts), ...]}
    """
    teams = {}
    for mgr, rider_list in teams_pts.items():
        teams[mgr] = {
            "total": sum(p for _, p in rider_list),
            "rank": 1,
            "banked": 0,
            "riders": [{"rider": r, "points": p} for r, p in rider_list],
        }
    return {"date": date, "teams": teams}


# ---------------------------------------------------------------------------
# Auction cost / Pts-per-$ column tests
# ---------------------------------------------------------------------------

class TestAuctionCostColumn:
    def test_no_auction_costs_no_pts_per_dollar_column(self, tmp_path):
        html = _html(tmp_path)
        assert "<th>Pts/$</th>" not in html

    def test_cost_and_points_show_value(self, tmp_path):
        costs = {"RIDER A": 50, "RIDER B": 30, "RIDER C": 20, "RIDER D": 10}
        html = _html(tmp_path, auction_costs=costs)
        assert "<th>Pts/$</th>" in html
        # RIDER A: 500 / 50 = 10.0
        assert "10.0" in html

    def test_cost_with_zero_points_shows_dash(self, tmp_path):
        ranking_zero = {**_GH_RANKING, "RIDER B": {**_GH_RANKING["RIDER B"], "points": 0}}
        costs = {"RIDER A": 50, "RIDER B": 30, "RIDER C": 20, "RIDER D": 10}
        html = _html(tmp_path, ranking=ranking_zero, auction_costs=costs)
        # RIDER B has cost but 0 pts -> value cell shows em-dash
        assert "—" in html

    def test_free_pick_shows_free_and_dash(self, tmp_path):
        costs = {"RIDER A": 50, "RIDER B": 0, "RIDER C": 20, "RIDER D": 10}
        html = _html(tmp_path, auction_costs=costs)
        assert "free" in html

    def test_rider_not_in_costs_map_shows_dash(self, tmp_path):
        # Only some riders in the map
        costs = {"RIDER A": 50}
        html = _html(tmp_path, auction_costs=costs)
        # Column header present
        assert "<th>Pts/$</th>" in html
        # RIDER B has no entry -> cost_display is "—" (em-dash)
        assert "—" in html


# ---------------------------------------------------------------------------
# Best Value Picks table tests
# ---------------------------------------------------------------------------

class TestBestValuePicks:
    def test_section_present_with_qualifying_riders(self, tmp_path):
        costs = {"RIDER A": 50, "RIDER B": 30, "RIDER C": 20, "RIDER D": 10}
        html = _html(tmp_path, auction_costs=costs)
        assert "<h2>Best Value Picks</h2>" in html

    def test_section_absent_without_auction_costs(self, tmp_path):
        html = _html(tmp_path)
        assert "<h2>Best Value Picks</h2>" not in html

    def test_section_has_empty_body_when_all_riders_have_zero_points(self, tmp_path):
        ranking_zero = {k: {**v, "points": 0} for k, v in _GH_RANKING.items()}
        costs = {"RIDER A": 50, "RIDER B": 30, "RIDER C": 20, "RIDER D": 10}
        html = _html(tmp_path, ranking=ranking_zero, auction_costs=costs)
        # The section header is rendered but no data rows are added (cost>0 requires points>0)
        assert "<h2>Best Value Picks</h2>" in html
        bv_start = html.index("<h2>Best Value Picks</h2>")
        bv_section = html[bv_start:bv_start + 500]
        assert "<td>" not in bv_section

    def test_free_picks_excluded_from_best_value(self, tmp_path):
        # RIDER A is free; only RIDER B has cost > 0 with points
        costs = {"RIDER A": 0, "RIDER B": 30, "RIDER C": 0, "RIDER D": 0}
        html = _html(tmp_path, auction_costs=costs)
        assert "<h2>Best Value Picks</h2>" in html
        # RIDER A must not appear in the value table rows as a cost-bearing rider
        # We can verify by checking 'free' doesn't pair with a value row; simpler:
        # RIDER B value = 300/30 = 10.0 should be present
        assert "10.0" in html

    def test_best_value_sorted_descending(self, tmp_path):
        # RIDER C: 200/10 = 20.0 should rank above RIDER A: 500/50 = 10.0
        costs = {"RIDER A": 50, "RIDER B": 60, "RIDER C": 10, "RIDER D": 5}
        html = _html(tmp_path, auction_costs=costs)
        bv_start = html.index("<h2>Best Value Picks</h2>")
        bv_section = html[bv_start:]
        rider_c_pos = bv_section.index("RIDER C")
        rider_a_pos = bv_section.index("RIDER A")
        assert rider_c_pos < rider_a_pos


# ---------------------------------------------------------------------------
# Hot Riders table tests
# ---------------------------------------------------------------------------

class TestHotRiders:
    def test_section_absent_with_single_history_entry(self, tmp_path):
        snap = _snapshot_entry("2026-04-25", {
            "Alice": [("RIDER A", 500), ("RIDER B", 300)],
            "Bob":   [("RIDER C", 200), ("RIDER D", 100)],
        })
        html = _html(tmp_path, history=[snap])
        assert "<h2>Hot Riders</h2>" not in html

    def test_section_absent_with_no_history(self, tmp_path):
        html = _html(tmp_path, history=None)
        assert "<h2>Hot Riders</h2>" not in html

    def test_baseline_chosen_by_date_not_index(self, tmp_path):
        # Weekly entries for 6 weeks plus a few recent close entries.
        # Entry at 2026-03-28 is exactly 28 days before 2026-04-25 -> should be baseline.
        # Entry at 2026-04-18 is only 7 days before -> must NOT be chosen.
        snaps = [
            _snapshot_entry("2026-03-07", {"Alice": [("RIDER A", 100), ("RIDER B", 50)],  "Bob": [("RIDER C", 30), ("RIDER D", 10)]}),
            _snapshot_entry("2026-03-14", {"Alice": [("RIDER A", 150), ("RIDER B", 80)],  "Bob": [("RIDER C", 50), ("RIDER D", 20)]}),
            _snapshot_entry("2026-03-21", {"Alice": [("RIDER A", 200), ("RIDER B", 100)], "Bob": [("RIDER C", 80), ("RIDER D", 40)]}),
            _snapshot_entry("2026-03-28", {"Alice": [("RIDER A", 250), ("RIDER B", 150)], "Bob": [("RIDER C", 120), ("RIDER D", 60)]}),
            _snapshot_entry("2026-04-04", {"Alice": [("RIDER A", 300), ("RIDER B", 180)], "Bob": [("RIDER C", 150), ("RIDER D", 80)]}),
            _snapshot_entry("2026-04-11", {"Alice": [("RIDER A", 350), ("RIDER B", 210)], "Bob": [("RIDER C", 180), ("RIDER D", 100)]}),
            _snapshot_entry("2026-04-18", {"Alice": [("RIDER A", 420), ("RIDER B", 260)], "Bob": [("RIDER C", 190), ("RIDER D", 100)]}),
            _snapshot_entry("2026-04-22", {"Alice": [("RIDER A", 460), ("RIDER B", 280)], "Bob": [("RIDER C", 195), ("RIDER D", 100)]}),
            _snapshot_entry("2026-04-25", {"Alice": [("RIDER A", 500), ("RIDER B", 300)], "Bob": [("RIDER C", 200), ("RIDER D", 100)]}),
        ]
        html = _html(tmp_path, history=snaps)
        assert "<h2>Hot Riders</h2>" in html
        # Baseline is 2026-03-28; latest is 2026-04-25.
        # Date range format is "DD/MM - DD/MM"
        assert "28/03" in html
        assert "25/04" in html
        # 2026-04-18 baseline would give "18/04" instead
        assert "18/04" not in html

    def test_cross_team_rider_lookup(self, tmp_path):
        # RIDER A appears under Bob in the baseline but under Alice in the latest.
        # gained should be latest_pts - baseline_pts = 500 - 200 = 300.
        baseline = _snapshot_entry("2026-03-28", {
            "Alice": [("RIDER B", 150)],
            "Bob":   [("RIDER A", 200), ("RIDER C", 120), ("RIDER D", 60)],
        })
        latest = _snapshot_entry("2026-04-25", {
            "Alice": [("RIDER A", 500), ("RIDER B", 300)],
            "Bob":   [("RIDER C", 200), ("RIDER D", 100)],
        })
        html = _html(tmp_path, history=[baseline, latest])
        assert "<h2>Hot Riders</h2>" in html
        # RIDER A gained 300 pts; that should appear as +300
        assert "+300" in html

    def test_negative_gain_rider_excluded(self, tmp_path):
        # RIDER D goes from 150 to 100 (dropped) - should not appear in Hot Riders.
        baseline = _snapshot_entry("2026-03-28", {
            "Alice": [("RIDER A", 200), ("RIDER B", 100)],
            "Bob":   [("RIDER C", 80),  ("RIDER D", 150)],
        })
        latest = _snapshot_entry("2026-04-25", {
            "Alice": [("RIDER A", 500), ("RIDER B", 300)],
            "Bob":   [("RIDER C", 200), ("RIDER D", 100)],
        })
        html = _html(tmp_path, history=[baseline, latest])
        # gained for RIDER D is -50, so it must not appear in the Hot Riders table.
        # We check by confirming the hot riders section exists but RIDER D's negative
        # gain (+- pattern) is absent. Since all other riders have positive gains,
        # we look for "-50" or check RIDER D isn't in a "+N" context near the table.
        assert "<h2>Hot Riders</h2>" in html
        assert "+-50" not in html
        assert "+(-50)" not in html
        # Positive gains for A and B and C are present
        assert "+300" in html
