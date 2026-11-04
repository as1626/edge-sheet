# The Edge Sheet

Model projections vs. FanDuel (Ohio) lines, published to https://as1626.github.io/edge-sheet

Runs on GitHub Actions (`.github/workflows/pipeline.yml`):

| ET time | Job | What it does |
|---|---|---|
| 4:45 PM | card | Odds (Odds API), betting splits + open lines (Action Network), injuries (ESPN), ratings and player stats (Basketball-Reference) → NBA sides, totals and props → `data/ledger.json` |
| 6:50, 8:20, 9:50 PM | close | Pinnacle closing lines for plays about to tip (closing-line value) |
| 3:30 AM, 9:00 AM | grade | Final scores and box scores (ESPN) → results |

Every run rebuilds `index.html`. Logs: `data/logs/last_run.json`. Requires repo secret `ODDS_API_KEY`.
Manual run: Actions → pipeline → Run workflow, or write `card|close|grade|page` to `triggers/cmd` and push.
