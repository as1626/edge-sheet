"""Build The Edge Sheet (shareable HTML) from ledger.json."""
import json, html, math, os
from datetime import datetime, timezone, timedelta
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
L = json.load(open(os.path.join(ROOT, "data", "ledger.json")))
BETS = L["bets"]
ET = timezone(timedelta(hours=-5))
UPDATED = L.get("updated_label", "Tue Nov 3 · 6:40 PM ET")
SLATE_DATE = L.get("slate_date", "2026-11-03")
SLATE_LABEL = L.get("slate_label", "Nov 3")
TODAY = datetime.now(timezone.utc).astimezone(ET).date().isoformat()
IS_TODAY = SLATE_DATE == TODAY

def dec(a): return 1 + (a / 100 if a > 0 else 100 / -a)
def am(a): return f"+{a}" if a > 0 else f"−{abs(a)}"
def num(x, plus=True):
    s = f"{abs(x):g}"
    return ("+" if x > 0 and plus else "−" if x < 0 else "") + s
def esc(s): return html.escape(str(s))
def t_et(iso):
    d = datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(ET)
    return d.strftime("%-I:%M %p")
def day_et(iso):
    return datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(ET).strftime("%a %b %-d")

def clv(b):
    if b["close_line"] is None or b["line"] is None: return None
    if b["market"] == "Spread": return round(b["line"] - b["close_line"], 1)
    if b["pick"].startswith("Over"): return round(b["close_line"] - b["line"], 1)
    return round(b["line"] - b["close_line"], 1)

# ---------- performance ----------
PARLAYS = [b for b in BETS if b["engine"] == "Parlay"]
BETS = [b for b in BETS if b["engine"] != "Parlay"]
ALL = {b["id"]: b for b in BETS}
staked = [b for b in BETS if b["stake"] > 0]
graded = [b for b in staked if b["status"] in ("won", "lost", "push")]
W = sum(b["status"] == "won" for b in graded); Lo = sum(b["status"] == "lost" for b in graded); P = sum(b["status"] == "push" for b in graded)
units = sum(b["result_units"] or 0 for b in graded)
risked = sum(b["stake"] for b in graded)
roi = units / risked * 100 if risked else None
clvs = [clv(b) for b in BETS if b["grade"] in "AB" and b["engine"] == "Projection" and clv(b) is not None]
avg_clv = sum(clvs) / len(clvs) if clvs else None
beat = sum(c > 0 for c in clvs); held = sum(c == 0 for c in clvs)
pending = [b for b in staked if b["status"] == "pending"]
track = [b for b in BETS if b["stake"] == 0]
track_g = [b for b in track if b["status"] in ("won", "lost", "push")]
tw = sum(b["status"] == "won" for b in track_g); tl = sum(b["status"] == "lost" for b in track_g)

def by_grade(g):
    gs = [b for b in graded if b["grade"] == g]
    w = sum(b["status"] == "won" for b in gs); l = sum(b["status"] == "lost" for b in gs)
    u = sum(b["result_units"] or 0 for b in gs)
    return w, l, u, len([b for b in staked if b["grade"] == g])

# ---------- number line ----------
def numline(b):
    if b.get("engine") == "Price edge" and b["market"] != "Moneyline": return ""
    if b["market"] == "Moneyline" and (b.get("fair_line") is None or b.get("price") is None): return ""
    if b["market"] != "Moneyline" and (b.get("line") is None or b.get("model_line") is None): return ""
    if b["market"] == "Moneyline":
        pts = [("FanDuel", b["price"], "fd"), ("Fair", b["fair_line"], "md"), ("Pinnacle", b["pinnacle_open"] if b["pinnacle_open"] is not None else b["fair_line"], "pn")]
        vals = [v for _, v, _ in pts]
        unit = "American odds"
    else:
        pn = b["close_line"] if b["close_line"] is not None else b["pinnacle_open"]
        pts = [("FanDuel", b["line"], "fd"), ("Our model", b["model_line"], "md"), ("Pinnacle", pn if pn is not None else b["line"], "pn")]
        vals = [v for _, v, _ in pts]
        unit = "points, from the pick's side" if b["market"] == "Spread" else ("projected stat" if b["market"] == "Prop" else "total points")
    lo, hi = min(vals), max(vals)
    pad = max(1.5, (hi - lo) * 0.18)
    lo, hi = lo - pad, hi + pad
    W_, X0, X1 = 520, 18, 502
    x = lambda v: X0 + (v - lo) / (hi - lo) * (X1 - X0)
    fx, mx = x(pts[0][1]), x(pts[1][1])
    out = [f'<svg class="nl" viewBox="0 0 {W_} 74" role="img" aria-label="FanDuel {pts[0][1]}, model {pts[1][1]}, Pinnacle {pts[2][1]} ({unit})">',
           f'<line x1="{X0}" y1="38" x2="{X1}" y2="38" class="ax"/>',
           f'<rect x="{min(fx,mx):.1f}" y="33" width="{abs(mx-fx):.1f}" height="10" rx="2" class="gapbar"/>']
    # ticks
    step = 1 if hi - lo <= 12 else 2 if hi - lo <= 24 else 5 if hi - lo <= 60 else 20
    t = math.ceil(lo / step) * step
    while t <= hi:
        out.append(f'<line x1="{x(t):.1f}" y1="35" x2="{x(t):.1f}" y2="41" class="tk"/>')
        t += step
    # markers: FanDuel above, model below, Pinnacle diamond (label above-right row 2)
    lab = lambda v: (am(v) if b["market"] == "Moneyline" else (f"{v:g}" if b["market"] in ("Total", "Prop") else num(v)))
    out.append(f'<circle cx="{fx:.1f}" cy="38" r="6.5" class="m-fd"/><text x="{fx:.1f}" y="18" class="l-fd" text-anchor="middle">FD {lab(pts[0][1])}</text>')
    out.append(f'<circle cx="{mx:.1f}" cy="38" r="6.5" class="m-md"/><text x="{mx:.1f}" y="66" class="l-md" text-anchor="middle">{"Fair" if b["market"]=="Moneyline" else "Proj" if b["market"]=="Prop" else "Model"} {lab(pts[1][1])}</text>')
    if b["market"] == "Prop":
        out.append("</svg>"); return "".join(out)
    px = x(pts[2][1])
    out.append(f'<path d="M{px:.1f} 31 l5 7 l-5 7 l-5 -7z" class="m-pn"/>')
    # Pinnacle label: put on whichever row side is less crowded
    py, anchor = (66, "middle") if abs(px - fx) < abs(px - mx) else (18, "middle")
    if abs(px - (mx if py == 66 else fx)) < 70: anchor = "start" if px > (mx if py == 66 else fx) else "end"; px += 9 if anchor == "start" else -9
    out.append(f'<text x="{px:.1f}" y="{py}" class="l-pn" text-anchor="{anchor}">PIN {lab(pts[2][1])}</text>')
    out.append("</svg>")
    return "".join(out)

STATUS = {"pending": ("Pending", "pend"), "won": ("Won", "won"), "lost": ("Lost", "lost"), "push": ("Push", "push")}

def ticket(b, i):
    st, cls = STATUS[b["status"]]
    c = clv(b)
    clv_txt = "—" if c is None else (num(c) + " pts")
    res = ""
    if b["status"] != "pending":
        res = f'<span class="res {cls}">{num(round(b["result_units"],2))}u</span>'
    score = f'<div class="score">Final: {esc(b["score"])}</div>' if b.get("score") else ""
    stake = f'{b["stake"]:g}u' if b["stake"] else "Track"
    fdmove = ""
    if b.get("fd_latest") is not None and b["fd_latest"] != b["line"]:
        fdmove = f'<li><span>FD now</span><b>{num(b["fd_latest"]) if b["market"]=="Spread" else b["fd_latest"]}</b></li>'
    when = f'{day_et(b["start"])} · {t_et(b["start"])}' if b["date"] != SLATE_DATE else t_et(b["start"])
    pick = b["pick"]
    if b["market"] == "Spread":
        team, ln = pick.rsplit(" ", 1)
        pick_html = f'{esc(team)} <em>{esc(ln.replace("-", "−"))}</em>'
    elif b["market"] == "Total":
        side, ln = pick.split(" ")
        pick_html = f'{esc(side)} <em>{esc(ln)}</em>'
    elif b["market"] == "Prop":
        pick_html = f'{esc(b["player"])} <em>{esc(b["side"][0])} {b["line"]:g}</em> <span class="stat">{esc(b["stat"].upper())}</span>'
    else:
        pick_html = f'{esc(pick.replace(" ML",""))} <em>ML</em>'
    return f'''<article class="tix g-{b["grade"]} s-{cls}" data-sport="{esc(b["sport"])}" style="--i:{i}">
  <div class="stub"><span class="gl">{b["grade"] if len(b["grade"])==1 else "$"}</span><span class="gk">{"Grade" if len(b["grade"])==1 else "Price"}</span></div>
  <div class="body">
    <div class="meta"><span>{esc(b["sport"])}</span>{('<span class="note">' + esc(b["engine"]) + '</span>') if b.get("engine") in ("Price edge", "Team model") else ""}<span>{when} ET</span><span>{esc(b["market"])}</span><span class="pill {cls}">{st}</span>{res}</div>
    <h3 class="pick">{pick_html}<span class="px">{am(b["price"])}</span></h3>
    <div class="game">{esc(b["away"])} <i>at</i> {esc(b["home"])}{(" · " + esc(b["projection"])) if b.get("projection") else ""}</div>
    {score}
    {numline(b)}
    <p class="why">{esc(b["why"].replace("Model: ", "Projection: ").split(" (")[0] if b["engine"]=="Projection" else b["why"])}</p>
    {('<p class="sigs">' + " · ".join(esc(s) for s in b.get("signals") or []) + '</p>') if b.get("signals") else ""}
    {('<p class="inj">Injuries: ' + esc(b["injuries"]) + '</p>') if b.get("injuries") else ""}
    <ul class="facts">
      <li><span>Stake</span><b>{stake}</b></li>
      <li><span>EV</span><b>{num(b["ev_pct"])}%</b></li>
      {"<li><span>Model gap</span><b>"+f'{b["gap"]:g} pts'+"</b></li>" if b.get("gap") is not None else ""}
      <li><span>Sharp move</span><b class="sig {b["market_signal"].split()[0]}">{esc(b["market_signal"])}</b></li>
      <li><span>CLV</span><b>{clv_txt}</b></li>
      {fdmove}
    </ul>
  </div>
</article>'''

today = [b for b in BETS if b["date"] == SLATE_DATE and b["stake"] > 0]
ORDER = {"A": 0, "B": 1, "Price": 2}
today.sort(key=lambda b: (ORDER.get(b["grade"], 3), b["start"], -(b["ev_pct"] or 0)))
later = [b for b in BETS if b["date"] > SLATE_DATE]
track_today = sorted([b for b in BETS if b["date"] == SLATE_DATE and b["stake"] == 0], key=lambda b: -(b["ev_pct"] or 0))[:6]


def parlay_card(p):
    st, cls = STATUS[p["status"]]
    legs = []
    for l in p["legs"]:
        lst, lcls = STATUS[l["status"]]
        legs.append(f'<li><span class="lg">{esc(l["pick"])}<small>{esc(l["away"])} at {esc(l["home"])} · {esc(l["note"])}</small></span><span class="mut">{am(l["price"])} · {t_et(l["start"])}</span><span class="pill {lcls}">{lst}</span></li>')
    res = f'<span class="res {cls}">{num(round(p["result_units"],2))}u</span>' if p["status"] != "pending" else ""
    return f'<div class="parlay s-{cls}"><div class="ph"><div><span class="kk">{len(p["legs"])}-leg lotto · {p["stake"]:g}u</span><b>{am(p["price"])}</b></div><div class="pr"><span class="pill {cls}">{st}</span>{res}<span class="mut sm">Pays {p["stake"]*(p["price"]/100):.2f}u · not counted in record</span></div></div><ul>{"".join(legs)}</ul></div>'

# ---------- units chart ----------
def chart():
    seq = sorted(graded, key=lambda b: b["start"])
    if not seq:
        return '<div class="chart-empty"><b>No graded plays yet</b><span>Running unit total appears after tonight\'s games are graded.</span></div>'
    run, pts = 0.0, [0.0]
    for b in seq: run += b["result_units"]; pts.append(run)
    W_, H_, pl, pr, pt, pb = 640, 200, 40, 16, 16, 28
    lo, hi = min(pts + [-1]), max(pts + [1])
    X = lambda i: pl + i / max(1, len(pts) - 1) * (W_ - pl - pr)
    Y = lambda v: pt + (hi - v) / (hi - lo) * (H_ - pt - pb)
    path = " ".join(f"{'M' if i==0 else 'L'}{X(i):.1f},{Y(v):.1f}" for i, v in enumerate(pts))
    area = path + f" L{X(len(pts)-1):.1f},{Y(0):.1f} L{X(0):.1f},{Y(0):.1f} Z"
    grid = "".join(f'<line x1="{pl}" x2="{W_-pr}" y1="{Y(v):.1f}" y2="{Y(v):.1f}" class="gr"/><text x="{pl-6}" y="{Y(v)+4:.1f}" text-anchor="end" class="gt">{num(v)}u</text>'
                   for v in sorted(set([math.floor(lo), 0, math.ceil(hi)])))
    end = pts[-1]
    return f'''<svg class="units" viewBox="0 0 {W_} {H_}" role="img" aria-label="Running units: {num(round(end,2))}u after {len(seq)} plays">
{grid}<path d="{area}" class="ar {'up' if end>=0 else 'dn'}"/><path d="{path}" class="ln {'up' if end>=0 else 'dn'}"/>
<circle cx="{X(len(pts)-1):.1f}" cy="{Y(end):.1f}" r="5" class="dot {'up' if end>=0 else 'dn'}"/>
<text x="{X(len(pts)-1)-8:.1f}" y="{Y(end)-10:.1f}" text-anchor="end" class="endl">{num(round(end,2))}u</text>
<text x="{pl}" y="{H_-8}" class="gt">Play 1</text><text x="{W_-pr}" y="{H_-8}" text-anchor="end" class="gt">Play {len(seq)}</text></svg>'''

def log_rows():
    rows = sorted([b for b in BETS if b["status"] != "pending"], key=lambda b: b["start"], reverse=True)
    if not rows: return '<tr><td colspan="8" class="empty">No graded plays yet.</td></tr>'
    out = []
    for b in rows:
        st, cls = STATUS[b["status"]]
        c = clv(b)
        out.append(f'<tr><td>{day_et(b["start"])}</td><td><span class="gchip g-{b["grade"]}">{b["grade"] if len(b["grade"])==1 else "$"}</span></td><td>{esc(b["pick"])} <span class="mut">{am(b["price"])}</span><div class="mut sm">{esc(b["game"])}</div></td>'
                   f'<td>{esc(b.get("score") or "")}</td><td class="n">{b["stake"]:g}u</td><td class="n">{"—" if c is None else num(c)}</td>'
                   f'<td><span class="pill {cls}">{st}</span></td><td class="n {cls}">{num(round(b["result_units"],2))}u</td></tr>')
    return "".join(out)

gA, gB = by_grade("A"), by_grade("B")
rec = f"{W}–{Lo}" + (f"–{P}" if P else "")
hero_units = num(round(units, 2)) + "u" if graded else "0.00u"
risk_tonight = sum(b["stake"] for b in today)
first_tip = min(b["start"] for b in today) if today else None

CSS = r"""
/* layout: masthead + tote-board record strip, then ticket-stub cards on a 2-col grid, ledger + method below */
:root{
  --paper:#e9eef1; --card:#f8fafb; --ink:#0c1a26; --mute:#56677a; --rule:#c4ced6;
  --sig:#ff4f1a; --won:#0f7a52; --lost:#c22c3a; --push:#7a6a12;
  --display:"Big Shoulders Display","Oswald","Arial Narrow",sans-serif;
  --body:"Instrument Sans","Helvetica Neue",Arial,sans-serif;
  --mono:"Martian Mono","SFMono-Regular",Menlo,monospace;
  color-scheme:light;
}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){
  --paper:#08121b; --card:#0f1d29; --ink:#e7eef4; --mute:#8fa1b3; --rule:#22364a;
  --sig:#ff6a3d; --won:#3cc48d; --lost:#ff6b77; --push:#e0c860; color-scheme:dark}}
:root[data-theme="dark"]{
  --paper:#08121b; --card:#0f1d29; --ink:#e7eef4; --mute:#8fa1b3; --rule:#22364a;
  --sig:#ff6a3d; --won:#3cc48d; --lost:#ff6b77; --push:#e0c860; color-scheme:dark}
*{box-sizing:border-box}
html{background:var(--paper)}
body{margin:0;background:var(--paper);color:var(--ink);font:15px/1.5 var(--body);-webkit-font-smoothing:antialiased}
.wrap{max-width:1160px;margin:0 auto;padding:0 20px 72px}
a{color:inherit}
:focus-visible{outline:2px solid var(--sig);outline-offset:2px}

/* masthead */
.mast{display:grid;grid-template-columns:1fr auto;gap:6px 24px;align-items:end;padding:32px 0 20px;border-bottom:1px solid var(--rule)}
.kick{font:600 11px/1 var(--mono);letter-spacing:.12em;text-transform:uppercase;color:var(--mute);display:flex;gap:14px;flex-wrap:wrap}
.kick b{color:var(--sig);font-weight:600}
h1{font:800 clamp(44px,6.5vw,72px)/.9 var(--display);letter-spacing:.005em;text-transform:uppercase;margin:12px 0 0;text-wrap:balance}
h1 span{color:var(--sig)}
.mast .dek{grid-column:1/-1;max-width:70ch;color:var(--mute);margin:8px 0 0;font-size:14.5px}
.stamp{font:600 11px/1.5 var(--mono);text-transform:uppercase;letter-spacing:.08em;text-align:right;color:var(--mute)}
.stamp b{display:block;color:var(--ink);font-size:13px}

/* tote board */
.tote{display:grid;grid-template-columns:repeat(5,1fr);border-bottom:1px solid var(--rule);margin-bottom:8px}
.tote div{padding:16px 16px 14px;border-right:1px solid var(--rule)}
.tote div:last-child{border-right:0}
.tote span{display:block;font:600 10.5px/1 var(--mono);letter-spacing:.1em;text-transform:uppercase;color:var(--mute)}
.tote b{display:block;font:700 clamp(28px,3.6vw,38px)/1 var(--display);margin-top:8px;font-variant-numeric:tabular-nums}
.tote small{display:block;color:var(--mute);font-size:12px;margin-top:4px}
.tote .pos{color:var(--won)} .tote .neg{color:var(--lost)}

/* section heads */
.sh{display:flex;align-items:baseline;justify-content:space-between;gap:16px;margin:36px 0 12px;flex-wrap:wrap}
.sh h2{font:700 22px/1 var(--display);text-transform:uppercase;letter-spacing:.03em;margin:0}
.sh p{margin:0;color:var(--mute);font-size:13.5px;max-width:60ch}
.sh .count{font:600 12px var(--mono);color:var(--sig)}

/* tickets */
.grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px}
.tix{display:grid;grid-template-columns:56px 1fr;background:var(--card);border:1px solid var(--rule);border-radius:8px;position:relative;
  animation:deal .5s cubic-bezier(.2,.8,.2,1) both;animation-delay:calc(var(--i) * 45ms)}
@keyframes deal{from{transform:translateY(6px)}to{transform:none}}
@media (prefers-reduced-motion:reduce){.tix{animation:none}}
.stub{border-right:1px dashed var(--rule);display:flex;flex-direction:column;align-items:center;justify-content:flex-start;padding:16px 0;gap:4px;position:relative}
.gl{font:800 34px/1 var(--display)}
.gk{font:600 9px/1 var(--mono);letter-spacing:.14em;text-transform:uppercase;color:var(--mute)}
.g-A .gl{color:var(--sig)}
.g-B .gl{color:var(--ink)}
.g-Price .gl{color:var(--sig)}
.body{padding:14px 16px 14px;min-width:0}
.meta{display:flex;flex-wrap:wrap;gap:6px 12px;align-items:center;font:600 10.5px/1 var(--mono);letter-spacing:.08em;text-transform:uppercase;color:var(--mute)}
.pill{font:600 9.5px/1 var(--mono);letter-spacing:.1em;text-transform:uppercase;padding:4px 7px;border-radius:999px;border:1px solid currentColor}
.pill.pend{color:var(--mute)} .pill.won{color:var(--won)} .pill.lost{color:var(--lost)} .pill.push{color:var(--push)}
.res{font:700 12px var(--mono)} .res.won{color:var(--won)} .res.lost{color:var(--lost)}
.pick{font:700 clamp(24px,2.8vw,30px)/1.05 var(--display);text-transform:uppercase;letter-spacing:.01em;margin:10px 0 2px;display:flex;align-items:baseline;gap:10px;flex-wrap:wrap;text-wrap:balance}
.pick em{font-style:normal;color:var(--sig)}
.px{font:600 13px var(--mono);color:var(--mute);align-self:center}
.game{color:var(--mute);font-size:13.5px}
.game i{font-style:normal;opacity:.7}
.score{font:600 12px var(--mono);margin-top:4px}
.s-won{border-color:color-mix(in srgb,var(--won) 55%,var(--rule))}
.s-lost{opacity:.82}
.nl{width:100%;height:auto;margin:10px 0 4px;display:block;overflow:visible}
.nl .ax{stroke:var(--rule);stroke-width:2}
.nl .tk{stroke:var(--rule);stroke-width:1.5}
.nl .gapbar{fill:var(--sig);opacity:.18}
.nl .m-fd{fill:var(--sig)}
.nl .m-md{fill:var(--card);stroke:var(--ink);stroke-width:2.5}
.nl .m-pn{fill:var(--mute)}
.nl text{font:600 11px var(--mono)}
.nl .l-fd{fill:var(--sig)} .nl .l-md{fill:var(--ink)} .nl .l-pn{fill:var(--mute);font-size:10px}
.why{margin:4px 0 4px;font-size:13px;color:var(--ink);max-width:62ch;font-family:var(--mono);font-size:11.5px}
.sigs,.inj{margin:0 0 4px;font-size:12.5px;color:var(--mute);max-width:70ch}
.sigs{color:var(--won)}
.pick .stat{font:600 12px var(--mono);color:var(--mute);align-self:center}
.facts{margin-top:8px}
.facts{list-style:none;margin:0;padding:10px 0 0;border-top:1px solid var(--rule);display:flex;flex-wrap:wrap;gap:8px 18px}
.facts li{display:flex;flex-direction:column;gap:3px}
.facts span{font:600 9.5px/1 var(--mono);letter-spacing:.1em;text-transform:uppercase;color:var(--mute)}
.facts b{font:600 13px var(--mono);font-variant-numeric:tabular-nums}
.sig.with{color:var(--won)} .sig.against{color:var(--lost)} .sig.neutral{color:var(--mute)}

/* tracking strip */
.track{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:0;border:1px solid var(--rule);border-radius:8px;background:var(--card)}
.track div{padding:12px 14px;border-right:1px solid var(--rule)}
.track div:last-child{border-right:0}
.track b{font:700 17px/1.2 var(--display);text-transform:uppercase;letter-spacing:.02em;display:block}
.track span{font:600 11px var(--mono);color:var(--mute)}

/* chart + ledger */
.two{display:grid;grid-template-columns:1.1fr .9fr;gap:16px}
.panel{background:var(--card);border:1px solid var(--rule);border-radius:8px;padding:16px}
.panel h4{font:600 10.5px/1 var(--mono);letter-spacing:.1em;text-transform:uppercase;color:var(--mute);margin:0 0 12px}
.units{width:100%;height:auto;display:block}
.units .gr{stroke:var(--rule)} .units .gt{font:600 10px var(--mono);fill:var(--mute)}
.units .ln{fill:none;stroke-width:2.5} .units .ln.up{stroke:var(--won)} .units .ln.dn{stroke:var(--lost)}
.units .ar.up{fill:var(--won);opacity:.12} .units .ar.dn{fill:var(--lost);opacity:.12}
.units .dot.up{fill:var(--won)} .units .dot.dn{fill:var(--lost)} .units .endl{font:700 12px var(--mono);fill:var(--ink)}
.chart-empty{min-height:180px;display:flex;flex-direction:column;justify-content:center;gap:6px;border:1px dashed var(--rule);border-radius:3px;padding:20px;
  background:repeating-linear-gradient(90deg,transparent 0 39px,color-mix(in srgb,var(--rule) 45%,transparent) 39px 40px)}
.chart-empty b{font:700 18px/1.1 var(--display);text-transform:uppercase;letter-spacing:.03em}
.chart-empty span{color:var(--mute);font-size:13.5px;max-width:44ch}
.split{width:100%;border-collapse:collapse;font-variant-numeric:tabular-nums}
.split th,.split td{padding:9px 6px;border-bottom:1px solid var(--rule);text-align:left;font-size:13.5px}
.split th{font:600 10px var(--mono);letter-spacing:.1em;text-transform:uppercase;color:var(--mute)}
.split td.n,.split th.n{text-align:right;font-family:var(--mono);font-size:12.5px}
.logwrap{overflow-x:auto;background:var(--card);border:1px solid var(--rule);border-radius:8px}
.log{width:100%;border-collapse:collapse;min-width:760px;font-variant-numeric:tabular-nums}
.log th{font:600 10px var(--mono);letter-spacing:.1em;text-transform:uppercase;color:var(--mute);text-align:left;padding:10px 12px;border-bottom:1px solid var(--rule)}
.log td{padding:10px 12px;border-bottom:1px solid var(--rule);font-size:13.5px;vertical-align:top}
.log td.n{font-family:var(--mono);font-size:12.5px;text-align:right}
.log td.won{color:var(--won)} .log td.lost{color:var(--lost)}
.log .empty{color:var(--mute);text-align:center;padding:26px}
.mut{color:var(--mute)} .sm{font-size:12px}
.gchip{font:800 16px/1 var(--display);display:inline-block;min-width:22px;text-align:center}
.gchip.g-A{color:var(--sig)}


.parlay{background:var(--card);border:1px solid var(--rule);border-radius:8px;padding:16px}
.ph{display:flex;justify-content:space-between;align-items:flex-end;gap:16px;flex-wrap:wrap;padding-bottom:12px;border-bottom:1px solid var(--rule)}
.ph .kk{display:block;font:600 10.5px/1 var(--mono);letter-spacing:.1em;text-transform:uppercase;color:var(--mute)}
.ph b{display:block;font:700 34px/1 var(--display);color:var(--sig);margin-top:6px}
.ph .pr{display:flex;gap:10px;align-items:center;flex-wrap:wrap}
.parlay ul{list-style:none;margin:0;padding:0}
.parlay li{display:grid;grid-template-columns:1fr auto auto;gap:14px;align-items:center;padding:10px 0;border-bottom:1px solid var(--rule)}
.parlay li:last-child{border-bottom:0;padding-bottom:0}
.parlay .lg{font:700 16px/1.2 var(--display);text-transform:uppercase;letter-spacing:.02em}
.parlay .lg small{display:block;font:500 12px/1.4 var(--body);text-transform:none;letter-spacing:0;color:var(--mute);margin-top:2px}
.parlay .mut{font:600 11.5px var(--mono)}

.chips{display:flex;flex-wrap:wrap;gap:6px;margin:0 0 12px}
.chip{font:600 11px/1 var(--mono);letter-spacing:.06em;text-transform:uppercase;padding:7px 11px;border-radius:999px;border:1px solid var(--rule);background:var(--card);color:var(--ink);cursor:pointer}
.chip.on{background:var(--ink);color:var(--paper);border-color:var(--ink)}
.note{font:600 10px var(--mono);letter-spacing:.06em;text-transform:uppercase;color:var(--push)}
/* method */
.method{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:16px}
.method section{border-top:1px solid var(--rule);padding-top:14px}
.method h3{font:700 16px/1.1 var(--display);text-transform:uppercase;letter-spacing:.04em;margin:0 0 8px}
.method p,.method li{font-size:13.5px;color:var(--mute);max-width:62ch;margin:0 0 8px}
.method ul{padding-left:18px;margin:6px 0}
.formula{font:500 12px/1.7 var(--mono);background:var(--card);border:1px solid var(--rule);border-left:3px solid var(--sig);padding:10px 12px;border-radius:2px;margin:8px 0;overflow-x:auto;white-space:nowrap}
.scale{display:flex;flex-direction:column;gap:8px;margin-top:6px}
.scale div{display:grid;grid-template-columns:24px 1fr;gap:10px;align-items:baseline;font-size:13.5px;color:var(--mute)}
.scale b{font:700 20px/1 var(--display)}
.scale div:first-child b{color:var(--sig)}
.foot{margin-top:40px;padding-top:16px;border-top:1px solid var(--rule);display:flex;justify-content:space-between;gap:16px;flex-wrap:wrap;color:var(--mute);font-size:12.5px}
.foot b{color:var(--ink)}

@media (max-width:860px){
  .tote{grid-template-columns:repeat(2,1fr)} .tote div:nth-child(2){border-right:0} .tote div:nth-child(-n+4){border-bottom:1px solid var(--rule)}
  .tote div:last-child{grid-column:1/-1}
  .grid,.two,.method{grid-template-columns:1fr}
  .track{grid-template-columns:1fr} .track div{border-right:0;border-bottom:1px solid var(--rule)} .track div:last-child{border-bottom:0}
  .mast{grid-template-columns:1fr} .stamp{text-align:left}
}
@media (max-width:420px){ .tix{grid-template-columns:48px 1fr} .gl{font-size:36px} }
"""

page = f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>The Edge Sheet</title>
<meta name="robots" content="noindex,nofollow">
<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Big+Shoulders+Display:wght@700;800;900&family=Instrument+Sans:wght@400;500;600&family=Martian+Mono:wght@500;600;700&display=swap" rel="stylesheet">
<style>{CSS}</style></head>
<body><div class="wrap">

<header class="mast">
  <div>
    <div class="kick"><b>{esc(SLATE_LABEL)}</b><span>NBA · NHL · NFL · NCAAF · NCAAM</span></div>
    <h1>The Edge <span>Sheet</span></h1>
  </div>
  <div class="stamp">Updated<b>{esc(UPDATED)}</b></div>
  <p class="dek">Model projections compared with FanDuel (Ohio) lines. Every play is logged before tip and graded on result and closing-line value.</p>
</header>

<div class="tote" aria-label="Season record">
  <div><span>Record</span><b>{rec}</b><small>{len(pending)} pending</small></div>
  <div><span>Units</span><b class="{'pos' if units>0 else 'neg' if units<0 else ''}">{hero_units}</b><small>1u = 1% bankroll</small></div>
  <div><span>ROI</span><b>{'—' if roi is None else num(round(roi,1))+'%'}</b><small>{risked:g}u settled</small></div>
  <div><span>Avg CLV</span><b class="{'pos' if (avg_clv or 0)>0 else 'neg' if (avg_clv or 0)<0 else ''}">{'—' if avg_clv is None else num(round(avg_clv,2))}</b><small>pts vs. Pinnacle · {beat} better, {held} even, {len(clvs)-beat-held} worse</small></div>
  <div><span>{"Today" if IS_TODAY else "Last slate"}</span><b>{len(today)} plays</b><small>{risk_tonight:g}u · first tip {t_et(first_tip) if first_tip else '—'} ET</small></div>
</div>

<div class="sh"><h2>{esc(SLATE_LABEL)} card</h2><p>FanDuel lines as of {esc(UPDATED)}. {esc(L.get("run_note",""))}</p></div>
<div class="chips" role="group" aria-label="Filter by sport">{''.join(f'<button type="button" class="chip{" on" if s_=="All" else ""}" data-s="{s_}">{s_}</button>' for s_ in ["All"] + sorted({b["sport"] for b in today + later}))}</div>
<div class="grid">
{''.join(ticket(b, i) for i, b in enumerate(today))}
</div>

{''.join('<div class="sh"><h2>For fun</h2><p>Light model leans from games not on the main card. Long odds, small stake.</p></div>' + parlay_card(p) for p in PARLAYS if p["date"] == SLATE_DATE)}

{('<div class="sh"><h2>Held</h2><p>Model likes these, but the line is far from the player\'s average. Check news before betting.</p></div><div class="track">' + ''.join(f'<div><b>{esc(h["player"])} {esc(h["side"])} {h["line"]:g} {esc(h["stat"].upper())}</b><span>{esc(h["reason"])}</span></div>' for h in L.get("holds", [])) + '</div>') if L.get("holds") and IS_TODAY else ''}

<div class="sh"><h2>Tracking</h2><p>Grade C. Graded for the record, no stake.</p></div>
<div class="track">
{''.join(f'<div><b>{esc(b["pick"])} {am(b["price"])}</b><span>{esc(b["away"])} at {esc(b["home"])} · {t_et(b["start"])} · CLV {"—" if clv(b) is None else num(clv(b))}{(" · FD now " + num(b["fd_latest"])) if b.get("fd_latest") not in (None, b["line"]) else ""}</span></div>' for b in track_today)}
</div>

{'<div class="sh"><h2>Upcoming</h2><p>Price edges: FanDuel is paying above the no-vig fair price.</p></div><div class="grid">' + ''.join(ticket(b, i) for i, b in enumerate(later)) + '</div>' if later else ''}

<div class="sh"><h2>Performance</h2><p>Closing-line value is the leading indicator; units take hundreds of plays to mean much.</p></div>
<div class="two">
  <div class="panel"><h4>Running units</h4>{chart()}</div>
  <div class="panel"><h4>By grade</h4>
    <table class="split"><thead><tr><th>Grade</th><th class="n">Plays</th><th class="n">W–L</th><th class="n">Units</th></tr></thead><tbody>
      <tr><td><span class="gchip g-A">A</span> 1u</td><td class="n">{gA[3]}</td><td class="n">{gA[0]}–{gA[1]}</td><td class="n">{num(round(gA[2],2)) if gA[0]+gA[1] else '—'}</td></tr>
      <tr><td><span class="gchip">B</span> 0.5u</td><td class="n">{gB[3]}</td><td class="n">{gB[0]}–{gB[1]}</td><td class="n">{num(round(gB[2],2)) if gB[0]+gB[1] else '—'}</td></tr>
      <tr><td><span class="gchip">C</span> tracked</td><td class="n">{len(track)}</td><td class="n">{tw}–{tl}</td><td class="n">—</td></tr>
    </tbody></table>
  </div>
</div>

<div class="sh"><h2>Ledger</h2><p>All graded plays, newest first.</p></div>
<div class="logwrap"><table class="log"><thead><tr><th>Date</th><th>Gr</th><th>Play</th><th>Final</th><th>Stake</th><th>CLV</th><th>Result</th><th>Units</th></tr></thead><tbody>{log_rows()}</tbody></table></div>

<div class="sh"><h2>Method</h2></div>
<div class="method">
  <section><h3>Projection</h3>
    <p>Ratings blend this season with last, weighted by games played. Each factor is converted to points.</p>
    <div class="formula">poss = pace<sub>H</sub> × pace<sub>A</sub> ÷ lg pace<br>pts = poss × (ORtg + opp DRtg − lg) ÷ 100<br>injuries = (BPM+2) × MPG/48 per player out<br>rest: B2B −1.5 · 3-in-4 −0.8 · travel −0.5<br>home +2.0 · altitude +1.0</div>
    <p>Props: minutes × per-minute rate × usage bump from teammates out × pace × opponent defense.</p></section>
  <section><h3>Market check</h3>
    <p>Fair line = 35% model + 65% Pinnacle (40/60 for props). EV is priced at FanDuel.</p>
    <p>Line movement from open, reverse line movement and money-vs-ticket splits confirm or downgrade a play. They never create one.</p></section>
  <section><h3>Grading</h3>
    <div class="scale">
      <div><b>A</b><span>Spread gap ≥ 3, total ≥ 4.5, EV ≥ 2.5%, market confirms · 1u</span></div>
      <div><b>B</b><span>Spread gap ≥ 2, total ≥ 3, EV ≥ 1.5%, market not against · 0.5u</span></div>
      <div><b>C</b><span>Smaller leans · tracked only. Props: A 0.5u, B 0.25u</span></div>
      <div><b>$</b><span>Price edge vs. no-vig fair line · ¼ Kelly</span></div>
    </div></section>
</div>

<footer class="foot">
  <span>For informational purposes only. Not financial advice. 21+ where legal.</span>
  <span>Problem gambling help: <b>1-800-GAMBLER</b></span>
</footer>
</div>
<script>
document.querySelectorAll('.chips').forEach(function(g){{g.addEventListener('click',function(e){{var b=e.target.closest('.chip');if(!b)return;
g.querySelectorAll('.chip').forEach(function(x){{x.classList.toggle('on',x===b)}});var s=b.dataset.s;
document.querySelectorAll('.tix').forEach(function(t){{t.style.display=(s==='All'||t.dataset.sport===s)?'':'none'}});}});}});
</script></body></html>"""
open(os.path.join(ROOT, "index.html"), "w").write(page)
print("ok", len(page), "bytes;", len(today), "plays,", len(track_today), "tracked,", len(later), "later; CLV", clvs)
