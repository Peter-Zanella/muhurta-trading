#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
app.py — Web-App für Muhūrta Trading (FastAPI, Render)

Rechnet über muhurta_trading.py, das seinerseits ausschliesslich astro_engine.py
(Ved Chart Calc) verwendet. Die Engine wird beim Build nach ./engine geklont (build.sh).

Routen:
  /            Formular + Zeitfenster mit Tagesleiste
  /abgleich    Detailwerte eines Zeitpunkts (wie --abgleich)
  /api/fenster Zeitfenster als JSON
  /login       Zugangsschlüssel (nur wenn APP_KEY gesetzt)
  /health      für Render

Umgebungsvariablen:
  APP_KEY                   Zugangsschlüssel (leer = offen)
  GEBURT, GEBURT_ORT        Standard-Geburtsdaten (Ort als Name, Geocoding wie Ved Chart Calc)
  GEBURT_LAT, GEBURT_LON, GEBURT_TZ   alternativ Koordinaten (nur wenn GEBURT_ORT leer)
  APP_ORT                   Standard-Handelsort (Standard «Wädenswil, Schweiz»)
  ENGINE_DIR                Unterordner von astro_engine.py im Engine-Repo
"""

from __future__ import annotations

import contextlib
import functools
import hashlib
import hmac
import html
import io
import os
import urllib.parse
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

_HIER = os.path.dirname(os.path.abspath(__file__))
if not os.environ.get("ASTRO_ENGINE_PATH"):
    os.environ["ASTRO_ENGINE_PATH"] = os.path.join(_HIER, "engine",
                                                   os.environ.get("ENGINE_DIR", "").strip())

import muhurta_trading as mt  # noqa: E402

from fastapi import FastAPI, Form, Request  # noqa: E402
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse  # noqa: E402

app = FastAPI(title="Muhūrta Trading", docs_url=None, redoc_url=None, openapi_url=None)

# ---------------------------------------------------------------------------
# Konfiguration
# ---------------------------------------------------------------------------

APP_KEY = os.environ.get("APP_KEY", "").strip()
COOKIE = "muhurta_zugang"
TZ_NAME = mt.STANDARD_TZ
STANDARD_HANDELSORT = os.environ.get("APP_ORT", "").strip() or "Wädenswil, Schweiz"
MAX_TAGE = 31

try:
    with open(os.path.join(_HIER, "ENGINE_COMMIT"), encoding="utf-8") as _fh:
        ENGINE_COMMIT = _fh.read().strip()
except OSError:
    ENGINE_COMMIT = "lokal"

MARKT_NAMEN = {"SIX": "SIX Zürich", "XETRA": "Xetra", "LSE": "London", "NYSE": "New York",
               "KRYPTO": "Krypto (24/7)", "ALLE": "Ganzer Tag"}

MONATE = ["Januar", "Februar", "März", "April", "Mai", "Juni", "Juli", "August",
          "September", "Oktober", "November", "Dezember"]


def _env(name: str, standard: str = "") -> str:
    return os.environ.get(name, "").strip() or standard


def zugangs_token() -> str:
    return hmac.new(APP_KEY.encode(), b"muhurta-trading", hashlib.sha256).hexdigest()


def zugang_ok(request: Request) -> bool:
    if not APP_KEY:
        return True
    return hmac.compare_digest(request.cookies.get(COOKIE, ""), zugangs_token())


@functools.lru_cache(maxsize=64)
def geburtsort(geburt: str, gort: str, glat: str, glon: str, gtz: str) -> dict:
    """Geburtsort auflösen. Ortsname hat Vorrang; Koordinaten nur bei leerem Ortsfeld."""
    if gort:
        return mt.geburtsort_aufloesen(geburt, gort)
    lat, lon = float(glat), float(glon)
    iana = gtz or mt.ae.iana_tz(lat, lon)
    if not iana:
        raise ValueError("Zeitzone für die Koordinaten nicht ermittelbar. Zeitzone eintragen, z. B. Europe/Zurich.")
    dt = datetime.strptime(geburt, "%Y-%m-%d %H:%M")
    offset = mt.ae.hist_offset(iana, dt.year, dt.month, dt.day, dt.hour, dt.minute)
    if offset is None:
        raise ValueError(f"Zeitzone «{iana}» unbekannt, z. B. Europe/Zurich.")
    h, m = int(abs(offset)), int(round((abs(offset) % 1) * 60))
    return {"lat": lat, "lon": lon, "iana": iana, "offset": offset, "approx": False,
            "label": f"{lat:.4f}, {lon:.4f}",
            "offset_str": f"UTC{'+' if offset >= 0 else '-'}{h:02d}:{m:02d}"}


@functools.lru_cache(maxsize=64)
def handelsort(name: str) -> dict:
    g = mt.ae.geocode(name)
    if not g:
        raise ValueError(f"Handelsort «{name}» nicht gefunden. Genauer angeben, z. B. «Zürich, Schweiz».")
    iana = g.get("iana") or mt.ae.iana_tz(g["lat"], g["lon"]) or TZ_NAME
    try:
        ZoneInfo(iana)
    except Exception:
        iana = TZ_NAME
    return {"lat": g["lat"], "lon": g["lon"], "iana": iana, "label": g["label"]}


@functools.lru_cache(maxsize=32)
def radix_cache(geburt: str, lat: float, lon: float, offset: float, ort: str) -> mt.Radix:
    return mt.radix_berechnen(geburt, None, lat, lon, offset=offset, ort=ort)

# ---------------------------------------------------------------------------
# Parameter
# ---------------------------------------------------------------------------

def _wert(q, name: str, standard: str) -> str:
    """Formularwert; ein abgeschicktes leeres Feld bleibt leer (überschreibt den Standard)."""
    return q.get(name, "").strip() if name in q else standard


def parameter(q) -> tuple[dict, list[str]]:
    fehler: list[str] = []
    p = {
        "modus": q.get("modus", "kauf"),
        "markt": q.get("markt", "SIX"),
        "von": q.get("von") or datetime.now(ZoneInfo(TZ_NAME)).date().isoformat(),
        "tage": q.get("tage", "5"),
        "schritt": q.get("schritt", "15"),
        "min": q.get("min", "5"),
        "hort": _wert(q, "hort", STANDARD_HANDELSORT) or STANDARD_HANDELSORT,
        "geburt": _wert(q, "geburt", _env("GEBURT")).replace("T", " "),
        "gort": _wert(q, "gort", _env("GEBURT_ORT")),
        "glat": _wert(q, "glat", "" if _env("GEBURT_ORT") else _env("GEBURT_LAT")),
        "glon": _wert(q, "glon", "" if _env("GEBURT_ORT") else _env("GEBURT_LON")),
        "gtz": _wert(q, "gtz", "" if _env("GEBURT_ORT") else _env("GEBURT_TZ")),
    }
    if p["modus"] not in ("kauf", "verkauf"):
        fehler.append("Modus muss «kauf» oder «verkauf» sein.")
    if p["markt"] not in mt.MAERKTE:
        fehler.append("Unbekannter Markt.")
    try:
        p["von_d"] = date.fromisoformat(p["von"])
    except ValueError:
        fehler.append("Startdatum im Format JJJJ-MM-TT angeben.")
    try:
        p["tage_i"] = max(1, min(MAX_TAGE, int(p["tage"])))
    except ValueError:
        fehler.append("Anzahl Tage als Zahl angeben.")
    try:
        p["schritt_i"] = int(p["schritt"])
        if p["schritt_i"] not in (10, 15, 30):
            raise ValueError
    except ValueError:
        fehler.append("Zeitschritt: 10, 15 oder 30 Minuten.")
    try:
        p["min_i"] = int(p["min"])
    except ValueError:
        fehler.append("Mindestscore als ganze Zahl angeben.")

    geburt_ok = False
    if not p["geburt"]:
        fehler.append("Geburtszeit fehlt. Unter «Geburtsdaten» eintragen.")
    else:
        try:
            datetime.strptime(p["geburt"], "%Y-%m-%d %H:%M")
            geburt_ok = True
        except ValueError:
            fehler.append("Geburtszeit im Format JJJJ-MM-TT HH:MM angeben.")
    if not p["gort"]:
        if not (p["glat"] and p["glon"]):
            fehler.append("Geburtsort fehlt. Unter «Geburtsdaten» den Ort eintragen, z. B. «Liestal, Schweiz».")
            geburt_ok = False
        else:
            try:
                float(p["glat"]), float(p["glon"])
            except ValueError:
                fehler.append("Breite und Länge als Dezimalzahl angeben (Ost positiv).")
                geburt_ok = False

    if geburt_ok:
        try:
            p["g"] = geburtsort(p["geburt"], p["gort"], p["glat"], p["glon"], p["gtz"])
        except ValueError as exc:
            fehler.append(str(exc))
    try:
        p["h"] = handelsort(p["hort"])
    except ValueError as exc:
        fehler.append(str(exc))
    return p, fehler


FORMFELDER = ("hort", "geburt", "gort", "glat", "glon", "gtz")


def geburt_query(p: dict) -> dict:
    """Orts- und Geburtsfelder in Links nur mitgeben, wenn sie vom Standard abweichen."""
    std, _ = parameter({})
    return {k: p[k] for k in FORMFELDER if p.get(k, "") != std.get(k, "")}


def berechnen(p: dict):
    g, h = p["g"], p["h"]
    tz = ZoneInfo(h["iana"])
    radix = radix_cache(p["geburt"], g["lat"], g["lon"], g["offset"], g["label"])
    kalender = mt.Tageskalender(h["lat"], h["lon"], tz)
    start = datetime.combine(p["von_d"], time(0, 0), tzinfo=tz)
    ende = datetime.combine(p["von_d"] + timedelta(days=p["tage_i"]), time(0, 0), tzinfo=tz)
    protokoll: list = []
    fenster = mt.fenster_berechnen(start, ende, p["schritt_i"], radix, p["modus"], p["markt"],
                                   p["min_i"], kalender, h["lat"], h["lon"], protokoll)
    return radix, kalender, fenster, protokoll

# ---------------------------------------------------------------------------
# HTML
# ---------------------------------------------------------------------------

E = html.escape

CSS = """
:root{
  --nacht:#141b33; --tafel:#1c2546; --linie:#2e3a66; --text:#ece6d6; --leise:#a3abc8;
  --kurkuma:#e3a531; --sperre-a:#4b2a37; --sperre-b:#2a1d2c;
  color-scheme:dark;
  box-sizing:border-box;
  padding-top:env(safe-area-inset-top,0px); padding-bottom:env(safe-area-inset-bottom,0px);
}
*,*::before,*::after{box-sizing:border-box}
html{scroll-padding-top:env(safe-area-inset-top,0px)}
body{margin:0;background:var(--nacht);color:var(--text);
  font:400 16px/1.55 "Work Sans",system-ui,-apple-system,"Segoe UI",sans-serif}
a{color:var(--kurkuma)}
:focus-visible{outline:2px solid var(--kurkuma);outline-offset:2px}
.seite{max-width:980px;margin:0 auto;padding:28px 20px 48px}
h1{font:600 2.4rem/1.1 "Spectral",Georgia,serif;margin:0 0 6px;letter-spacing:-.01em}
h2{font:600 1.35rem/1.2 "Spectral",Georgia,serif;margin:0}
.radix{color:var(--leise);margin:0 0 24px}
.radix strong{color:var(--text);font-weight:500}
form.steuerung{background:var(--tafel);border:1px solid var(--linie);border-radius:10px;
  padding:16px;display:flex;flex-wrap:wrap;gap:14px 18px;align-items:flex-end;margin-bottom:28px}
label{display:flex;flex-direction:column;gap:4px;font-size:.85rem;color:var(--leise)}
input,select{background:var(--nacht);color:var(--text);border:1px solid var(--linie);
  border-radius:6px;padding:8px 10px;font:inherit;font-size:.95rem;min-width:0}
input[type=number]{width:5.5rem}
.modus{display:flex;border:1px solid var(--linie);border-radius:6px;overflow:hidden}
.modus input{position:absolute;opacity:0;pointer-events:none}
.modus span{padding:8px 14px;cursor:pointer;color:var(--leise)}
.modus input:checked+span{background:var(--kurkuma);color:#1b1404;font-weight:500}
.modus input:focus-visible+span{outline:2px solid var(--text);outline-offset:-3px}
button{background:var(--kurkuma);color:#1b1404;border:0;border-radius:6px;padding:10px 18px;
  font:500 .95rem "Work Sans",system-ui,sans-serif;cursor:pointer}
details.geburt{flex-basis:100%;min-width:0;color:var(--leise);font-size:.9rem}
details.geburt summary{cursor:pointer}
details.geburt .felder{display:flex;flex-wrap:wrap;gap:12px;margin-top:10px}
details.koordinaten{margin-top:12px}
label.ort{flex:1 1 220px;min-width:0;max-width:100%}
label.ort input{width:100%}
.hinweis{color:#e8c27a}
.fehler{background:#3a1f2a;border:1px solid #7a3a4c;border-radius:8px;padding:12px 16px;margin-bottom:24px}
.fehler p{margin:4px 0}
.legende{display:flex;flex-wrap:wrap;gap:16px;font-size:.85rem;color:var(--leise);margin:0 0 20px}
.legende i{display:inline-block;width:14px;height:14px;border-radius:3px;vertical-align:-2px;margin-right:6px}
.tag{border-top:1px solid var(--linie);padding:22px 0 8px}
.tag-kopf{display:flex;flex-wrap:wrap;justify-content:space-between;gap:6px 16px;align-items:baseline}
.sonne{color:var(--leise);font-size:.88rem}
.leiste-rahmen{overflow-x:auto;margin:14px 0 6px}
.leiste{display:grid;gap:1px;min-width:100%}
.slot{height:30px;border-radius:2px;background:var(--linie)}
.slot.gesperrt{background:repeating-linear-gradient(135deg,var(--sperre-a) 0 3px,var(--sperre-b) 3px 6px)}
.slot.top{box-shadow:inset 0 -3px 0 var(--text)}
.stunde{font-size:.7rem;color:var(--leise);white-space:nowrap;overflow:visible;height:1.1em}
.fenster{list-style:none;margin:12px 0 0;padding:0;display:grid;gap:10px}
.fenster li{background:var(--tafel);border:1px solid var(--linie);border-radius:8px;padding:12px 14px}
.zeile{display:flex;flex-wrap:wrap;gap:6px 16px;align-items:baseline}
.zeit{font:600 1.15rem "Spectral",Georgia,serif}
.score{color:var(--kurkuma);font-weight:500}
.merkmale{color:var(--leise);font-size:.9rem}
.fenster details{margin-top:6px;font-size:.88rem;color:var(--leise)}
.fenster details ul{margin:6px 0 0;padding-left:18px}
.plus{color:var(--text)}
.leer{color:var(--leise);margin:10px 0 0}
pre{background:var(--tafel);border:1px solid var(--linie);border-radius:8px;padding:16px;
  overflow-x:auto;font-size:.82rem;line-height:1.45}
footer{margin-top:40px;color:var(--leise);font-size:.82rem}
@media (max-width:600px){h1{font-size:1.9rem}.seite{padding:20px 14px 40px}}
"""

KOPF = """<!doctype html><html lang="de"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>{titel}</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Spectral:wght@500;600&family=Work+Sans:wght@400;500&display=swap" rel="stylesheet">
<style>{css}</style></head><body><main class="seite">"""


def seite(titel: str, inhalt: str, ort: str = "") -> HTMLResponse:
    ort_text = f"Handelsort {E(ort)}. " if ort else ""
    fuss = (f"<footer>Rechenquelle: astro_engine.py (Ved Chart Calc, Commit {E(ENGINE_COMMIT)}). "
            f"{ort_text}Astrologische Auswertung, keine Anlageberatung.</footer>")
    return HTMLResponse(KOPF.format(titel=E(titel), css=CSS) + inhalt + fuss + "</main></body></html>")


def datum_lang(d: date, wt: int) -> str:
    return f"{mt.WOCHENTAG[wt]}, {d.day}. {MONATE[d.month - 1]}"


def slot_style(bew, min_score: int) -> tuple[str, str]:
    if bew.gesperrt:
        return "slot gesperrt", ""
    if bew.score < min_score:
        return "slot", ""
    alpha = 0.35 + 0.65 * min(1.0, (bew.score - min_score) / max(1, 12 - min_score))
    return "slot", f"background:rgba(227,165,49,{alpha:.2f})"


def formular(p: dict) -> str:
    def opt(wert, text, aktiv):
        return f'<option value="{E(wert)}"{" selected" if wert == aktiv else ""}>{E(text)}</option>'
    maerkte = "".join(opt(k, v, p["markt"]) for k, v in MARKT_NAMEN.items())
    schritte = "".join(opt(str(s), f"{s} Min.", p["schritt"]) for s in (10, 15, 30))
    modus = "".join(
        f'<label class="radio"><input type="radio" name="modus" value="{w}"'
        f'{" checked" if p["modus"] == w else ""}><span>{t}</span></label>'
        for w, t in (("kauf", "Kauf"), ("verkauf", "Verkauf")))
    offen = " open" if not (p["geburt"] and (p["gort"] or (p["glat"] and p["glon"]))) else ""
    koord_offen = " open" if (not p["gort"] and p["glat"]) else ""
    geburt_wert = p["geburt"].replace(" ", "T") if p["geburt"] else ""
    return f"""
<form class="steuerung" method="get" action="/">
  <div><span style="font-size:.85rem;color:var(--leise)">Vorhaben</span>
    <div class="modus" role="radiogroup" aria-label="Vorhaben">{modus}</div></div>
  <label>Markt<select name="markt">{maerkte}</select></label>
  <label>Ab<input type="date" name="von" value="{E(p['von'])}"></label>
  <label>Tage<input type="number" name="tage" min="1" max="{MAX_TAGE}" value="{E(str(p['tage']))}"></label>
  <label>Raster<select name="schritt">{schritte}</select></label>
  <label>Mindestscore<input type="number" name="min" value="{E(str(p['min']))}"></label>
  <label class="ort">Handelsort<input name="hort" value="{E(p['hort'])}" placeholder="z. B. Zürich, Schweiz"></label>
  <button type="submit">Zeitfenster berechnen</button>
  <details class="geburt"{offen}><summary>Geburtsdaten</summary>
    <div class="felder">
      <label>Geburtszeit (Ortszeit)<input type="datetime-local" name="geburt" value="{E(geburt_wert)}"></label>
      <label class="ort">Geburtsort<input name="gort" value="{E(p['gort'])}" placeholder="z. B. Liestal, Schweiz"></label>
    </div>
    <details class="koordinaten"{koord_offen}><summary>Koordinaten statt Ort (nur bei leerem Geburtsort)</summary>
      <div class="felder">
        <label>Breite<input name="glat" inputmode="decimal" value="{E(p['glat'])}"></label>
        <label>Länge (Ost +)<input name="glon" inputmode="decimal" value="{E(p['glon'])}"></label>
        <label>Zeitzone<input name="gtz" value="{E(p['gtz'])}" placeholder="automatisch"></label>
      </div>
    </details>
  </details>
</form>"""


def tage_html(p, kalender, fenster, protokoll) -> str:
    tz = kalender.tz
    nach_tag: dict[date, list] = {}
    for t, bew in protokoll:
        nach_tag.setdefault(t.date(), []).append((t, bew))
    extra = geburt_query(p)
    teile = []
    d = p["von_d"]
    for _ in range(p["tage_i"]):
        tag = kalender.fuer_datum(d)
        rk = [mt.dt_aus_jd(x, tz) for x in tag.achtel_zeit(mt.RAHU_KALA[tag.wochentag])]
        kopf = (f'<div class="tag-kopf"><h2>{E(datum_lang(d, tag.wochentag))}</h2>'
                f'<span class="sonne">{E(mt.VARA[tag.wochentag])}, Aufgang '
                f'{mt.dt_aus_jd(tag.aufgang, tz):%H:%M}, Untergang '
                f'{mt.dt_aus_jd(tag.untergang, tz):%H:%M}, Rāhu Kāla '
                f'{rk[0]:%H:%M}–{rk[1]:%H:%M}</span></div>')
        slots = nach_tag.get(d, [])
        beste = sorted((f for f in fenster if f.von.date() == d),
                       key=lambda f: (-f.score, f.von))[:3]
        if not slots:
            teile.append(f'<section class="tag">{kopf}<p class="leer">Kein Handel an diesem Tag.</p></section>')
            d += timedelta(days=1)
            continue
        zellen, stunden = [], []
        for t, bew in slots:
            klasse, stil = slot_style(bew, p["min_i"])
            if any(f.von <= t < f.bis for f in beste):
                klasse += " top"
            if bew.gesperrt:
                tip = f"{t:%H:%M} gesperrt: {', '.join(bew.sperren)}"
            else:
                tip = f"{t:%H:%M} Score {bew.score}"
            zellen.append(f'<div class="{klasse}" style="{stil}" title="{E(tip)}"></div>')
            stunden.append(f'<div class="stunde">{t:%H}</div>' if t.minute == 0 else "<div></div>")
        spalten = f"grid-template-columns:repeat({len(slots)},minmax(5px,1fr))"
        leiste = (f'<div class="leiste-rahmen"><div class="leiste" style="{spalten}" role="img" '
                  f'aria-label="Bewertung im Tagesverlauf">{"".join(zellen)}</div>'
                  f'<div class="leiste" style="{spalten}">{"".join(stunden)}</div></div>')
        if beste:
            items = []
            for f in sorted(beste, key=lambda f: f.von):
                i = f.info
                plus = "".join(f'<li class="plus">{E(t)} ({pkt:+d})</li>' if pkt > 0
                               else f"<li>{E(t)} ({pkt:+d})</li>"
                               for pkt, t in sorted(f.faktoren, key=lambda x: -x[0]))
                link = "/abgleich?" + urllib.parse.urlencode(
                    {"zeit": f"{f.von:%Y-%m-%d %H:%M}", "modus": p["modus"], **extra})
                items.append(
                    f'<li><div class="zeile"><span class="zeit">{f.von:%H:%M}–{f.bis:%H:%M}</span>'
                    f'<span class="score">{f.sterne} Score {f.score}, {E(f.bewertung)}</span>'
                    f'<span class="merkmale">Horā {E(i["hora"])}, {E(i["nakshatra"])}, '
                    f'Tārā {E(i["tara"])}, Lagna {E(i["lagna"])}</span></div>'
                    f'<details><summary>Faktoren</summary><ul>{plus}</ul>'
                    f'<p><a href="{E(link)}">Abgleich für {f.von:%H:%M}</a></p></details></li>')
            liste = f'<ul class="fenster">{"".join(items)}</ul>'
        else:
            liste = '<p class="leer">Kein Zeitfenster erreicht den Mindestscore.</p>'
        teile.append(f'<section class="tag">{kopf}{leiste}{liste}</section>')
        d += timedelta(days=1)
    return "".join(teile)


def radix_html(p: dict, radix) -> str:
    g, h = p["g"], p["h"]
    herren = ", ".join(sorted(mt.PLANET_DE[x] for x in radix.wohlstandsherren))
    geschaetzt = (' <span class="hinweis">Zeitzone nur geschätzt – Koordinaten und '
                  'Zeitzone manuell eintragen.</span>') if g.get("approx") else ""
    return (f'<p class="radix">Mond in <strong>{E(mt.RASHI[radix.mond_rashi])}</strong>, '
            f'Nakṣatra <strong>{E(mt.NAKSHATRA[radix.mond_nak])}</strong>, Lagna '
            f'<strong>{E(mt.RASHI[radix.lagna_rashi])}</strong>. Herren von 2. und 11. Haus: '
            f'{E(herren)}.<br>Geburtsort {E(g["label"])} ({g["lat"]:.4f}, {g["lon"]:.4f}, '
            f'{E(g["offset_str"])}).{geschaetzt} Handelsort {E(h["label"])} '
            f'({h["lat"]:.4f}, {h["lon"]:.4f}, {E(h["iana"])}).</p>')


LEGENDE = """<div class="legende">
<span><i style="background:rgba(227,165,49,1)"></i>hoher Score</span>
<span><i style="background:rgba(227,165,49,.4)"></i>knapp über Mindestscore</span>
<span><i style="background:var(--linie)"></i>unter Mindestscore</span>
<span><i style="background:repeating-linear-gradient(135deg,var(--sperre-a) 0 3px,var(--sperre-b) 3px 6px)"></i>gesperrt</span>
<span><i style="background:var(--linie);box-shadow:inset 0 -3px 0 var(--text)"></i>beste Fenster</span>
</div>"""

# ---------------------------------------------------------------------------
# Routen
# ---------------------------------------------------------------------------

@app.get("/health")
def health():
    return {"ok": True, "engine": ENGINE_COMMIT}


@app.get("/login", response_class=HTMLResponse)
def login_form(fehler: str = ""):
    hinweis = '<div class="fehler"><p>Schlüssel stimmt nicht.</p></div>' if fehler else ""
    return seite("Zugang", f"""<h1>Muhūrta für Trades</h1>{hinweis}
<form class="steuerung" method="post" action="/login">
<label>Zugangsschlüssel<input type="password" name="key" autocomplete="current-password" autofocus></label>
<button type="submit">Anmelden</button></form>""")


@app.post("/login")
def login(key: str = Form("")):
    if not APP_KEY or not hmac.compare_digest(key.strip(), APP_KEY):
        return RedirectResponse("/login?fehler=1", status_code=303)
    antwort = RedirectResponse("/", status_code=303)
    antwort.set_cookie(COOKIE, zugangs_token(), max_age=90 * 86400, httponly=True,
                       secure=True, samesite="lax")
    return antwort


@app.get("/", response_class=HTMLResponse)
def start(request: Request):
    if not zugang_ok(request):
        return RedirectResponse("/login", status_code=303)
    p, fehler = parameter(request.query_params)
    inhalt = "<h1>Muhūrta für Trades</h1>"
    if fehler:
        inhalt += '<p class="radix">Zeitfenster für Kauf und Verkauf nach dem Geburtshoroskop.</p>'
        inhalt += formular(p)
        inhalt += '<div class="fehler">' + "".join(f"<p>{E(x)}</p>" for x in fehler) + "</div>"
        return seite("Muhūrta für Trades", inhalt)
    try:
        radix, kalender, fenster, protokoll = berechnen(p)
    except Exception as exc:  # Engine-/Eingabefehler sichtbar machen
        inhalt += formular(p) + f'<div class="fehler"><p>Berechnung fehlgeschlagen: {E(str(exc))}</p></div>'
        return seite("Muhūrta für Trades", inhalt)
    inhalt += radix_html(p, radix)
    inhalt += formular(p) + LEGENDE + tage_html(p, kalender, fenster, protokoll)
    return seite("Muhūrta für Trades", inhalt, p["h"]["label"])


@app.get("/abgleich", response_class=HTMLResponse)
def abgleich(request: Request):
    if not zugang_ok(request):
        return RedirectResponse("/login", status_code=303)
    q = request.query_params
    p, fehler = parameter(q)
    zeit = (q.get("zeit") or "").replace("T", " ")
    tz = ZoneInfo(p["h"]["iana"]) if "h" in p else ZoneInfo(TZ_NAME)
    try:
        dt = datetime.strptime(zeit, "%Y-%m-%d %H:%M").replace(tzinfo=tz)
    except ValueError:
        fehler.append("Zeitpunkt im Format JJJJ-MM-TT HH:MM angeben.")
    zurueck = "/?" + urllib.parse.urlencode({"modus": p["modus"], **geburt_query(p)})
    kopf = f'<h1>Abgleich</h1><p class="radix"><a href="{E(zurueck)}">Zurück zu den Zeitfenstern</a></p>'
    if fehler:
        return seite("Abgleich", kopf + '<div class="fehler">'
                     + "".join(f"<p>{E(x)}</p>" for x in fehler) + "</div>")
    puffer = io.StringIO()
    try:
        g, h = p["g"], p["h"]
        radix = radix_cache(p["geburt"], g["lat"], g["lon"], g["offset"], g["label"])
        kalender = mt.Tageskalender(h["lat"], h["lon"], tz)
        with contextlib.redirect_stdout(puffer):
            mt.abgleich_radix(radix)
            mt.abgleich_zeitpunkt(dt, radix, p["modus"], h["lat"], h["lon"], kalender, tz)
    except Exception as exc:
        return seite("Abgleich", kopf + f'<div class="fehler"><p>Berechnung fehlgeschlagen: {E(str(exc))}</p></div>')
    return seite("Abgleich", kopf + radix_html(p, radix) + f"<pre>{E(puffer.getvalue())}</pre>",
                 p["h"]["label"])


@app.get("/api/fenster")
def api_fenster(request: Request):
    if not zugang_ok(request):
        return JSONResponse({"fehler": "Kein Zugang"}, status_code=401)
    p, fehler = parameter(request.query_params)
    if fehler:
        return JSONResponse({"fehler": fehler}, status_code=400)
    radix, _, fenster, _ = berechnen(p)
    return {
        "radix": {"mond_rashi": mt.RASHI[radix.mond_rashi],
                  "nakshatra": mt.NAKSHATRA[radix.mond_nak],
                  "lagna": mt.RASHI[radix.lagna_rashi]},
        "geburtsort": {k: p["g"][k] for k in ("label", "lat", "lon", "iana", "offset_str")},
        "handelsort": p["h"],
        "engine": ENGINE_COMMIT,
        "fenster": [{"von": f.von.isoformat(), "bis": f.bis.isoformat(), "score": f.score,
                     "bewertung": f.bewertung, **f.info,
                     "faktoren": [{"punkte": pk, "text": t} for pk, t in f.faktoren]}
                    for f in fenster],
    }
