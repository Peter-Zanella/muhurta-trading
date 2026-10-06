# Muhūrta Trading

Günstige Zeitfenster für Kauf (kraya) oder Verkauf (vikraya) nach klassischem Muhūrta,
individualisiert auf das Geburtshoroskop. Sämtliche Berechnungen kommen aus
`astro_engine.py` im Repository **Ved Chart Calc** (Radix über `generate_chart`,
Positionen, Pañcāṅga, Sonnenaufgang). Die Engine wird nicht kopiert, sondern bei jedem
Lauf frisch ausgecheckt – es gibt nur eine Rechenquelle.

## Einrichtung (GitHub-Weboberfläche)

1. Neues **privates** Repository `muhurta-trading` anlegen.
2. `muhurta_trading.py`, `requirements.txt`, `README.md`, `.gitignore` hochladen
   (*Add file → Upload files*).
3. Workflow: *Add file → Create new file*, Pfad `.github/workflows/muhurta.yml`, Inhalt einfügen.
4. *Settings → Secrets and variables → Actions*:

   **Variables**

   | Name | Inhalt |
   |---|---|
   | `ENGINE_REPO` | `<github-name>/<ved-chart-calc-repo>` |
   | `ENGINE_DIR` | Unterordner von `astro_engine.py`, leer wenn im Wurzelverzeichnis |
   | `ENGINE_REF` | optional, Branch/Tag/Commit (Standard `main`) |

   **Secrets**

   | Name | Inhalt |
   |---|---|
   | `ENGINE_TOKEN` | nur nötig, wenn Ved Chart Calc privat ist: Fine-grained Token, nur dieses Repo, *Contents: Read-only* |
   | `GEBURT` | `1957-08-24 13:55` |
   | `GEBURT_ORT` | `Liestal, Schweiz` (Geocoding wie Ved Chart Calc) |

   Alternativ statt `GEBURT_ORT`: `GEBURT_LAT`, `GEBURT_LON`, `GEBURT_TZ`.

5. *Actions → Muhūrta Trading → Run workflow*. Tabelle in der Job-Zusammenfassung
   (mit Engine-Commit), CSV als Artefakt; zusätzlich jeden Montag automatisch.
   Mit Eingabe *abgleich* erscheinen statt der Fenster die Detailwerte zum Vergleich mit dem Report.

## Web-App auf Render

`app.py` stellt dasselbe als Web-App bereit: Formular, Tagesleiste mit Bewertung im
15-Minuten-Raster, beste Fenster mit Faktoren und Abgleich-Link. `build.sh` klont beim
Build Ved Chart Calc nach `./engine` und schreibt den Engine-Commit in die Fusszeile.

1. Render → *New → Blueprint* → dieses Repository wählen. `render.yaml` legt den Dienst an.
2. Beim Anlegen die Werte eintragen:

   | Variable | Inhalt |
   |---|---|
   | `ENGINE_REPO` | `<github-name>/<ved-chart-calc-repo>` |
   | `ENGINE_DIR` | Unterordner von `astro_engine.py`, sonst leer |
   | `ENGINE_TOKEN` | nur bei privatem Ved Chart Calc (Fine-grained, *Contents: Read-only*) |
   | `APP_KEY` | frei wählbarer Zugangsschlüssel – ohne ihn ist die App öffentlich |
   | `GEBURT`, `GEBURT_ORT` | Standard-Geburtsdaten wie oben |
   | `APP_ORT` | Standard-Handelsort, vorbelegt mit `Wädenswil, Schweiz` |

   Geburtsort und Handelsort lassen sich in der App jederzeit als Ortsname ändern. Die App
   zeigt den gefundenen Ort mit Koordinaten und UTC-Offset an, damit sich das Geocoding prüfen lässt.

3. Nach dem Deploy die Render-URL öffnen, Schlüssel eingeben (Cookie gilt 90 Tage).

Engine geändert? In Render *Manual Deploy → Deploy latest commit* – der Build holt den
aktuellen Stand von Ved Chart Calc. Routen: `/`, `/slot?zeit=JJJJ-MM-TT HH:MM` (Muhūrta-Detail mit Chart, per Klick in der Tagesleiste), `/abgleich?zeit=…`,
`/api/fenster` (JSON, gleiche Parameter wie das Formular), `/health`.

## Lokal

```bash
git clone https://github.com/<github-name>/<ved-chart-calc-repo> engine
pip install -r requirements.txt
uvicorn app:app --reload              # Web-App auf http://127.0.0.1:8000
python muhurta_trading.py --geburt "1957-08-24 13:55" --geburt-ort "Liestal, Schweiz" \
  --modus kauf --markt SIX --tage 7
```

Optionen: `--lat/--lon/--tz` (Handelsort, Standard Wädenswil), `--von`, `--tage`,
`--modus kauf|verkauf`, `--markt SIX|XETRA|LSE|NYSE|KRYPTO|ALLE`, `--schritt` (Min.),
`--min-score`, `--top`, `--csv`, `--markdown`, `--abgleich "JJJJ-MM-TT HH:MM"`.
Engine-Pfad: `ASTRO_ENGINE_PATH` → `./engine` → Skriptverzeichnis.
Horā wie die Engine (60 Min. ab Sonnenaufgang); `HORA_PROPORTIONAL = True` für Tag-/Nachtzwölftel.

## Bewertung

Drei Ebenen: **Phase** (Daśā und Transit vom Janma-Mond) als Ampel oben, darunter der
**Muhūrta** jedes Zeitpunkts mit Score. Die Phase gilt für den ganzen Zeitraum gleich und
geht deshalb nicht in den Score ein.

**Sperren** (Zeitpunkt ausgeschlossen): Rāhu Kāla, Durmuhūrta, Amāvasyā, Viṣṭi-Karaṇa,
Vyatīpāta/Vaidhṛti-Yoga, Vadha-Tārā, Candrāṣṭama, Mond im 8. vom Muhūrta-Lagna,
Mars/Saturn/Rāhu/Ketu im Muhūrta-Lagna.

| Bereich | Faktor | Punkte |
|---|---|---|
| Nakṣatra | Trading-Nakṣatra: Aśvinī, Mṛgaśirā, Punarvasu, Hasta, Citrā, Svātī, Anurādhā, Śravaṇa, Dhaniṣṭhā, Revatī | +3 |
| | sonst MC-Nakṣatra des Modus (Kauf: Śatabhiṣā; Verkauf: Bharaṇī, Kṛttikā, Āśleṣā, Pūrvas) | +2 |
| | Kauf in Bharaṇī, Kṛttikā, Āśleṣā oder einer Pūrva | −2 |
| | Amṛta-Siddhi-Yoga (Vāra + Nakṣatra) | +2 |
| Tithi | 2, 3, 5, 7, 10, 11, 13 / Riktā 4, 9, 14 / Aṣṭamī | +2 / −3 / −1 |
| | Dagdha-Tithi des Wochentags | −2 |
| | zunehmender Mond, nur beim Kauf | +1 |
| Vāra | Mi / Do / Fr / Di, Sa | +3 / +2 / +1 / −2 |
| Yoga, Karaṇa | ungünstiger Yoga / Vaṇija-Karaṇa | −2 / +1 |
| Horā | Merkur / Jupiter, Venus / Mond / Mars, Saturn | +3 / +2 / +1 / −2 |
| | Horā-Herr = Herr des 2. oder 11. Radix-Hauses | +1 |
| Tagesabschnitte | Yamagaṇḍa / Gulika / Kāla Velā | −3 / −2 / −2 |
| | Abhijit (nicht mittwochs) | +2 |
| Tārā, Candrabala | Sampat, Kṣema, Sādhaka, Mitra, Parama Mitra / Vipat, Pratyari / Janma | +2 / −3 / −1 |
| | Mond im 1, 3, 6, 7, 10, 11 / 4, 12 vom Janma-Mond | +2 / −2 |
| Lagna | Sonne im Lagna | −1 |
| | 8. Haus besetzt | −2 |
| | Wohltäter in Kendra/Trikoṇa (max.) | +2 |
| Lagna-Herr | in Kendra/Trikoṇa/11 / in 6, 8, 12 | +1 / −2 |
| | mit Mars/Saturn/Rāhu/Ketu / verbrannt | −1 / −1 |
| Mond | im 6. oder 12. vom Muhūrta-Lagna | −2 |
| | innerhalb 8° von Rāhu oder Ketu | −3 |
| Merkur | im 1, 2, 5, 10, 11 / im 6, 8, 12 | +2 / −2 |
| | rückläufig / verbrannt (Orbis der Engine) / mit Übeltäter | −2 / −2 / −1 |
| | zusätzlich, wenn Merkur im Radix das 8. Haus beherrscht und belastet ist | −1 |
| | Svātī mit sauberem Merkur | +1 |
| 2. und 5. Haus | besetzt von Mars/Saturn/Ketu (Rāhu im 5. siehe unten) / nur Sonne | −2 / −1 |
| (je Haus) | aspektiert von Übeltäter / Herr im 6, 8, 12 / unbelastet | −1 / −1 / +1 |
| | Rāhu im 5. bei schwachem / gut gestelltem 5. Herrn | −3 / −1 |
| 11. Haus | Herr in Kendra/Trikoṇa/11 / in 6, 8, 12 (Verkauf ×2) | +1 / −2 |
| | Wohltäter im 11. (Verkauf ×2) / Rāhu im 11. | +1 / +1 |
| Jupiter, Venus | Jupiter im 2, 5, 9, 11 / aspektiert eines davon | +2 / +1 |
| | Venus im 2. oder 11. | +1 |
| Radix | Herren von Janma-Lagna und 5. Haus im Muhūrta in Kendra/Trikoṇa/11 / in 6, 8, 12 | je +1 / −1 |
| | Muhūrta-Lagna im 2., 11. / 1., 10. / 6., 8., 12. vom Janma-Lagna | +2 / +1 / −2 |

### Verkaufsprofil (Exit, Gewinnmitnahme)

Im Modus *verkauf* gilt eine eigene Gewichtung:

| Priorität | Kriterium | Umsetzung |
|---|---|---|
| Pflicht | 11. Haus / 11. Herr stark | Herr im 6/8/12, verbrannt oder mit Mars/Saturn/Rāhu/Ketu = Belastung |
| Pflicht | 2. Haus stabil | Mars/Saturn/Rāhu/Ketu im 2. oder belasteter 2. Herr = Belastung |
| Pflicht | Lagna-Herr stark | wie 11. Herr |
| | | je Pflichtkriterium: 1 Belastung −3, ab 2 Belastungen gesperrt (`PFLICHT_GRENZE`) |
| Sehr wichtig | Merkur stark | wie oben (Haus, rückläufig, verbrannt, Übeltäter) |
| Sehr wichtig | Mond nicht in 6/8/12 | 8. gesperrt, 6./12. −3 |
| Gut | Jupiter im oder Aspekt auf 2/5/11 | +2 / +1 |
| Gut | abnehmender Mond | +1 |
| Gut | Merkur-, Jupiter-, Venus-Horā | +3 / +2 / +2 |
| Radix | Herren von Janma-Lagna, 2., 5., 11. Haus im Muhūrta | gut gestellt +2, in 6/8/12 −2 |
| Nakṣatra | Hasta, Svātī, Anurādhā, Śravaṇa, Dhaniṣṭhā, Revatī / sonst MC-Vikraya | +2 / +1 |
| Meiden | Mars im 11. / Mars-Aspekt auf 2. oder 11. | −2 / −1 zusätzlich |

Lagna-Herr gut gestellt +2, 2. Haus unbelastet +2, 11. Haus doppelt gewichtet.

**Phase:** Daśā-Herren (Mahā-, Antar-, Pratyantardaśā) je nach Herrschaft über 1, 2, 5, 9, 11
bzw. 6, 8, 12 und Stellung im Radix; Transit Jupiter, Saturn (inkl. Sāḍe Sātī) und Rāhu vom
Janma-Mond.

★★★ ab 12, ★★ ab 8, ★ ab Mindestscore (Standard 5). Gewichte und Listen oben in
`muhurta_trading.py`. Börsenfeiertage werden nicht berücksichtigt.

## Hinweis

Astrologische Auswertung, keine Anlageberatung und kein Handelssignal.
