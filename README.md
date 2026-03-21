# 2026 Fantasy Cycling League

Automated fantasy cycling league table, powered by [ProCyclingStats](https://www.procyclingstats.com/) season rankings.

## Overview

9 managers each drafted 8 riders. Each rider's points come from the PCS individual ranking. The manager's total is the sum of their 8 riders' points. This script fetches the latest rankings, computes standings, and publishes an HTML league table via GitHub Pages.

## Managers & Riders

| Manager | Riders |
|---------|--------|
| Nick | Ayuso, Vingegaard, Vine, Narváez, Tarling, Williams, Fortunato, O'Connor |
| Tim | Philipsen, Kooij, Merlier, Pedersen, Strong, Christen, Blackmore, Hindley |
| Cameron | Healy, Jorgenson, Pidcock T., Onley, Roglič, Arensman, Ganna, Alaphilippe |
| Andy | Evenepoel, Brennan, McNulty, Almeida, Grégoire, Moschetti, Yates, Uijtdebroeks |
| Dave | Vauquelin, Ciccone, Gall, Del Toro, Martinez, Powless, Girmay, Jeannière |
| Mike | De Lie, Skjelmose, Magnier, Van der Poel, Carapaz, Nys, Seixas, Gee |
| Campbell | Van Aert, Lipowitz, Milan, Scaroni, Wright, Pellizzari, Storer, Del Grosso |
| Joe | Pidcock J., Craps, Jakobsen, Söderqvist, Widar, Hirschi, Wang, Pogačar |
| Geminiani's Hipsters | Bisiaux, Nordhagen, Lamperti, August, Agostinacchio, Finn, Tulett, Abrahamsen |

## Points Calculation

- Each rider's points = their PCS season ranking points (0 if not ranked)
- Manager total = sum of all 8 riders' points
- Null count = number of riders with 0 points

## Setup

```bash
pip install -r requirements.txt
python update_league.py
```

## Output Files

| File | Description |
|------|-------------|
| `docs/index.html` | Styled HTML league table (served by GitHub Pages) |
| `league_table.csv` | Rank, Manager, Points, Null Count |
| `league_detailed.csv` | Manager, Rider, Points (per-rider breakdown) |
| `ranking.csv` | Full PCS ranking with manager column for drafted riders |

## GitHub Pages

The league table is published automatically:
1. Push this repo to GitHub
2. Go to Settings → Pages → Source: "Deploy from a branch", Branch: `main`, Folder: `/docs`
3. The table is live at `https://<username>.github.io/fantasy-cycling/`

## GitHub Actions

The workflow runs every Monday at 8am UTC (and can be triggered manually). It fetches the latest PCS rankings, regenerates all outputs, and commits any changes.

## Name Matching

PCS returns names as `Pogačar Tadej`. The script converts to `POGAČAR Tadej` (uppercase surname) to match the spreadsheet format. Any drafted riders not found in the ranking are logged as warnings.
