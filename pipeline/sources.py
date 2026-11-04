"""Data sources: Odds API, Action Network (splits/open), ESPN (schedule, injuries, box scores), Basketball-Reference."""
import io, os, re, urllib.parse
from datetime import timedelta
import pandas as pd
from common import get, get_json, log, team, pname, now_et, to_et, BBREF_ABBR

ODDS = "https://api.the-odds-api.com/v4"
BOOKS = "fanduel,pinnacle,draftkings,betmgm,williamhill_us,betrivers,espnbet,fanatics,betonlineag,lowvig"
CREDITS = {"remaining": None, "used": None}

def _key():
    k = os.environ.get("ODDS_API_KEY", "").strip()
    if not k: raise RuntimeError("ODDS_API_KEY secret is not set")
    return k

def _track(h):
    CREDITS["remaining"] = h.get("x-requests-remaining") or h.get("X-Requests-Remaining") or CREDITS["remaining"]
    CREDITS["used"] = h.get("x-requests-used") or h.get("X-Requests-Used") or CREDITS["used"]

def odds_games(sport="basketball_nba", day=None):
    """Game markets for games starting on ET date `day` (datetime.date)."""
    day = day or now_et().date()
    start = f"{day.isoformat()}T05:00:00Z"; end = f"{(day + timedelta(days=1)).isoformat()}T09:00:00Z"
    q = urllib.parse.urlencode({"apiKey": _key(), "bookmakers": BOOKS, "markets": "h2h,spreads,totals",
                                "oddsFormat": "american", "commenceTimeFrom": start, "commenceTimeTo": end})
    data, h = get_json(f"{ODDS}/sports/{sport}/odds?{q}"); _track(h)
    log(f"odds {sport}: {len(data)} games, credits remaining {CREDITS['remaining']}")
    return data

PROP_MARKETS = "player_points,player_rebounds,player_assists"
def odds_props(event_id, sport="basketball_nba"):
    q = urllib.parse.urlencode({"apiKey": _key(), "bookmakers": "fanduel,pinnacle,draftkings,betmgm,williamhill_us",
                                "markets": PROP_MARKETS, "oddsFormat": "american"})
    data, h = get_json(f"{ODDS}/sports/{sport}/events/{event_id}/odds?{q}"); _track(h)
    return data

def action_network(day=None):
    """Open line (book 30), consensus + public/money splits (book 15) keyed by (away, home)."""
    day = day or now_et().date()
    d, _ = get_json(f"https://api.actionnetwork.com/web/v1/scoreboard/nba?period=game&bookIds=15,30&date={day.strftime('%Y%m%d')}")
    out = {}
    for g in d.get("games", []):
        tm = {t["id"]: team(t["full_name"]) for t in g.get("teams", [])}
        key = (tm.get(g["away_team_id"]), tm.get(g["home_team_id"]))
        books = {o.get("book_id"): o for o in g.get("odds", []) if o.get("type", "game") == "game"}
        cons, opn = books.get(15, {}), books.get(30, {})
        out[key] = dict(
            open_spread_home=opn.get("spread_home"), open_total=opn.get("total"),
            cur_spread_home=cons.get("spread_home"), cur_total=cons.get("total"),
            spread_home_tix=cons.get("spread_home_public"), spread_away_tix=cons.get("spread_away_public"),
            spread_home_money=cons.get("spread_home_money"), spread_away_money=cons.get("spread_away_money"),
            over_tix=cons.get("total_over_public"), under_tix=cons.get("total_under_public"),
            over_money=cons.get("total_over_money"), under_money=cons.get("total_under_money"),
            num_bets=g.get("num_bets"))
    log(f"action network: {len(out)} games, splits on {sum(v['spread_home_tix'] is not None for v in out.values())}")
    return out

def espn_injuries():
    d, _ = get_json("https://site.api.espn.com/apis/site/v2/sports/basketball/nba/injuries")
    out = {}
    for t in d.get("injuries", []):
        tn = team(t.get("displayName"))
        for i in t.get("injuries", []):
            a = i.get("athlete", {})
            out.setdefault(tn, []).append(dict(name=a.get("displayName"), key=pname(a.get("displayName")),
                                               status=i.get("status"), date=i.get("date"), note=i.get("shortComment", "")))
    log(f"injuries: {sum(len(v) for v in out.values())} listed across {len(out)} teams")
    return out

def espn_scoreboard(day, path="basketball/nba"):
    d, _ = get_json(f"https://site.api.espn.com/apis/site/v2/sports/{path}/scoreboard?dates={day.strftime('%Y%m%d')}&limit=300")
    games = []
    for ev in d.get("events", []):
        c = ev["competitions"][0]
        side = {x["homeAway"]: x for x in c["competitors"]}
        games.append(dict(id=ev["id"], date=ev["date"], home=team(side["home"]["team"]["displayName"]),
                          away=team(side["away"]["team"]["displayName"]),
                          home_pts=int(side["home"].get("score") or 0), away_pts=int(side["away"].get("score") or 0),
                          final=c["status"]["type"].get("completed", False)))
    return games

def espn_box(event_id):
    """{player_key: {'pts','reb','ast','min'}} from ESPN game summary."""
    d, _ = get_json(f"https://site.api.espn.com/apis/site/v2/sports/basketball/nba/summary?event={event_id}")
    out = {}
    for tb in d.get("boxscore", {}).get("players", []):
        for st in tb.get("statistics", []):
            labels = [l.upper() for l in st.get("labels", [])]
            for a in st.get("athletes", []):
                vals = dict(zip(labels, a.get("stats", [])))
                if not vals: continue
                try:
                    out[pname(a["athlete"]["displayName"])] = dict(pts=float(vals.get("PTS", 0)), reb=float(vals.get("REB", 0)),
                                                                   ast=float(vals.get("AST", 0)), min=vals.get("MIN"))
                except ValueError: pass
    return out

# ---------- Basketball-Reference ----------
def _tables(url):
    html, _ = get(url)
    html = html.replace("<!--", "").replace("-->", "")
    return html, pd.read_html(io.StringIO(html))

def _num(df, cols):
    for c in cols:
        if c in df: df[c] = pd.to_numeric(df[c], errors="coerce")
    return df

def bbref_teams(season):
    """Team advanced table: ORtg, DRtg, NRtg, Pace, G per team (full name)."""
    _, tabs = _tables(f"https://www.basketball-reference.com/leagues/NBA_{season}.html")
    adv = None
    for t in tabs:
        cols = [c[-1] if isinstance(c, tuple) else c for c in t.columns]
        if "ORtg" in cols and "DRtg" in cols and "Pace" in cols:
            t = t.copy(); t.columns = cols; adv = t; break
    if adv is None: raise RuntimeError("advanced team table not found")
    adv = adv[adv["Team"].notna() & (adv["Team"] != "League Average")]
    adv["Team"] = adv["Team"].map(team)
    adv = _num(adv, ["ORtg", "DRtg", "NRtg", "Pace", "MOV", "SRS", "W", "L"])
    adv["G"] = adv["W"] + adv["L"]
    return {r.Team: dict(ortg=r.ORtg, drtg=r.DRtg, pace=r.Pace, g=r.G) for r in adv.itertuples() if r.Team in BBREF_ABBR.values()}

def bbref_players(season):
    """Per-game + advanced (USG%, BPM) per player; latest team for traded players."""
    _, pg = _tables(f"https://www.basketball-reference.com/leagues/NBA_{season}_per_game.html")
    _, ad = _tables(f"https://www.basketball-reference.com/leagues/NBA_{season}_advanced.html")
    pg, ad = pg[0], ad[0]
    for df in (pg, ad):
        df.drop(df[df["Player"] == "Player"].index, inplace=True)
        df.drop(df[df["Player"].isna()].index, inplace=True)
    pg = _num(pg, ["G", "GS", "MP", "PTS", "TRB", "AST", "3P", "FGA", "FTA", "TOV"])
    ad = _num(ad, ["USG%", "BPM", "MP", "G"])
    out = {}
    for r in pg.to_dict("records"):
        k = pname(r["Player"]); tm = r["Team"]
        if tm in ("2TM", "3TM", "4TM", "TOT"): continue           # keep per-team rows; last one wins = current team
        usg = ad.loc[(ad["Player"].map(pname) == k) & (ad["Team"] == tm), "USG%"]
        bpm = ad.loc[(ad["Player"].map(pname) == k) & (ad["Team"] == tm), "BPM"]
        out[k] = dict(name=r["Player"], team=BBREF_ABBR.get(tm, tm), g=r["G"], gs=r.get("GS"), mp=r["MP"], pts=r["PTS"],
                      reb=r["TRB"], ast=r["AST"], fg3=r.get("3P"), usg=float(usg.iloc[0]) if len(usg) else None,
                      bpm=float(bpm.iloc[0]) if len(bpm) else None)
    log(f"bbref players {season}: {len(out)}")
    return out
