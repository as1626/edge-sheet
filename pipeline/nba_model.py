"""NBA factor model: sides, totals and player props vs FanDuel.

Projection (points, home perspective):
  possessions = pace_h * pace_a / league_pace
  pts_team    = poss * (ORtg_team_adj + DRtg_opp_adj - league_ORtg) / 100
  ratings     = current season blended with last season, weight G/(G+12)
  injuries    = sum over out players of max(0, BPM+2) * MPG/48 * share of team games played * status weight,
                split 60% offense / 40% defense (per 100 possessions)
  rest        = back-to-back -1.5, 3-in-4 nights -0.8, back-to-back with travel -0.5 more
  location    = home court +2.0, altitude (DEN/UTA) +1.0
Fair line = 35% model + 65% Pinnacle (consensus if no Pinnacle). EV is at FanDuel's price.
Market signals (Action Network): line move from open, reverse line movement, money% vs ticket% gap.
They only confirm or downgrade; they never create a play.
"""
import math, statistics
from common import (log, am_to_dec, prob_to_am, devig2, Phi, team, pname, to_et, now_et, BBREF_ABBR)

HCA, ALT_BONUS = 2.0, 1.0
B2B, THREE_IN_FOUR, TRAVEL = 1.5, 0.8, 0.5
SD_MARGIN, SD_TOTAL = 12.5, 18.0
W_MODEL = 0.35
PRIOR_G = 20            # early-season results get less weight (Nov 4: hot starts DET/MEM lost outright)
STATUS_W = {"Out": 1.0, "Doubtful": 0.8, "Day-To-Day": 0.35, "Questionable": 0.4, "Probable": 0.1}
MAX_STAKED_SIDES, MAX_PROPS = 4, 4

# ---------- ratings ----------
def blend_ratings(cur, prev):
    lg = {k: statistics.mean(v[k] for v in cur.values()) for k in ("ortg", "drtg", "pace")}
    out = {}
    for t, c in cur.items():
        p = prev.get(t, lg); w = c["g"] / (c["g"] + PRIOR_G)
        out[t] = {k: w * c[k] + (1 - w) * p[k] for k in ("ortg", "drtg", "pace")}
        out[t]["g"] = c["g"]
    return out, lg

def rest_flags(sched_by_day, today_games):
    """sched_by_day: {offset(1..3): [games]} for previous days. Returns {team: dict(b2b, three_in_four, travel)}."""
    flags = {}
    for g in today_games:
        for tm, venue_home in ((g["home"], g["home"]), (g["away"], g["home"])):
            played = {}
            for off, games in sched_by_day.items():
                for x in games:
                    if tm in (x["home"], x["away"]): played[off] = x["home"]
            b2b = 1 in played
            flags[tm] = dict(b2b=b2b, three_in_four=len(played) >= 2,
                             travel=b2b and played[1] != venue_home)
    return flags

def injury_impact(team_name, injuries, players, team_games):
    """Per-100-possession impact of listed injuries + list of affected players."""
    tot, names = 0.0, []
    for inj in injuries.get(team_name, []):
        w = STATUS_W.get(inj["status"], 0.3)
        p = players.get(inj["key"])
        if not p or p["team"] != team_name or not p.get("mp"): continue
        mins = (p["g"] or 0) * p["mp"]
        bpm = p["bpm"] if p["bpm"] is not None else -2.0
        bpm = (bpm * mins + (-1.0) * 400) / (mins + 400)               # regress small samples
        share = min(1.0, (p["g"] or 0) / max(1, team_games))            # already-missing players are priced into ratings
        val = max(0.0, bpm + 2.0) * p["mp"] / 48 * share * w
        if val > 0.15:
            tot += val; names.append(f'{p["name"]} ({inj["status"]})')
    return min(tot, 8.0), names

# ---------- market ----------
def _mk(book, key):
    for m in book.get("markets", []):
        if m["key"] == key: return {o["name"]: o for o in m["outcomes"]}
    return None

def market_lines(ev):
    """FanDuel lines/prices + market fair margin/total from Pinnacle (or consensus)."""
    books = {b["key"]: b for b in ev.get("bookmakers", [])}
    home, away = team(ev["home_team"]), team(ev["away_team"])
    rename = lambda d: {team(k) if k not in ("Over", "Under") else k: v for k, v in d.items()} if d else None
    fd = books.get("fanduel")
    if not fd: return None
    fsp, ftot = rename(_mk(fd, "spreads")), _mk(fd, "totals")
    res = dict(home=home, away=away, fd_updated=fd.get("last_update"))
    def fair_from(sp, tot):
        out = {}
        if sp and home in sp and away in sp:
            ph, _ = devig2(sp[home]["price"], sp[away]["price"])
            out["margin"] = -sp[home]["point"] + (ph - 0.5) * SD_MARGIN * math.sqrt(2 * math.pi)
        if tot and "Over" in tot and "Under" in tot:
            po, _ = devig2(tot["Over"]["price"], tot["Under"]["price"])
            out["total"] = tot["Over"]["point"] + (po - 0.5) * SD_TOTAL * math.sqrt(2 * math.pi)
        return out
    if fsp and home in fsp:
        res.update(fd_spread_home=fsp[home]["point"], fd_home_price=fsp[home]["price"], fd_away_price=fsp[away]["price"])
    if ftot and "Over" in ftot:
        res.update(fd_total=ftot["Over"]["point"], fd_over_price=ftot["Over"]["price"], fd_under_price=ftot["Under"]["price"])
    pn = books.get("pinnacle")
    pf = fair_from(rename(_mk(pn, "spreads")), _mk(pn, "totals")) if pn else {}
    others = [fair_from(rename(_mk(b, "spreads")), _mk(b, "totals")) for k, b in books.items() if k not in ("fanduel", "pinnacle")]
    cons = {k: statistics.median([o[k] for o in others if k in o]) for k in ("margin", "total") if sum(k in o for o in others) >= 3}
    for k in ("margin", "total"):
        if k in pf: res[f"mkt_{k}"], res[f"mkt_{k}_src"] = pf[k], "Pinnacle"
        elif k in cons: res[f"mkt_{k}"], res[f"mkt_{k}_src"] = cons[k], "Consensus"
    if pn:
        psp = rename(_mk(pn, "spreads")); ptot = _mk(pn, "totals")
        if psp and home in psp: res["pn_spread_home"] = psp[home]["point"]
        if ptot and "Over" in ptot: res["pn_total"] = ptot["Over"]["point"]
    return res

def signals(an, side):
    """Score market signals for a pick side: 'home','away','over','under'. Returns (score, notes)."""
    if not an: return 0, []
    s, notes = 0, []
    if side in ("home", "away"):
        o, c = an.get("open_spread_home"), an.get("cur_spread_home")
        tix = an.get(f"spread_{side}_tix"); money = an.get(f"spread_{side}_money")
        move = None if o is None or c is None else ((o - c) if side == "home" else (c - o))   # + = moved toward side
    else:
        o, c = an.get("open_total"), an.get("cur_total")
        tix = an.get(f"{side}_tix"); money = an.get(f"{side}_money")
        move = None if o is None or c is None else ((c - o) if side == "over" else (o - c))
    if move is not None and abs(move) >= 0.5:
        s += 1 if move > 0 else (-1 if move <= -1.0 else 0)
        notes.append(f"line moved {'toward' if move > 0 else 'against'} ({o:+g}→{c:+g})" if side in ("home", "away") else
                     f"total moved {'toward' if move > 0 else 'against'} ({o:g}→{c:g})")
        if move > 0 and tix is not None and tix < 45:
            s += 1; notes.append(f"reverse line movement ({tix:.0f}% of bets)")
    if tix is not None and money is not None:
        gap = money - tix
        if gap >= 10: s += 1; notes.append(f"money {money:.0f}% vs bets {tix:.0f}%")
        elif gap <= -10: s -= 1; notes.append(f"money against ({money:.0f}% vs {tix:.0f}% bets)")
    return s, notes

def grade_side(gap, ev, sig, kind):
    a, b, c = (3.0, 2.0, 1.5) if kind == "Spread" else (4.5, 3.0, 2.0)
    if gap >= a and ev >= 0.025 and sig >= 1: return "A"
    if gap >= b and ev >= 0.015 and sig >= 0: return "B"
    if gap >= c and ev > 0: return "C"
    return ""

# ---------- team markets ----------
def project_games(odds, ratings, lg, injuries, players, rest, an_map):
    rows, ctx = [], {}
    for ev in odds:
        m = market_lines(ev)
        if not m or "fd_spread_home" not in m: continue
        h, a = m["home"], m["away"]
        if h not in ratings or a not in ratings: log(f"skip {a}@{h}: no ratings"); continue
        rh, ra = ratings[h], ratings[a]
        ih, nh = injury_impact(h, injuries, players, rh["g"]); ia, na = injury_impact(a, injuries, players, ra["g"])
        poss = rh["pace"] * ra["pace"] / lg["pace"]
        base_h = poss * (rh["ortg"] + ra["drtg"] - lg["ortg"]) / 100
        base_a = poss * (ra["ortg"] + rh["drtg"] - lg["ortg"]) / 100
        f_ratings = base_h - base_a
        f_inj = poss / 100 * (ia - ih)                                   # home benefits from away injuries
        tot_inj = -poss / 100 * 0.2 * (ih + ia)                          # injuries trim scoring slightly
        def rest_pen(t):
            f = rest.get(t, {}); return (B2B if f.get("b2b") else 0) + (THREE_IN_FOUR if f.get("three_in_four") else 0) + (TRAVEL if f.get("travel") else 0)
        f_rest = rest_pen(a) - rest_pen(h)
        f_loc = HCA + (ALT_BONUS if h in ("Denver Nuggets", "Utah Jazz") else 0)
        margin = f_ratings + f_inj + f_rest + f_loc
        total = base_h + base_a + tot_inj - 0.6 * (rest.get(h, {}).get("b2b", 0) + rest.get(a, {}).get("b2b", 0))
        ctx[(a, h)] = dict(poss=poss, ratings_h=rh, ratings_a=ra, inj_h=ih, inj_a=ia, margin=margin, total=total)
        an = an_map.get((a, h))
        fx = dict(ratings=f_ratings, injuries=f_inj, rest=f_rest, location=f_loc)
        inj_note = "; ".join(x for x in [f"{h}: " + ", ".join(nh) if nh else "", f"{a}: " + ", ".join(na) if na else ""] if x)
        start = ev["commence_time"]
        # spread
        if "mkt_margin" in m:
            fair = W_MODEL * margin + (1 - W_MODEL) * m["mkt_margin"]
            for side in ("home", "away"):
                sp = m["fd_spread_home"] if side == "home" else -m["fd_spread_home"]
                price = m["fd_home_price"] if side == "home" else m["fd_away_price"]
                mod = margin if side == "home" else -margin
                fr = fair if side == "home" else -fair
                gap = mod + sp
                if gap <= 0: continue
                p = Phi((fr + sp) / SD_MARGIN); ev_ = p * am_to_dec(price) - 1
                sig, notes = signals(an, side)
                tm = h if side == "home" else a
                factors = {k: (v if side == "home" else -v) for k, v in fx.items()}
                rows.append(dict(kind="Spread", side=side, team=tm, game=(a, h), start=start, line=sp, price=price,
                                 model_line=-mod, fair_line=-fr, mkt_line=-(m["mkt_margin"] if side == "home" else -m["mkt_margin"]),
                                 mkt_src=m["mkt_margin_src"], pn_line=(m.get("pn_spread_home") if side == "home" else (-m["pn_spread_home"] if m.get("pn_spread_home") is not None else None)),
                                 gap=round(gap, 1), p=p, ev=ev_, sig=sig, notes=notes, factors=factors, inj_note=inj_note,
                                 grade=grade_side(gap, ev_, sig, "Spread"), proj=f"{h} {base_h + f_loc/2 + (f_inj+f_rest)/2:.0f}, {a} {base_a - f_loc/2 - (f_inj+f_rest)/2:.0f}"))
        if "mkt_total" in m and "fd_total" in m:
            fair = W_MODEL * total + (1 - W_MODEL) * m["mkt_total"]
            side = "over" if total > m["fd_total"] else "under"
            gap = abs(total - m["fd_total"]); price = m["fd_over_price"] if side == "over" else m["fd_under_price"]
            p = 1 - Phi((m["fd_total"] - fair) / SD_TOTAL) if side == "over" else Phi((m["fd_total"] - fair) / SD_TOTAL)
            ev_ = p * am_to_dec(price) - 1
            sig, notes = signals(an, side)
            rows.append(dict(kind="Total", side=side, team=None, game=(a, h), start=start, line=m["fd_total"], price=price,
                             model_line=total, fair_line=fair, mkt_line=m["mkt_total"], mkt_src=m["mkt_total_src"], pn_line=m.get("pn_total"),
                             gap=round(gap, 1), p=p, ev=ev_, sig=sig, notes=notes,
                             factors=dict(pace=poss - lg["pace"], injuries=tot_inj), inj_note=inj_note,
                             grade=grade_side(gap, ev_, sig, "Total"), proj=f"{poss:.0f} possessions, total {total:.1f}"))
    # totals: strip the model's slate-wide bias vs market (it ran ~+5 high on Nov 4), keep game-to-game differences
    tots = [r for r in rows if r["kind"] == "Total"]
    if len(tots) >= 4:
        bias = statistics.mean(r["model_line"] - r["mkt_line"] for r in tots)
        for r in tots:
            r["model_line"] -= bias
            r["fair_line"] = W_MODEL * r["model_line"] + (1 - W_MODEL) * r["mkt_line"]
            side = "over" if r["model_line"] > r["line"] else "under"
            if side != r["side"]:
                r["grade"] = ""; r["gap"] = 0; continue
            r["gap"] = round(abs(r["model_line"] - r["line"]), 1)
            p = 1 - Phi((r["line"] - r["fair_line"]) / SD_TOTAL) if side == "over" else Phi((r["line"] - r["fair_line"]) / SD_TOTAL)
            r["p"] = p; r["ev"] = p * am_to_dec(r["price"]) - 1
            r["grade"] = grade_side(r["gap"], r["ev"], r["sig"], "Total")
            r["proj"] += f" (bias-adjusted {-bias:+.1f})"
        log(f"totals bias removed: {bias:+.1f}")
    # one play per game per market, cap staked plays
    rows.sort(key=lambda r: -r["ev"])
    staked = 0
    for r in rows:
        if r["grade"] in ("A", "B"):
            if staked >= MAX_STAKED_SIDES: r["grade"] = "C"
            else: staked += 1
    return rows, ctx

# ---------- props ----------
STAT = {"player_points": ("pts", "Pts"), "player_rebounds": ("reb", "Reb"), "player_assists": ("ast", "Ast")}
def _rate(cur, prev, stat, k=300):
    cm = (cur.get("g") or 0) * (cur.get("mp") or 0)
    cr = (cur.get(stat) or 0) / cur["mp"] if cur.get("mp") else 0
    if prev and prev.get("mp"):
        pr = (prev.get(stat) or 0) / prev["mp"]
        return (cr * cm + pr * k) / (cm + k)
    return cr

def _sd(stat, mean):
    if stat == "pts": return 0.28 * mean + 2.5
    if stat == "reb": return 1.15 * math.sqrt(max(mean, 0.5)) + 0.8
    return 1.2 * math.sqrt(max(mean, 0.5)) + 0.6

def team_context(tm, injuries, players):
    out_keys = {i["key"] for i in injuries.get(tm, []) if STATUS_W.get(i["status"], 0) >= 0.8}
    shaky = {i["key"] for i in injuries.get(tm, []) if 0 < STATUS_W.get(i["status"], 0) < 0.8}
    roster = {k: p for k, p in players.items() if p["team"] == tm and p.get("mp") and (p.get("g") or 0) >= 2}
    tg = max([p["g"] for p in roster.values()] + [1])
    outs = [p for k, p in roster.items() if k in out_keys]
    act = {k: p for k, p in roster.items() if k not in out_keys and p["mp"] >= 12}
    out_min = sum(p["mp"] * min(1, p["g"] / tg) for p in outs)
    out_usg = sum((p["usg"] or 15) * p["mp"] * min(1, p["g"] / tg) for p in outs)
    act_min = sum(p["mp"] for p in act.values()) or 1
    act_usg = sum((p["usg"] or 15) * p["mp"] for p in act.values()) or 1
    return dict(out_keys=out_keys, shaky=shaky, act=act, out_min=out_min, usg_bump=min(1.25, 1 + 0.5 * out_usg / act_usg),
                act_min=act_min, outs=[p["name"] for p in outs])

def sanity_props(rows, players):
    """Hold props whose line sits >25% from the player's season average without an injury/usage explanation:
    the market likely knows something (minutes limit, role change). Then re-apply caps."""
    for r in rows:
        p = players.get(pname(r["player"]))
        avg = (p or {}).get(r["stat"])
        # our injury/usage adjustments only push projections up, so they can only explain a line set ABOVE the average
        explained = avg is not None and r["line"] > avg and (r.get("extra_min", 0) >= 2 or r.get("usg_bump", 1) >= 1.05)
        if avg and r["line"] > 0 and abs(avg - r["line"]) / r["line"] > 0.25 and not explained:
            r["grade"], r["hold"] = "", f"line {r['line']:g} vs season avg {avg:.1f}: market likely pricing news"
        elif r.get("edge", 0) > 0.20 and r["line"] >= 3:
            r["grade"], r["hold"] = "", f"projection {r['model_line']:g} vs line {r['line']:g}: gap too large to trust without news"
        elif r.get("mkt_src", "").startswith("Consensus (1)") and r["grade"] == "A":
            r["grade"] = "B"                                   # one outside book is thin market confirmation
    rows.sort(key=lambda r: -r["ev"])
    seen, n = set(), 0
    for r in rows:
        if r["grade"] in ("A", "B"):
            if r["player"] in seen or n >= MAX_PROPS: r["grade"] = "C"
            else: seen.add(r["player"]); n += 1
    return rows

ALLPROPS = []

def calibrate_props(rows):
    """Remove the slate-wide over-projection per stat (40 of 42 prop plays were overs through Nov 5).
    Divide each projection by the median projection/line ratio for that stat, then re-price and re-grade."""
    ratios = {}
    for st in {r["stat"] for r in rows}:
        xs = sorted(x for s_, x in ALLPROPS if s_ == st and 0.3 < x < 3)
        if len(xs) >= 10: ratios[st] = xs[len(xs) // 2]
    out = []
    for r in rows:
        k = ratios.get(r["stat"], 1.0)
        mean = r["model_line"] / k; line = r["line"]
        sd = _sd(r["stat"], mean)
        p_over_m = 1 - Phi((line - mean) / sd)
        p_over = 0.4 * p_over_m + 0.6 * r["p_mkt_over"]
        side = r["side"]
        pf = p_over if side == "Over" else 1 - p_over
        ev_ = pf * am_to_dec(r["price"]) - 1
        edge = (mean - line) / max(line, 1) * (1 if side == "Over" else -1)
        mkt_ev = (r["p_mkt_over"] if side == "Over" else 1 - r["p_mkt_over"]) * am_to_dec(r["price"]) - 1
        if edge <= 0: continue
        # grades: modest model gap AND the market agrees FanDuel's price is fair-or-better. Big gaps are suspect, not strong.
        gr = ("A" if 0.06 <= edge <= 0.18 and ev_ >= 0.04 and mkt_ev >= 0.0 else
              "B" if 0.05 <= edge <= 0.20 and ev_ >= 0.025 and mkt_ev >= -0.02 else
              "C" if edge >= 0.04 and ev_ > 0 else "")
        r.update(model_line=round(mean, 1), p=pf, ev=ev_, edge=edge, gap=round(abs(mean - line), 1), grade=gr, mkt_ev=round(mkt_ev, 3),
                 why=r["why"] + (f", slate calib ÷{k:.2f}" if abs(k - 1) > 0.01 else ""))
        out.append(r)
    log("prop calibration ratios: " + ", ".join(f"{k} {v:.2f}" for k, v in ratios.items()))
    return out

def project_props(prop_events, players, prev_players, ratings, lg, injuries, ctx):
    rows = []
    for ev in prop_events:
        h, a = team(ev["home_team"]), team(ev["away_team"])
        g = ctx.get((a, h))
        if not g: continue
        books = {b["key"]: b for b in ev.get("bookmakers", [])}
        fd = books.get("fanduel")
        if not fd: continue
        tctx = {h: team_context(h, injuries, players), a: team_context(a, injuries, players)}
        for mk in fd.get("markets", []):
            if mk["key"] not in STAT: continue
            stat, lab = STAT[mk["key"]]
            byp = {}
            for o in mk["outcomes"]:
                byp.setdefault((o.get("description"), o.get("point")), {})[o["name"]] = o["price"]
            for (player, line), pr in byp.items():
                if "Over" not in pr or "Under" not in pr or line is None: continue
                k = pname(player); p = players.get(k)
                if not p or p["team"] not in (h, a): continue
                tc = tctx[p["team"]]
                if k in tc["out_keys"] or k in tc["shaky"] or k not in tc["act"]: continue
                opp = a if p["team"] == h else h
                extra = min(3.0, tc["out_min"] * 0.5 * p["mp"] / tc["act_min"])   # much of the freed minutes go to the bench
                mins = min(38.0, p["mp"] + extra)
                pace_f = g["poss"] / ratings[p["team"]]["pace"]
                rate = _rate(p, prev_players.get(k), stat)
                if stat == "pts":
                    dfac = (ratings[opp]["drtg"] + (g["inj_a"] if opp == a else g["inj_h"]) * 0.4) / lg["ortg"]
                    mean = rate * mins * tc["usg_bump"] * pace_f * dfac
                elif stat == "ast":
                    mean = rate * mins * pace_f * (1 + (tc["usg_bump"] - 1) * 0.5)
                else:
                    mean = rate * mins * pace_f
                ALLPROPS.append((stat, mean / line if line else 1.0))
                sd = _sd(stat, mean)
                p_over_model = 1 - Phi((line - mean) / sd)
                # market fair: Pinnacle same line, else median of other books at same line, else FanDuel itself
                mk_probs = []
                for bk, b in books.items():
                    if bk == "fanduel": continue
                    for m2 in b.get("markets", []):
                        if m2["key"] != mk["key"]: continue
                        pp = {o["name"]: o["price"] for o in m2["outcomes"] if o.get("description") == player and o.get("point") == line}
                        if "Over" in pp and "Under" in pp: mk_probs.append((bk, devig2(pp["Over"], pp["Under"])[0]))
                pin = [x for b_, x in mk_probs if b_ == "pinnacle"]
                if pin: p_mkt, src = pin[0], "Pinnacle"
                elif mk_probs: p_mkt, src = statistics.median(x for _, x in mk_probs), f"Consensus ({len(mk_probs)})"
                else: p_mkt, src = devig2(pr["Over"], pr["Under"])[0], "FanDuel no-vig"
                fair_over = 0.4 * p_over_model + 0.6 * p_mkt
                for side, pf, price in (("Over", fair_over, pr["Over"]), ("Under", 1 - fair_over, pr["Under"])):
                    ev_ = pf * am_to_dec(price) - 1
                    edge = (mean - line) / max(line, 1) * (1 if side == "Over" else -1)
                    if edge <= -0.10: continue
                    gr = "A" if edge >= 0.12 and ev_ >= 0.05 else "B" if edge >= 0.08 and ev_ >= 0.03 else "C" if edge >= 0.05 and ev_ > 0 else ""
                    why = f"{mins:.0f} min proj"
                    if extra > 1: why += f" (+{extra:.1f} with {', '.join(tc['outs'][:2])} out)"
                    if tc["usg_bump"] > 1.02 and stat != "reb": why += f", usage ×{tc['usg_bump']:.2f}"
                    why += f", pace ×{pace_f:.2f}"
                    if stat == "pts": why += f", opp D ×{dfac:.2f}"
                    rows.append(dict(kind="Prop", player=p["name"], stat=stat, stat_label=lab, side=side, game=(a, h), team=p["team"],
                                     start=ev["commence_time"], event_id=ev["id"], line=line, price=price, model_line=round(mean, 1),
                                     p=pf, ev=ev_, gap=round(abs(mean - line), 1), edge=edge, mkt_src=src, grade=gr, why=why,
                                     extra_min=round(extra, 1), usg_bump=round(tc["usg_bump"], 3), p_mkt_over=p_mkt))
    rows = calibrate_props(rows)
    return sanity_props(rows, players)
