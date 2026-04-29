# Personal Garmin AI Coach
Ein autonomer Python-Agent, der Garmin-Fitnessdaten in Echtzeit analysiert und darauf basierend eine adaptive Trainingsplanung erstellt. Anstatt einem starren Plan zu folgen, reagiert die KI dynamisch auf tatsächliche körperliche Verfassung und letzte Performance. Ein Dashboard zur Konfiguration von Präferenzen und Einrichtung weiterer Nutzer wird aufgebaut.

**Technischer Stand**
- **Sprache & Bibliotheken:** Python; `garminconnect` (Garmin-API-Client) und `python-dotenv` (für `.env`-Laden) sind eingetragen in `requirements.txt`.
- **Authentifizierung:** Lädt `GARMIN_EMAIL` / `GARMIN_PASSWORD` aus dem Repo-Root `.env` (Pfad-Handling in `core/fetch_garmin_data.py`).
- **Datenbeschaffung:** Holt neueste Aktivitäten (`get_activities`), Tages-Stats (`get_stats`) und bei Bedarf Schlafdaten (`get_sleep_data`) über `python-garminconnect`.
- **Extrahierte Kennzahlen:** Body Battery, Sleep Score (priorisiert `dailySleepDTO.sleepScores.overall.value`), durchschnittlicher Stress, VO2Max (bevorzugt aus Benutzerprofil `userData.vo2MaxRunning`), Ruhepuls.
- **Aktivitätsverarbeitung:** Für Ausdaueraktivitäten wird der Training Effect verwendet; für Kraft/Strength-Aktivitäten werden `summarizedExerciseSets`-Kategorien extrahiert und `distance` weggelassen.
- **Fehlerbehandlung & Robustheit:** Spezielle Behandlung für Authentifizierungs- und Verbindungsfehler; Exponential-Backoff für Garmin-Rate-Limits (HTTP 429) implementiert.
- **Persistenz:** Speichert die letzten 7 Tage in `data/daily_stats.json` und die letzten 7 Aktivitäten in `data/activities.json` via `core/data_persistence.py`.
- **Aufräumen:** Entwicklungs-Debug-Ausgaben/Helper entfernt; Produktionslauf entspricht jetzt schlanker Ausgabe + JSON-Persistenz.

**Schnellstart**
- `.env` im Repo-Root mit `GARMIN_EMAIL` und `GARMIN_PASSWORD` anlegen.
- Abhängigkeiten installieren:

```bash
pip install -r requirements.txt
```

- Script ausführen:

```bash
python core/fetch_garmin_data.py
```

**Hinweis:** Häufige Aufrufe können von Garmin ip-basierend rate-limited werden; das Skript verwendet Backoff, aber vermeiden Sie zu häufige Ausführungen.
