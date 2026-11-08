"""Pipeline entry point.  python pipeline/run.py card|close|grade|page"""
import sys, os, json, time, traceback
from datetime import timedelta, datetime, timezone
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import log, LOG, load, save, now_et, to_et, am_to_dec, ROOT
import sources as S

LEDGER = "data/ledger.json"
GAMES = {}   # (sport, game) -> per-game summary for the "Every game" table

def note_game(sport, away, home, start, kind, fd_line, our_line, gap, grade, ev, side_label):
    k = (sport, f"{away} @ {home}")
    g = GAMES.setdefault(k, dict(sport=sport, game=f"{away} @ {home}", away=away, home=home, start=start, markets={}))
    cur = g["markets"].get(kind)
    if cur is None or (gap or 0) > (cur["gap"] or 0):
        g["markets"][kind] = dict(fd=fd_line, ours=None if our_line is None else round(our_line, 1), gap=gap, grade=grade or "",
                                  ev=None if ev is None else round(ev * 100, 1), side=side_label)

def note_rows(rows, sport=None):
    for r in rows:
        a, h = r["game"]
        if r["kind"] == "Spread":
            lab = f'{r["team"]} {r["line"]:+g}'
            note_game(sport or r.get("sport", "NBA"), a, h, r["start"], "Spread", r["line"], r["model_line"], r["gap"], r["grade"], r["ev"], lab)
        elif r["kind"] == "Total":
            note_game(sport or r.get("sport", "NBA"), a, h, r["start"], "Total", r["line"], r["model_line"], r["gap"], r["grade"], r["ev"], f'{r["side"].title()} {r["line"]:g}')
        elif r["kind"] == "Moneyline":
            note_game(sport or r.get("sport", "NBA"), a, h, r["start"], "Moneyline", r["price"], None, r["gap"], r["grade"], r["ev"], f'{r["team"]} ML')

def save_games(L, ds):
    keep = [g for g in GAMES.values() if to_et(g["start"]) > now_et() - timedelta(hours=4) and (to_et(g["start"]) - now_et()).total_seconds() < 30 * 3600]
    keep.sort(key=lambda g: (g["sport"], g["start"]))
    if keep: L["games"] = dict(date=ds, rows=keep)
SEASON, PREV = 2027, 2026
PROP_CREDIT_FLOOR = 120          # skip props when Odds API credits run low
STAKE_SCALE = 0.5   # half stakes until ~50 plays have closing-line data
STAKE = {k: v * STAKE_SCALE for k, v in {"A": 1.0, "B": 0.5, "C": 0.0}.items()}
PROP_STAKE = {k: v * STAKE_SCALE for k, v in {"A": 0.5, "B": 0.25, "C": 0.0}.items()}

def fmt(x): return f"{x:+.1f}".replace("+0.0", "0.0")

def cached(path, fn):
    d = load(path, None)
    if d is None: d = fn(); save(path, d)
    return d

def bet_from_side(r, day):
    a, h = r["game"]
    if r["kind"] == "Spread":
        pick = f'{r["team"]} {r["line"]:+g}'; mid = f'{day}|{a} @ {h}|Spread'
        lab = {"ratings": "Ratings", "injuries": "Injuries", "rest": "Rest", "location": "Home/court"}
    else:
        pick = f'{r["side"].title()} {r["line"]:g}'; mid = f'{day}|{a} @ {h}|Total'
        lab = {"pace": "Pace vs avg (poss)", "injuries": "Injuries"}
    why = " · ".join(f'{lab[k]} {fmt(v)}' for k, v in r["factors"].items() if abs(v) >= 0.05)
    sig = "with us" if r["sig"] >= 1 else "against us" if r["sig"] < 0 else "neutral"
    return dict(id=mid, date=day, start=r["start"], sport="NBA", engine="Factor model", game=f"{a} @ {h}", away=a, home=h,
                market=r["kind"], pick=pick, pick_team=r["team"], side=r["side"], line=r["line"], price=r["price"],
                grade=r["grade"], stake=STAKE[r["grade"]], model_line=round(r["model_line"], 1), fair_line=round(r["fair_line"], 1),
                pinnacle_open=r["pn_line"] if r["pn_line"] is not None else round(r["mkt_line"], 1), ev_pct=round(r["ev"] * 100, 1),
                gap=r["gap"], why=why, signals=r["notes"], injuries=r["inj_note"], projection=r["proj"], market_signal=sig,
                mkt_src=r["mkt_src"], close_line=None, close_src=None, status="pending", score=None, result_units=None)

def bet_from_prop(r, day):
    a, h = r["game"]
    return dict(id=f'{day}|{r["player"]}|{r["stat"]}', date=day, start=r["start"], sport="NBA", engine="Prop model",
                game=f"{a} @ {h}", away=a, home=h, market="Prop", player=r["player"], stat=r["stat"], side=r["side"],
                pick=f'{r["player"]} {r["side"]} {r["line"]:g} {r["stat_label"]}', pick_team=r["team"], event_id=r["event_id"],
                line=r["line"], price=r["price"], grade=r["grade"], stake=PROP_STAKE[r["grade"]], model_line=r["model_line"],
                fair_line=None, pinnacle_open=None, ev_pct=round(r["ev"] * 100, 1), gap=r["gap"], why=r["why"], signals=[],
                market_signal="neutral", mkt_src=r["mkt_src"], close_line=None, close_src=None, status="pending", score=None,
                result_units=None)

def add_multi(L, bets, ds, refresh=True):
    started = lambda b: to_et(b["start"]) <= now_et()
    refreshable = lambda b: b.get("engine") == "Team model" or b["id"].endswith("|scan")
    if refresh:   # replace unstarted tracked entries with this run's view (only when this call carries the full multi-sport set)
        L["bets"] = [b for b in L["bets"] if not (refreshable(b) and b["status"] == "pending" and not started(b) and b["date"] >= ds and b["stake"] == 0)]
    have = {b["id"] for b in L["bets"]}
    same_side = {(b["game"], b["market"], (b.get("pick_team") or b.get("side") or "").lower()) for b in L["bets"] if b["status"] == "pending"}
    for b in bets:
        k = (b["game"], b["market"], (b.get("pick_team") or b.get("side") or "").lower())
        if b["id"] in have or k in same_side or not pregame(b): continue
        L["bets"].append(b); same_side.add(k)
    L["slate_date"] = ds; L["slate_label"] = now_et().strftime("%a %b %-d"); L["updated_label"] = now_et().strftime("%a %b %-d · %-I:%M %p ET")

def bet_from_multi(r, policy):
    a, h = r["game"]; d = to_et(r["start"]).date().isoformat()
    kind = r["kind"]
    if kind == "Spread": pick = f'{r["team"]} {r["line"]:+g}'
    elif kind == "Total": pick = f'{r["side"].title()} {r["line"]:g}'
    else: pick = f'{r["team"]} ML'
    stake = STAKE.get(r["grade"], 0) * policy
    why = " · ".join(f"{k.title()} {fmt(v)}" for k, v in (r.get("factors") or {}).items() if abs(v) >= 0.05) or r.get("proj", "")
    return dict(id=f'{d}|{a} @ {h}|{kind}', date=d, start=r["start"], sport=r["sport"], engine="Team model", game=f"{a} @ {h}",
                away=a, home=h, market=kind, pick=pick, pick_team=r["team"], side=r["side"], line=r["line"], price=r["price"],
                grade=r["grade"], stake=round(stake, 2), model_line=round(r["model_line"], 1) if kind != "Moneyline" else None,
                fair_line=round(r["fair_line"], 1) if kind != "Moneyline" and r["fair_line"] is not None else None,
                pinnacle_open=round(r["mkt_line"], 1) if kind != "Moneyline" and r.get("mkt_line") is not None else None,
                ev_pct=round(r["ev"] * 100, 1), gap=r["gap"], why=why, projection=r.get("proj"), signals=[],
                market_signal="neutral", close_line=None, close_src=None, status="pending", score=None, result_units=None,
                note=("Tracked until this model is validated" if policy == 0 else None))

def bet_from_propscan(f):
    a, h = f["game"]; d = to_et(f["start"]).date().isoformat()
    stake = round(min(f["stake"], 0.25 if f["stale"] else f["stake"]) * STAKE_SCALE, 2)
    return dict(id=f'{d}|{f["player"]}|{f["stat"]}|scan', date=d, start=f["start"], sport=f["sport"], engine="Price edge",
                game=f"{a} @ {h}", away=a, home=h, market="Prop", player=f["player"], stat=f["stat"], side=f["side"],
                pick=f'{f["player"]} {f["side"]} {f["line"]:g} {f["stat_label"]}', pick_team=None, event_id=f["event_id"],
                line=f["line"], price=f["price"], grade="Price", stake=stake, model_line=None, fair_line=None, pinnacle_open=None,
                ev_pct=round(f["ev"] * 100, 1), gap=None,
                why=f'{f["src"]} no-vig fair {("+" if f["fair_price"] > 0 else "")}{f["fair_price"]} ({f["fair_prob"]:.1%}) vs FanDuel {("+" if f["price"] > 0 else "")}{f["price"]}' + (" · verify: edge this large is often a stale line" if f["stale"] else ""),
                signals=[], market_signal="neutral", close_line=None, close_src=None, status="pending", score=None, result_units=None)

def nfl_props(hours=30, max_plays=6):
    """Prop price scan for NFL games kicking off in the next `hours` (about 5 credits per game)."""
    import multi as X
    odds = S.odds_window("americanfootball_nfl", 2)
    evs = [e for e in odds if (to_et(e["commence_time"]) - now_et()).total_seconds() / 3600 <= hours]
    rem = int(S.CREDITS["remaining"] or 0)
    if rem - 5 * len(evs) < PROP_CREDIT_FLOOR: log(f"nfl props skipped: {rem} credits"); return []
    data = []
    for e in evs:
        try: data.append(S.odds_props(e["id"], "americanfootball_nfl", S.NFL_PROP_MARKETS))
        except Exception as ex: log(f"nfl props {e['id']} failed: {ex}")
    flags = X.prop_scan(data, "NFL", diag=True)
    for ev in data:
        a, h = S.team(ev["away_team"]), S.team(ev["home_team"])
        best = X.LAST_PROP_BEST.get((a, h))
        if best:
            k = ("NFL", f"{a} @ {h}"); g = GAMES.setdefault(k, dict(sport="NFL", game=f"{a} @ {h}", away=a, home=h, start=ev["commence_time"], markets={}))
            g["prop_lean"] = best
    return [bet_from_propscan(f) for f in flags[:max_plays]]

def bet_from_scan(f):
    a, h = f["game"]; d = to_et(f["start"]).date().isoformat()
    kind = {"h2h": "Moneyline", "spreads": "Spread", "totals": "Total"}[f["key"]]
    pick = f["pick"] if kind != "Total" else f'{f["side"]} {f["line"]:g}'
    return dict(id=f'{d}|{a} @ {h}|{kind}|{f["side"]}|scan', date=d, start=f["start"], sport=f["sport"], engine="Price edge",
                game=f"{a} @ {h}", away=a, home=h, market=kind, pick=pick, pick_team=f["team"], side=f["side"].lower(), line=f["line"],
                price=f["price"], grade="Price", stake=round(f["stake"] * STAKE_SCALE, 2), model_line=None, fair_line=f["fair_price"] if kind == "Moneyline" else None,
                pinnacle_open=None, ev_pct=round(f["ev"] * 100, 1), gap=None,
                why=f'{f["src"]} no-vig fair {("+" if f["fair_price"] > 0 else "")}{f["fair_price"]} ({f["fair_prob"]:.1%}) vs FanDuel {("+" if f["price"] > 0 else "")}{f["price"]}' + (" · verify: edge this large is often a stale line" if f["stale"] else ""),
                signals=[], market_signal="neutral", close_line=None, close_src=None, status="pending", score=None, result_units=None)

MULTI = [("icehockey_nhl", 1), ("americanfootball_nfl", 6), ("americanfootball_ncaaf", 4), ("basketball_ncaab", 1)]
def multi_card(nba_odds):
    import multi as X
    bets, scans = [], []
    if nba_odds: scans += X.price_scan(nba_odds, "NBA")
    for key, days in MULTI:
        label = X.SPORTS[key][0]
        try:
            odds = S.odds_window(key, days) if days > 1 else S.odds_games(key, now_et().date())
        except Exception as ex:
            log(f"{label} odds failed: {ex}"); continue
        if not odds: log(f"{label}: no games"); continue
        scans += X.price_scan(odds, label)
        try:
            if label == "NHL":
                cur = S.nhl_teams(SEASON); prev = cached(f"data/cache/nhl2_{PREV}.json", lambda: S.nhl_teams(PREV))
                R, lg = X.blend(cur, prev, X.CFG["NHL"]["prior_g"], ["gf", "ga"])
                rows = X.nhl_model(odds, R, lg, X.resolver(list(R)))
            elif label == "NFL":
                R = S.espn_fpi("nfl"); lg = {}
                rows = X.point_model("NFL", odds, R, lg, X.resolver(list(R)))
            elif label == "NCAAF":
                R = S.espn_fpi("college-football"); lg = {}
                rows = X.point_model("NCAAF", odds, R, lg, X.resolver(list(R)))
            else:
                cur = S.cbb_teams(SEASON); prev = cached(f"data/cache/cbb_{PREV}.json", lambda: S.cbb_teams(PREV))
                R, lg = X.blend(cur, prev, X.CFG["NCAAM"]["prior_g"], ["ortg", "drtg", "pace"])
                rows = X.point_model("NCAAM", odds, R, lg, X.resolver(list(R)))
            note_rows(rows, label)
            unmatched = sorted({n for e in odds for n in (S.team(e["home_team"]), S.team(e["away_team"])) if not X.resolver(list(R))(n)})
            if unmatched: log(f"{label} unmatched teams ({len(unmatched)}): {unmatched[:12]}")
            pol = X.STAKE_POLICY.get(label, 0)
            rows.sort(key=lambda r: -r["ev"])
            ab = [r for r in rows if r["grade"] in ("A", "B")][:4]
            bets += [bet_from_multi(r, pol) for r in ab]
            bets += [bet_from_multi(r, 0) for r in [r for r in rows if r["grade"] == "C"][:4]]
        except Exception as ex:
            log(f"{label} model failed: {traceback.format_exc()[-800:]}")
    # price-scan plays: top 6 by EV, skip any that duplicate a model play's side
    taken = {(b["game"], b["market"], (b.get("side") or "").lower()) for b in bets}
    sc = [bet_from_scan(f) for f in scans]
    sc = [b for b in sc if (b["game"], b["market"], b["side"]) not in taken][:6]
    log(f"multi: {len(bets)} model plays, {len(sc)} price plays, credits {S.CREDITS['remaining']}")
    return bets + sc

def probe4():
    import re, html as H
    body, _ = S.get("https://dknetwork.draftkings.com/draftkings-sportsbook-betting-splits/?tb_eg=87637&tb_edate=today&tb_emt=0")
    blocks = body.split('<div class="tb-se ')[1:3]
    for b in blocks:
        t = H.unescape(re.sub(r"<[^>]+>", " | ", b)); t = re.sub(r"(\s*\|\s*)+", " | ", t); t = re.sub(r"\s+", " ", t)
        log("BLOCK", t[:1500])
    log("pages", sorted(set(re.findall(r"tb_page=(\d+)", body))), "n blocks", body.count('<div class="tb-se '))

def probe3():
    import re
    for u in ["https://dknetwork.draftkings.com/draftkings-sportsbook-betting-splits/",
              "https://dknetwork.draftkings.com/draftkings-sportsbook-betting-splits/?tb_eg=87637&tb_edate=today&tb_emt=0",
              "https://api.actionnetwork.com/web/v1/scoreboard/ncaaf?period=game&bookIds=15,30&division=FBS"]:
        try:
            body, h = S.get(u)
            log("URL", u, "len", len(body), "ctype", h.get("Content-Type"))
            log("tb_eg ids", sorted(set(re.findall(r"tb_eg=(\d+)", body)))[:20])
            i = body.find("tb-se"); j = body.find("Spread")
            log("snippet tb-se", body[i-300:i+1500].replace("\n", " ")[:1800] if i > 0 else "none")
            log("snippet Spread", body[j-800:j+600].replace("\n", " ")[:1400] if j > 0 else "none")
            if "actionnetwork" in u:
                d = json.loads(body); g = d["games"][0]
                log("AN ncaaf games", len(d["games"]), "odds sample", json.dumps(g.get("odds", [])[:2])[:900])
        except Exception as ex: log("ERR", u, repr(ex))

def probe2():
    import io, re
    for lg in ("nfl", "college-football"):
        try:
            d, _ = S.get_json(f"https://site.web.api.espn.com/apis/fitt/v3/sports/football/{lg}/powerindex?region=us&lang=en&limit=500")
            log(lg, "keys", list(d.keys()))
            cats = d.get("categories") or []
            log(lg, "categories", json.dumps(cats)[:600])
            t0 = (d.get("teams") or [None])[0]
            log(lg, "team0", json.dumps(t0)[:1500])
            log(lg, "n teams", len(d.get("teams") or []))
        except Exception as ex: log(lg, "powerindex failed", repr(ex))
    html, _ = S.get("https://www.hockey-reference.com/leagues/NHL_2027.html")
    html = html.replace("<!--", "").replace("-->", "")
    log("nhl table ids", re.findall(r'<table[^>]*id="([^"]+)"', html))
    import pandas as pd
    for i, t in enumerate(pd.read_html(io.StringIO(html))[:8]):
        log("nhl tab", i, t.shape, [str(c) for c in t.columns][:20], t.head(2).astype(str).values.tolist()[:2])

def pregame(b, minutes=5):
    return to_et(b["start"]) > now_et() + timedelta(minutes=minutes)

CLOSES = "data/closes.json"
def merge_closes(L):
    C = load(CLOSES, {})
    for b in L["bets"]:
        c = C.get(b["id"])
        if c: b.update(c)
    return L

def cfbday():
    """Saturday college football card (before noon kickoffs)."""
    import multi as X
    ds = now_et().date().isoformat()
    odds = S.odds_games("americanfootball_ncaaf", now_et().date())
    fpi = S.espn_fpi("college-football"); dk = S.dk_splits("NCAAF"); an = S.an_lines("ncaaf")
    rows = X.cfb_card(odds, fpi, dk, an)
    scans = X.price_scan(odds, "NCAAF")
    bets = []
    staked = 0
    for r in rows:
        stake = {"A": 0.5, "B": 0.25}.get(r["grade"], 0.0) * STAKE_SCALE
        if stake and staked >= 8: stake = 0.0
        if stake: staked += 1
        b = bet_from_multi(dict(r, model_line=r["model_line"] if r["model_line"] is not None else r["mkt_line"],
                                fair_line=r["mkt_line"], factors={}), 1.0)
        b.update(stake=round(stake, 3), signals=r["notes"], engine="CFB card", why=(r["proj"] + (" · " if r["proj"] and r["notes"] else "") + " · ".join(r["notes"])) or "market signals",
                 market_signal="with us" if r["sig"] >= 1 else "against us" if r["sig"] < 0 else "neutral",
                 ev_pct=round(r["ev"] * 100, 1), note=None)
        b["id"] = b["id"] + "|cfb"
        bets.append(b)
    bets += [bet_from_scan(f) for f in scans][:4]
    L = load(LEDGER, {"bets": []})
    L["bets"] = [b for b in L["bets"] if not (b.get("engine") == "CFB card" and b["status"] == "pending" and b["stake"] == 0 and pregame(b))]
    add_multi(L, bets, ds, refresh=False)
    for r in rows: note_game("NCAAF", r["game"][0], r["game"][1], r["start"], r["kind"], r["line"], r["model_line"], r["gap"], r["grade"], r["ev"],
                             f'{r["team"]} {r["line"]:+g}' if r["kind"] == "Spread" else f'{r["side"].title()} {r["line"]:g}')
    save_games(L, ds); L["credits"] = S.CREDITS["remaining"]; save(LEDGER, L)
    log("staked:", [(b["pick"], b["price"], b["stake"], b["grade"]) for b in bets if b["stake"] > 0])

def card():
    import nba_model as M
    day = now_et().date(); ds = day.isoformat()
    if os.environ.get("EVENT") == "schedule" and load(f"data/cards/{ds}.json", None):
        log(f"card for {ds} already built; scheduled retry skipped"); return
    odds = S.odds_games("basketball_nba", day)
    if not odds:
        log("no NBA games today")
        L = load(LEDGER, {"bets": [], "snapshots": []}); add_multi(L, multi_card([]), ds); save(LEDGER, L); return
    an = S.action_network(day)
    inj = S.espn_injuries()
    t_cur = S.bbref_teams(SEASON); t_prev = cached(f"data/cache/teams_{PREV}.json", lambda: S.bbref_teams(PREV))
    p_cur = S.bbref_players(SEASON); p_prev = cached(f"data/cache/players_{PREV}.json", lambda: S.bbref_players(PREV))
    ratings, lg = M.blend_ratings(t_cur, t_prev)
    sched = {i: S.espn_scoreboard(day - timedelta(days=i)) for i in (1, 2, 3)}
    today = [dict(home=S.team(e["home_team"]), away=S.team(e["away_team"])) for e in odds]
    rest = M.rest_flags(sched, today)
    sides, ctx = M.project_games(odds, ratings, lg, inj, p_cur, rest, an)
    note_rows(sides, "NBA")
    props = []
    rem = int(S.CREDITS["remaining"] or 0)
    if rem - 3 * len(odds) >= PROP_CREDIT_FLOOR:
        evs = []
        for e in odds:
            if to_et(e["commence_time"]) > now_et():
                try: evs.append(S.odds_props(e["id"]))
                except Exception as ex: log(f"props {e['id']} failed: {ex}")
        props = M.project_props(evs, p_cur, p_prev, ratings, lg, inj, ctx)
        note_props = f"Props priced for {len(evs)} games."
    else:
        note_props = f"Props skipped: {rem} Odds API credits left (floor {PROP_CREDIT_FLOOR})."
    log(note_props, f"credits remaining {S.CREDITS['remaining']}")
    holds_today = [dict(player=r["player"], stat=r["stat"], side=r["side"], line=r["line"], reason=r["hold"])
                   for r in props if r.get("hold") and r["ev"] > 0.03][:6]
    save(f"data/cards/{ds}.json", dict(sides=sides, props=props, rest=rest, injuries=inj, credits=S.CREDITS))
    L = load(LEDGER, {"bets": [], "snapshots": []})
    started = lambda b: to_et(b["start"]) <= now_et()
    # posted staked plays are permanent; only unstaked (tracked) entries are refreshed
    L["bets"] = [b for b in L["bets"] if not (b["date"] == ds and b["status"] == "pending" and not started(b) and b["stake"] == 0
                                               and b.get("engine") not in ("Analyst read", "CFB card"))]
    have = {b["id"] for b in L["bets"]}
    new = [bet_from_side(r, ds) for r in sides if r["grade"]] + [bet_from_prop(r, ds) for r in props if r["grade"] in ("A", "B")]
    new += [bet_from_prop(r, ds) for r in [x for x in props if x["grade"] == "C"][:10]]
    L["bets"] += [b for b in new if b["id"] not in have and pregame(b)]
    add_multi(L, multi_card(odds), ds)
    try: add_multi(L, nfl_props(), ds, refresh=False)
    except Exception: log("nfl props failed: " + traceback.format_exc()[-400:])
    L["slate_date"] = ds; L["slate_label"] = day.strftime("%a %b %-d")
    L["updated_label"] = now_et().strftime("%a %b %-d · %-I:%M %p ET")
    L["run_note"] = note_props
    save_games(L, ds)
    L["holds"] = holds_today
    L["credits"] = S.CREDITS["remaining"]
    save(LEDGER, L)
    log(f"card {ds}: {sum(b['grade']=='A' for b in new)} A, {sum(b['grade']=='B' for b in new)} B, {sum(b['grade']=='C' for b in new)} C")

def rebuild():
    """Re-grade today's saved card (no new odds credits): refresh player stats, apply prop sanity rules, rewrite ledger."""
    import nba_model as M
    day = now_et().date(); ds = day.isoformat()
    C = load(f"data/cards/{ds}.json", None)
    if not C: log("no saved card"); return
    p_cur = S.bbref_players(SEASON)
    for r in C["props"]:
        r.setdefault("extra_min", 0); r.setdefault("usg_bump", 1)
        if "with" in r["why"] and "min proj (+" in r["why"]:
            try: r["extra_min"] = float(r["why"].split("(+")[1].split(" ")[0])
            except Exception: pass
        if "usage ×" in r["why"]: r["usg_bump"] = float(r["why"].split("usage ×")[1].split(",")[0])
        if r["grade"] == "" and "hold" not in r:                 # re-evaluate original grade thresholds
            e, ev = r["edge"], r["ev"]
            r["grade"] = "A" if e >= 0.12 and ev >= 0.05 else "B" if e >= 0.08 and ev >= 0.03 else "C" if e >= 0.05 and ev > 0 else ""
        elif r["grade"] == "C":
            e, ev = r["edge"], r["ev"]
            r["grade"] = "A" if e >= 0.12 and ev >= 0.05 else "B" if e >= 0.08 and ev >= 0.03 else "C"
    C["props"] = M.sanity_props(C["props"], p_cur)
    save(f"data/cards/{ds}.json", C)
    L = load(LEDGER, {"bets": []})
    L["bets"] = [b for b in L["bets"] if not (b["date"] == ds and b["status"] == "pending" and b["market"] == "Prop" and b["stake"] == 0)]
    props = C["props"]
    L["bets"] += [bet_from_prop(r, ds) for r in props if r["grade"] in ("A", "B")]
    L["bets"] += [bet_from_prop(r, ds) for r in [x for x in props if x["grade"] == "C"][:10]]
    held = [r for r in props if r.get("hold")]
    L["holds"] = [dict(player=r["player"], stat=r["stat"], side=r["side"], line=r["line"], reason=r["hold"]) for r in held if r["ev"] > 0.03][:6]
    save(LEDGER, L)
    log(f"rebuild: props A {sum(r['grade']=='A' for r in props)}, B {sum(r['grade']=='B' for r in props)}, held {len(held)}")
    for r in props:
        if r["grade"] in ("A", "B"): log(r["grade"], r["player"], r["side"], r["line"], r["stat"], r["price"], "proj", r["model_line"], "ev", round(r["ev"]*100, 1), r["mkt_src"])
    for h in L["holds"]: log("HOLD", h)

def close():
    day = now_et().date(); ds = day.isoformat()
    L = load(LEDGER, {"bets": []})
    soon = [b for b in L["bets"] if b["status"] == "pending" and b["market"] in ("Spread", "Total", "Moneyline")
            and -15 <= (to_et(b["start"]) - now_et()).total_seconds() / 60 <= 80]
    if not soon: log("close: nothing tipping soon"); return
    import multi as X
    keys = {v[0]: k for k, v in X.SPORTS.items()}
    for sport in sorted({b["sport"] for b in soon}):
        try: odds = S.odds_games(keys[sport], day)
        except Exception as ex: log(f"close {sport} failed: {ex}"); continue
        for e in odds:
            h, a = S.team(e["home_team"]), S.team(e["away_team"])
            pn = next((bk for bk in e.get("bookmakers", []) if bk["key"] == "pinnacle"), None)
            if not pn: continue
            for b in soon:
                if b["sport"] != sport or (b["away"], b["home"]) != (a, h): continue
                mk = {"Spread": "spreads", "Total": "totals", "Moneyline": "h2h"}[b["market"]]
                mm = next((m for m in pn["markets"] if m["key"] == mk), None)
                if not mm: continue
                for o in mm["outcomes"]:
                    nm = o["name"]
                    hit = (nm.lower() == (b.get("side") or "").lower()) if b["market"] == "Total" else (S.team(nm) == S.team(b["pick_team"] or ""))
                    if hit:
                        b["close_line"] = o.get("point") if b["market"] != "Moneyline" else None
                        b["close_price"] = o["price"]; b["close_src"] = "Pinnacle close"
                        log(f"close {b['pick']}: {o.get('point')} {o['price']}")
    L["updated_label"] = now_et().strftime("%a %b %-d · %-I:%M %p ET")
    save(LEDGER, L)

def _publish(msg):
    import subprocess, runpy
    try: runpy.run_path(os.path.join(ROOT, "pipeline", "build_sheet.py"))
    except Exception: log(traceback.format_exc()[-400:])
    g = ["git", "-c", "user.name=edge-bot", "-c", "user.email=edge-bot@users.noreply.github.com"]
    subprocess.run(g + ["add", "data/closes.json", "index.html"], cwd=ROOT)
    if subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=ROOT).returncode == 0: return
    subprocess.run(g + ["commit", "-qm", msg], cwd=ROOT)
    for _ in range(4):
        if subprocess.run(["git", "pull", "--rebase", "-q"], cwd=ROOT).returncode == 0 and subprocess.run(["git", "push", "-q"], cwd=ROOT).returncode == 0: return
        time.sleep(5)

def closer(max_minutes=340):
    """Stay running through the evening; capture Pinnacle closing lines 6-15 minutes before each tip."""
    import multi as X
    keys = {v[0]: k for k, v in X.SPORTS.items()}
    t0 = time.time(); done = set()
    while (time.time() - t0) / 60 < max_minutes:
        import subprocess; subprocess.run(["git", "pull", "--rebase", "-q"], cwd=ROOT)
        L = merge_closes(load(LEDGER, {"bets": []}))
        pend = [b for b in L["bets"] if b["status"] == "pending" and b["market"] in ("Spread", "Total", "Moneyline")
                and b.get("close_src") != "Pinnacle close" and to_et(b["start"]) > now_et()]
        if not pend: log("closer: no pending games left"); break
        due = [b for b in pend if (to_et(b["start"]) - now_et()).total_seconds() / 60 <= 15]
        for sport in sorted({b["sport"] for b in due}):
            try: odds = S.odds_games(keys[sport], now_et().date())
            except Exception as ex: log(f"closer {sport}: {ex}"); continue
            for e in odds:
                h, a = S.team(e["home_team"]), S.team(e["away_team"])
                pn = next((bk for bk in e.get("bookmakers", []) if bk["key"] == "pinnacle"), None)
                for b in due:
                    if b["sport"] != sport or (b["away"], b["home"]) != (a, h) or not pn: continue
                    mk = {"Spread": "spreads", "Total": "totals", "Moneyline": "h2h"}[b["market"]]
                    mm = next((m for m in pn["markets"] if m["key"] == mk), None)
                    for o in (mm or {}).get("outcomes", []):
                        hit = (o["name"].lower() == (b.get("side") or "").lower()) if b["market"] == "Total" else (S.team(o["name"]) == S.team(b["pick_team"] or ""))
                        if hit:
                            b["close_line"] = o.get("point") if b["market"] != "Moneyline" else None
                            b["close_price"] = o["price"]; b["close_src"] = "Pinnacle close"
                            log(f"close {b['pick']}: {o.get('point')} {o['price']}")
        if due:
            C = load(CLOSES, {})
            for b in due:
                if b.get("close_src") == "Pinnacle close":
                    C[b["id"]] = {k: b.get(k) for k in ("close_line", "close_price", "close_src")}
            save(CLOSES, C)
            _publish(f"closer: {len(C)} closing lines on file")
        nxt = min(to_et(b["start"]) for b in pend)
        wait = max(60, min(300, (nxt - now_et()).total_seconds() - 14 * 60))
        time.sleep(wait)

def settle(units, price, stake, res):
    return round(stake * (am_to_dec(price) - 1), 3) if res == "won" else (-stake if res == "lost" else 0.0)

def grade():
    L = load(LEDGER, {"bets": []})
    due = [b for b in L["bets"] if b["status"] == "pending" and b.get("engine") != "Parlay"
           and (now_et() - to_et(b["start"])).total_seconds() > 3.5 * 3600]
    path = {"NBA": "basketball/nba", "NHL": "hockey/nhl", "NFL": "football/nfl", "NCAAM": "basketball/mens-college-basketball", "NCAAF": "football/college-football"}
    cache, boxes = {}, {}
    for b in due:
        d = to_et(b["start"]).date(); key = (b["sport"], d)
        if key not in cache:
            pth = path.get(b["sport"], "basketball/nba")
            try: cache[key] = S.espn_scoreboard(d, pth)
            except Exception as ex: log(f"scoreboard {key} failed: {ex}"); cache[key] = []
        same = lambda x, y: S.pname(x) == S.pname(y) or S.pname(x).startswith(S.pname(y) + " ") or S.pname(y).startswith(S.pname(x) + " ")
        g = next((x for x in cache[key] if same(x["home"], b["home"]) and same(x["away"], b["away"])), None)
        if not g or not g["final"]: continue
        hp, ap = g["home_pts"], g["away_pts"]
        if b["market"] == "Prop":
            if g["id"] not in boxes: boxes[g["id"]] = S.espn_box_nfl(g["id"]) if b["sport"] == "NFL" else S.espn_box(g["id"])
            st = boxes[g["id"]].get(S.pname(b["player"]))
            if st is not None and b["stat"] not in st: st = dict(st, **{b["stat"]: 0.0})     # played but no stat line in that category
            if st is None: b["status"], b["result_units"], b["score"] = "push", 0.0, "Did not play (void)"
            else:
                v = st[b["stat"]]; diff = (v - b["line"]) * (1 if b["side"] == "Over" else -1)
                b["status"] = "won" if diff > 0 else "lost" if diff < 0 else "push"
                b["score"] = f'{b["player"]}: {v:g} {b["stat"].replace("_", " ").upper()}'
                b["result_units"] = settle(None, b["price"], b["stake"], b["status"])
            continue
        b["score"] = f'{b["away"]} {ap}, {b["home"]} {hp}'
        if b["market"] == "Total" or (b.get("side") or "").lower() in ("over", "under"):
            diff = (hp + ap - b["line"]) * (1 if (b.get("side") or b["pick"]).lower().startswith("over") else -1)
        else:
            mine, theirs = (hp, ap) if same(b["pick_team"], g["home"]) else (ap, hp)
            diff = mine - theirs + (b["line"] if b["market"] == "Spread" else 0)
        b["status"] = "won" if diff > 0 else "lost" if diff < 0 else "push"
        b["result_units"] = settle(None, b["price"], b["stake"], b["status"])
        log(f"graded {b['pick']}: {b['status']} {b['result_units']:+.2f}u")
    settle_parlays(L, cache)
    L["updated_label"] = now_et().strftime("%a %b %-d · %-I:%M %p ET")
    save(LEDGER, L)

def settle_parlays(L, cache):
    same = lambda x, y: S.pname(x) == S.pname(y) or S.pname(x).startswith(S.pname(y) + " ") or S.pname(y).startswith(S.pname(x) + " ")
    paths = {"NCAAF": "football/college-football", "NBA": "basketball/nba", "NCAAM": "basketball/mens-college-basketball", "NFL": "football/nfl"}
    for p in L["bets"]:
        if p.get("engine") != "Parlay" or p["status"] != "pending" or not p.get("legs"): continue
        for l in p["legs"]:
            if l["status"] != "pending" or (now_et() - to_et(l["start"])).total_seconds() < 3.5 * 3600: continue
            d = to_et(l["start"]).date(); key = (p["sport"], d)
            if key not in cache:
                try: cache[key] = S.espn_scoreboard(d, paths.get(p["sport"], "football/college-football"))
                except Exception: cache[key] = []
            g = next((x for x in cache[key] if same(x["home"], l["home"]) and same(x["away"], l["away"])), None)
            if not g or not g["final"]: continue
            hp, ap = g["home_pts"], g["away_pts"]
            if l.get("market") == "Total": diff = (hp + ap - l["line"]) * (1 if l["side"] == "over" else -1)
            else:
                mine, theirs = (hp, ap) if same(l["pick_team"], g["home"]) else (ap, hp); diff = mine - theirs + l["line"]
            l["status"] = "won" if diff > 0 else "lost" if diff < 0 else "push"; l["score"] = f'{l["away"]} {ap}, {l["home"]} {hp}'
        if any(l["status"] == "lost" for l in p["legs"]): p["status"], p["result_units"] = "lost", -p["stake"]
        elif all(l["status"] != "pending" for l in p["legs"]):
            dd = 1.0
            for l in p["legs"]:
                if l["status"] == "won": dd *= am_to_dec(l["price"])
            p["status"] = "won" if dd > 1 else "push"; p["result_units"] = round(p["stake"] * (dd - 1), 3)
        log(f"parlay {p['id']}: {p['status']}")

def dry():
    """Exercise every non-odds source and the rest/injury logic."""
    import nba_model as M
    day = now_et().date()
    an = S.action_network(day); log("AN sample", next(iter(an.items()), None))
    inj = S.espn_injuries()
    t_cur = S.bbref_teams(SEASON); t_prev = cached(f"data/cache/teams_{PREV}.json", lambda: S.bbref_teams(PREV))
    log("teams cur/prev", len(t_cur), len(t_prev), "sample", next(iter(t_cur.items())))
    p_cur = S.bbref_players(SEASON); p_prev = cached(f"data/cache/players_{PREV}.json", lambda: S.bbref_players(PREV))
    log("player sample", next(iter(p_cur.values())))
    ratings, lg = M.blend_ratings(t_cur, t_prev); log("league", {k: round(v, 1) for k, v in lg.items()})
    sched = {i: S.espn_scoreboard(day - timedelta(days=i)) for i in (1, 2, 3)}
    today = [dict(home=g["home"], away=g["away"]) for g in S.espn_scoreboard(day)]
    rest = M.rest_flags(sched, today); log("rest flags", {k: v for k, v in rest.items() if any(v.values())})
    for g in today[:14]:
        ih, nh = M.injury_impact(g["home"], inj, p_cur, ratings[g["home"]]["g"]); ia, na = M.injury_impact(g["away"], inj, p_cur, ratings[g["away"]]["g"])
        log(f'{g["away"]} @ {g["home"]}: inj {ia:.1f}/{ih:.1f}', na, nh, "AN" , (g["away"], g["home"]) in an)
    miss = [k for k in {x for g in today for x in (g["home"], g["away"])} if k not in ratings]
    log("teams missing ratings:", miss)

def preview():
    """Model-only numbers for today's games (no odds): margin, total, factor breakdown."""
    import nba_model as M
    day = now_et().date()
    inj = S.espn_injuries(); an = S.action_network(day)
    t_cur = S.bbref_teams(SEASON); t_prev = cached(f"data/cache/teams_{PREV}.json", lambda: S.bbref_teams(PREV))
    p_cur = S.bbref_players(SEASON)
    R, lg = M.blend_ratings(t_cur, t_prev)
    sched = {i: S.espn_scoreboard(day - timedelta(days=i)) for i in (1, 2, 3)}
    games = S.espn_scoreboard(day)
    rest = M.rest_flags(sched, games)
    out = []
    for g in games:
        h, a = g["home"], g["away"]; rh, ra = R[h], R[a]
        ih, nh = M.injury_impact(h, inj, p_cur, rh["g"]); ia, na = M.injury_impact(a, inj, p_cur, ra["g"])
        poss = rh["pace"] * ra["pace"] / lg["pace"]
        bh = poss * (rh["ortg"] + ra["drtg"] - lg["ortg"]) / 100; ba = poss * (ra["ortg"] + rh["drtg"] - lg["ortg"]) / 100
        pen = lambda t: (M.B2B if rest[t]["b2b"] else 0) + (M.THREE_IN_FOUR if rest[t]["three_in_four"] else 0) + (M.TRAVEL if rest[t]["travel"] else 0)
        fx = dict(ratings=bh - ba, injuries=poss / 100 * (ia - ih), rest=pen(a) - pen(h), home=M.HCA + (M.ALT_BONUS if h in ("Denver Nuggets", "Utah Jazz") else 0))
        m = sum(fx.values()); tot = bh + ba - poss / 100 * 0.2 * (ih + ia)
        x = an.get((a, h), {})
        out.append(dict(game=f"{a} @ {h}", time=to_et(g["date"]).strftime("%-I:%M"), model_spread_home=round(-m, 1), model_total=round(tot, 1),
                        mkt_spread_home=x.get("cur_spread_home"), mkt_total=x.get("cur_total"), open_spread_home=x.get("open_spread_home"),
                        open_total=x.get("open_total"), tix_home=x.get("spread_home_tix"), money_home=x.get("spread_home_money"),
                        over_tix=x.get("over_tix"), over_money=x.get("over_money"),
                        factors={k: round(v, 1) for k, v in fx.items()}, inj_home=nh, inj_away=na, rest_home=rest[h], rest_away=rest[a]))
    save(f"data/preview/{day}.json", out)
    for o in out: log(json.dumps(o))

if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "page"
    ok = True
    try:
        _L = load(LEDGER, None)
        if _L: save(LEDGER, merge_closes(_L))
    except Exception: pass
    try:
        if cmd == "auto":   # scheduled runs: pick the job by Eastern time, not by cron string
            h = now_et().hour
            wd = now_et().weekday()
            cmd = ("nflday" if wd == 6 and 9 <= h < 12 else "cfbday" if wd == 5 and 8 <= h < 12 else
                   "card" if 12 <= h < 19 else "grade")
            log(f"auto -> {cmd} ({now_et():%H:%M} ET)")
        if cmd == "card": card()
        elif cmd == "close": close()
        elif cmd == "grade": grade()
        elif cmd == "dry": dry()
        elif cmd == "rebuild": rebuild()
        elif cmd == "closer": closer()
        elif cmd == "cfbday": cfbday()
        elif cmd == "nflday":      # Sunday morning: NFL lines (price scan + tracked model) and props before 1 PM kickoffs
            import multi as X
            L = load(LEDGER, {"bets": []}); ds = now_et().date().isoformat()
            odds = S.odds_window("americanfootball_nfl", 1)
            scans = [bet_from_scan(f) for f in X.price_scan(odds, "NFL")][:6]
            R = S.espn_fpi("nfl"); rows = X.point_model("NFL", odds, R, {}, X.resolver(list(R))); note_rows(rows, "NFL")
            model = [bet_from_multi(r, X.STAKE_POLICY.get("NFL", 0)) for r in sorted(rows, key=lambda r: -r["ev"]) if r["grade"] in ("A", "B", "C")][:6]
            add_multi(L, model + scans + nfl_props(hours=14, max_plays=8), ds)
            L["credits"] = S.CREDITS["remaining"]; save_games(L, ds); save(LEDGER, L)
        elif cmd == "all":         # on demand: spend down credits for NFL props, then the full card for every sport
            PROP_CREDIT_FLOOR = 10
            L = load(LEDGER, {"bets": []}); ds = now_et().date().isoformat()
            bets = nfl_props(hours=14, max_plays=8); log("nfl prop plays:", [(b["pick"], b["price"], b["ev_pct"], b["stake"]) for b in bets])
            add_multi(L, bets, ds, refresh=False); L["credits"] = S.CREDITS["remaining"]; save_games(L, ds); save(LEDGER, L)
            card()
        elif cmd == "nflprops":
            L = load(LEDGER, {"bets": []}); ds = now_et().date().isoformat()
            bets = nfl_props(); log("nfl prop plays:", [(b["pick"], b["price"], b["ev_pct"], b["stake"]) for b in bets])
            add_multi(L, bets, ds, refresh=False); L["credits"] = S.CREDITS["remaining"]; save_games(L, ds); save(LEDGER, L)
        elif cmd == "probe2": probe2()
        elif cmd == "probe3": probe3()
        elif cmd == "probe4": probe4()
        elif cmd == "multi":
            L = load(LEDGER, {"bets": []}); ds = now_et().date().isoformat()
            add_multi(L, multi_card([]), ds); L["credits"] = S.CREDITS["remaining"]; save_games(L, ds); save(LEDGER, L)
        elif cmd == "preview": preview()
    except Exception:
        ok = False; log(traceback.format_exc())
    try:
        import build_sheet  # builds index.html on import
    except Exception:
        ok = False; log(traceback.format_exc())
    save("data/logs/last_run.json", dict(cmd=cmd, ok=ok, at=datetime.now(timezone.utc).isoformat(), log=LOG[-200:]))
