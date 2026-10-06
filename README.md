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
   | `GEBURT_TZ` | `Europe/Zurich` |
   | `GEBURT_LAT` | `47.4833` |
   | `GEBURT_LON` | `7.7356` |

5. *Actions → Muhūrta Trading → Run workflow*. Tabelle in der Job-Zusammenfassung
   (mit Engine-Commit), CSV als Artefakt; zusätzlich jeden Montag automatisch.
   Mit Eingabe *abgleich* erscheinen statt der Fenster die Detailwerte zum Vergleich mit dem Report.

## Lokal

```bash
git clone https://github.com/<github-name>/<ved-chart-calc-repo> engine
pip install -r requirements.txt
python muhurta_trading.py --geburt "1957-08-24 13:55" --geburt-tz Europe/Zurich \
  --geburt-lat 47.4833 --geburt-lon 7.7356 --modus kauf --markt SIX --tage 7
```

Optionen: `--lat/--lon/--tz` (Handelsort, Standard Wädenswil), `--von`, `--tage`,
`--modus kauf|verkauf`, `--markt SIX|XETRA|LSE|NYSE|KRYPTO|ALLE`, `--schritt` (Min.),
`--min-score`, `--top`, `--csv`, `--markdown`, `--abgleich "JJJJ-MM-TT HH:MM"`.
Engine-Pfad: `ASTRO_ENGINE_PATH` → `./engine` → Skriptverzeichnis.
Horā wie die Engine (60 Min. ab Sonnenaufgang); `HORA_PROPORTIONAL = True` für Tag-/Nachtzwölftel.

## Bewertung

**Sperren** (Zeitpunkt ausgeschlossen): Rāhu Kāla, Viṣṭi-Karaṇa, Amāvasyā,
Vyatīpāta/Vaidhṛti-Yoga, Vadha-Tārā, Candrāṣṭama.

| Faktor | Punkte |
|---|---|
| Nakṣatra günstig für Modus / Gegenmodus (Muhūrta Cintāmaṇi) | +3 / −2 |
| Tithi 2,3,5,7,10,11,13 / Riktā 4,9,14 / Aṣṭamī | +2 / −3 / −1 |
| Śukla Pakṣa | +1 |
| Vāra Mi, Do / Fr / Sa / Di | +2 / +1 / −1 / −2 |
| Ungünstiger Yoga | −2 |
| Vaṇija-Karaṇa | +1 |
| Tārā Sampat, Kṣema, Sādhaka, Mitra, Parama Mitra / Vipat, Pratyari / Janma | +2 / −3 / −1 |
| Candrabala 1,3,6,7,10,11 / 4,12 | +2 / −2 |
| Horā Merkur / Jupiter, Venus / Mond / Mars, Saturn | +3 / +2 / +1 / −2 |
| Horā-Herr = Herr des 2. oder 11. Radix-Hauses | +1 |
| Yamagaṇḍa / Gulika Kāla | −3 / −2 |
| Abhijit Muhūrta (nicht mittwochs) | +2 |
| 8. Haus vom Muhūrta-Lagna besetzt | −2 |
| Wohltäter in Kendra/Trikoṇa (max.) | +2 |
| Lagna im 2./11. / 1./10. / 6./8./12. vom Janma-Lagna | +2 / +1 / −2 |

★★★ ab 12, ★★ ab 8, ★ ab Mindestscore (Standard 5). Gewichte und Listen oben in
`muhurta_trading.py`. Börsenfeiertage werden nicht berücksichtigt.

## Hinweis

Astrologische Auswertung, keine Anlageberatung und kein Handelssignal.
