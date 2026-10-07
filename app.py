#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
app.py — Web-App für Muhūrta Trading (FastAPI, Render)

Rechnet über muhurta_trading.py, das seinerseits ausschliesslich astro_engine.py
(Ved Chart Calc) verwendet. Die Engine wird beim Build nach ./engine geklont (build.sh).

Routen:
  /            Formular + Zeitfenster mit Tagesleiste
  /slot        Muhūrta-Detail eines Zeitpunkts mit Chart (Klick in der Tagesleiste)
  /abgleich    Rohdaten eines Zeitpunkts (wie --abgleich)
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
MAX_TAGE = 90                     # längster Zeitraum pro Abfrage

try:
    with open(os.path.join(_HIER, "ENGINE_COMMIT"), encoding="utf-8") as _fh:
        ENGINE_COMMIT = _fh.read().strip()
except OSError:
    ENGINE_COMMIT = "lokal"

MARKT_NAMEN = {"SIX": "SIX Zürich", "XETRA": "Xetra", "LSE": "London", "NYSE": "New York (NYSE/Nasdaq)",
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
    # +1 Tag, damit Sitzungen über Mitternacht (z. B. NYSE von Asien aus) vollständig sind
    ende = datetime.combine(p["von_d"] + timedelta(days=p["tage_i"] + 1), time(0, 0), tzinfo=tz)
    protokoll: list = []
    fenster = mt.fenster_berechnen(start, ende, p["schritt_i"], radix, p["modus"], p["markt"],
                                   p["min_i"], kalender, h["lat"], h["lon"], protokoll)
    letzter = p["von_d"] + timedelta(days=p["tage_i"] - 1)
    fenster = [f for f in fenster if p["von_d"] <= f.tag <= letzter]
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
a.slot{display:block}
.slot.aktiv{outline:2px solid var(--text);outline-offset:1px;position:relative;z-index:1}
a.slot:focus-visible{outline:2px solid var(--kurkuma);outline-offset:1px;position:relative;z-index:2}
a.detail{margin-left:auto;font-size:.9rem}
.status{margin:4px 0 0}
.gesperrt-text{color:#e79aa9}
h2.abstand{margin-top:24px}
.detail-raster{display:grid;grid-template-columns:minmax(0,420px) minmax(0,1fr);gap:32px;
  align-items:start;margin:24px 0 32px}
.detail-raster h2{margin-bottom:10px}
.chart-wahl{display:inline-flex;border:1px solid var(--linie);border-radius:6px;overflow:hidden;margin-bottom:10px}
.chart-wahl button{background:transparent;color:var(--leise);border-radius:0;padding:6px 12px}
.chart-wahl button[aria-pressed=true]{background:var(--kurkuma);color:#1b1404}
.chart[data-stil=nord] .sued,.chart[data-stil=sued] .nord{display:none}
.chart-svg{width:100%;max-width:420px;height:auto;display:block}
.chart-svg .feld{fill:var(--tafel);stroke:#4a5890;stroke-width:1}
.chart-svg .linie{fill:none;stroke:#4a5890;stroke-width:1}
.chart-svg .mitte{fill:var(--nacht);stroke:#4a5890;stroke-width:1}
.chart-svg text{font-family:"Work Sans",system-ui,sans-serif;font-size:13px;fill:var(--text)}
.chart-svg .zeichen{font-size:10px;fill:var(--leise)}
.chart-svg .nummer{font-size:11px;fill:var(--leise)}
.chart-svg .la{fill:var(--kurkuma);font-weight:500}
.chart-svg .lagna-strich{stroke:var(--kurkuma);stroke-width:1.5}
.chart-svg .mitte-text{font-family:"Spectral",Georgia,serif;font-size:16px}
dl.panchanga{display:grid;grid-template-columns:max-content 1fr;gap:6px 16px;margin:0}
dl.panchanga dt{color:var(--leise)}
dl.panchanga dd{margin:0}
ul.faktoren{margin:0;padding-left:18px;color:var(--leise)}
.tabelle{overflow-x:auto;margin-top:10px}
table.grahas{border-collapse:collapse;width:100%;font-size:.9rem}
table.grahas th,table.grahas td{text-align:left;padding:7px 12px 7px 0;border-bottom:1px solid var(--linie);white-space:nowrap}
table.grahas th{color:var(--leise);font-weight:400}
.navigation{display:flex;justify-content:space-between;gap:12px;flex-wrap:wrap;margin-top:28px}
details.phase{border:1px solid var(--linie);border-left:4px solid var(--leise);border-radius:8px;
  padding:10px 14px;margin:0 0 20px;background:var(--tafel)}
details.phase.gut{border-left-color:#7fbf8f}
details.phase.mittel{border-left-color:var(--kurkuma)}
details.phase.vorsicht{border-left-color:#e79aa9}
details.phase summary{cursor:pointer}
details.phase ul{margin:8px 0 0;padding-left:18px;color:var(--leise)}
.leise{color:var(--leise);font-size:.88rem}
@media (max-width:760px){.detail-raster{grid-template-columns:1fr}}
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


def wochentag_so0(d: date) -> int:
    return (d.weekday() + 1) % 7


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


def basis_query(p: dict) -> dict:
    q = {"modus": p["modus"], "markt": p["markt"],
         "schritt": str(p.get("schritt_i", 15)), "min": str(p.get("min_i", 5))}
    q.update(geburt_query(p))
    return q


def url(pfad: str, p: dict, **extra) -> str:
    return pfad + "?" + urllib.parse.urlencode({**basis_query(p), **extra})


def slot_url(p: dict, t: datetime) -> str:
    return url("/slot", p, zeit=f"{t:%Y-%m-%d %H:%M}")


def leiste_html(p: dict, slots: list, beste: list, aktuell: datetime | None = None) -> str:
    """Tagesleiste; jedes Feld führt zum Muhūrta-Detail dieses Zeitpunkts."""
    zellen, stunden = [], []
    for t, bew in slots:
        klasse, stil = slot_style(bew, p["min_i"])
        if any(f.von <= t < f.bis for f in beste):
            klasse += " top"
        if aktuell is not None and t == aktuell:
            klasse += " aktiv"
        if bew.gesperrt:
            tip = f"{t:%H:%M} gesperrt: {', '.join(bew.sperren)}"
        else:
            tip = f"{t:%H:%M} Score {bew.score}"
        aktiv = ' aria-current="true"' if "aktiv" in klasse else ""
        zellen.append(f'<a class="{klasse}" style="{stil}" href="{E(slot_url(p, t))}" '
                      f'title="{E(tip)}" aria-label="{E(tip)}"{aktiv}></a>')
        stunden.append(f'<div class="stunde">{t:%H}</div>' if t.minute == 0 else "<div></div>")
    spalten = f"grid-template-columns:repeat({len(slots)},minmax(5px,1fr))"
    return (f'<div class="leiste-rahmen"><nav class="leiste" style="{spalten}" '
            f'aria-label="Zeitpunkte des Tages">{"".join(zellen)}</nav>'
            f'<div class="leiste" style="{spalten}" aria-hidden="true">{"".join(stunden)}</div></div>')


def zeit_label(t: datetime, d: date) -> str:
    """Uhrzeit; mit Datum, wenn der Zeitpunkt nach Mitternacht des Handelstags liegt."""
    return f"{t:%H:%M}" if t.date() == d else f"{t:%d.%m. %H:%M}"


def tag_kopf(d: date, tag, tz) -> str:
    rk = [mt.dt_aus_jd(x, tz) for x in tag.achtel_zeit(mt.RAHU_KALA[tag.wochentag])]
    return (f'<div class="tag-kopf"><h2>{E(datum_lang(d, tag.wochentag))}</h2>'
            f'<span class="sonne">{E(mt.VARA[tag.wochentag])}, Aufgang '
            f'{mt.dt_aus_jd(tag.aufgang, tz):%H:%M}, Untergang '
            f'{mt.dt_aus_jd(tag.untergang, tz):%H:%M}, Rāhu Kāla '
            f'{rk[0]:%H:%M}–{rk[1]:%H:%M}</span></div>')


def faktoren_liste(faktoren: list) -> str:
    return "".join(f'<li class="plus">{E(t)} ({pkt:+d})</li>' if pkt > 0
                   else f"<li>{E(t)} ({pkt:+d})</li>"
                   for pkt, t in sorted(faktoren, key=lambda x: -x[0]))


def tage_html(p, kalender, fenster, protokoll) -> str:
    tz = kalender.tz
    nach_tag: dict[date, list] = {}
    for t, bew in protokoll:
        nach_tag.setdefault(mt.handelstag(t, p["markt"]), []).append((t, bew))
    teile = []
    d = p["von_d"]
    for _ in range(p["tage_i"]):
        tag = kalender.fuer_datum(d)
        kopf = tag_kopf(d, tag, tz)
        slots = nach_tag.get(d, [])
        beste = sorted((f for f in fenster if f.tag == d),
                       key=lambda f: (-f.score, f.von))[:3]
        if slots:
            kopf = kopf.replace("</span></div>", f", Handelszeit {E(mt.sitzungszeit(slots, p['schritt_i']))}"
                                                 f" Ortszeit</span></div>", 1)
        if not slots:
            teile.append(f'<section class="tag">{kopf}<p class="leer">Kein Handel an diesem Tag.</p></section>')
            d += timedelta(days=1)
            continue
        leiste = leiste_html(p, slots, beste)
        if beste:
            items = []
            for f in sorted(beste, key=lambda f: f.von):
                i = f.info
                items.append(
                    f'<li><div class="zeile"><span class="zeit">{zeit_label(f.von, d)}–{f.bis:%H:%M}</span>'
                    f'<span class="score">{f.sterne} Score {f.score}, {E(f.bewertung)}</span>'
                    f'<span class="merkmale">Horā {E(i["hora"])}, {E(i["nakshatra"])}, '
                    f'Tārā {E(i["tara"])}, Lagna {E(i["lagna"])}</span>'
                    f'<a class="detail" href="{E(slot_url(p, f.von))}">Muhūrta-Chart</a></div>'
                    f'<details><summary>Faktoren</summary><ul>{faktoren_liste(f.faktoren)}</ul></details></li>')
            liste = f'<ul class="fenster">{"".join(items)}</ul>'
        else:
            liste = '<p class="leer">Kein Zeitfenster erreicht den Mindestscore.</p>'
        teile.append(f'<section class="tag">{kopf}{leiste}{liste}</section>')
        d += timedelta(days=1)
    return "".join(teile)

# ---------------------------------------------------------------------------
# Muhūrta-Chart (SVG, Nord- und Südindisch) — nur Darstellung, Daten aus der Engine
# ---------------------------------------------------------------------------

KURZ = {"Ascendant": "La", "Sun": "So", "Moon": "Mo", "Mars": "Ma", "Mercury": "Me",
        "Jupiter": "Ju", "Venus": "Ve", "Saturn": "Sa", "Rahu": "Ra", "Ketu": "Ke"}

# Südindisch: Zeichen → (Zeile, Spalte), fest
SUED_ZELLE = {11: (0, 0), 0: (0, 1), 1: (0, 2), 2: (0, 3), 10: (1, 0), 3: (1, 3),
              9: (2, 0), 4: (2, 3), 8: (3, 0), 7: (3, 1), 6: (3, 2), 5: (3, 3)}

# Nordindisch: Haus → Textmitte und Position der Zeichennummer (Feld 400×400)
NORD_MITTE = {1: (200, 92), 2: (100, 34), 3: (38, 100), 4: (100, 200), 5: (38, 300),
              6: (100, 362), 7: (200, 304), 8: (300, 362), 9: (362, 300), 10: (300, 200),
              11: (362, 100), 12: (300, 34)}
NORD_NUMMER = {1: (200, 182), 2: (100, 88), 3: (86, 104), 4: (180, 204), 5: (86, 304),
               6: (100, 322), 7: (200, 226), 8: (300, 322), 9: (314, 304), 10: (220, 204),
               11: (314, 104), 12: (300, 88)}

RASHI_KURZ = ["Meṣa", "Vṛṣa", "Mith", "Karka", "Siṃha", "Kanyā",
              "Tulā", "Vṛśc", "Dhanu", "Makara", "Kumbha", "Mīna"]


def belegung(lons: dict, retro: dict) -> dict[int, list[tuple[str, str]]]:
    """Zeichen → [(Beschriftung, Klasse)], Lagna zuerst."""
    out: dict[int, list[tuple[str, str]]] = {}
    for g in ["Ascendant"] + mt.GRAHAS:
        l = lons[g]
        r = "R" if retro.get(g) else ""
        out.setdefault(int(l // 30) % 12, []).append(
            (f"{KURZ[g]}{r} {int(l % 30)}°", "la" if g == "Ascendant" else "gr"))
    return out


def _texte(x: float, y: float, eintraege: list, zeilenhoehe: float = 15) -> str:
    start = y - (len(eintraege) - 1) * zeilenhoehe / 2
    return "".join(f'<text x="{x}" y="{start + i * zeilenhoehe:.1f}" class="{k}" '
                   f'text-anchor="middle" dominant-baseline="middle">{E(txt)}</text>'
                   for i, (txt, k) in enumerate(eintraege))


def chart_sued(bel: dict, lagna: int, mitte: list[str]) -> str:
    teile = ['<svg class="chart-svg sued" viewBox="0 0 400 400" role="img" '
             'aria-label="Muhūrta-Chart südindisch">']
    for zeichen, (r, c) in SUED_ZELLE.items():
        x, y = c * 100, r * 100
        teile.append(f'<rect x="{x}" y="{y}" width="100" height="100" class="feld"/>')
        teile.append(f'<text x="{x + 6}" y="{y + 14}" class="zeichen">{E(RASHI_KURZ[zeichen])}</text>')
        if zeichen == lagna:
            teile.append(f'<line x1="{x + 78}" y1="{y}" x2="{x + 100}" y2="{y + 22}" class="lagna-strich"/>')
        teile.append(_texte(x + 50, y + 58, bel.get(zeichen, [])))
    teile.append('<rect x="100" y="100" width="200" height="200" class="mitte"/>')
    teile.append(_texte(200, 200, [(z, "mitte-text") for z in mitte], 20))
    teile.append("</svg>")
    return "".join(teile)


def chart_nord(bel: dict, lagna: int) -> str:
    teile = ['<svg class="chart-svg nord" viewBox="0 0 400 400" role="img" '
             'aria-label="Muhūrta-Chart nordindisch">',
             '<rect x="0" y="0" width="400" height="400" class="feld"/>',
             '<path d="M0 0L400 400M400 0L0 400M200 0L400 200L200 400L0 200Z" class="linie"/>']
    for haus in range(1, 13):
        zeichen = (lagna + haus - 1) % 12
        nx, ny = NORD_NUMMER[haus]
        teile.append(f'<text x="{nx}" y="{ny}" class="nummer" text-anchor="middle" '
                     f'dominant-baseline="middle">{zeichen + 1}</text>')
        mx, my = NORD_MITTE[haus]
        teile.append(_texte(mx, my, bel.get(zeichen, []), 14))
    teile.append("</svg>")
    return "".join(teile)


CHART_SKRIPT = """<script>
(function(){
  var box=document.querySelector('.chart');if(!box)return;
  var stil='nord';try{stil=localStorage.getItem('muhurta_chart')||'nord';}catch(e){}
  function setze(s){box.setAttribute('data-stil',s);
    document.querySelectorAll('.chart-wahl button').forEach(function(b){
      b.setAttribute('aria-pressed',b.dataset.stil===s?'true':'false');});
    try{localStorage.setItem('muhurta_chart',s);}catch(e){}}
  document.querySelectorAll('.chart-wahl button').forEach(function(b){
    b.addEventListener('click',function(){setze(b.dataset.stil);});});
  setze(stil);
})();
</script>"""


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


def phase_html(ph: dict, titel: str) -> str:
    klasse = "gut" if ph["summe"] >= 3 else "vorsicht" if ph["summe"] <= -2 else "mittel"
    punkte = "".join(f'<li class="{"plus" if pk > 0 else ""}">{E(t)} ({pk:+d})</li>'
                     for pk, t in ph["zeilen"])
    return (f'<details class="phase {klasse}"><summary><strong>Phase: {E(ph["urteil"])}</strong> '
            f'({ph["summe"]:+d}) <span class="leise">{E(titel)}</span></summary>'
            f'<ul>{punkte}</ul><p class="leise">Daśā und Transit gelten für den ganzen Zeitraum gleich '
            f'und fliessen deshalb nicht in den Score der Zeitpunkte ein.</p></details>')


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
    h = p["h"]
    ph = mt.phase_bewerten(radix, mt.jd_aus_dt(datetime.combine(p["von_d"], time(12, 0),
                                                                tzinfo=kalender.tz)),
                           h["lat"], h["lon"])
    inhalt += phase_html(ph, f"Daśā Stand heute, Transit am {p['von_d']:%d.%m.%Y}")
    inhalt += formular(p) + LEGENDE + tage_html(p, kalender, fenster, protokoll)
    return seite("Muhūrta für Trades", inhalt, p["h"]["label"])


@app.get("/slot", response_class=HTMLResponse)
def slot(request: Request):
    """Muhūrta-Detail eines Zeitpunkts mit Chart, Pañcāṅga, Faktoren und Grahas."""
    if not zugang_ok(request):
        return RedirectResponse("/login", status_code=303)
    q = request.query_params
    p, fehler = parameter(q)
    tz = ZoneInfo(p["h"]["iana"]) if "h" in p else ZoneInfo(TZ_NAME)
    zeit = (q.get("zeit") or "").replace("T", " ")
    try:
        dt = datetime.strptime(zeit, "%Y-%m-%d %H:%M").replace(tzinfo=tz)
    except ValueError:
        fehler.append("Zeitpunkt im Format JJJJ-MM-TT HH:MM angeben.")
    zurueck_von = mt.handelstag(dt, p["markt"]).isoformat() if not fehler else p["von"]
    zurueck = url("/", p, von=zurueck_von, tage="5")
    kopf = f'<p class="radix"><a href="{E(zurueck)}">Zurück zur Übersicht</a></p>'
    if fehler:
        return seite("Muhūrta-Detail", kopf + '<div class="fehler">'
                     + "".join(f"<p>{E(x)}</p>" for x in fehler) + "</div>")

    g, h = p["g"], p["h"]
    try:
        radix = radix_cache(p["geburt"], g["lat"], g["lon"], g["offset"], g["label"])
        kalender = mt.Tageskalender(h["lat"], h["lon"], tz)
        jd = mt.jd_aus_dt(dt)
        tag = kalender.fuer_jd(jd)
        bew = mt.bewerten(jd, tag, radix, p["modus"], h["lat"], h["lon"])
        try:
            retro = mt.ae._retro_flags(jd)
        except Exception:
            retro = {}
        # Tagesleiste zur Navigation
        hd = mt.handelstag(dt, p["markt"])
        tag_start = datetime.combine(hd - timedelta(days=1), time(0, 0), tzinfo=tz)
        tag_ende = datetime.combine(hd + timedelta(days=2), time(0, 0), tzinfo=tz)
        protokoll: list = []
        fenster = mt.fenster_berechnen(tag_start, tag_ende, p["schritt_i"], radix, p["modus"],
                                       p["markt"], p["min_i"], kalender, h["lat"], h["lon"],
                                       protokoll)
        protokoll = [x for x in protokoll if mt.handelstag(x[0], p["markt"]) == hd]
        fenster = [f for f in fenster if f.tag == hd]
        ende_tithi = mt.element_ende(jd, mt._idx_tithi)
        ende_nak = mt.element_ende(jd, mt._idx_nak)
        ende_yoga = mt.element_ende(jd, mt._idx_yoga)
        ende_karana = mt.element_ende(jd, mt._idx_karana)
    except Exception as exc:
        return seite("Muhūrta-Detail", kopf + f'<div class="fehler"><p>Berechnung fehlgeschlagen: '
                     f'{E(str(exc))}</p></div>')

    z = lambda x: f"{mt.dt_aus_jd(x, tz):%H:%M}"
    ztag = lambda x: (f"{mt.dt_aus_jd(x, tz):%H:%M}" if mt.dt_aus_jd(x, tz).date() == dt.date()
                      else f"{mt.dt_aus_jd(x, tz):%d.%m. %H:%M}")
    lons = bew.lons
    lagna = int(lons["Ascendant"] // 30) % 12
    i = bew.info
    schritt = timedelta(minutes=p["schritt_i"])
    tag_d = mt.dt_aus_jd(tag.aufgang, tz).date()

    # Status
    if bew.gesperrt:
        status = (f'<p class="status gesperrt-text">Gesperrt: {E(", ".join(bew.sperren))}. '
                  f'Score ohne Sperre: {bew.score}.</p>')
    else:
        stufe = mt.Fenster(dt, dt + schritt, bew.score, bew.faktoren, i)
        unter = "" if bew.score >= p["min_i"] else f" Unter dem Mindestscore {p['min_i']}."
        status = (f'<p class="status"><span class="score">{stufe.sterne} Score {bew.score}, '
                  f'{E(stufe.bewertung)}</span>{E(unter)}</p>')
    if not mt.im_markt(dt, p["markt"]):
        status += f'<p class="leer">Ausserhalb der Handelszeit von {E(MARKT_NAMEN[p["markt"]])}.</p>'

    # Tagesleiste
    beste = sorted(fenster, key=lambda f: (-f.score, f.von))[:3]
    aktuell = next((t for t, _ in protokoll if t <= dt < t + schritt), None)
    leiste = leiste_html(p, protokoll, beste, aktuell) if protokoll else ""

    # Chart
    bel = belegung(lons, retro)
    mitte = ["Muhūrta", f"{dt:%d.%m.%Y}", f"{dt:%H:%M}", h["label"].split(",")[0]]
    chart = (f'<div class="chart-wahl" role="group" aria-label="Chart-Stil">'
             f'<button type="button" data-stil="nord" aria-pressed="true">Nordindisch</button>'
             f'<button type="button" data-stil="sued" aria-pressed="false">Südindisch</button></div>'
             f'<div class="chart" data-stil="nord">{chart_nord(bel, lagna)}'
             f'{chart_sued(bel, lagna, mitte)}</div>')

    # Pañcāṅga
    hora_bis = ""
    if not mt.HORA_PROPORTIONAL:
        n = int((jd - tag.aufgang) * 24.0)
        hora_bis = f", bis {z(tag.aufgang + (n + 1) / 24.0)}"
    rk = tag.achtel_zeit(mt.RAHU_KALA[tag.wochentag])
    yg = tag.achtel_zeit(mt.YAMAGANDA[tag.wochentag])
    gk = tag.achtel_zeit(mt.GULIKA[tag.wochentag])
    ab = tag.abhijit()
    abh = " (mittwochs nicht verwendet)" if tag.wochentag == mt.MITTWOCH else ""
    asc = lons["Ascendant"]
    panchanga = f"""<dl class="panchanga">
<dt>Tithi</dt><dd>{E(i['tithi'])}, bis {ztag(ende_tithi)}</dd>
<dt>Vāra</dt><dd>{E(mt.VARA[tag.wochentag])} ({E(mt.WOCHENTAG[tag.wochentag])})</dd>
<dt>Nakṣatra</dt><dd>{E(i['nakshatra'])}, bis {ztag(ende_nak)}</dd>
<dt>Yoga</dt><dd>{E(i['yoga'])}, bis {ztag(ende_yoga)}</dd>
<dt>Karaṇa</dt><dd>{E(i['karana'])}, bis {ztag(ende_karana)}</dd>
<dt>Horā</dt><dd>{E(i['hora'])}{hora_bis}</dd>
<dt>Muhūrta-Lagna</dt><dd>{E(mt.RASHI[lagna])} {int(asc % 30)}°{int((asc % 1) * 60):02d}′</dd>
<dt>Tārā</dt><dd>{E(i['tara'])} (vom Janma-Nakṣatra {E(mt.NAKSHATRA[radix.mond_nak])})</dd>
<dt>Candrabala</dt><dd>Mond im {E(i['chandrabala'])} vom Janma-Mond</dd>
<dt>Sonne</dt><dd>Aufgang {z(tag.aufgang)} ({tag_d:%d.%m.}), Untergang {z(tag.untergang)}</dd>
<dt>Rāhu Kāla</dt><dd>{z(rk[0])}–{z(rk[1])}</dd>
<dt>Yamagaṇḍa</dt><dd>{z(yg[0])}–{z(yg[1])}</dd>
<dt>Gulika Kāla</dt><dd>{z(gk[0])}–{z(gk[1])}</dd>
<dt>Abhijit</dt><dd>{z(ab[0])}–{z(ab[1])}{abh}</dd>
</dl>"""

    # Grahas
    zeilen = []
    for gname in ["Ascendant"] + mt.GRAHAS:
        l = lons[gname]
        zeichen = int(l // 30) % 12
        nak, _, pada = mt.ae.nakshatra_of(l)
        r = " R" if retro.get(gname) else ""
        h_muh = (zeichen - lagna) % 12 + 1
        h_janma = (zeichen - radix.lagna_rashi) % 12 + 1
        h_mond = (zeichen - radix.mond_rashi) % 12 + 1
        zeilen.append(
            f"<tr><td>{E(mt.PLANET_DE[gname])}{r}</td><td>{E(mt.RASHI[zeichen])}</td>"
            f"<td>{int(l % 30)}°{int((l % 1) * 60):02d}′</td>"
            f"<td>{E(mt.NAKSHATRA[mt.ae.nak_index(nak)])} {pada}</td>"
            f"<td>{h_muh}</td><td>{h_janma}</td><td>{h_mond}</td></tr>")
    grahas = ('<div class="tabelle"><table class="grahas"><thead><tr><th>Graha</th><th>Zeichen</th>'
              '<th>Grad</th><th>Nakṣatra</th><th>Haus vom Muhūrta-Lagna</th>'
              '<th>vom Janma-Lagna</th><th>vom Janma-Mond</th></tr></thead><tbody>'
              + "".join(zeilen) + "</tbody></table></div>")

    faktoren = (f'<ul class="faktoren">{faktoren_liste(bew.faktoren)}</ul>'
                if bew.faktoren else '<p class="leer">Keine wertenden Faktoren.</p>')

    vor = slot_url(p, dt - schritt)
    nach = slot_url(p, dt + schritt)
    roh = url("/abgleich", p, zeit=f"{dt:%Y-%m-%d %H:%M}")
    navigation = (f'<nav class="navigation" aria-label="Zeitpunkt wechseln">'
                  f'<a href="{E(vor)}">{(dt - schritt):%H:%M} früher</a>'
                  f'<a href="{E(roh)}">Rohdaten zum Abgleich</a>'
                  f'<a href="{E(nach)}">{(dt + schritt):%H:%M} später</a></nav>')

    inhalt = (kopf + f'<h1>{E(datum_lang(dt.date(), wochentag_so0(dt.date())))}, {dt:%H:%M}</h1>'
              + status
              + phase_html(mt.phase_bewerten(radix, jd, h["lat"], h["lon"]),
                           f"Daśā Stand heute, Transit am {dt:%d.%m.%Y}")
              + leiste
              + f'<div class="detail-raster"><div>{chart}</div><div><h2>Pañcāṅga</h2>{panchanga}'
              + f'<h2 class="abstand">Faktoren ({E(p["modus"])})</h2>{faktoren}</div></div>'
              + f'<h2>Grahas</h2>{grahas}{navigation}{CHART_SKRIPT}')
    return seite("Muhūrta-Detail", inhalt, h["label"])


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
        "fenster": [{"handelstag": f.tag.isoformat(), "von": f.von.isoformat(), "bis": f.bis.isoformat(), "score": f.score,
                     "bewertung": f.bewertung, **f.info,
                     "faktoren": [{"punkte": pk, "text": t} for pk, t in f.faktoren]}
                    for f in fenster],
    }
