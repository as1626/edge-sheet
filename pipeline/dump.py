"""One-off: fetch each source and save compact samples to debug/ for schema inspection."""
import json, os, re, io, urllib.request, datetime as dt
import pandas as pd
os.makedirs("debug", exist_ok=True)
UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/124 Safari/537.36"}
def get(u):
    with urllib.request.urlopen(urllib.request.Request(u, headers=UA), timeout=30) as r: return r.read().decode("utf-8", "replace")
today = (dt.datetime.utcnow() - dt.timedelta(hours=5)).strftime("%Y%m%d")
out = {}
try:
    an = json.loads(get(f"https://api.actionnetwork.com/web/v1/scoreboard/nba?period=game&bookIds=15,30,68,69,71,75,76,79&date={today}"))
    g = an["games"][0] if an.get("games") else {}
    out["an_keys"] = list(an.keys()); out["an_ngames"] = len(an.get("games", []))
    out["an_game_keys"] = list(g.keys())
    out["an_teams0"] = g.get("teams", [None])[0]
    out["an_odds"] = g.get("odds", [])[:8]
    out["an_misc"] = {k: g.get(k) for k in ("id","start_time","home_team_id","away_team_id","status","num_bets")}
except Exception as e: out["an_err"] = repr(e)
try:
    inj = json.loads(get("https://site.api.espn.com/apis/site/v2/sports/basketball/nba/injuries"))
    t0 = inj["injuries"][0]
    out["inj_team_keys"] = list(t0.keys()); out["inj_team0_name"] = t0.get("displayName")
    out["inj_item0"] = t0["injuries"][0] if t0.get("injuries") else None
except Exception as e: out["inj_err"] = repr(e)
try:
    sb = json.loads(get(f"https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard?dates={today}"))
    ev = sb["events"][0]; c = ev["competitions"][0]
    out["sb_n"] = len(sb["events"]); out["sb_ev"] = {k: ev.get(k) for k in ("id","date","name","shortName")}
    out["sb_comp_keys"] = list(c.keys())
    out["sb_competitor0"] = {k: c["competitors"][0].get(k) for k in ("homeAway","score","team")}
    out["sb_venue"] = c.get("venue")
except Exception as e: out["sb_err"] = repr(e)
for name, url in [("team", "https://www.basketball-reference.com/leagues/NBA_2027.html"),
                  ("padv", "https://www.basketball-reference.com/leagues/NBA_2027_advanced.html"),
                  ("ppg", "https://www.basketball-reference.com/leagues/NBA_2027_per_game.html")]:
    try:
        h = get(url).replace("<!--", "").replace("-->", "")
        tabs = pd.read_html(io.StringIO(h))
        ids = re.findall(r'<table[^>]*id="([^"]+)"', h)
        out[f"bbref_{name}"] = {"ids": ids, "shapes": [t.shape for t in tabs],
                                "cols": [[str(c) for c in t.columns][:40] for t in tabs[:12]],
                                "head": tabs[min(len(tabs)-1, 0)].head(2).astype(str).values.tolist()}
        for i, t in enumerate(tabs[:12]): t.head(3).to_csv(f"debug/{name}_{i}.csv", index=False)
    except Exception as e: out[f"bbref_{name}_err"] = repr(e)
json.dump(out, open("debug/schema.json", "w"), indent=1, default=str)
print(json.dumps(out, default=str)[:3000])
