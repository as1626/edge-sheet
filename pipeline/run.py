"""Pipeline entry point.  python pipeline/run.py card|close|grade|page"""
import sys, os, json, traceback
from datetime import timedelta, datetime, timezone
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import log, LOG, load, save, now_et, to_et, am_to_dec, ROOT
import sources as S

LEDGER = "data/ledger.json"
SEASON, PREV = 2027, 2026
PROP_CREDIT_FLOOR = 120          # skip props when Odds API credits run low
STAKE = {"A": 1.0, "B": 0.5, "C": 0.0}
PROP_STAKE = {"A": 0.5, "B": 0.25, "C": 0.0}

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

def card():
    import nba_model as M
    day = now_et().date(); ds = day.isoformat()
    odds = S.odds_games("basketball_nba", day)
    if not odds: log("no NBA games today"); return
    an = S.action_network(day)
    inj = S.espn_injuries()
    t_cur = S.bbref_teams(SEASON); t_prev = cached(f"data/cache/teams_{PREV}.json", lambda: S.bbref_teams(PREV))
    p_cur = S.bbref_players(SEASON); p_prev = cached(f"data/cache/players_{PREV}.json", lambda: S.bbref_players(PREV))
    ratings, lg = M.blend_ratings(t_cur, t_prev)
    sched = {i: S.espn_scoreboard(day - timedelta(days=i)) for i in (1, 2, 3)}
    today = [dict(home=S.team(e["home_team"]), away=S.team(e["away_team"])) for e in odds]
    rest = M.rest_flags(sched, today)
    sides, ctx = M.project_games(odds, ratings, lg, inj, p_cur, rest, an)
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
    save(f"data/cards/{ds}.json", dict(sides=sides, props=props, rest=rest, injuries=inj, credits=S.CREDITS))
    L = load(LEDGER, {"bets": [], "snapshots": []})
    started = lambda b: to_et(b["start"]) <= now_et()
    L["bets"] = [b for b in L["bets"] if not (b["date"] == ds and b["status"] == "pending" and not started(b))]
    have = {b["id"] for b in L["bets"]}
    new = [bet_from_side(r, ds) for r in sides if r["grade"]] + [bet_from_prop(r, ds) for r in props if r["grade"]]
    L["bets"] += [b for b in new if b["id"] not in have]
    L["slate_date"] = ds; L["slate_label"] = day.strftime("%a %b %-d")
    L["updated_label"] = now_et().strftime("%a %b %-d · %-I:%M %p ET")
    L["run_note"] = note_props
    L["credits"] = S.CREDITS["remaining"]
    save(LEDGER, L)
    log(f"card {ds}: {sum(b['grade']=='A' for b in new)} A, {sum(b['grade']=='B' for b in new)} B, {sum(b['grade']=='C' for b in new)} C")

def close():
    day = now_et().date(); ds = day.isoformat()
    L = load(LEDGER, {"bets": []})
    soon = [b for b in L["bets"] if b["date"] == ds and b["status"] == "pending" and b["market"] in ("Spread", "Total")
            and -15 <= (to_et(b["start"]) - now_et()).total_seconds() / 60 <= 80]
    if not soon: log("close: nothing tipping soon"); return
    odds = S.odds_games("basketball_nba", day)
    import nba_model as M
    for e in odds:
        m = M.market_lines(e)
        if not m: continue
        for b in soon:
            if (b["away"], b["home"]) != (m["away"], m["home"]): continue
            if b["market"] == "Spread" and m.get("pn_spread_home") is not None:
                b["close_line"] = m["pn_spread_home"] if b["side"] == "home" else -m["pn_spread_home"]
                b["fd_close"] = m.get("fd_spread_home") if b["side"] == "home" else -m.get("fd_spread_home", 0)
            elif b["market"] == "Total" and m.get("pn_total") is not None:
                b["close_line"] = m["pn_total"]; b["fd_close"] = m.get("fd_total")
            b["close_src"] = "Pinnacle close"
            log(f"close {b['pick']}: {b.get('close_line')}")
    L["updated_label"] = now_et().strftime("%a %b %-d · %-I:%M %p ET")
    save(LEDGER, L)

def settle(units, price, stake, res):
    return round(stake * (am_to_dec(price) - 1), 3) if res == "won" else (-stake if res == "lost" else 0.0)

def grade():
    L = load(LEDGER, {"bets": []})
    due = [b for b in L["bets"] if b["status"] == "pending" and b.get("engine") != "Parlay"
           and (now_et() - to_et(b["start"])).total_seconds() > 3.5 * 3600]
    path = {"NBA": "basketball/nba", "NFL": "football/nfl", "NCAAM": "basketball/mens-college-basketball", "NCAAF": "football/college-football"}
    cache, boxes = {}, {}
    for b in due:
        d = to_et(b["start"]).date(); key = (b["sport"], d)
        if key not in cache:
            pth = path.get(b["sport"], "basketball/nba")
            try: cache[key] = S.espn_scoreboard(d, pth)
            except Exception as ex: log(f"scoreboard {key} failed: {ex}"); cache[key] = []
        g = next((x for x in cache[key] if x["home"] == S.team(b["home"]) or x["away"] == S.team(b["away"])), None)
        if not g or not g["final"]: continue
        hp, ap = g["home_pts"], g["away_pts"]
        if b["market"] == "Prop":
            if g["id"] not in boxes: boxes[g["id"]] = S.espn_box(g["id"])
            st = boxes[g["id"]].get(S.pname(b["player"]))
            if st is None: b["status"], b["result_units"], b["score"] = "push", 0.0, "Did not play (void)"
            else:
                v = st[b["stat"]]; diff = (v - b["line"]) * (1 if b["side"] == "Over" else -1)
                b["status"] = "won" if diff > 0 else "lost" if diff < 0 else "push"
                b["score"] = f'{b["player"]}: {v:g} {b["stat"].upper()}'
                b["result_units"] = settle(None, b["price"], b["stake"], b["status"])
            continue
        b["score"] = f'{b["away"]} {ap}, {b["home"]} {hp}'
        if b["market"] == "Total":
            diff = (hp + ap - b["line"]) * (1 if b["pick"].lower().startswith("over") else -1)
        else:
            mine, theirs = (hp, ap) if S.team(b["pick_team"]) == g["home"] else (ap, hp)
            diff = mine - theirs + (b["line"] if b["market"] == "Spread" else 0)
        b["status"] = "won" if diff > 0 else "lost" if diff < 0 else "push"
        b["result_units"] = settle(None, b["price"], b["stake"], b["status"])
        log(f"graded {b['pick']}: {b['status']} {b['result_units']:+.2f}u")
    L["updated_label"] = now_et().strftime("%a %b %-d · %-I:%M %p ET")
    save(LEDGER, L)

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
        if cmd == "card": card()
        elif cmd == "close": close()
        elif cmd == "grade": grade()
        elif cmd == "dry": dry()
        elif cmd == "preview": preview()
    except Exception:
        ok = False; log(traceback.format_exc())
    try:
        import build_sheet  # builds index.html on import
    except Exception:
        ok = False; log(traceback.format_exc())
    save("data/logs/last_run.json", dict(cmd=cmd, ok=ok, at=datetime.now(timezone.utc).isoformat(), log=LOG[-200:]))
