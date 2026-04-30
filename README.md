# Personal Garmin AI Coach
Ein autonomer Python-Agent, der Garmin-Fitnessdaten in Echtzeit analysiert und darauf basierend eine adaptive Trainingsplanung erstellt. Anstatt einem starren Plan zu folgen, reagiert die KI dynamisch auf tatsächliche körperliche Verfassung und letzte Performance. Ein Dashboard zur Konfiguration von Präferenzen und Einrichtung weiterer Nutzer wird aufgebaut.

**Technischer Stand**
- **Sprache & Bibliotheken:** Python; `garminconnect`, `python-dotenv`, `groq` und `streamlit` sind eingetragen in `requirements.txt`.
- **Authentifizierung:** 
  - Garmin: Lädt `GARMIN_EMAIL` / `GARMIN_PASSWORD` aus dem Repo-Root `.env`.
  - Discord: Verwendet `DISCORD_BOT_TOKEN` für DM-Versand und Server-Invite über `DISCORD_SERVER_INVITE`.
  - Coach LLM: `GROQ_CLOUD_KEY` für Groq-Modell.
- **User Management:**
  - Discord-basierte Registrierung und Verifikation (`core/user_management.py`).
  - Benutzer registrieren sich mit ihrer Discord-ID; ein 6-stelliger Verifikations-Code wird per DM gesendet.
  - Nach erfolgreicher Verifikation erhalten Nutzer Zugang zum Dashboard.
  - Benutzerprofile werden in `data/users.json` mit Locking persistent gespeichert.
- **Datenbeschaffung:**
  - Holt neueste Aktivitäten, Tages-Stats und Schlafdaten über `python-garminconnect`.
  - Alternative: Manuelle Eingabe von Gesundheitsdaten (Sleep Score, Body Battery, Stress, VO2Max, Resting HR) und Aktivitäten über das Dashboard.
  - Garmin OAuth ist als Stub vorbereitet (`core/data_entry.py`); künftige Implementierung für automatische Synchronisation geplant.
- **Extrahierte Kennzahlen:** Body Battery, Sleep Score, durchschnittlicher Stress, VO2Max, Ruhepuls.
- **Coach-Logic:**
  - `core/coach_agent.py` baut aus JSON-Daten einen Prompt und nutzt Groq für die Empfehlung.
  - Deterministische Fallback-Empfehlung mit Sicherheit (z.B. "Ruhetag" bei Body Battery < 35).
  - Prompt berücksichtigt Trainingsziel (Kraft Fokus, Ausdauer Fokus, Marathon, etc.) und erzwingt konkrete Empfehlungen (keine Wochenpläne).
  - Recovery-Schutz: Body Battery < 50 oder Sleep Score < 60 triggern Low-Intensity-Recommendations.
  - Intensity-Baseline: Zielabhängig (z.B. Kraft Fokus → 9/10, Ausdauer → 6–8/10).
- **Coach-Cache:** Empfehlungen werden 6 Stunden zwischengespeichert; `--refresh` erzwingt neue Anfrage.
- **Benachrichtigung:** Neue Modell-Empfehlungen können per Discord-DM versendet werden (optional pro Nutzer).
- **Streamlit-Dashboard:**
  - **Verifikations-Gate:** Benutzer müssen sich mit Discord-ID registrieren und Code verifizieren.
  - **Dashboard-Tab:** Fitnessdaten, Coach-Empfehlung mit Intensität/Begründung, letzte Aktivitäten.
  - **Datenquellen-Tab:** Garmin OAuth-Setup (Stub) und manuelle Daten-/Aktivitäten-Eingabeformulare.
  - Session-basierte Profil-Verwaltung (Mobilität, Trainingsziel, Sonstige Aspekte).
  - Discord DM-Optionen konfigurierbar im Sidebar.
- **Persistent Storage:**
  - `data/users.json` - Benutzer und Verifikationsstatus.
  - `data/daily_stats.json` - Letzte 7 Tage Fitnessdaten.
  - `data/activities.json` - Letzte 7 Aktivitäten.
  - `data/user_profile.json` - Dashboard-Präferenzen (pro Anmeldung).

**Umgebungsvariablen (.env)**
```env
# Garmin-Daten
GARMIN_EMAIL=your_email@example.com
GARMIN_PASSWORD=your_password

# Groq LLM (Coach-Engine)
GROQ_CLOUD_KEY=your_groq_api_key

# Discord (Verifikation & Benachrichtigungen)
DISCORD_BOT_TOKEN=your_bot_token_here
DISCORD_SERVER_INVITE=https://discord.gg/YOUR_SERVER_CODE

# Optional: Garmin OAuth (Zukunft)
# GARMIN_CLIENT_ID=your_client_id
# GARMIN_REDIRECT_URI=http://localhost:8501/callback
```

**Schnellstart**

1. **Abhängigkeiten installieren:**
   ```bash
   pip install -r requirements.txt
   ```

2. **.env im Repo-Root anlegen** mit Garmin-Credentials, `GROQ_CLOUD_KEY` und `DISCORD_BOT_TOKEN`.

3. **Streamlit-Dashboard starten:**
   ```bash
   streamlit run web/app.py
   ```

4. **Registrieren & Verifizieren:**
   - Discord-ID eingeben.
   - "Registrieren & Code senden" klicken → Code kommt per Discord DM.
   - Code eingeben und verifizieren.
   - Nach erfolgreichem Login: Dashboard und Datenquellen-Optionen verfügbar.

5. **Daten eintragen:**
   - **Garmin:** "Garmin-Daten neu laden" im Sidebar oder über den Datenquellen-Tab.
   - **Manuell:** Im Datenquellen-Tab können Gesundheitsdaten und Aktivitäten manuell eingetragen werden.

6. **(Optional) Coach direkt aufrufen:**
   ```bash
   python core/coach_agent.py --run-model
   ```

**Tests**

Unit-Tests für Registrierung und Verifikation:
```bash
python tests/test_user_management.py
```

**Architektur**

```
PersonalGarminAICoach/
├── core/
│   ├── coach_agent.py          # Coach-Logic, LLM-Integration, Fallbacks
│   ├── fetch_garmin_data.py    # Garmin-Daten abrufen
│   ├── data_persistence.py     # JSON-Speicherung
│   ├── user_management.py      # Registrierung, Verifikation, User-Store
│   ├── notification_service.py # Discord DM-Versand
│   └── data_entry.py           # Garmin OAuth Stubs, manuelle Eingabeformulare
├── web/
│   └── app.py                  # Streamlit Dashboard
├── data/
│   ├── users.json              # Benutzerprofile
│   ├── daily_stats.json        # Tägliche Fitnessdaten
│   ├── activities.json         # Aktivitäten
│   └── user_profile.json       # Dashboard-Präferenzen
├── tests/
│   └── test_user_management.py # Unit-Tests
├── requirements.txt            # Abhängigkeiten
└── README.md                   # Diese Datei
```

**Hinweise**

- Die Verifikation ist Discord-basiert: Ein Bot muss eingeladen werden und darf DMs senden.
- Garmin OAuth ist momentan ein Stub und wird künftig implementiert.
- Manuelle Dateneinträge überschreiben nicht automatisch Garmin-Daten; beide Quellen können parallel genutzt werden.
- Der Coach bevorzugt Groq LLM; ohne `GROQ_CLOUD_KEY` greift das System auf deterministische Fallbacks zurück.
```

**Hinweis:** Häufige Aufrufe können von Garmin ip-basierend rate-limited werden; das Skript verwendet Backoff, aber vermeiden Sie zu häufige Ausführungen.
