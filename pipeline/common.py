"""Shared helpers: HTTP, time, odds math, name normalization."""
import json, math, os, re, time, unicodedata, urllib.request, urllib.parse
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
ET = timezone(timedelta(hours=-5))          # EST (Nov–Mar). Revisit in March for EDT.
UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"}
LOG = []

def log(*a):
    s = " ".join(str(x) for x in a)
    print(s, flush=True); LOG.append(s)

def get(url, tries=3, headers=None):
    h = dict(UA); h.update(headers or {})
    for i in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=h), timeout=40) as r:
                return r.read().decode("utf-8", "replace"), dict(r.headers)
        except Exception as e:
            if i == tries - 1: raise
            time.sleep(3 * (i + 1))

def get_json(url, **kw):
    body, hdr = get(url, **kw)
    return json.loads(body), hdr

def now_et(): return datetime.now(timezone.utc).astimezone(ET)
def to_et(iso): return datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(ET)

def load(path, default):
    p = os.path.join(ROOT, path)
    return json.load(open(p)) if os.path.exists(p) else default
def save(path, obj):
    p = os.path.join(ROOT, path); os.makedirs(os.path.dirname(p), exist_ok=True)
    json.dump(obj, open(p, "w"), indent=1, default=str)

# ---------- odds math ----------
def am_to_dec(a): return 1 + (a / 100 if a > 0 else 100 / -a)
def dec_to_am(d): return round((d - 1) * 100) if d >= 2 else round(-100 / (d - 1))
def prob_to_am(p): return dec_to_am(1 / max(1e-6, min(1 - 1e-6, p)))
def devig2(a, b):
    """Power-method no-vig probabilities for a two-way market (american odds)."""
    ia, ib = 1 / am_to_dec(a), 1 / am_to_dec(b)
    lo, hi = 0.5, 3.0
    for _ in range(100):
        k = (lo + hi) / 2; s = ia ** k + ib ** k
        if s > 1: lo = k
        else: hi = k
    return ia ** k, ib ** k
Phi = lambda x: 0.5 * (1 + math.erf(x / math.sqrt(2)))

# ---------- names ----------
TEAM_ALIASES = {"LA Clippers": "Los Angeles Clippers", "LA Lakers": "Los Angeles Lakers"}
def team(name):
    name = re.sub(r"\*$", "", (name or "").strip())
    return TEAM_ALIASES.get(name, name)
BBREF_ABBR = {"ATL":"Atlanta Hawks","BOS":"Boston Celtics","BRK":"Brooklyn Nets","CHO":"Charlotte Hornets","CHI":"Chicago Bulls",
 "CLE":"Cleveland Cavaliers","DAL":"Dallas Mavericks","DEN":"Denver Nuggets","DET":"Detroit Pistons","GSW":"Golden State Warriors",
 "HOU":"Houston Rockets","IND":"Indiana Pacers","LAC":"Los Angeles Clippers","LAL":"Los Angeles Lakers","MEM":"Memphis Grizzlies",
 "MIA":"Miami Heat","MIL":"Milwaukee Bucks","MIN":"Minnesota Timberwolves","NOP":"New Orleans Pelicans","NYK":"New York Knicks",
 "OKC":"Oklahoma City Thunder","ORL":"Orlando Magic","PHI":"Philadelphia 76ers","PHO":"Phoenix Suns","POR":"Portland Trail Blazers",
 "SAC":"Sacramento Kings","SAS":"San Antonio Spurs","TOR":"Toronto Raptors","UTA":"Utah Jazz","WAS":"Washington Wizards"}
def pname(n):
    n = unicodedata.normalize("NFKD", n or "").encode("ascii", "ignore").decode()
    n = re.sub(r"[^a-z ]", "", n.lower().replace("-", " "))
    n = re.sub(r"\b(jr|sr|ii|iii|iv)\b", "", n)
    return " ".join(n.split())
