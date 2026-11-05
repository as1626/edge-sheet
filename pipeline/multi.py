"""Multi-sport layer: price scan (all sports) + team-rating projection models (NHL, NFL, NCAAF, NCAAM).

Price scan: FanDuel price vs Pinnacle no-vig at the SAME line (consensus of 3+ books if no Pinnacle).
            Flag EV >= 2% (3% on consensus). Stake = 1/4 Kelly, cap 1u. Edges > 8% are capped at 0.25u (likely stale).
Team models (points/goals, home perspective), ratings blended with last season by games played:
  NFL / NCAAF : pts_home = lg + OSRS_h - DSRS_a + HFA/2 ; pts_away = lg + OSRS_a - DSRS_h - HFA/2
  NCAAM       : poss = pace_h*pace_a/lg_pace ; pts = poss*(ORtg + opp DRtg - lg)/100, home +3 (split)
  NHL         : goals = GF/G * opp GA/G / lg (home x1.04, away /1.04), Poisson for ML/total
Fair = model weight W + market (1-W). Stakes follow STAKE_POLICY until each model proves out on closing lines.
"""
import math, statistics
from common import log, am_to_dec, prob_to_am, devig2, Phi, team, pname

SPORTS = {  # odds key: (label, ESPN path)
    "basketball_nba": ("NBA", "basketball/nba"),
    "icehockey_nhl": ("NHL", "hockey/nhl"),
    "americanfootball_nfl": ("NFL", "football/nfl"),
    "americanfootball_ncaaf": ("NCAAF", "football/college-football"),
    "basketball_ncaab": ("NCAAM", "basketball/mens-college-basketball"),
}
CFG = {
    "NFL":   dict(hfa=1.5, sd_m=13.5, sd_t=10.0, w=0.25, gap=(3.0, 2.0, 1.5), tgap=(4.0, 3.0, 2.0), prior_g=6),
    "NCAAF": dict(hfa=2.5, sd_m=16.0, sd_t=14.0, w=0.25, gap=(4.0, 3.0, 2.0), tgap=(5.0, 3.5, 2.5), prior_g=5, max_spread=24),
    "NCAAM": dict(hfa=3.0, sd_m=11.0, sd_t=13.0, w=0.25, gap=(4.0, 3.0, 2.0), tgap=(5.0, 3.5, 2.5), prior_g=8, max_spread=18),
    "NHL":   dict(home=1.04, w=0.30, prior_g=15),
}
# stake multiplier per model until validated (A=1u, B=0.5u scaled by this; 0 = tracked only)
STAKE_POLICY = {"NHL": 0.5, "NFL": 0.0, "NCAAF": 0.0, "NCAAM": 0.0}

def _mk(book, key, home, away):
    for m in book.get("markets", []):
        if m["key"] == key:
            out = {}
            for o in m["outcomes"]:
                n = o["name"] if o["name"] in ("Over", "Under") else ("home" if team(o["name"]) == home else "away" if team(o["name"]) == away else None)
                if n: out[n] = o
            return out
    return None

# ---------- price scan ----------
def price_scan(odds, label, min_ev=0.02, cons_extra=0.01):
    flags = []
    for ev in odds:
        home, away = team(ev["home_team"]), team(ev["away_team"])
        books = {b["key"]: b for b in ev.get("bookmakers", [])}
        fd = books.get("fanduel")
        if not fd: continue
        for key in ("h2h", "spreads", "totals"):
            f = _mk(fd, key, home, away)
            if not f or len(f) != 2: continue
            sides = list(f.keys())
            line_of = lambda d: tuple(d[s].get("point") for s in sides)
            fair = {}
            for bk, b in books.items():
                if bk == "fanduel": continue
                o = _mk(b, key, home, away)
                if not o or set(o) != set(sides) or line_of(o) != line_of(f): continue
                fair[bk] = devig2(o[sides[0]]["price"], o[sides[1]]["price"])
            if "pinnacle" in fair: p, src = fair["pinnacle"], "Pinnacle"
            elif len(fair) >= 3: p, src = (statistics.median(v[0] for v in fair.values()), statistics.median(v[1] for v in fair.values())), f"Consensus ({len(fair)})"
            else: continue
            for i, s in enumerate(sides):
                price = f[s]["price"]; pr = p[i]; e = pr * am_to_dec(price) - 1
                if e < min_ev + (0 if src == "Pinnacle" else cons_extra): continue
                b_ = am_to_dec(price) - 1; kelly = max(0, (pr * b_ - (1 - pr)) / b_)
                stake = min(1.0, round(kelly * 0.25 * 100, 2))
                if e > 0.08: stake = min(stake, 0.25)
                tm = home if s == "home" else away if s == "away" else None
                pt = f[s].get("point")
                pick = (f"{tm} ML" if key == "h2h" else f"{tm} {pt:+g}" if key == "spreads" else f"{s} {pt:g}")
                flags.append(dict(sport=label, key=key, side=s, team=tm, game=(away, home), start=ev["commence_time"], line=pt,
                                  price=price, fair_prob=pr, fair_price=prob_to_am(pr), ev=e, src=src, stake=stake, pick=pick,
                                  stale=e > 0.08, fd_updated=fd.get("last_update")))
    flags.sort(key=lambda r: -r["ev"])
    log(f"price scan {label}: {len(flags)} flags")
    return flags

# ---------- market fair numbers ----------
def market_numbers(ev, sd_m, sd_t):
    home, away = team(ev["home_team"]), team(ev["away_team"])
    books = {b["key"]: b for b in ev.get("bookmakers", [])}
    def fair(b):
        out = {}
        sp, to, ml = _mk(b, "spreads", home, away), _mk(b, "totals", home, away), _mk(b, "h2h", home, away)
        if sp and len(sp) == 2:
            ph, _ = devig2(sp["home"]["price"], sp["away"]["price"])
            out["margin"] = -sp["home"]["point"] + (ph - 0.5) * sd_m * math.sqrt(2 * math.pi)
        if to and len(to) == 2:
            po, _ = devig2(to["Over"]["price"], to["Under"]["price"])
            out["total"] = to["Over"]["point"] + (po - 0.5) * sd_t * math.sqrt(2 * math.pi)
        if ml and len(ml) == 2: out["p_home"] = devig2(ml["home"]["price"], ml["away"]["price"])[0]
        return out
    res = dict(home=home, away=away)
    fd = books.get("fanduel")
    if not fd: return None
    res["fd"] = {k: _mk(fd, k, home, away) for k in ("spreads", "totals", "h2h")}
    pn = fair(books["pinnacle"]) if "pinnacle" in books else {}
    others = [fair(b) for k, b in books.items() if k not in ("fanduel", "pinnacle")]
    for k in ("margin", "total", "p_home"):
        if k in pn: res[k], res[k + "_src"] = pn[k], "Pinnacle"
        else:
            vals = [o[k] for o in others if k in o]
            if len(vals) >= 3: res[k], res[k + "_src"] = statistics.median(vals), "Consensus"
    return res

def _grade(gap, e, gaps):
    a, b, c = gaps
    return "A" if gap >= a and e >= 0.025 else "B" if gap >= b and e >= 0.015 else "C" if gap >= c and e > 0 else ""

# ---------- ratings-based point models (NFL, NCAAF, NCAAM) ----------
def blend(cur, prev, prior_g, keys):
    lg = {k: statistics.mean(v[k] for v in (cur or prev).values() if v.get(k) is not None) for k in keys}
    out = {}
    for t in set(cur) | set(prev):
        c = cur.get(t) or {**prev[t], "g": 0}
        p = prev.get(t) or {k: (0.0 if k in ("osrs", "dsrs") else lg[k]) for k in keys}
        w = (c.get("g") or 0) / ((c.get("g") or 0) + prior_g)
        out[t] = {k: w * c[k] + (1 - w) * p.get(k, lg[k]) for k in keys if c.get(k) is not None}
        out[t]["g"] = c.get("g") or 0
    return out, lg

def point_model(label, odds, ratings, lg, resolve):
    cfg = CFG[label]; rows = []
    for ev in odds:
        m = market_numbers(ev, cfg["sd_m"], cfg["sd_t"])
        if not m or "margin" not in m: continue
        rh, ra = resolve(m["home"]), resolve(m["away"])
        if not rh or not ra: continue
        h, a = ratings[rh], ratings[ra]
        if label == "NCAAM":
            if min(h.get("g", 0), a.get("g", 0)) < 0: continue
            poss = h["pace"] * a["pace"] / lg["pace"]
            ph = poss * (h["ortg"] + a["drtg"] - lg["ortg"]) / 100 + cfg["hfa"] / 2
            pa = poss * (a["ortg"] + h["drtg"] - lg["ortg"]) / 100 - cfg["hfa"] / 2
            fx = dict(ratings=ph - pa - cfg["hfa"], home=cfg["hfa"]); proj = f"{poss:.0f} possessions"
        elif "fpi" in h:
            margin_ = h["fpi"] - a["fpi"] + cfg["hfa"]
            ph, pa = 50 + margin_ / 2, 50 - margin_ / 2           # totals not modeled from FPI
            fx = dict(ratings=h["fpi"] - a["fpi"], home=cfg["hfa"]); proj = f"FPI {m['home']} {h['fpi']:+.1f}, {m['away']} {a['fpi']:+.1f}"
        else:
            ph = lg["ppg"] + h["osrs"] - a["dsrs"] + cfg["hfa"] / 2
            pa = lg["ppg"] + a["osrs"] - h["dsrs"] - cfg["hfa"] / 2
            fx = dict(ratings=ph - pa - cfg["hfa"], home=cfg["hfa"]); proj = f"{m['home']} {ph:.0f}, {m['away']} {pa:.0f}"
        margin, total = ph - pa, ph + pa
        fd = m["fd"]; sp = fd.get("spreads"); to = fd.get("totals")
        if sp and len(sp) == 2 and abs(sp["home"]["point"]) <= cfg.get("max_spread", 99):
            fair = cfg["w"] * margin + (1 - cfg["w"]) * m["margin"]
            for side in ("home", "away"):
                line = sp[side]["point"]; mod = margin if side == "home" else -margin; fr = fair if side == "home" else -fair
                gap = mod + line
                if gap <= 0: continue
                p = Phi((fr + line) / cfg["sd_m"]); e = p * am_to_dec(sp[side]["price"]) - 1
                rows.append(dict(sport=label, kind="Spread", side=side, team=m[side], game=(m["away"], m["home"]), start=ev["commence_time"],
                                 line=line, price=sp[side]["price"], model_line=-mod, fair_line=-fr, mkt_line=-(m["margin"] if side == "home" else -m["margin"]),
                                 gap=round(gap, 1), p=p, ev=e, grade=_grade(gap, e, cfg["gap"]),
                                 factors={k: (v if side == "home" else -v) for k, v in fx.items()}, proj=proj))
        if to and len(to) == 2 and "total" in m and "fpi" not in h:
            fair = cfg["w"] * total + (1 - cfg["w"]) * m["total"]; line = to["Over"]["point"]
            side = "Over" if total > line else "Under"; gap = abs(total - line)
            p = (1 - Phi((line - fair) / cfg["sd_t"])) if side == "Over" else Phi((line - fair) / cfg["sd_t"])
            e = p * am_to_dec(to[side]["price"]) - 1
            rows.append(dict(sport=label, kind="Total", side=side.lower(), team=None, game=(m["away"], m["home"]), start=ev["commence_time"],
                             line=line, price=to[side]["price"], model_line=total, fair_line=fair, mkt_line=m["total"],
                             gap=round(gap, 1), p=p, ev=e, grade=_grade(gap, e, cfg["tgap"]), factors={}, proj=proj))
    log(f"{label} model: {len(rows)} priced, {sum(r['grade'] in ('A','B') for r in rows)} A/B")
    return rows

# ---------- NHL Poisson ----------
def _pois(k, lam): return math.exp(-lam) * lam ** k / math.factorial(k)
def nhl_model(odds, ratings, lg, resolve):
    cfg = CFG["NHL"]; rows = []
    for ev in odds:
        m = market_numbers(ev, 1.6, 2.2)
        if not m: continue
        rh, ra = resolve(m["home"]), resolve(m["away"])
        if not rh or not ra: continue
        h, a = ratings[rh], ratings[ra]
        lh = h["gf"] * a["ga"] / lg["gf"] * cfg["home"]; la = a["gf"] * h["ga"] / lg["gf"] / cfg["home"]
        grid = [[_pois(i, lh) * _pois(j, la) for j in range(12)] for i in range(12)]
        p_reg_h = sum(grid[i][j] for i in range(12) for j in range(12) if i > j)
        p_tie = sum(grid[i][i] for i in range(12))
        p_home = p_reg_h + 0.52 * p_tie
        fd = m["fd"]; ml = fd.get("h2h"); to = fd.get("totals")
        proj = f"xG {m['home']} {lh:.2f}, {m['away']} {la:.2f}"
        if ml and len(ml) == 2 and "p_home" in m:
            fair_h = cfg["w"] * p_home + (1 - cfg["w"]) * m["p_home"]
            for side, pf, pm in (("home", fair_h, p_home), ("away", 1 - fair_h, 1 - p_home)):
                mk = m["p_home"] if side == "home" else 1 - m["p_home"]
                if pm <= mk: continue
                e = pf * am_to_dec(ml[side]["price"]) - 1; gap = (pm - mk) * 100
                g = "A" if gap >= 6 and e >= 0.03 else "B" if gap >= 4 and e >= 0.02 else "C" if gap >= 2.5 and e > 0 else ""
                rows.append(dict(sport="NHL", kind="Moneyline", side=side, team=m[side], game=(m["away"], m["home"]), start=ev["commence_time"],
                                 line=None, price=ml[side]["price"], model_line=prob_to_am(pm), fair_line=prob_to_am(pf), mkt_line=prob_to_am(mk),
                                 gap=round(gap, 1), p=pf, ev=e, grade=g, factors={}, proj=proj + f"; win {pm:.0%} vs market {mk:.0%}"))
        if to and len(to) == 2:
            line = to["Over"]["point"]; tot = lh + la
            p_over_m = sum(grid[i][j] for i in range(12) for j in range(12) if i + j > line)
            mk_over = devig2(to["Over"]["price"], to["Under"]["price"])[0]
            p_over = cfg["w"] * p_over_m + (1 - cfg["w"]) * mk_over
            side = "Over" if p_over_m > mk_over else "Under"
            pf = p_over if side == "Over" else 1 - p_over
            e = pf * am_to_dec(to[side]["price"]) - 1; gap = abs(tot - line)
            g = "A" if gap >= 0.7 and e >= 0.03 else "B" if gap >= 0.45 and e >= 0.02 else "C" if gap >= 0.3 and e > 0 else ""
            rows.append(dict(sport="NHL", kind="Total", side=side.lower(), team=None, game=(m["away"], m["home"]), start=ev["commence_time"],
                             line=line, price=to[side]["price"], model_line=round(tot, 2), fair_line=None, mkt_line=None,
                             gap=round(gap, 2), p=pf, ev=e, grade=g, factors={}, proj=proj))
    log(f"NHL model: {len(rows)} priced, {sum(r['grade'] in ('A','B') for r in rows)} A/B")
    return rows

# ---------- name resolution for college teams ----------
def resolver(names):
    """Map an odds-feed name ('Ohio Bobcats') to a ratings name ('Ohio') by longest normalized prefix."""
    norm = {pname(n): n for n in names}
    alias = {"miami hurricanes": ["Miami (FL)"], "miami oh redhawks": ["Miami (OH)"], "uconn huskies": ["Connecticut", "UConn"],
             "ole miss rebels": ["Ole Miss", "Mississippi"], "ucf knights": ["UCF", "Central Florida"], "usc trojans": ["USC", "Southern California"],
             "lsu tigers": ["LSU", "Louisiana State"], "smu mustangs": ["SMU", "Southern Methodist"], "byu cougars": ["BYU", "Brigham Young"],
             "tcu horned frogs": ["TCU", "Texas Christian"], "utsa roadrunners": ["UTSA", "Texas-San Antonio"], "utep miners": ["UTEP", "Texas-El Paso"],
             "unlv rebels": ["UNLV", "Nevada-Las Vegas"], "uab blazers": ["UAB", "Alabama-Birmingham"], "pitt panthers": ["Pitt", "Pittsburgh"],
             "pittsburgh panthers": ["Pittsburgh", "Pitt"], "app state mountaineers": ["Appalachian State", "App State Mountaineers"],
             "appalachian state mountaineers": ["App State Mountaineers", "Appalachian State"],
             "southern mississippi golden eagles": ["Southern Miss Golden Eagles", "Southern Miss"],
             "sam houston state bearkats": ["Sam Houston Bearkats", "Sam Houston"], "umass minutemen": ["Massachusetts Minutemen", "Massachusetts", "UMass"], 
             "nc state wolfpack": ["NC State", "North Carolina State"], "st johns red storm": ["St. John's (NY)", "St. John's"],
             "saint marys gaels": ["Saint Mary's (CA)", "Saint Mary's"], "vcu rams": ["VCU", "Virginia Commonwealth"],
             "southern miss golden eagles": ["Southern Miss", "Southern Mississippi"], "louisiana ragin cajuns": ["Louisiana", "Louisiana-Lafayette"],
             "ul monroe warhawks": ["Louisiana-Monroe", "UL Monroe"], "hawaii rainbow warriors": ["Hawaii"], "sam houston bearkats": ["Sam Houston", "Sam Houston State"],
             "fiu panthers": ["FIU", "Florida International"], "fau owls": ["Florida Atlantic", "FAU"], "texas am aggies": ["Texas A&M"],
             "umbc retrievers": ["UMBC", "Maryland-Baltimore County"], "liu sharks": ["LIU", "Long Island University"],
             "csu northridge matadors": ["Cal State Northridge"], "se louisiana lions": ["Southeastern Louisiana"],
             "queens university royals": ["Queens (NC)", "Queens"], "fairleigh dickinson knights": ["Fairleigh Dickinson"],
             "saint josephs hawks": ["Saint Joseph's"], "loyola marymount lions": ["Loyola Marymount"],
             "california golden bears": ["California"], "south dakota coyotes": ["South Dakota"]}
    cache = {}
    def f(name):
        if name in cache: return cache[name]
        k = pname(name); r = None
        hit = next((x for x in alias.get(k, []) if x in names), None)
        if hit: r = hit
        elif k in norm: r = norm[k]
        else:
            best = [n for n in norm if k.startswith(n + " ") or k == n]
            if best: r = norm[max(best, key=len)]
            else:
                k2 = k.replace(" st ", " state ").replace("state", "st")
                best = [n for n in norm if k2.startswith(n.replace("state", "st") + " ")]
                if best: r = norm[max(best, key=len)]
        cache[name] = r
        return r
    return f


# ---------- prop price scan (any sport): FanDuel vs Pinnacle / consensus no-vig at the same line ----------
PROP_LABEL = {"player_pass_yds": ("pass_yds", "Pass Yds"), "player_pass_tds": ("pass_tds", "Pass TDs"), "player_rush_yds": ("rush_yds", "Rush Yds"),
              "player_receptions": ("receptions", "Rec"), "player_reception_yds": ("rec_yds", "Rec Yds"),
              "player_points": ("pts", "Pts"), "player_rebounds": ("reb", "Reb"), "player_assists": ("ast", "Ast")}
def prop_scan(events, label, min_ev=0.04, cons_extra=0.01):
    flags = []
    for ev in events:
        home, away = team(ev["home_team"]), team(ev["away_team"])
        books = {b["key"]: b for b in ev.get("bookmakers", [])}
        fd = books.get("fanduel")
        if not fd: continue
        for mk in fd.get("markets", []):
            if mk["key"] not in PROP_LABEL: continue
            stat, lab = PROP_LABEL[mk["key"]]
            fdp = {}
            for o in mk["outcomes"]:
                fdp.setdefault((o.get("description"), o.get("point")), {})[o["name"]] = o["price"]
            for (player, line), pr in fdp.items():
                if "Over" not in pr or "Under" not in pr or line is None: continue
                fair = {}
                for bk, b in books.items():
                    if bk == "fanduel": continue
                    for m2 in b.get("markets", []):
                        if m2["key"] != mk["key"]: continue
                        pp = {o["name"]: o["price"] for o in m2["outcomes"] if o.get("description") == player and o.get("point") == line}
                        if "Over" in pp and "Under" in pp: fair[bk] = devig2(pp["Over"], pp["Under"])[0]
                if "pinnacle" in fair: po, src = fair["pinnacle"], "Pinnacle"
                elif len(fair) >= 2: po, src = statistics.median(fair.values()), f"Consensus ({len(fair)})"
                else: continue
                for side, p, price in (("Over", po, pr["Over"]), ("Under", 1 - po, pr["Under"])):
                    e = p * am_to_dec(price) - 1
                    if e < min_ev + (0 if src == "Pinnacle" else cons_extra): continue
                    b_ = am_to_dec(price) - 1; kelly = max(0, (p * b_ - (1 - p)) / b_)
                    flags.append(dict(sport=label, player=player, stat=stat, stat_label=lab, side=side, line=line, price=price, fair_prob=p,
                                      fair_price=prob_to_am(p), ev=e, src=src, books=len(fair), stake=min(0.5, round(kelly * 0.25 * 100, 2)),
                                      game=(away, home), start=ev["commence_time"], event_id=ev["id"], stale=e > 0.10))
    flags.sort(key=lambda r: -r["ev"])
    seen, out = set(), []
    for f in flags:                       # one play per player
        if f["player"] in seen: continue
        seen.add(f["player"]); out.append(f)
    log(f"prop scan {label}: {len(flags)} flags, {len(out)} after one-per-player")
    return out
