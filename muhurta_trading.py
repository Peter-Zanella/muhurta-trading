#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
muhurta_trading.py — Muhūrta-Zeitfenster für Trades nach individuellem Geburtshoroskop

Rechenquelle ist ausschliesslich astro_engine.py (AstroVeda):
  - Radix über generate_chart()  → identisch mit dem Report
  - Positionen über compute_positions(), Pañcāṅga über compute_panchang()
  - Sonnenauf-/untergang über _sun_rise_set() (Hindu-Aufgang wie im Report)
  - Horā wie in der Engine: gleiche 60-Minuten-Horās ab Sonnenaufgang
Dieses Skript enthält nur die Muhūrta-Bewertung (Kraya/Vikraya) und die Ausgabe.

Eigenes Repository. astro_engine.py wird NICHT kopiert, sondern aus dem
Repo «Ved Chart Calc» geholt (GitHub Actions: zweiter Checkout nach ./engine).
Suchreihenfolge: ASTRO_ENGINE_PATH → ./engine → Skriptverzeichnis.

Aufruf:
  python muhurta_trading.py --geburt "1957-08-24 13:55" --geburt-tz Europe/Zurich \
      --geburt-lat 47.4833 --geburt-lon 7.7356 --modus kauf --markt SIX --tage 7

Abgleich (Detailwerte zum Vergleich mit Report / Prokerala / Kala):
  python muhurta_trading.py ... --abgleich "2026-10-07 14:45"

Geburtsort als Name (Geocoding wie Ved Chart Calc):
  python muhurta_trading.py --geburt "1957-08-24 13:55" --geburt-ort "Liestal, Schweiz"

Geburtsdaten auch über Umgebungsvariablen (GitHub Secrets):
  GEBURT, GEBURT_ORT  oder  GEBURT, GEBURT_TZ, GEBURT_LAT, GEBURT_LON
"""

from __future__ import annotations

import argparse
import csv
import os
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

_HIER = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [p for p in (os.environ.get("ASTRO_ENGINE_PATH"),
                            os.path.join(_HIER, "engine"), _HIER) if p]
try:
    import astro_engine as ae
except ImportError:
    sys.exit("astro_engine.py nicht gefunden – ASTRO_ENGINE_PATH setzen oder "
             "Ved Chart Calc nach ./engine auschecken.")
if not ae._SWE:
    sys.exit("pyswisseph fehlt – Muhūrta braucht Swiss Ephemeris "
             "(Sonnenaufgang, Rāhu Kāla, Lagna im Minutentakt).")
swe = ae.swe

# ---------------------------------------------------------------------------
# Konfiguration
# ---------------------------------------------------------------------------

# False = gleiche 60-Minuten-Horās ab Sonnenaufgang (wie astro_engine / Varshaphala)
# True  = Tag- und Nachtbogen je in 12 gleiche Teile (proportionale Horās)
HORA_PROPORTIONAL = False

STANDARD_LAT = 47.2296   # Handelsort Wädenswil
STANDARD_LON = 8.6716
STANDARD_TZ = "Europe/Zurich"

# ---------------------------------------------------------------------------
# Anzeige-Namen (Indizes wie astro_engine; Wochentag 0 = Sonntag)
# ---------------------------------------------------------------------------

RASHI = ["Meṣa", "Vṛṣabha", "Mithuna", "Karka", "Siṃha", "Kanyā",
         "Tulā", "Vṛścika", "Dhanu", "Makara", "Kumbha", "Mīna"]

NAKSHATRA = ["Aśvinī", "Bharaṇī", "Kṛttikā", "Rohiṇī", "Mṛgaśirā", "Ārdrā",
             "Punarvasu", "Puṣya", "Āśleṣā", "Maghā", "Pūrvaphalgunī",
             "Uttaraphalgunī", "Hasta", "Citrā", "Svātī", "Viśākhā",
             "Anurādhā", "Jyeṣṭhā", "Mūla", "Pūrvāṣāḍhā", "Uttarāṣāḍhā",
             "Śravaṇa", "Dhaniṣṭhā", "Śatabhiṣā", "Pūrvabhādrapadā",
             "Uttarabhādrapadā", "Revatī"]

YOGA = ["Viṣkambha", "Prīti", "Āyuṣmān", "Saubhāgya", "Śobhana", "Atigaṇḍa",
        "Sukarma", "Dhṛti", "Śūla", "Gaṇḍa", "Vṛddhi", "Dhruva", "Vyāghāta",
        "Harṣaṇa", "Vajra", "Siddhi", "Vyatīpāta", "Varīyān", "Parigha",
        "Śiva", "Siddha", "Sādhya", "Śubha", "Śukla", "Brahma", "Indra",
        "Vaidhṛti"]

TITHI = ["Pratipadā", "Dvitīyā", "Tṛtīyā", "Caturthī", "Pañcamī", "Ṣaṣṭhī",
         "Saptamī", "Aṣṭamī", "Navamī", "Daśamī", "Ekādaśī", "Dvādaśī",
         "Trayodaśī", "Caturdaśī"]

KARANA_DE = {"Bava": "Bava", "Balava": "Bālava", "Kaulava": "Kaulava",
             "Taitila": "Taitila", "Gara": "Gara", "Vanija": "Vaṇija",
             "Vishti": "Viṣṭi", "Kimstughna": "Kiṃstughna", "Shakuni": "Śakuni",
             "Chatushpada": "Catuṣpada", "Naga": "Nāga"}

TARA = ["Janma", "Sampat", "Vipat", "Kṣema", "Pratyari", "Sādhaka",
        "Vadha", "Mitra", "Parama Mitra"]

WOCHENTAG = ["Sonntag", "Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag"]
VARA = ["Ravivāra", "Somavāra", "Maṅgalavāra", "Budhavāra", "Guruvāra", "Śukravāra", "Śanivāra"]

PLANET_DE = {"Sun": "Sonne", "Moon": "Mond", "Mars": "Mars", "Mercury": "Merkur",
             "Jupiter": "Jupiter", "Venus": "Venus", "Saturn": "Saturn",
             "Rahu": "Rāhu", "Ketu": "Ketu", "Ascendant": "Lagna"}

GRAHAS = ["Sun", "Moon", "Mars", "Mercury", "Jupiter", "Venus", "Saturn", "Rahu", "Ketu"]

# Tagesachtel (1–8), Index 0 = Sonntag
RAHU_KALA = [8, 2, 7, 5, 6, 4, 3]
YAMAGANDA = [5, 4, 3, 2, 1, 7, 6]
GULIKA = [7, 6, 5, 4, 3, 2, 1]
MITTWOCH = 3

# ---------------------------------------------------------------------------
# Bewertungsregeln (Punkte) — Trading-Regelwerk
#   Schwerpunkte: Merkur, Mond, 2./5./11. Haus, Lagna und Lagna-Herr
#   Klassische Basis: Muhūrta Cintāmaṇi (Kraya/Vikraya), Pañcāṅga, Tārā/Candrabala
# ---------------------------------------------------------------------------

# Nakṣatras für Trading (beide Modi)
NAK_TRADING = {0, 4, 6, 12, 13, 14, 16, 21, 22, 26}
#   Aśvinī, Mṛgaśirā, Punarvasu, Hasta, Citrā, Svātī, Anurādhā, Śravaṇa, Dhaniṣṭhā, Revatī
SVATI = 14
# Muhūrta Cintāmaṇi: Kauf (kraya) / Verkauf (vikraya)
NAK_KAUF_MC = {0, 13, 14, 21, 23, 26}       # Aśvinī, Citrā, Svātī, Śravaṇa, Śatabhiṣā, Revatī
NAK_VERKAUF_MC = {1, 2, 8, 10, 19, 24}      # Bharaṇī, Kṛttikā, Āśleṣā, die drei Pūrvas
P_NAK_TRADING = 3
P_NAK_MC = 2                                # MC-Nakṣatra des Modus (sofern nicht schon Trading-Nakṣatra)
P_NAK_KAUF_VERBOTEN = -2                    # Kauf in einem Vikraya-Nakṣatra

# ---------------------------------------------------------------------------
# Verkaufsprofil (Exit / Gewinnmitnahme bei Aktien und ETFs)
#   Pflicht: starkes 11. Haus/-Herr, stabiles 2. Haus, starker Lagna-Herr
#   Sehr wichtig: Merkur stark, Mond nicht in 6/8/12
#   Gut: Jupiter-Bezug zu 2/5/11, abnehmender Mond, Merkur-/Jupiter-/Venus-Horā
#   Radix-Herren von 1, 2, 5, 11 wiegen mehr als ein einzelnes Nakṣatra
# ---------------------------------------------------------------------------
NAK_VERKAUF = {12, 14, 16, 21, 22, 26}      # Hasta, Svātī, Anurādhā, Śravaṇa, Dhaniṣṭhā, Revatī
P_NAK_VERKAUF = 2
P_NAK_VERKAUF_MC = 1
VERKAUF_PFLICHT_SPERRE = True               # Pflicht stark verfehlt (≥ 2 Belastungen) → gesperrt
P_VERKAUF_PFLICHT = -4                      # falls Sperre ausgeschaltet
P_VERKAUF_PFLICHT_EINE = -3                 # genau eine Belastung: abwerten statt sperren
PFLICHT_GRENZE = 2                          # ab so vielen Belastungen gilt «stark beschädigt»
P_KRSNA_VERKAUF = 1                         # abnehmender Mond passt zum Exit
P_MOND_6_12_VERKAUF = -3
P_LAGNAHERR_GUT_VERKAUF = 2
P_2_REIN_VERKAUF = 2
P_MARS_IN_11_VERKAUF = -2                   # aggressiver Mars auf das 11. Haus
P_MARS_ASPEKT_2_11_VERKAUF = -1             # Mars-Aspekt auf 2. oder 11. Haus (zusätzlich)
P_RADIXHERR_VERKAUF = 2                     # Radix-Herren 1/2/5/11: gut +2, in 6/8/12 −2
JUPITER_HAEUSER_KAUF = {2, 5, 9, 11}
JUPITER_HAEUSER_VERKAUF = {2, 5, 11}

GUTE_TITHI = {2, 3, 5, 7, 10, 11, 13}      # Pakṣa-Tithi
RIKTA_TITHI = {4, 9, 14}

# Vāra: Mittwoch am besten, dann Donnerstag, Freitag; Dienstag/Samstag meiden
VARA_PUNKTE = [0, 0, -2, 3, 2, 1, -2]       # So, Mo, Di, Mi, Do, Fr, Sa

# Dagdha-Tithi je Vāra (Pakṣa-Tithi) und Amṛta-Siddhi (Vāra + Nakṣatra)
DAGDHA_TITHI = [12, 11, 5, 3, 6, 8, 9]       # So … Sa
AMRITA_SIDDHI = [12, 4, 0, 16, 7, 26, 3]     # So Hasta, Mo Mṛgaśirā, Di Aśvinī, Mi Anurādhā,
                                             # Do Puṣya, Fr Revatī, Sa Rohiṇī

YOGA_SPERRE = {16, 26}                      # Vyatīpāta, Vaidhṛti
YOGA_UNGUENSTIG = {0, 5, 8, 9, 12, 14, 18}  # Viṣkambha, Atigaṇḍa, Śūla, Gaṇḍa, Vyāghāta, Vajra, Parigha

TARA_GUT = {1, 3, 5, 7, 8}
TARA_SCHLECHT = {2, 4}
TARA_SPERRE = {6}                           # Vadha / Naidhana

CHANDRA_GUT = {1, 3, 6, 7, 10, 11}
CHANDRA_SCHLECHT = {4, 12}

HORA_PUNKTE = {"Mercury": 3, "Jupiter": 2, "Venus": 2, "Moon": 1,
               "Sun": 0, "Mars": -2, "Saturn": -2}

# Durmuhūrta (15 Muhūrtas je Tag-/Nachtbogen), Index 0 = Sonntag
DURMUHURTA_TAG = [{14}, {9, 12}, {4}, {8}, {6, 12}, {4, 9}, {1, 2}]
DURMUHURTA_NACHT = [set(), set(), {7}, set(), set(), set(), set()]
P_YAMAGANDA = -3
P_GULIKA = -2
P_KALA_VELA = -2

WOHLTAETER = {"Mercury", "Jupiter", "Venus"}
UEBELTAETER = {"Sun", "Mars", "Saturn", "Rahu", "Ketu"}
HARTE_UEBELTAETER = {"Mars", "Saturn", "Rahu", "Ketu"}
KENDRA_TRIKONA = {1, 4, 5, 7, 9, 10}
DUSTHANA = {6, 8, 12}

# Lagna: keine Übeltäter; Lagna-Herr stark
LAGNA_UEBEL_SPERRE = True      # Mars/Saturn/Rāhu/Ketu im Lagna sperrt
LAGNA_UEBEL_BESETZT = -3       # falls Sperre ausgeschaltet
LAGNA_SONNE_BESETZT = -1

# Merkur (Handel): Häuser vom Muhūrta-Lagna
MERKUR_GUTE_HAEUSER = {1, 2, 5, 10, 11}
P_MERKUR_GUT = 2
P_MERKUR_DUSTHANA = -2
P_MERKUR_RETRO = -2
P_MERKUR_VERBRANNT = -2
P_MERKUR_UEBEL_KONJ = -1
P_MERKUR_RADIX_8 = -1          # Zusatz, wenn Merkur im Radix das 8. Haus beherrscht
P_SVATI_MERKUR = 1             # Svātī mit sauberem Merkur

# Mond
P_SUKLA_KAUF = 1               # zunehmender Mond nur beim Kauf belohnt
P_MOND_6_12 = -2               # Mond im 6./12. vom Muhūrta-Lagna (8. = Sperre)
P_MOND_KNOTEN = -3             # Mond nahe Rāhu/Ketu
MOND_KNOTEN_ORB = 8.0

# 2. und 5. Haus geschützt
GESCHUETZTE_HAEUSER = {2: "2. Haus (Kapital)", 5: "5. Haus (Spekulation)"}
HAUS_UEBEL_BESETZT = -2
HAUS_SONNE_BESETZT = -1
HAUS_UEBEL_ASPEKT = -1
HAUS_HERR_DUSTHANA = -1
HAUS_REIN = 1
P_RAHU_5_SCHWACH = -3          # Rāhu im 5. bei schwachem 5. Herrn (Gier, Übertreibung)
P_RAHU_5_KONTROLLIERT = -1     # Rāhu im 5. bei gut gestelltem 5. Herrn

# 11. Haus (Gewinne) stark — beim Verkauf doppelt gewichtet
P_11_HERR_GUT = 1
P_11_HERR_DUSTHANA = -2
P_11_WOHLTAETER = 1
P_11_RAHU = 1

# Jupiter / Venus
P_JUPITER_IN = 2               # Jupiter im 2./5./9./11.
P_JUPITER_ASPEKT = 1           # Jupiter aspektiert 2./5./9./11.
P_VENUS_2_11 = 1

# Lagna-Herr
P_LAGNAHERR_GUT = 1            # in Kendra/Trikoṇa oder 11.
P_LAGNAHERR_DUSTHANA = -2
P_LAGNAHERR_UEBEL_KONJ = -1
P_LAGNAHERR_VERBRANNT = -1

# Radix-Schlüsselplaneten (Herren von Janma-Lagna und 5. Haus) im Muhūrta
P_RADIXHERR_GUT = 1
P_RADIXHERR_DUSTHANA = -1

# Handelszeiten: (Zeitzone, Öffnung, Schluss, nur Werktage)
MAERKTE = {
    "SIX": ("Europe/Zurich", time(9, 0), time(17, 30), True),
    "XETRA": ("Europe/Berlin", time(9, 0), time(17, 30), True),
    "LSE": ("Europe/London", time(8, 0), time(16, 30), True),
    "NYSE": ("America/New_York", time(9, 30), time(16, 0), True),
    "KRYPTO": (None, None, None, False),
    "ALLE": (None, None, None, False),
}

# ---------------------------------------------------------------------------
# Zeit
# ---------------------------------------------------------------------------

def jd_aus_dt(dt: datetime) -> float:
    u = dt.astimezone(timezone.utc)
    return ae.get_jd(u.year, u.month, u.day,
                     u.hour + u.minute / 60.0 + u.second / 3600.0)


def dt_aus_jd(jd: float, tz: ZoneInfo) -> datetime:
    y, m, d, h = swe.revjul(jd)
    basis = datetime(y, m, d, tzinfo=timezone.utc)
    return (basis + timedelta(seconds=round(h * 3600))).astimezone(tz)


def wochentag_so0(d: date) -> int:
    return (d.weekday() + 1) % 7

# ---------------------------------------------------------------------------
# Vedischer Tag (Sonnenaufgang bis Sonnenaufgang) — Zeiten aus astro_engine
# ---------------------------------------------------------------------------

@dataclass
class Tag:
    datum: date
    aufgang: float
    untergang: float
    naechster_aufgang: float
    wochentag: int          # 0 = Sonntag (Konvention astro_engine.vedic_day)

    def achtel(self, jd: float) -> int | None:
        if not (self.aufgang <= jd < self.untergang):
            return None
        return int((jd - self.aufgang) / ((self.untergang - self.aufgang) / 8)) + 1

    def achtel_zeit(self, nr: int) -> tuple[float, float]:
        teil = (self.untergang - self.aufgang) / 8
        return self.aufgang + (nr - 1) * teil, self.aufgang + nr * teil

    def abhijit(self) -> tuple[float, float]:
        m = (self.untergang - self.aufgang) / 15
        return self.aufgang + 7 * m, self.aufgang + 8 * m

    def ist_abhijit(self, jd: float) -> bool:
        a, e = self.abhijit()
        return a <= jd < e

    def muhurta_nr(self, jd: float) -> tuple[str, int]:
        """15 Muhūrtas je Tag- und Nachtbogen → ('tag'|'nacht', 1..15)."""
        if self.aufgang <= jd < self.untergang:
            return "tag", int((jd - self.aufgang) / ((self.untergang - self.aufgang) / 15)) + 1
        n = int((jd - self.untergang) / ((self.naechster_aufgang - self.untergang) / 15)) + 1
        return "nacht", max(1, min(n, 15))

    def kala_vela_achtel(self) -> int:
        """Sonnenabschnitt des Tagbogens (Folge ab Wochentagsherr, wie Upagraha Kāla der Engine)."""
        return (0 - self.wochentag) % 7 + 1

    def hora_herr(self, jd: float) -> str:
        if HORA_PROPORTIONAL:
            if jd < self.untergang:
                n = int((jd - self.aufgang) / ((self.untergang - self.aufgang) / 12))
            else:
                n = 12 + int((jd - self.untergang)
                             / ((self.naechster_aufgang - self.untergang) / 12))
        else:
            n = int((jd - self.aufgang) * 24.0)
        reihe = ae._HORA_ORDER
        start = reihe.index(ae._WEEKDAY_LORDS[self.wochentag])
        return reihe[(start + max(n, 0)) % 7]


class Tageskalender:
    def __init__(self, lat: float, lon: float, tz: ZoneInfo):
        self.lat, self.lon, self.tz = lat, lon, tz
        self._cache: dict[date, Tag] = {}

    def _ereignis(self, jd: float, art: int) -> float:
        t = ae._sun_rise_set(jd, self.lat, self.lon, art)
        if t is None:
            raise RuntimeError("Sonnenauf-/untergang nicht berechenbar (Polarregion?)")
        return t

    def fuer_datum(self, d: date) -> Tag:
        if d not in self._cache:
            jd0 = jd_aus_dt(datetime.combine(d, time(0, 0), tzinfo=self.tz))
            auf = self._ereignis(jd0, swe.CALC_RISE)
            unter = self._ereignis(auf, swe.CALC_SET)
            naechst = self._ereignis(unter, swe.CALC_RISE)
            wt = int(auf + self.lon / 360.0 + 1.5) % 7   # wie astro_engine.vedic_day
            self._cache[d] = Tag(d, auf, unter, naechst, wt)
        return self._cache[d]

    def fuer_jd(self, jd: float) -> Tag:
        d = dt_aus_jd(jd, self.tz).date()
        tag = self.fuer_datum(d)
        if jd < tag.aufgang:
            tag = self.fuer_datum(d - timedelta(days=1))
        return tag

# ---------------------------------------------------------------------------
# Radix über astro_engine.generate_chart
# ---------------------------------------------------------------------------

@dataclass
class Radix:
    chart: dict

    @property
    def mond_rashi(self) -> int:
        return self.chart["planets"]["Moon"]["sign_idx"]

    @property
    def mond_nak(self) -> int:
        return ae.nak_index(self.chart["planets"]["Moon"]["nakshatra"])

    @property
    def lagna_rashi(self) -> int:
        return self.chart["lagna_idx"]

    def herren(self, haeuser: set[int]) -> list[str]:
        """Radix-Herren der angegebenen Häuser, ohne Doppelungen."""
        return [p for p, h in self.chart["lordships"].items() if set(h) & haeuser]

    @property
    def schluesselplaneten(self) -> list[str]:
        """Herren von Janma-Lagna und 5. Haus (Kauf)."""
        return self.herren({1, 5})

    @property
    def wohlstandsherren(self) -> set[str]:
        """Herren des 2. und 11. Hauses (engine: compute_lordships)."""
        return {p for p, h in self.chart["lordships"].items() if 2 in h or 11 in h}


def radix_berechnen(geburt: str, iana: str | None, lat: float, lon: float,
                    offset: float | None = None, ort: str = "Geburtsort") -> Radix:
    """Radix über astro_engine.generate_chart. Offset direkt oder aus IANA-Zeitzone."""
    dt = datetime.strptime(geburt, "%Y-%m-%d %H:%M")
    if offset is None:
        offset = ae.hist_offset(iana or "", dt.year, dt.month, dt.day, dt.hour, dt.minute)
        if offset is None:
            raise ValueError(f"Zeitzone «{iana}» unbekannt.")
    chart = ae.generate_chart(dt.year, dt.month, dt.day, dt.hour, dt.minute,
                              lat, lon, offset, location=ort)
    return Radix(chart)


def geburtsort_aufloesen(geburt: str, ort: str) -> dict:
    """Ortsname → Koordinaten, Zeitzone und historischer UTC-Offset
    (astro_engine.resolve_location, wie in Ved Chart Calc)."""
    dt = datetime.strptime(geburt, "%Y-%m-%d %H:%M")
    loc = ae.resolve_location(ort, dt.year, dt.month, dt.day, dt.hour, dt.minute)
    if not loc:
        raise ValueError(f"Ort «{ort}» nicht gefunden. Genauer angeben, z. B. «Liestal, Schweiz».")
    return loc

# ---------------------------------------------------------------------------
# Bewertung eines Zeitpunkts
# ---------------------------------------------------------------------------

@dataclass
class Bewertung:
    jd: float
    score: int = 0
    sperren: list[str] = field(default_factory=list)
    faktoren: list[tuple[int, str]] = field(default_factory=list)
    info: dict[str, str] = field(default_factory=dict)
    lons: dict[str, float] = field(default_factory=dict)
    ayan: float = 0.0
    engine: str = ""

    @property
    def gesperrt(self) -> bool:
        return bool(self.sperren)

    def punkte(self, p: int, text: str) -> None:
        if p:
            self.faktoren.append((p, text))
            self.score += p


def tithi_name(tithi: int) -> str:
    if tithi == 15:
        return "Pūrṇimā"
    if tithi == 30:
        return "Amāvasyā"
    return f"{'Ś' if tithi <= 15 else 'K'}-{TITHI[(tithi - 1) % 15]}"


def _abstand(a: float, b: float) -> float:
    return abs((a - b + 180.0) % 360.0 - 180.0)


def bewerten(jd: float, tag: Tag, radix: Radix, modus: str,
             lat: float, lon: float) -> Bewertung:
    b = Bewertung(jd)
    lons, ayan, engine = ae.compute_positions(jd, lat, lon)
    b.lons, b.ayan, b.engine = lons, ayan, engine
    sonne, mond = lons["Sun"], lons["Moon"]
    try:
        retro = ae._retro_flags(jd)
    except Exception:
        retro = {}

    nak_name, nak_herr, _ = ae.nakshatra_of(mond)
    nak = ae.nak_index(nak_name)
    pan = ae.compute_panchang(sonne, mond, tag.wochentag, nak_name, nak_herr)
    tithi = pan["tithi_num"]                  # 1..30
    pakṣa_tithi = (tithi - 1) % 15 + 1
    yoga = ae.YOGA_NAMES.index(pan["yoga"])
    karana = pan["karana"]
    mond_rashi = int(mond // 30) % 12
    wt = tag.wochentag
    vk = modus == "verkauf"

    def pflicht(was: str, belastungen: list[str]) -> None:
        """Verkaufs-Pflichtkriterium: eine Belastung abwerten, ab PFLICHT_GRENZE sperren."""
        if not belastungen:
            return
        text = f"{was}: {', '.join(belastungen)}"
        if len(belastungen) >= PFLICHT_GRENZE:
            if VERKAUF_PFLICHT_SPERRE:
                b.sperren.append(f"Pflicht verfehlt, {text}")
            else:
                b.punkte(P_VERKAUF_PFLICHT, f"Pflicht verfehlt, {text}")
        else:
            b.punkte(P_VERKAUF_PFLICHT_EINE, f"Pflicht belastet, {text}")

    def herr_belastungen(g: str) -> list[str]:
        out = []
        if haus[g] in DUSTHANA:
            out.append(f"{PLANET_DE[g]} im {haus[g]}. Haus")
        if verbrannt(g):
            out.append(f"{PLANET_DE[g]} verbrannt")
        konj = [x for x in uebel_konj(g)] if g not in HARTE_UEBELTAETER else []
        if konj:
            out.append(f"{PLANET_DE[g]} mit {', '.join(PLANET_DE[x] for x in konj)}")
        return out

    zeichen = {g: int(lons[g] // 30) % 12 for g in GRAHAS}
    lagna = int(lons["Ascendant"] // 30) % 12
    haus = {g: (zeichen[g] - lagna) % 12 + 1 for g in GRAHAS}
    besetzung: dict[int, list[str]] = {}
    for g in GRAHAS:
        besetzung.setdefault(zeichen[g], []).append(g)
    aspekte = ae.graha_aspects_by_sign({g: {"sign_idx": zeichen[g]} for g in GRAHAS})

    def herr_von(h: int) -> str:
        return ae.SIGN_LORDS[ae.SIGNS[(lagna + h - 1) % 12]]

    def verbrannt(g: str) -> bool:
        orb = getattr(ae, "_COMBUST_ORB", {}).get(g, 8.0)
        return g not in ("Sun", "Rahu", "Ketu") and _abstand(lons[g], sonne) < orb

    def uebel_konj(g: str) -> list[str]:
        return [x for x in besetzung.get(zeichen[g], []) if x != g and x in HARTE_UEBELTAETER]

    # ── Pañcāṅga ────────────────────────────────────────────────────────────
    if tithi == 30:
        b.sperren.append("Amāvasyā")
    elif pakṣa_tithi in RIKTA_TITHI:
        b.punkte(-3, f"Riktā-Tithi {tithi_name(tithi)}")
    elif pakṣa_tithi in GUTE_TITHI:
        b.punkte(2, f"Tithi {tithi_name(tithi)}")
    elif pakṣa_tithi == 8:
        b.punkte(-1, f"Tithi {tithi_name(tithi)}")
    if tithi <= 15 and not vk:
        b.punkte(P_SUKLA_KAUF, "Zunehmender Mond (Kauf)")
    if tithi > 15 and vk:
        b.punkte(P_KRSNA_VERKAUF, "Abnehmender Mond (Verkauf)")
    if pakṣa_tithi == DAGDHA_TITHI[wt] and tithi != 30:
        b.punkte(-2, f"Dagdha-Tithi ({tithi_name(tithi)} am {WOCHENTAG[wt]})")

    b.punkte(VARA_PUNKTE[wt], f"Vāra {VARA[wt]}")

    if vk:
        if nak in NAK_VERKAUF:
            b.punkte(P_NAK_VERKAUF, f"Verkaufs-Nakṣatra {NAKSHATRA[nak]}")
        elif nak in NAK_VERKAUF_MC:
            b.punkte(P_NAK_VERKAUF_MC, f"Nakṣatra {NAKSHATRA[nak]} (Verkauf, Muhūrta Cintāmaṇi)")
    elif nak in NAK_TRADING:
        b.punkte(P_NAK_TRADING, f"Trading-Nakṣatra {NAKSHATRA[nak]}")
    elif nak in NAK_KAUF_MC:
        b.punkte(P_NAK_MC, f"Nakṣatra {NAKSHATRA[nak]} (Kauf, Muhūrta Cintāmaṇi)")
    if not vk and nak in NAK_VERKAUF_MC:
        b.punkte(P_NAK_KAUF_VERBOTEN, f"Nakṣatra {NAKSHATRA[nak]} (ungünstig für Kauf)")
    if nak == AMRITA_SIDDHI[wt]:
        b.punkte(2, f"Amṛta-Siddhi-Yoga ({VARA[wt]} + {NAKSHATRA[nak]})")

    if yoga in YOGA_SPERRE:
        b.sperren.append(f"Yoga {YOGA[yoga]}")
    elif yoga in YOGA_UNGUENSTIG:
        b.punkte(-2, f"Yoga {YOGA[yoga]}")

    if karana == "Vishti":
        b.sperren.append("Viṣṭi-Karaṇa (Bhadrā)")
    elif karana == "Vanija":
        b.punkte(1, "Vaṇija-Karaṇa (Handel)")

    # ── Individuell: Tārābala, Candrabala ───────────────────────────────────
    tara = ((nak - radix.mond_nak) % 27) % 9
    if tara in TARA_SPERRE:
        b.sperren.append(f"Tārā {TARA[tara]}")
    elif tara in TARA_SCHLECHT:
        b.punkte(-3, f"Tārā {TARA[tara]}")
    elif tara in TARA_GUT:
        b.punkte(2, f"Tārā {TARA[tara]}")
    elif tara == 0:
        b.punkte(-1, "Janma-Tārā")

    chandra_haus = (mond_rashi - radix.mond_rashi) % 12 + 1
    if chandra_haus == 8:
        b.sperren.append("Candrāṣṭama")
    elif chandra_haus in CHANDRA_GUT:
        b.punkte(2, f"Candrabala ({chandra_haus}. Haus)")
    elif chandra_haus in CHANDRA_SCHLECHT:
        b.punkte(-2, f"Mond im {chandra_haus}. vom Janma-Mond")

    # ── Horā ───────────────────────────────────────────────────────────────
    hora = tag.hora_herr(jd)
    b.punkte(HORA_PUNKTE[hora], f"Horā {PLANET_DE[hora]}")
    if hora in radix.wohlstandsherren:
        b.punkte(1, f"Horā-Herr {PLANET_DE[hora]} = Herr 2./11. Radix")

    # ── Tagesabschnitte ─────────────────────────────────────────────────────
    achtel = tag.achtel(jd)
    if achtel is not None:
        if achtel == RAHU_KALA[wt]:
            b.sperren.append("Rāhu Kāla")
        else:
            if achtel == YAMAGANDA[wt]:
                b.punkte(P_YAMAGANDA, "Yamagaṇḍa")
            if achtel == GULIKA[wt]:
                b.punkte(P_GULIKA, "Gulika Kāla")
            if achtel == tag.kala_vela_achtel():
                b.punkte(P_KALA_VELA, "Kāla Velā")
    bogen, nr = tag.muhurta_nr(jd)
    if (bogen == "tag" and nr in DURMUHURTA_TAG[wt]) or \
       (bogen == "nacht" and nr in DURMUHURTA_NACHT[wt]):
        b.sperren.append(f"Durmuhūrta ({nr}. Muhūrta {'des Tages' if bogen == 'tag' else 'der Nacht'})")
    if tag.ist_abhijit(jd) and wt != MITTWOCH:
        b.punkte(2, "Abhijit Muhūrta")

    # ── Muhūrta-Lagna: keine Übeltäter ──────────────────────────────────────
    im_lagna = [g for g in besetzung.get(lagna, []) if g in UEBELTAETER]
    if im_lagna:
        namen = ", ".join(PLANET_DE[g] for g in im_lagna)
        if im_lagna == ["Sun"]:
            b.punkte(LAGNA_SONNE_BESETZT, "Sonne im Muhūrta-Lagna")
        elif LAGNA_UEBEL_SPERRE:
            b.sperren.append(f"Übeltäter im Lagna ({namen})")
        else:
            b.punkte(LAGNA_UEBEL_BESETZT, f"Übeltäter im Muhūrta-Lagna ({namen})")

    achtes = (lagna + 7) % 12
    if besetzung.get(achtes):
        b.punkte(-2, f"8. vom Lagna besetzt ({', '.join(PLANET_DE[g] for g in besetzung[achtes])})")

    wohl = sum(1 for g in WOHLTAETER if haus[g] in KENDRA_TRIKONA)
    b.punkte(min(wohl, 2), "Wohltäter in Kendra/Trikoṇa")

    # ── Lagna-Herr stark ────────────────────────────────────────────────────
    lh = herr_von(1)
    if vk:
        pflicht("Lagna-Herr", herr_belastungen(lh))
        if haus[lh] in KENDRA_TRIKONA or haus[lh] == 11:
            b.punkte(P_LAGNAHERR_GUT_VERKAUF, f"Lagna-Herr {PLANET_DE[lh]} im {haus[lh]}. Haus")
    elif haus[lh] in DUSTHANA:
        b.punkte(P_LAGNAHERR_DUSTHANA, f"Lagna-Herr {PLANET_DE[lh]} im {haus[lh]}. Haus")
    elif haus[lh] in KENDRA_TRIKONA or haus[lh] == 11:
        b.punkte(P_LAGNAHERR_GUT_VERKAUF if vk else P_LAGNAHERR_GUT,
                 f"Lagna-Herr {PLANET_DE[lh]} im {haus[lh]}. Haus")
    if not vk and lh not in HARTE_UEBELTAETER and uebel_konj(lh):
        b.punkte(P_LAGNAHERR_UEBEL_KONJ,
                 f"Lagna-Herr {PLANET_DE[lh]} mit {', '.join(PLANET_DE[x] for x in uebel_konj(lh))}")
    if verbrannt(lh) and not vk:
        b.punkte(P_LAGNAHERR_VERBRANNT, f"Lagna-Herr {PLANET_DE[lh]} verbrannt")

    # ── Mond ───────────────────────────────────────────────────────────────
    if haus["Moon"] == 8:
        b.sperren.append("Mond im 8. vom Muhūrta-Lagna")
    elif haus["Moon"] in (6, 12):
        b.punkte(P_MOND_6_12_VERKAUF if vk else P_MOND_6_12,
                 f"Mond im {haus['Moon']}. vom Muhūrta-Lagna")
    knoten = [k for k in ("Rahu", "Ketu") if _abstand(mond, lons[k]) <= MOND_KNOTEN_ORB]
    if knoten:
        k = knoten[0]
        b.punkte(P_MOND_KNOTEN, f"Mond nahe {PLANET_DE[k]} ({_abstand(mond, lons[k]):.1f}°)")

    # ── Merkur ─────────────────────────────────────────────────────────────
    merkur_minus = 0
    if haus["Mercury"] in MERKUR_GUTE_HAEUSER:
        b.punkte(P_MERKUR_GUT, f"Merkur im {haus['Mercury']}. Haus")
    elif haus["Mercury"] in DUSTHANA:
        b.punkte(P_MERKUR_DUSTHANA, f"Merkur im {haus['Mercury']}. Haus")
        merkur_minus += 1
    if retro.get("Mercury"):
        b.punkte(P_MERKUR_RETRO, "Merkur rückläufig")
        merkur_minus += 1
    if verbrannt("Mercury"):
        b.punkte(P_MERKUR_VERBRANNT, f"Merkur verbrannt ({_abstand(lons['Mercury'], sonne):.1f}° zur Sonne)")
        merkur_minus += 1
    if uebel_konj("Mercury"):
        b.punkte(P_MERKUR_UEBEL_KONJ,
                 f"Merkur mit {', '.join(PLANET_DE[x] for x in uebel_konj('Mercury'))}")
        merkur_minus += 1
    if merkur_minus and 8 in radix.chart["lordships"].get("Mercury", []):
        b.punkte(P_MERKUR_RADIX_8, "Merkur ist Radix-8.-Herr: Stellung muss sauber sein")
    if nak == SVATI and merkur_minus == 0 and haus["Mercury"] in MERKUR_GUTE_HAEUSER:
        b.punkte(P_SVATI_MERKUR, "Svātī mit starkem Merkur")

    # ── 2. und 5. Haus geschützt ────────────────────────────────────────────
    for h, name in GESCHUETZTE_HAEUSER.items():
        z = (lagna + h - 1) % 12
        herr = herr_von(h)
        herr_schwach = haus[herr] in DUSTHANA or verbrannt(herr)
        rein = True
        insassen = [g for g in besetzung.get(z, []) if g in UEBELTAETER]
        if h == 5 and "Rahu" in insassen:
            rein = False
            insassen = [g for g in insassen if g != "Rahu"]
            if herr_schwach:
                b.punkte(P_RAHU_5_SCHWACH, f"Rāhu im 5. bei schwachem 5. Herrn ({PLANET_DE[herr]})")
            else:
                b.punkte(P_RAHU_5_KONTROLLIERT, "Rāhu im 5. (5. Herr gut gestellt)")
        if vk and h == 2:
            hart = [g for g in insassen if g in HARTE_UEBELTAETER]
            bel = ([f"besetzt von {', '.join(PLANET_DE[g] for g in hart)}"] if hart else [])
            bel += [f"Herr {x}" for x in herr_belastungen(herr)]
            if bel:
                rein = False
                pflicht("2. Haus", bel)
                insassen = [g for g in insassen if g not in HARTE_UEBELTAETER]
        if insassen:
            rein = False
            p_bes = HAUS_SONNE_BESETZT if insassen == ["Sun"] else HAUS_UEBEL_BESETZT
            b.punkte(p_bes, f"{name} besetzt von {', '.join(PLANET_DE[g] for g in insassen)}")
        aspektierer = [g for g in aspekte.get(z, []) if g in UEBELTAETER
                       and z != zeichen[g]]
        if aspektierer:
            rein = False
            b.punkte(HAUS_UEBEL_ASPEKT,
                     f"{name} aspektiert von {', '.join(PLANET_DE[g] for g in aspektierer)}")
        if vk and h == 2 and "Mars" in aspektierer:
            b.punkte(P_MARS_ASPEKT_2_11_VERKAUF, f"Aggressiver Mars-Aspekt auf {name}")
        if haus[herr] in DUSTHANA and not (vk and h == 2):
            rein = False
            b.punkte(HAUS_HERR_DUSTHANA, f"Herr vom {name} ({PLANET_DE[herr]}) im {haus[herr]}. Haus")
        if rein:
            b.punkte(P_2_REIN_VERKAUF if (vk and h == 2) else HAUS_REIN, f"{name} unbelastet")

    # ── 11. Haus (Gewinne) — beim Verkauf doppelt ───────────────────────────
    f11 = 2 if vk else 1
    h11 = herr_von(11)
    if vk and herr_belastungen(h11):
        pflicht("11. Herr", herr_belastungen(h11))
    elif haus[h11] in DUSTHANA:
        b.punkte(P_11_HERR_DUSTHANA * f11, f"Herr vom 11. Haus ({PLANET_DE[h11]}) im {haus[h11]}. Haus")
    elif haus[h11] in KENDRA_TRIKONA or haus[h11] == 11:
        b.punkte(P_11_HERR_GUT * f11, f"Herr vom 11. Haus ({PLANET_DE[h11]}) im {haus[h11]}. Haus")
    in_11 = besetzung.get((lagna + 10) % 12, [])
    if any(g in WOHLTAETER for g in in_11):
        b.punkte(P_11_WOHLTAETER * f11,
                 f"Wohltäter im 11. Haus ({', '.join(PLANET_DE[g] for g in in_11 if g in WOHLTAETER)})")
    if "Rahu" in in_11:
        b.punkte(P_11_RAHU, "Rāhu im 11. Haus (Spekulation, Upacaya)")
    if vk:
        if "Mars" in in_11:
            b.punkte(P_MARS_IN_11_VERKAUF, "Aggressiver Mars im 11. Haus")
        elif "Mars" in aspekte.get((lagna + 10) % 12, []):
            b.punkte(P_MARS_ASPEKT_2_11_VERKAUF, "Aggressiver Mars-Aspekt auf 11. Haus (Gewinne)")

    # ── Jupiter und Venus ──────────────────────────────────────────────────
    jup_haeuser = JUPITER_HAEUSER_VERKAUF if vk else JUPITER_HAEUSER_KAUF
    if haus["Jupiter"] in jup_haeuser:
        b.punkte(P_JUPITER_IN, f"Jupiter im {haus['Jupiter']}. Haus")
    else:
        asp = sorted(h for h in jup_haeuser if "Jupiter" in aspekte.get((lagna + h - 1) % 12, []))
        if asp:
            b.punkte(P_JUPITER_ASPEKT, f"Jupiter aspektiert {', '.join(f'{h}.' for h in asp)} Haus")
    if haus["Venus"] in (2, 11):
        b.punkte(P_VENUS_2_11, f"Venus im {haus['Venus']}. Haus")

    # ── Radix-Schlüsselplaneten (Herren Janma-Lagna und 5. Haus) ────────────
    radixherren = radix.herren({1, 2, 5, 11}) if vk else radix.schluesselplaneten
    p_gut = P_RADIXHERR_VERKAUF if vk else P_RADIXHERR_GUT
    p_schlecht = -P_RADIXHERR_VERKAUF if vk else P_RADIXHERR_DUSTHANA
    for rh in radixherren:
        rolle = "/".join(f"{h}." for h in sorted(radix.chart["lordships"].get(rh, [])))
        if haus[rh] in DUSTHANA:
            b.punkte(p_schlecht, f"Radix-Herr {PLANET_DE[rh]} ({rolle}) im {haus[rh]}. Haus")
        elif haus[rh] in KENDRA_TRIKONA or haus[rh] == 11:
            b.punkte(p_gut, f"Radix-Herr {PLANET_DE[rh]} ({rolle}) im {haus[rh]}. Haus")

    # ── Bezug Muhūrta-Lagna ↔ Janma-Lagna ──────────────────────────────────
    rel = (lagna - radix.lagna_rashi) % 12 + 1
    if rel in (6, 8, 12):
        b.punkte(-2, f"Lagna im {rel}. vom Janma-Lagna")
    elif rel in (2, 11):
        b.punkte(2, f"Lagna im {rel}. vom Janma-Lagna (Wohlstand)")
    elif rel in (1, 10):
        b.punkte(1, f"Lagna im {rel}. vom Janma-Lagna")

    b.info = {
        "tithi": tithi_name(tithi),
        "nakshatra": NAKSHATRA[nak],
        "yoga": YOGA[yoga],
        "karana": KARANA_DE.get(karana, karana),
        "tara": TARA[tara],
        "chandrabala": f"{chandra_haus}. Haus",
        "hora": PLANET_DE[hora],
        "lagna": RASHI[lagna],
    }
    return b

# ---------------------------------------------------------------------------
# Phase: Daśā (Radix) und Gochara (Transit vom Janma-Mond) — Ebene 1 und 2
# Nicht im Slot-Score enthalten: sie gilt für den ganzen Zeitraum gleich.
# ---------------------------------------------------------------------------

DASHA_GUTE_HERRSCHAFT = {1, 2, 5, 9, 11}
DASHA_GUTE_STELLUNG = {1, 2, 5, 9, 10, 11}
GOCHARA = {
    "Jupiter": ({2, 5, 7, 9, 11}, {4, 8, 12}, ""),
    "Saturn": ({3, 6, 11}, {12, 1, 2, 4, 8}, ""),
    "Rahu": ({3, 6, 11}, {8, 12}, ""),
}


def phase_bewerten(radix: Radix, jd: float, lat: float, lon: float) -> dict:
    c = radix.chart
    herrschaft = c["lordships"]
    zeilen: list[tuple[int, str]] = []

    aktuell = (c.get("dashas") or {}).get("current") or {}
    for ebene, p in (("Mahādaśā", aktuell.get("maha")), ("Antardaśā", aktuell.get("antar")),
                     ("Pratyantardaśā", aktuell.get("pratyantar"))):
        if not p:
            continue
        haeuser = sorted(herrschaft.get(p, []))
        stellung = c["planets"][p].get("house", 0)
        pk = sum(1 for h in haeuser if h in DASHA_GUTE_HERRSCHAFT) \
            - sum(1 for h in haeuser if h in DUSTHANA)
        pk += 1 if stellung in DASHA_GUTE_STELLUNG else -1 if stellung in DUSTHANA else 0
        herr_txt = f"Herr von {', '.join(f'{h}.' for h in haeuser)} Haus, " if haeuser else ""
        zeilen.append((pk, f"{ebene} {PLANET_DE[p]}: {herr_txt}steht im {stellung}. Haus"))

    lons, _, _ = ae.compute_positions(jd, lat, lon)
    for g, (gut, schlecht, _) in GOCHARA.items():
        h = (int(lons[g] // 30) % 12 - radix.mond_rashi) % 12 + 1
        pk = 1 if h in gut else -1 if h in schlecht else 0
        zusatz = ""
        if g == "Saturn" and h in (12, 1, 2):
            zusatz = " (Sāḍe Sātī)"
        elif g == "Saturn" and h in (4, 8):
            zusatz = " (Kaṇṭaka/Aṣṭama Śani)"
        zeilen.append((pk, f"Transit {PLANET_DE[g]} im {h}. vom Janma-Mond{zusatz}"))

    summe = sum(pk for pk, _ in zeilen)
    if summe >= 3:
        urteil = "günstige Phase für Risiko"
    elif summe <= -2:
        urteil = "zurückhaltende Phase: Positionen klein halten"
    else:
        urteil = "gemischte Phase"
    return {"zeilen": zeilen, "summe": summe, "urteil": urteil}


def phase_text(ph: dict) -> str:
    teile = [f"Phase: {ph['urteil']} ({ph['summe']:+d})"]
    teile += [f"  {pk:+d}  {t}" for pk, t in ph["zeilen"]]
    return "\n".join(teile)


# ---------------------------------------------------------------------------
# Zeitfenster
# ---------------------------------------------------------------------------

@dataclass
class Fenster:
    von: datetime
    bis: datetime
    score: int
    faktoren: list[tuple[int, str]]
    info: dict[str, str]

    @property
    def sterne(self) -> str:
        if self.score >= 12:
            return "★★★"
        if self.score >= 8:
            return "★★"
        return "★"

    @property
    def bewertung(self) -> str:
        return {"★★★": "sehr günstig", "★★": "günstig", "★": "brauchbar"}[self.sterne]


def im_markt(dt: datetime, markt: str) -> bool:
    tzname, auf, zu, werktage = MAERKTE[markt]
    if tzname is None:
        return True
    lokal = dt.astimezone(ZoneInfo(tzname))
    if werktage and lokal.weekday() >= 5:
        return False
    return auf <= lokal.time() < zu


def fenster_berechnen(start: datetime, ende: datetime, schritt: int, radix: Radix,
                      modus: str, markt: str, min_score: int, kalender: Tageskalender,
                      lat: float, lon: float,
                      protokoll: list | None = None) -> list[Fenster]:
    """Zeitfenster berechnen. Optional: protokoll erhält jedes bewertete (Zeit, Bewertung)."""
    fenster: list[Fenster] = []
    aktuell: Fenster | None = None
    delta = timedelta(minutes=schritt)
    t = start
    while t < ende:
        naechst = t + delta
        if not im_markt(t, markt):
            aktuell = None
            t = naechst
            continue
        jd = jd_aus_dt(t)
        bew = bewerten(jd, kalender.fuer_jd(jd), radix, modus, lat, lon)
        if protokoll is not None:
            protokoll.append((t, bew))
        if bew.gesperrt or bew.score < min_score:
            aktuell = None
        elif aktuell and aktuell.bis == t and aktuell.score == bew.score \
                and aktuell.von.date() == t.date():
            aktuell.bis = naechst
        else:
            aktuell = Fenster(t, naechst, bew.score, bew.faktoren, bew.info)
            fenster.append(aktuell)
        t = naechst
    return fenster

# ---------------------------------------------------------------------------
# Ausgabe
# ---------------------------------------------------------------------------

def faktoren_text(faktoren: list[tuple[int, str]]) -> str:
    return ", ".join(f"{t} ({p:+d})" for p, t in sorted(faktoren, key=lambda x: -x[0]))


def radix_zeile(radix: Radix) -> str:
    herren = ", ".join(sorted(PLANET_DE[p] for p in radix.wohlstandsherren))
    return (f"Janma-Rāśi {RASHI[radix.mond_rashi]}, Janma-Nakṣatra "
            f"{NAKSHATRA[radix.mond_nak]}, Lagna {RASHI[radix.lagna_rashi]} "
            f"(Herren 2./11.: {herren})")


def ausgabe_konsole(fenster: list[Fenster], kalender: Tageskalender, tz: ZoneInfo,
                    start: datetime, ende: datetime, top: int) -> None:
    d = start.date()
    while d < ende.date():
        tag = kalender.fuer_datum(d)
        rk_von, rk_bis = (dt_aus_jd(x, tz) for x in tag.achtel_zeit(RAHU_KALA[tag.wochentag]))
        print(f"\n=== {WOCHENTAG[tag.wochentag]}, {d:%d.%m.%Y} ({VARA[tag.wochentag]}) — "
              f"Aufgang {dt_aus_jd(tag.aufgang, tz):%H:%M}, "
              f"Untergang {dt_aus_jd(tag.untergang, tz):%H:%M}, "
              f"Rāhu Kāla {rk_von:%H:%M}–{rk_bis:%H:%M} ===")
        beste = sorted((f for f in fenster if f.von.date() == d),
                       key=lambda f: (-f.score, f.von))[:top]
        if not beste:
            print("  Keine geeigneten Zeitfenster.")
        for f in sorted(beste, key=lambda f: f.von):
            i = f.info
            print(f"  {f.sterne:<3} {f.von:%H:%M}–{f.bis:%H:%M}  Score {f.score:>2}  "
                  f"| Horā {i['hora']}, {i['nakshatra']}, Tārā {i['tara']}, Lagna {i['lagna']}")
            print(f"        {faktoren_text(f.faktoren)}")
        d += timedelta(days=1)


def ausgabe_csv(fenster: list[Fenster], pfad: str) -> None:
    felder = ["datum", "von", "bis", "score", "bewertung", "hora", "tithi", "nakshatra",
              "yoga", "karana", "tara", "chandrabala", "lagna", "faktoren"]
    with open(pfad, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh, delimiter=";")
        w.writerow(felder)
        for f in fenster:
            i = f.info
            w.writerow([f"{f.von:%Y-%m-%d}", f"{f.von:%H:%M}", f"{f.bis:%H:%M}", f.score,
                        f.bewertung, i["hora"], i["tithi"], i["nakshatra"], i["yoga"],
                        i["karana"], i["tara"], i["chandrabala"], i["lagna"],
                        faktoren_text(f.faktoren)])


def ausgabe_markdown(fenster: list[Fenster], pfad: str, radix: Radix, modus: str,
                     markt: str, top: int, phase: dict | None = None) -> None:
    zeilen = [f"## Muhūrta-Zeitfenster — {modus.capitalize()}, Markt {markt}", "",
              radix_zeile(radix), "",
              f"Engine: {radix.chart['meta']['engine']}", ""]
    if phase:
        zeilen += [f"**Phase: {phase['urteil']} ({phase['summe']:+d})**", ""]
        zeilen += [f"- {pk:+d} {t}" for pk, t in phase["zeilen"]] + [""]
    for d in sorted({f.von.date() for f in fenster}):
        beste = sorted((f for f in fenster if f.von.date() == d),
                       key=lambda f: (-f.score, f.von))[:top]
        zeilen += [f"### {WOCHENTAG[wochentag_so0(d)]}, {d:%d.%m.%Y}", "",
                   "| Zeit | Score | Bewertung | Horā | Nakṣatra | Tārā | Lagna |",
                   "|---|---|---|---|---|---|---|"]
        for f in sorted(beste, key=lambda f: f.von):
            i = f.info
            zeilen.append(f"| {f.von:%H:%M}–{f.bis:%H:%M} | {f.score} | {f.sterne} "
                          f"{f.bewertung} | {i['hora']} | {i['nakshatra']} | "
                          f"{i['tara']} | {i['lagna']} |")
        zeilen.append("")
    with open(pfad, "a", encoding="utf-8") as fh:
        fh.write("\n".join(zeilen) + "\n")

# ---------------------------------------------------------------------------
# Abgleich: Detailwerte (alles aus astro_engine)
# ---------------------------------------------------------------------------

def grad_text(laenge: float) -> str:
    laenge %= 360.0
    sek = int(round((laenge % 30.0) * 3600))
    g, rest = divmod(sek, 3600)
    m, s = divmod(rest, 60)
    return f"{RASHI[int(laenge // 30) % 12]:<9} {g:2d}°{m:02d}'{s:02d}\"  ({laenge:8.4f}°)"


def nak_pada(laenge: float) -> str:
    name, _, pada = ae.nakshatra_of(laenge)
    return f"{NAKSHATRA[ae.nak_index(name)]} {pada}"


def _idx_tithi(jd: float) -> int:
    s, m = ae._sun_moon_sid(jd)
    return int(((m - s) % 360.0) // 12)


def _idx_karana(jd: float) -> int:
    s, m = ae._sun_moon_sid(jd)
    return int(((m - s) % 360.0) // 6)


def _idx_nak(jd: float) -> int:
    return int(ae._sun_moon_sid(jd)[1] // (360 / 27))


def _idx_yoga(jd: float) -> int:
    s, m = ae._sun_moon_sid(jd)
    return int(((s + m) % 360.0) // (360 / 27))


def element_ende(jd: float, funktion) -> float:
    """Ende des aktuellen Pañcāṅga-Elements (Schrittsuche + Bisektion)."""
    wert = funktion(jd)
    schritt = 1 / 96
    t = jd
    for _ in range(96 * 3):
        if funktion(t + schritt) != wert:
            break
        t += schritt
    lo, hi = t, t + schritt
    for _ in range(25):
        mitte = (lo + hi) / 2
        if funktion(mitte) == wert:
            lo = mitte
        else:
            hi = mitte
    return hi


def abgleich_radix(radix: Radix) -> None:
    c = radix.chart
    m = c["meta"]
    print("=" * 78)
    print(f"RADIX  {m['birth']}  ({m['tz']})  →  {m['ut']}")
    print(f"Breite {m['lat']:.4f}  Länge {m['lon']:.4f}  JD {m['jd']:.5f}  "
          f"Ayanāṃśa {m['ayan']:.4f}°")
    print(f"Engine: {m['engine']}")
    print("-" * 78)
    for p in ["Ascendant"] + GRAHAS:
        r = c["planets"][p]
        retro = " (R)" if r.get("retrograde") else ""
        haus = f"H{r['house']:<2}" if "house" in r else "   "
        wuerde = r.get("dignity", "-")
        print(f"  {PLANET_DE[p]:<7} {grad_text(r['lon'])}  {haus} "
              f"{NAKSHATRA[ae.nak_index(r['nakshatra'])]} {r['pada']}  {wuerde}{retro}")
    pan = c["panchang"]
    print("-" * 78)
    print(f"  Pañcāṅga Geburt: {pan['tithi']} ({pan['tithi_pct']} %), {pan['vara']}, "
          f"{pan['nakshatra']}, Yoga {pan['yoga']}, Karaṇa {pan['karana']}")
    print(f"  {radix_zeile(radix)}")


def abgleich_zeitpunkt(dt: datetime, radix: Radix, modus: str, lat: float, lon: float,
                       kalender: Tageskalender, tz: ZoneInfo) -> None:
    jd = jd_aus_dt(dt)
    tag = kalender.fuer_jd(jd)
    bew = bewerten(jd, tag, radix, modus, lat, lon)
    z = lambda x: f"{dt_aus_jd(x, tz):%d.%m. %H:%M:%S}"
    s, m, asc = bew.lons["Sun"], bew.lons["Moon"], bew.lons["Ascendant"]
    diff = (m - s) % 360.0
    yl = (s + m) % 360.0
    nak_span = 360 / 27

    print("=" * 78)
    print(f"ZEITPUNKT  {dt:%d.%m.%Y %H:%M} ({dt.tzname()})  JD {jd:.6f}  "
          f"Ort {lat:.4f}/{lon:.4f}  Modus {modus}")
    print(f"Ayanāṃśa {bew.ayan:.6f}°   Engine: {bew.engine}")
    print("-" * 78)
    print(f"  Vāra        {VARA[tag.wochentag]} ({WOCHENTAG[tag.wochentag]})  "
          f"Aufgang {z(tag.aufgang)}  Untergang {z(tag.untergang)}")
    for titel, tabelle in (("Rāhu Kāla", RAHU_KALA), ("Yamagaṇḍa", YAMAGANDA),
                           ("Gulika", GULIKA)):
        a, e = tag.achtel_zeit(tabelle[tag.wochentag])
        print(f"  {titel:<11} {z(a)} – {z(e)}")
    a, e = tag.abhijit()
    hinweis = "  (mittwochs nicht verwendet)" if tag.wochentag == MITTWOCH else ""
    print(f"  Abhijit     {z(a)} – {z(e)}{hinweis}")
    print("-" * 78)
    print(f"  Sonne       {grad_text(s)}  {nak_pada(s)}")
    print(f"  Mond        {grad_text(m)}  {nak_pada(m)}")
    print(f"  Lagna       {grad_text(asc)}  {nak_pada(asc)}")
    print("-" * 78)
    print(f"  Tithi       {bew.info['tithi']:<18} {diff % 12 / 12 * 100:5.1f} %  "
          f"endet {z(element_ende(jd, _idx_tithi))}")
    print(f"  Nakṣatra    {bew.info['nakshatra']:<18} {m % nak_span / nak_span * 100:5.1f} %  "
          f"endet {z(element_ende(jd, _idx_nak))}")
    print(f"  Yoga        {bew.info['yoga']:<18} {yl % nak_span / nak_span * 100:5.1f} %  "
          f"endet {z(element_ende(jd, _idx_yoga))}")
    print(f"  Karaṇa      {bew.info['karana']:<18} {diff % 6 / 6 * 100:5.1f} %  "
          f"endet {z(element_ende(jd, _idx_karana))}")
    modus_hora = "proportional" if HORA_PROPORTIONAL else "60 Min. ab Aufgang"
    print(f"  Horā        {bew.info['hora']}  ({modus_hora})")
    print(f"  Tārā        {bew.info['tara']}   Candrabala {bew.info['chandrabala']}")
    print("-" * 78)
    for p, t in sorted(bew.faktoren, key=lambda x: -x[0]):
        print(f"  {p:+3d}  {t}")
    if bew.sperren:
        print(f"  GESPERRT: {', '.join(bew.sperren)}")
    print(f"  SCORE {bew.score}")

# ---------------------------------------------------------------------------
# Kommandozeile
# ---------------------------------------------------------------------------

def _env(name: str, standard: str | None = None) -> str | None:
    wert = os.environ.get(name, "").strip()
    return wert or standard


def argumente() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Muhūrta-Zeitfenster für Trades nach Geburtshoroskop")
    p.add_argument("--geburt", default=_env("GEBURT"), help='Geburtszeit "JJJJ-MM-TT HH:MM"')
    p.add_argument("--geburt-ort", default=_env("GEBURT_ORT"),
                   help='Geburtsort als Name, z. B. "Liestal, Schweiz" (statt Koordinaten)')
    p.add_argument("--geburt-tz", default=_env("GEBURT_TZ", STANDARD_TZ), help="IANA-Zeitzone Geburtsort")
    p.add_argument("--geburt-lat", type=float, default=_env("GEBURT_LAT"), help="Breite Geburtsort")
    p.add_argument("--geburt-lon", type=float, default=_env("GEBURT_LON"), help="Länge Geburtsort (Ost +)")
    p.add_argument("--lat", type=float, default=STANDARD_LAT, help="Breite Handelsort")
    p.add_argument("--lon", type=float, default=STANDARD_LON, help="Länge Handelsort")
    p.add_argument("--tz", default=STANDARD_TZ, help="Zeitzone Handelsort / Ausgabe")
    p.add_argument("--von", default=None, help="Startdatum JJJJ-MM-TT (Standard: heute)")
    p.add_argument("--tage", type=int, default=7, help="Anzahl Tage")
    p.add_argument("--modus", choices=["kauf", "verkauf"], default="kauf")
    p.add_argument("--markt", choices=list(MAERKTE), default="SIX")
    p.add_argument("--schritt", type=int, default=15, help="Zeitschritt in Minuten")
    p.add_argument("--min-score", type=int, default=5, help="Mindestscore für ein Fenster")
    p.add_argument("--top", type=int, default=3, help="Beste Fenster pro Tag")
    p.add_argument("--csv", default=None, help="Alle Fenster als CSV speichern")
    p.add_argument("--markdown", default=None, help="Markdown-Zusammenfassung anhängen")
    p.add_argument("--abgleich", action="append", default=None, metavar='"JJJJ-MM-TT HH:MM"',
                   help="Detailwerte für Radix und Zeitpunkt ausgeben (mehrfach möglich)")
    a = p.parse_args()
    if not a.geburt or not (a.geburt_ort or (a.geburt_lat is not None and a.geburt_lon is not None)):
        p.error("Geburtsdaten fehlen: --geburt und --geburt-ort "
                "(oder --geburt-lat/--geburt-lon; Umgebung GEBURT, GEBURT_ORT)")
    return a


def main() -> int:
    a = argumente()
    tz = ZoneInfo(a.tz)
    try:
        if a.geburt_ort and (a.geburt_lat is None or a.geburt_lon is None):
            loc = geburtsort_aufloesen(a.geburt, a.geburt_ort)
            print(f"Geburtsort: {loc['label']} ({loc['lat']:.4f}, {loc['lon']:.4f}, "
                  f"{loc['offset_str']})")
            radix = radix_berechnen(a.geburt, loc["iana"], loc["lat"], loc["lon"],
                                    offset=loc["offset"], ort=loc["label"])
        else:
            radix = radix_berechnen(a.geburt, a.geburt_tz, a.geburt_lat, a.geburt_lon)
    except ValueError as exc:
        sys.exit(str(exc))
    kalender = Tageskalender(a.lat, a.lon, tz)

    if a.abgleich is not None:
        abgleich_radix(radix)
        for eintrag in a.abgleich:
            dt = datetime.strptime(eintrag, "%Y-%m-%d %H:%M").replace(tzinfo=tz)
            abgleich_zeitpunkt(dt, radix, a.modus, a.lat, a.lon, kalender, tz)
        return 0

    start_datum = date.fromisoformat(a.von) if a.von else datetime.now(tz).date()
    start = datetime.combine(start_datum, time(0, 0), tzinfo=tz)
    ende = datetime.combine(start_datum + timedelta(days=a.tage), time(0, 0), tzinfo=tz)

    fenster = fenster_berechnen(start, ende, a.schritt, radix, a.modus, a.markt,
                                a.min_score, kalender, a.lat, a.lon)

    print(f"Radix: {radix_zeile(radix)}")
    print(phase_text(phase_bewerten(radix, jd_aus_dt(start), a.lat, a.lon)))
    print(f"Engine: {radix.chart['meta']['engine']}")
    print(f"Modus: {a.modus} | Markt: {a.markt} | {start:%d.%m.%Y}–"
          f"{ende - timedelta(days=1):%d.%m.%Y} | Schritt {a.schritt} Min.")
    ausgabe_konsole(fenster, kalender, tz, start, ende, a.top)

    if a.csv:
        ausgabe_csv(fenster, a.csv)
        print(f"\nCSV gespeichert: {a.csv}")
    if a.markdown:
        ausgabe_markdown(fenster, a.markdown, radix, a.modus, a.markt, a.top,
                         phase_bewerten(radix, jd_aus_dt(start), a.lat, a.lon))
    return 0


if __name__ == "__main__":
    sys.exit(main())
