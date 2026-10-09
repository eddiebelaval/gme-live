#!/usr/bin/env python3
"""Self-updating GME TA page. Writes site/index.html atomically."""
import json, math, os, subprocess, tempfile, datetime as dt
from zoneinfo import ZoneInfo
ET = ZoneInfo("America/New_York")
import os
BASE = os.environ.get("GME_BASE", "."); OUT = os.path.join(BASE, "site", "index.html")
F0, F100 = 17.79, 26.88; BREAK = 23.06; Z5 = (26.60, 27.70); STRIKE = 32.0
EXPIRY = dt.datetime(2026, 10, 30, 17, 0, tzinfo=ET)

def fetch(sym, interval, rng):
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}?interval={interval}&range={rng}"
    raw = subprocess.run(["curl", "-s", "--max-time", "20", "-A", "Mozilla/5.0", url], capture_output=True, text=True, check=True).stdout
    r = json.loads(raw)["chart"]["result"][0]; q = r["indicators"]["quote"][0]; bars = []
    for i, t in enumerate(r.get("timestamp") or []):
        o, h, l, c, v = (q[k][i] for k in ("open", "high", "low", "close", "volume"))
        if None in (o, h, l, c): continue
        bars.append(dict(t=dt.datetime.fromtimestamp(t, ET), o=o, h=h, l=l, c=c, v=v or 0))
    return r["meta"], bars

def sma(x, n): return sum(x[-n:]) / n if len(x) >= n else None
def rsi(c, n=14):
    g = [max(c[i]-c[i-1], 0) for i in range(1, len(c))]; L = [max(c[i-1]-c[i], 0) for i in range(1, len(c))]
    ag, al = sum(g[:n])/n, sum(L[:n])/n
    for i in range(n, len(g)): ag = (ag*(n-1)+g[i])/n; al = (al*(n-1)+L[i])/n
    return 100 if al == 0 else 100 - 100/(1+ag/al)
def dmi(b, n=14):
    tr, pdm, mdm = [], [], []
    for i in range(1, len(b)):
        h, l, pc = b[i]["h"], b[i]["l"], b[i-1]["c"]; up, dn = h-b[i-1]["h"], b[i-1]["l"]-l
        tr.append(max(h-l, abs(h-pc), abs(l-pc))); pdm.append(up if up > dn and up > 0 else 0); mdm.append(dn if dn > up and dn > 0 else 0)
    s = lambda a: [sum(a[:n])] + [0]*0
    T, P, M = sum(tr[:n]), sum(pdm[:n]), sum(mdm[:n]); dxs = []
    for i in range(n, len(tr)+1):
        if i > n: T = T - T/n + tr[i-1]; P = P - P/n + pdm[i-1]; M = M - M/n + mdm[i-1]
        pdi, mdi = 100*P/T, 100*M/T; dxs.append(100*abs(pdi-mdi)/(pdi+mdi) if pdi+mdi else 0)
    adx = sum(dxs[:n])/n
    for d in dxs[n:]: adx = (adx*(n-1)+d)/n
    return pdi, mdi, adx
def ncdf(x): return 0.5*(1+math.erf(x/math.sqrt(2)))
def bs_call(S, K, T, r, s):
    d1 = (math.log(S/K)+(r+s*s/2)*T)/(s*math.sqrt(T)); d2 = d1 - s*math.sqrt(T)
    return S*ncdf(d1) - K*math.exp(-r*T)*ncdf(d2), ncdf(d2)
def implied_vol(P, S, K, T, r=0.04):
    lo, hi = 0.01, 10.0
    for _ in range(100):
        m = (lo+hi)/2
        if bs_call(S, K, T, r, m)[0] > P: hi = m
        else: lo = m
    return m, bs_call(S, K, T, r, m)[1]

def candle_patterns(bars, label):
    out = []
    for i in range(len(bars)-3, len(bars)):
        if i < 1: continue
        b, p = bars[i], bars[i-1]; o, h, l, c = b["o"], b["h"], b["l"], b["c"]
        rng = h - l
        if rng <= 0: continue
        body = abs(c-o); up = h-max(o, c); lw = min(o, c)-l; f = []
        if c > o and p["c"] < p["o"] and c >= p["o"] and o <= p["c"]: f.append("Bullish engulfing")
        if c < o and p["c"] > p["o"] and o >= p["c"] and c <= p["o"]: f.append("Bearish engulfing")
        if body <= 0.1*rng: f.append("Doji")
        elif lw >= 2*body and up <= 0.3*rng: f.append("Hammer shape (long lower wick, small top wick)")
        elif up >= 2*body and lw <= 0.3*rng: f.append("Shooting star shape (long upper wick)")
        elif body <= 0.3*rng and up > body and lw > body: f.append("Spinning top")
        if body >= 0.9*rng: f.append("Marubozu (" + ("bullish" if c > o else "bearish") + ")")
        if lw >= 0.5*rng and "Hammer" not in " ".join(f): f.append("Long lower wick")
        if up >= 0.5*rng and "Shooting" not in " ".join(f): f.append("Long upper wick")
        ts = b["t"].strftime("%b %d") if label == "Daily" else b["t"].strftime("%H:%M")
        out.append((label, ts, f"O {o:.2f} H {h:.2f} L {l:.2f} C {c:.2f}", ", ".join(f) or "No pattern"))
    return out

def linfit(ys):
    n = len(ys); xm = (n-1)/2; ym = sum(ys)/n
    sxx = sum((i-xm)**2 for i in range(n)) or 1
    s = sum((i-xm)*(y-ym) for i, y in enumerate(ys))/sxx
    return s, ym - s*xm

def pennant(b):
    best = None
    for end in range(len(b)-3, 0, -1):          # pole end; need >=3 bars after it
        for st in range(max(0, end-10), end):
            g = b[end]["h"]/b[st]["l"] - 1
            if g >= 0.03 and b[end]["h"] == max(x["h"] for x in b[st:end+1]):
                if best is None or g > best[2]: best = (st, end, g)
        if best: break
    if not best: return None
    st, end, g = best; cons = b[end+1:]
    if len(cons) < 3: return None
    hs, _ = linfit([x["h"] for x in cons]); ls_, _ = linfit([x["l"] for x in cons])
    hs2, hi0 = linfit([b[end]["h"]] + [x["h"] for x in cons]); ls2, lo0 = linfit([x["l"] for x in cons])
    n = len(cons); upper = hi0 + hs2*n; lower = lo0 + ls2*(n-1)
    pole = b[end]["h"] - b[st]["l"]
    return dict(lo=b[st]["l"], hi=b[end]["h"], t0=b[st]["t"], t1=b[end]["t"], g=g, falling=hs < 0, rising=ls_ > 0,
                upper=upper, lower=lower, mm=upper+pole, bars=n, pole=pole)

def svg_candles(bars, w=1100, h=520, levels=(), zone=None, extra="", lines=(), xlabel="month", pivots=()):
    L, R, T, B = 60, 200, 20, 40
    lo = min([x["l"] for x in bars] + [v for v, *_ in levels]); hi = max([x["h"] for x in bars] + [v for v, *_ in levels])
    pad = (hi-lo)*0.04; lo -= pad; hi += pad
    Y = lambda p: T + (hi-p)/(hi-lo)*(h-T-B); n = len(bars); dx = (w-L-R)/max(n, 1); X = lambda i: L + dx*(i+0.5)
    s = [f'<svg viewBox="0 0 {w} {h}" width="100%" xmlns="http://www.w3.org/2000/svg" style="border:1px solid #eee;border-radius:6px">']
    if zone: s.append(f'<rect x="{L}" y="{Y(zone[1]):.1f}" width="{w-L-R}" height="{Y(zone[0])-Y(zone[1]):.1f}" fill="#fff3cd"/><text x="{L+6}" y="{Y(zone[1])+12:.1f}" font-size="11" fill="#8a6d00">{zone[2]}</text>')
    step = max(0.5, round((hi-lo)/8*2)/2)
    p = math.ceil(lo/step)*step
    while p < hi:
        s.append(f'<line x1="{L}" x2="{w-R}" y1="{Y(p):.1f}" y2="{Y(p):.1f}" stroke="#f2f2f2"/><text x="{L-6}" y="{Y(p)+4:.1f}" font-size="11" text-anchor="end" fill="#666">${p:g}</text>'); p += step
    for v, txt, col, dash in levels:
        s.append(f'<line x1="{L}" x2="{w-R}" y1="{Y(v):.1f}" y2="{Y(v):.1f}" stroke="{col}" stroke-dasharray="{dash}" opacity=".8"/><text x="{w-R+6}" y="{Y(v)+4:.1f}" font-size="11" fill="{col}">{txt}</text>')
    last = None; cw = max(1.5, dx*0.65)
    for i, x in enumerate(bars):
        col = "#2e7d32" if x["c"] >= x["o"] else "#c62828"
        s.append(f'<line x1="{X(i):.1f}" x2="{X(i):.1f}" y1="{Y(x["h"]):.1f}" y2="{Y(x["l"]):.1f}" stroke="{col}"/><rect x="{X(i)-cw/2:.1f}" y="{Y(max(x["o"],x["c"])):.1f}" width="{cw:.1f}" height="{max(1,abs(Y(x["o"])-Y(x["c"]))):.1f}" fill="{col}"/>')
        lab = x["t"].strftime("%b") if xlabel == "month" else (x["t"].strftime("%H:%M") if x["t"].minute == 0 else None)
        if lab and lab != last: s.append(f'<text x="{X(i):.1f}" y="{h-15}" font-size="11" text-anchor="middle" fill="#666">{lab}</text>'); last = lab
    for pts, col, name in lines:
        s.append(f'<polyline points="{" ".join(f"{X(i):.1f},{Y(v):.1f}" for i, v in pts)}" fill="none" stroke="{col}" stroke-width="2"/>')
        if pts: s.append(f'<text x="{X(pts[-1][0])+4:.1f}" y="{Y(pts[-1][1])+14:.1f}" font-size="11" fill="{col}">{name}</text>')
    if pivots:
        s.append(f'<polyline points="{" ".join(f"{X(i):.1f},{Y(v):.1f}" for i, v, _ in pivots)}" fill="none" stroke="#ef6c00" stroke-width="2"/>')
        for k, (i, v, lab) in enumerate(pivots):
            yy = Y(v) + (16 if k % 2 == 0 else -8)
            s.append(f'<circle cx="{X(i):.1f}" cy="{Y(v):.1f}" r="3" fill="#ef6c00"/><text x="{X(i):.1f}" y="{yy:.1f}" font-size="13" font-weight="700" text-anchor="middle" fill="#ef6c00">{lab}</text>')
    s.append(extra.replace("{XL}", f"{X(n-1):.1f}") if "{XL}" in extra else "")
    for v, txt in [(bars[-1]["c"], "Now"), ] : pass
    s.append("</svg>"); return "\n".join(s), Y, X

def trading_days_left(now):
    d = now.date(); n = 0
    if now.hour >= 16: d += dt.timedelta(days=1)
    while d <= EXPIRY.date():
        if d.weekday() < 5: n += 1
        d += dt.timedelta(days=1)
    return n

def build():
    now = dt.datetime.now(ET)
    meta, daily = fetch("GME", "1d", "2y")
    m15m, b15 = fetch("GME", "15m", "1d")
    m1m, b1 = fetch("GME", "1m", "1d")
    wmeta, _ = fetch("GME-WT", "1d", "5d")
    reg = meta["currentTradingPeriod"]["regular"]
    rs, re_ = dt.datetime.fromtimestamp(reg["start"], ET), dt.datetime.fromtimestamp(reg["end"], ET)
    price = meta["regularMarketPrice"]; dhigh = meta.get("regularMarketDayHigh", price); dlow = meta.get("regularMarketDayLow", price)
    dvol = meta.get("regularMarketVolume", 0); mtime = dt.datetime.fromtimestamp(meta["regularMarketTime"], ET)
    is_open = rs <= now <= re_
    closed = not is_open
    frac = 1.0 if closed else max(0.05, min(1, (now-rs).total_seconds()/(re_-rs).total_seconds()))
    # use today's live bar as last daily bar
    if daily and daily[-1]["t"].date() == mtime.date():
        daily[-1].update(c=price, h=max(daily[-1]["h"], dhigh), l=min(daily[-1]["l"], dlow), v=dvol)
    prior = daily[:-1] if daily[-1]["t"].date() == mtime.date() else daily
    avgv = sum(x["v"] for x in prior[-63:]) / len(prior[-63:])
    need = 1.3*avgv*frac; heavy = dvol >= need
    # gauge
    if closed and price < BREAK: g = 0
    elif price > F100 and heavy: g = 3
    elif price < 23.75 or (price >= 26.0 and not heavy): g = 1
    else: g = 2
    S = [["Invalid", "#c62828", "The close is below $23.06. The wave count is broken."],
         ["In Question", "#ef8f00", "Price is near a decision level. It is near $23.06, or it stalls near $26.88 without heavy volume."],
         ["Valid", "#2e7d32", "The structure is intact. Price holds between $23.06 and $26.88."],
         ["Strong", "#1565c0", "Price is above $26.88 on heavy volume. The breakout has volume support."]]
    # Elliott
    since = [x for x in daily if x["t"].date() > dt.date(2026, 9, 25)]
    w5 = max(since, key=lambda x: x["h"]) if since else daily[-1]
    if price < BREAK: ew = "Price is below $23.06. The wave count is broken."
    elif price > F100 and heavy: ew = "Price is above $26.88 on heavy volume. The alternate count gains weight. This can be wave 1 of a larger move."
    elif Z5[0] <= price <= Z5[1]: ew = "Price is in the $26.60 to $27.70 zone. Wave 5 is in its target zone."
    else: ew = "Wave 5 is in progress."
    # Fib
    rngf = F100 - F0
    fibs = [(r, F0 + rngf*r) for r in (0, .236, .382, .5, .618, .786, 1)] + [(1.272, 29.35), (1.618, 32.50)]
    # Daily chart
    dch = [x for x in daily if x["t"].date() >= dt.date(2026, 5, 1)]
    levels = [(v, f"{r*100:.1f}%  ${v:.2f}" if r <= 1 else f"Target {r*100:.1f}%  ${v:.2f}", "#1565c0" if r <= 1 else "#6a1b9a", "4 4" if r <= 1 else "6 3") for r, v in fibs]
    levels += [(BREAK, "Count break $23.06", "#c62828", ""), (price, f"Now ${price:.2f}", "#000", "2 2"), (dhigh, f"Day high ${dhigh:.2f}", "#555", "1 3")]
    def idx(d): return next((i for i, x in enumerate(dch) if x["t"].date() == d), None)
    piv = []
    for d, v, lab in [(dt.date(2026, 8, 20), 17.79, "(0)"), (dt.date(2026, 9, 11), 21.36, "(1)"), (dt.date(2026, 9, 15), 21.13, "(2)"),
                      (dt.date(2026, 9, 24), 25.25, "(3)"), (dt.date(2026, 9, 25), 23.06, "(4)"), (w5["t"].date(), w5["h"], "(5?)")]:
        i = idx(d)
        if i is not None: piv.append((i, v, lab))
    dsvg, _, _ = svg_candles(dch, levels=levels, zone=(*Z5, "Wave 5 target zone $26.60–$27.70"), pivots=piv)
    # 15m chart + VWAP
    cv = cpv = 0; vw15 = []
    for i, x in enumerate(b15):
        cv += x["v"]; cpv += x["v"]*(x["h"]+x["l"]+x["c"])/3; vw15.append((i, cpv/cv if cv else x["c"]))
    pen = pennant(b15) if len(b15) >= 6 else None
    lv15 = [(price, f"Now ${price:.2f}", "#000", "2 2"), (F100, "May high $26.88", "#1565c0", "4 4")]
    if pen: lv15 += [(pen["upper"], f"Break up ${pen['upper']:.2f}", "#2e7d32", "5 3"), (pen["lower"], f"Fail ${pen['lower']:.2f}", "#c62828", "5 3")]
    isvg = svg_candles(b15, h=420, levels=lv15, lines=[(vw15, "#7b1fa2", "VWAP")], xlabel="time")[0] if b15 else "<p>No 15-minute bars yet today.</p>"
    # Indicators
    closes = [x["c"] for x in daily]
    R = rsi(closes); pdi, mdi, adx = dmi(daily)
    sm = {n: sma(closes, n) for n in (9, 20, 50, 200)}
    s50p, s200p = sma(closes[:-1], 50), sma(closes[:-1], 200)
    cross = ("Golden cross today" if sm[50] > sm[200] and s50p <= s200p else "Death cross today" if sm[50] < sm[200] and s50p >= s200p
             else "50 above 200 (bullish)" if sm[50] > sm[200] else f"50 below 200. The gap is ${sm[200]-sm[50]:.2f}.")
    def vwap(bars, cap=None):
        v = pv = 0
        for x in bars:
            if cap and x["v"] > cap: continue
            v += x["v"]; pv += x["v"]*(x["h"]+x["l"]+x["c"])/3
        return pv/v if v else None
    big = [x for x in b1 if x["v"] > 3e6]
    vw_all, vw_clean = vwap(b1), vwap(b1, 3e6)
    rsi_read = "Overbought. A pause often comes above 70." if R > 70 else "Oversold. A bounce often comes below 30." if R < 30 else "Neutral zone."
    adx_read = ("Very strong trend. Above 50 is rare and often late in a move." if adx > 50 else "Trend is present." if adx > 25 else "No strong trend.")
    di_read = "Buyers control the trend." if pdi > mdi else "Sellers control the trend."
    above = [n for n in sm if price > sm[n]]
    comb = []
    comb.append(f"Price is above {len(above)} of 4 moving averages.")
    comb.append(di_read)
    if R > 70 and adx > 40: comb.append("RSI and ADX are stretched. This fits a late wave 5. Expect chop or a pullback before the next leg.")
    elif R < 50: comb.append("Momentum is weak. Watch $23.06.")
    else: comb.append("Momentum supports the trend.")
    if vw_all: comb.append(f"Price is {'above' if price > vw_all else 'below'} VWAP. Intraday {'buyers' if price > vw_all else 'sellers'} are in control.")
    # Warrants
    wp = wmeta["regularMarketPrice"]; wpc = wmeta.get("chartPreviousClose") or wp; wch = wp - wpc
    T = (EXPIRY - now).total_seconds()/86400/365
    try: iv, pabove = implied_vol(wp, price, STRIKE, T)
    except Exception: iv, pabove = float("nan"), float("nan")
    tdl = trading_days_left(now); be = STRIKE + wp
    itab = [(26.88, "May high / cup rim"), (27.70, "Top of wave 5 zone"), (29.35, "Fib 127.2%"), (29.85, "2030 note conversion (~)"), (32.00, "Warrant strike (filed)"),
            (32.50, "Fib 161.8%"), (34, ""), (36, ""), (38, ""), (40, "")]
    # Summary
    summ = [f"GME is at ${price:.2f}.", f"The high today is ${dhigh:.2f}.",
            f"Volume is {dvol/1e6:.1f}M. Heavy volume now needs {need/1e6:.1f}M.",
            f"The gauge reads {S[g][0]}.", ew,
            "A close above $26.88 on heavy volume opens $29.35 and $32.50.", "A close below $23.06 breaks the wave count."]
    # patterns
    cps = candle_patterns(daily, "Daily") + (candle_patterns(b15, "15m") if len(b15) >= 4 else [])
    if pen:
        ptxt = (f"Pole: ${pen['lo']:.2f} ({pen['t0']:%H:%M}) to ${pen['hi']:.2f} ({pen['t1']:%H:%M}), +{pen['g']*100:.1f}%. "
                f"After the pole: {pen['bars']} bars. Highs are {'falling' if pen['falling'] else 'not falling'}. Lows are {'rising' if pen['rising'] else 'not rising'}. "
                + ("The shape is a pennant. " if pen['falling'] and pen['rising'] else "The shape is not a clean pennant. ")
                + f"Upper bound ${pen['upper']:.2f}. Lower bound ${pen['lower']:.2f}. A break above ${pen['upper']:.2f} is bullish. A break below ${pen['lower']:.2f} fails. Measured move ${pen['mm']:.2f}.")
    else: ptxt = "No pole of 3% or more within 10 bars today. No pennant check."
    tri = f"Flat top about $25.25. Measured target about $27.10. Price is now {'above' if price > 25.25 else 'below'} $25.25{'' if price > 25.25 else '. The break is undone'}."
    cup = f"Rim at $26.88. Price is ${abs(price-F100):.2f} {'above' if price > F100 else 'below'} the rim."
    gbar = "".join(f'<div style="flex:1;text-align:center;padding:8px 4px;border-radius:6px;font-weight:600;{"background:"+s[1]+";color:#fff" if i == g else "background:#f1f1f1;color:#888"}">{s[0]}</div>' for i, s in enumerate(S))
    tag = lambda a: '<span style="font-size:11px;padding:1px 6px;border-radius:4px;background:#e3f2fd;color:#1565c0">Automated check</span>' if a else '<span style="font-size:11px;padding:1px 6px;border-radius:4px;background:#fff3e0;color:#e65100">Analyst note</span>'
    stamp = now.strftime("%H:%M:%S")
    H = f"""<!doctype html><html><head><meta charset="utf-8"><meta http-equiv="refresh" content="60"><meta name="viewport" content="width=device-width,initial-scale=1"><title>GME live TA</title>
<style>body{{background:#fff;color:#111;font:15px/1.5 -apple-system,Helvetica,Arial,sans-serif;max-width:1150px;margin:24px auto;padding:0 16px}}h1{{font-size:22px;margin:0}}.sub{{color:#666;margin-bottom:12px}}table{{border-collapse:collapse;margin:8px 0}}td,th{{border:1px solid #ddd;padding:4px 10px;text-align:left}}.k{{background:#f6f8fa;padding:12px 16px;border-radius:8px}}.stamp{{float:right;font-weight:700;color:#1565c0}}</style></head><body>
<h1>GME technical read (live) <span class="stamp">Updated {stamp} ET</span></h1>
<div class="sub">{now:%B %d, %Y}. Market {'open' if is_open else 'closed'}. Last trade {mtime:%H:%M} ET. The page reloads every 60 seconds. Source: Yahoo Finance.</div>
<div style="margin:14px 0;padding:14px 16px;border:1px solid #ddd;border-radius:8px"><div style="font-weight:700;margin-bottom:8px">Thesis gauge</div>
<div style="display:flex;gap:6px">{gbar}</div><div style="margin-top:10px"><b style="color:{S[g][1]}">Now: {S[g][0]}.</b> {S[g][2]}</div>
<div style="margin-top:6px;color:#666;font-size:13px">Price ${price:.2f}. High ${dhigh:.2f}. Volume {dvol/1e6:.2f}M. 3-month average {avgv/1e6:.2f}M. Heavy needs {need/1e6:.2f}M ({'pro-rated, ' + f'{frac*100:.0f}% of session' if not closed else 'full day'}). Heavy: {'yes' if heavy else 'no'}. {'Closed session.' if closed else 'Intraday. Invalid applies to the close only.'}</div></div>
<div class="k"><b>Summary.</b> {' '.join(summ)}</div>
<h2>Daily chart, Fibonacci grid and Elliott count</h2>{dsvg}
<p><b>Fib grid.</b> From the May 1 high $26.88 to the Aug 20 low $17.79. Targets: 127.2% at $29.35 and 161.8% at $32.50.</p>
<p><b>Elliott count.</b> 0 Aug 20 $17.79. 1 Sep 11 $21.36. 2 Sep 15 $21.13. 3 Sep 24 $25.25. 4 Sep 25 $23.06. 5 running high ${w5['h']:.2f} on {w5['t']:%b %d}. <b>Status:</b> {ew}</p>
<h2>Today, 15-minute bars</h2>{isvg}
<p style="color:#666;font-size:13px">Purple line: VWAP from 15-minute bars.</p>
<h2>Chart patterns</h2><table><tr><th>Pattern</th><th>Type</th><th>Read</th></tr>
<tr><td>Pennant / flag (15m)</td><td>{tag(1)}</td><td>{ptxt}</td></tr>
<tr><td>Pennant reference</td><td>{tag(0)}</td><td>Pole $24.90 to $26.76. Breaks up above about $26.55. Fails below about $26.28. Written Oct 9.</td></tr>
<tr><td>Daily ascending triangle</td><td>{tag(0)} + live price</td><td>Broken on Oct 8. {tri}</td></tr>
<tr><td>Cup rim</td><td>{tag(0)} + live price</td><td>{cup}</td></tr></table>
<h2>Candlestick patterns</h2><p style="color:#666;font-size:13px">{tag(1)} Rule-based. Last 3 daily bars and last 3 15-minute bars. The last bar can still change.</p>
<table><tr><th>Frame</th><th>Bar</th><th>OHLC</th><th>Pattern</th></tr>{''.join(f'<tr><td>{a}</td><td>{b}</td><td>{c}</td><td>{d}</td></tr>' for a, b, c, d in cps)}</table>
<h2>Indicators</h2><p style="color:#666;font-size:13px">Daily bars. Today's live bar is the last bar. RSI and DMI use 14 periods, Wilder method. VWAP uses 1-minute bars.</p>
<table><tr><th>Tool</th><th>Value</th><th>Read</th></tr>
<tr><td>RSI (14)</td><td>{R:.1f}</td><td>{rsi_read}</td></tr>
<tr><td>ADX (14)</td><td>{adx:.1f}</td><td>{adx_read}</td></tr>
<tr><td>+DI / −DI</td><td>{pdi:.1f} / {mdi:.1f}</td><td>{di_read}</td></tr>
{''.join(f'<tr><td>SMA {n}</td><td>${v:.2f}</td><td>Price is {"above" if price > v else "below"}.</td></tr>' for n, v in sm.items())}
<tr><td>50 / 200 cross</td><td>{sm[50]:.2f} / {sm[200]:.2f}</td><td>{cross}</td></tr>
<tr><td>VWAP (all prints)</td><td>{f'${vw_all:.2f}' if vw_all else 'n/a'}</td><td>Price is {'above' if vw_all and price > vw_all else 'below'}.</td></tr>
<tr><td>VWAP (no prints &gt;3M)</td><td>{f'${vw_clean:.2f}' if vw_clean else 'n/a'}</td><td>{f'{len(big)} one-minute print(s) over 3M shares: ' + ', '.join(f"{x['t']:%H:%M} {x['v']/1e6:.1f}M" for x in big) + '. These can be block trades or bad data.' if big else 'No single 1-minute print over 3M shares.'}</td></tr></table>
<p><b>Combined read.</b> {' '.join(comb)}</p>
<h2>Warrants (GME WS)</h2><table><tr><th>Item</th><th>Value</th></tr>
<tr><td>Warrant price (GME-WT)</td><td>${wp:.3f}. Change {wch:+.3f} ({wch/wpc*100:+.1f}%). Volume {wmeta.get('regularMarketVolume',0)/1e3:.0f}K.</td></tr>
<tr><td>Strike</td><td>$32.00 in the SEC filings. It can adjust. Some holders cite $32.46. That is not confirmed.</td></tr>
<tr><td>Expiry</td><td>Oct 30, 2026, 5:00 PM ET. {tdl} trading days left. {(EXPIRY-now).days} calendar days.</td></tr>
<tr><td>Note conversion prices</td><td>2032 notes about $28.91. 2030 notes about $29.85. From the filings. Some holders cite $29.55.</td></tr>
<tr><td>Implied volatility</td><td>{iv*100:.0f}% (Black-Scholes, r = 4%, calendar days).</td></tr>
<tr><td>Implied odds above $32</td><td>About {pabove*100:.0f}% at expiry.</td></tr>
<tr><td>Break-even</td><td>${be:.2f} (strike + warrant price). GME needs {(be/price-1)*100:+.1f}%.</td></tr></table>
<h3>Intrinsic value at expiry</h3><table><tr><th>GME price</th><th>Value per warrant</th><th>Note</th></tr>
{''.join(f'<tr><td>${p:.2f}</td><td>${max(0,p-STRIKE):.2f}</td><td>{n}</td></tr>' for p, n in itab)}</table>
<p><b>Read.</b> The warrants are out of the money below $32. The wave 5 zone ends at $27.70. That is far below the strike. The 127.2% target ($29.35) sits near the note conversion prices. The 161.8% target ($32.50) sits just above the strike. It pays only $0.50 per warrant. The break-even is ${be:.2f}. That is above both Fib targets.</p>
<p style="color:#666;font-size:13px;margin-top:30px;border-top:1px solid #ddd;padding-top:8px">Not financial advice. Data: Yahoo Finance, may be delayed.</p></body></html>"""
    return H, dict(state=S[g][0], price=price, high=dhigh, vol=dvol, need=need, avg=avgv, heavy=heavy, rsi=R, adx=adx, pdi=pdi, mdi=mdi,
                   vwap=vw_all, vwap_clean=vw_clean, big=len(big), wt=wp, iv=iv, p32=pabove, tdl=tdl, w5=w5["h"], ew=ew, pen=pen and (pen["upper"], pen["lower"], pen["mm"]))

def main():
    html, info = build()
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(OUT), prefix=".idx", suffix=".tmp")
    with os.fdopen(fd, "w") as f: f.write(html)
    os.chmod(tmp, 0o644); os.replace(tmp, OUT)
    print(json.dumps(info, default=str))

if __name__ == "__main__": main()
