# Personal Garmin AI Coach
Ein autonomer Python-Agent, der Garmin-Fitnessdaten in Echtzeit analysiert und darauf basierend eine adaptive Trainingsplanung erstellt. Anstatt einem starren Plan zu folgen, reagiert die KI dynamisch auf tatsächliche körperliche Verfassung und letzte Performance. Ein Dashboard zur Konfiguration von Präferenzen und Einrichtung weiterer Nutzer wird aufgebaut.

**Technischer Stand**
- **Sprache & Bibliotheken:** Python; `garminconnect`, `python-dotenv` und `google-genai` (Gemini SDK) sind eingetragen in `requirements.txt`.
- **Authentifizierung:** Lädt `GARMIN_EMAIL` / `GARMIN_PASSWORD` aus dem Repo-Root `.env` (Pfad-Handling in `core/fetch_garmin_data.py`).
- **Datenbeschaffung:** Holt neueste Aktivitäten (`get_activities`), Tages-Stats (`get_stats`) und bei Bedarf Schlafdaten (`get_sleep_data`) über `python-garminconnect`.
- **Extrahierte Kennzahlen:** Body Battery, Sleep Score (priorisiert `dailySleepDTO.sleepScores.overall.value`), durchschnittlicher Stress, VO2Max (bevorzugt aus Benutzerprofil `userData.vo2MaxRunning`), Ruhepuls.
- **Aktivitätsverarbeitung:** Für Ausdaueraktivitäten wird der Training Effect verwendet; für Kraft/Strength-Aktivitäten werden `summarizedExerciseSets`-Kategorien extrahiert und `distance` weggelassen.
- **Fehlerbehandlung & Robustheit:** Spezielle Behandlung für Authentifizierungs- und Verbindungsfehler; Exponential-Backoff für Garmin-Rate-Limits (HTTP 429) implementiert.
- **Persistenz:** Speichert die letzten 7 Tage in `data/daily_stats.json` und die letzten 7 Aktivitäten in `data/activities.json` via `core/data_persistence.py`.
- **Coach-Logic:** `core/coach_agent.py` baut aus den JSON-Daten den Coach-Prompt und kann optional mit `GOOGLE_API_KEY` direkt Gemini ansprechen.
- **Coach-Lauf:** `core/coach_agent.py --run-model` nutzt Gemini mit kompaktem JSON-Output; falls das Modell keine sauber parsebare Antwort liefert, greift ein lokaler Fallback auf dieselben Garmin-Daten.
- **Coach-Cache:** Empfehlungen werden 6 Stunden zwischengespeichert; ohne `--refresh` wird innerhalb dieses Fensters einfach die letzte Empfehlung geladen statt einen neuen Prompt zu senden.
- **Ausgabeformat:** Für Discord/Streamlit gibt es jetzt eine kompakte, gut lesbare Markdown-Ausgabe mit Titel, Intensität, Empfehlung, Begründung und Quelle.
- **Benachrichtigung:** Neue Modell-Empfehlungen (`source=model`) können optional per Discord-DM oder E-Mail versendet werden.
- **Streamlit-Dashboard:** Der Ordner `web/` enthält jetzt den Dashboard-Startpunkt mit Verifikations-Gate, Datenansicht, Präferenzen und Coach-Empfehlung.
- **Aufräumen:** Entwicklungs-Debug-Ausgaben/Helper entfernt; Produktionslauf entspricht jetzt schlanker Ausgabe + JSON-Persistenz.

**Schnellstart**
- `.env` im Repo-Root mit `GARMIN_EMAIL` und `GARMIN_PASSWORD` anlegen.
- Optional für den Coach-Agenten: `GOOGLE_API_KEY` in `.env` hinterlegen, wenn der Prompt direkt an Gemini gesendet werden soll.
- Coach direkt starten:

```bash
python core/coach_agent.py --run-model
```

- Cache bewusst erneuern:

```bash
python core/coach_agent.py --run-model --refresh
```

- Streamlit-Dashboard starten:

```bash
streamlit run web/app.py
```

- Abhängigkeiten installieren:

```bash
pip install -r requirements.txt
```

- Script ausführen:

```bash
python core/fetch_garmin_data.py
```

**Hinweis:** Häufige Aufrufe können von Garmin ip-basierend rate-limited werden; das Skript verwendet Backoff, aber vermeiden Sie zu häufige Ausführungen.
