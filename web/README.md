# Streamlit Dashboard

Dieses Verzeichnis enthält das Dashboard für den Personal Garmin AI Coach.

## Start

```bash
streamlit run web/app.py
```

## Funktionen
- Verifikations-Gate als Platzhalter für den späteren E-Mail-Zugriff
- Anzeige der letzten Fitnessdaten aus `data/daily_stats.json`
- Anzeige der letzten Aktivitäten aus `data/activities.json`
- Einstellbare Präferenzen für Mobilität, Ziel und Trainingsstil
- Anzeige der aktuellen Coach-Empfehlung mit 6-Stunden-Cache
- Kopierbare Nachricht für Discord, Push oder spätere Integrationen
- Button zum Neuladen der Garmin-Fitnessdaten direkt aus dem Dashboard
- Optionaler Versand neuer Modell-Empfehlungen per Discord-DM oder E-Mail

## Hinweise
- Die E-Mail-Verifikation ist aktuell lokal simuliert und kann später durch einen echten Mail-Flow ersetzt werden.
- Die aktuelle Empfehlung wird über `core/coach_agent.py` bezogen und spart durch den Cache Token.
- Versand wird nur bei neuer Modell-Empfehlung ausgelöst (`source=model`), nicht bei Cache-Treffern.
- E-Mail-Versand nutzt immer die verifizierte Account-E-Mail aus dem Dashboard.

## Notification Setup
- Discord-DM:
	- `DISCORD_BOT_TOKEN` in `.env` setzen
	- Im Dashboard `Discord User-ID` des Empfängers eintragen
- E-Mail (SMTP):
	- Dashboard: Schalter `E-Mail senden` aktivieren (kein separates E-Mail-Feld)
	- `SMTP_HOST`
	- `SMTP_PORT` (z. B. `587`)
	- `SMTP_USERNAME`
	- `SMTP_PASSWORD`
	- `SMTP_FROM` (optional, sonst Username)
	- `SMTP_USE_TLS` (`true`/`false`, optional)
