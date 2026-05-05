# Personal Garmin AI Coach
An autonomous Python agent that analyzes Garmin fitness data in real time and generates adaptive training recommendations. Instead of following a rigid plan, the AI responds dynamically to current readiness and recent performance. A dashboard is included for configuring preferences and onboarding additional users.

**Technical Overview**
- **Language and libraries:** Python; `garminconnect`, `python-dotenv`, `groq`, and `streamlit` are listed in `requirements.txt`.
- **Authentication:**
  - Garmin: loads `GARMIN_EMAIL` / `GARMIN_PASSWORD` from the repo-root `.env`.
  - Discord: uses `DISCORD_BOT_TOKEN` for DM delivery and server invites via `DISCORD_SERVER_INVITE`.
  - Coach LLM: uses `GROQ_CLOUD_KEY` for the Groq model.
- **User management:**
  - Discord-based registration and verification (`core/user_management.py`).
  - Users register with their Discord ID; a 6-digit verification code is sent by DM.
  - After verification, users can access the dashboard.
  - User profiles are stored persistently in `data/users.json` with locking.
- **Data acquisition:**
  - Fetches recent activities, daily stats, and sleep data through `python-garminconnect`.
  - Alternative: manual entry of health data (Sleep Score, Body Battery, Stress, VO2Max, Resting HR) and activities through the dashboard.
  - Garmin OAuth is prepared as a stub in `core/data_entry.py`; a future automatic sync implementation is planned.
- **Extracted metrics:** Body Battery, Sleep Score, average stress, VO2Max, resting heart rate.
- **Coach logic:**
  - `core/coach_agent.py` builds a prompt from JSON data and uses Groq for recommendations.
  - Deterministic fallback recommendation with safety checks, e.g. "Rest Day" when Body Battery < 35.
  - The prompt considers training goals such as Strength Focus, Endurance Focus, Marathon, and more, and forces concrete recommendations instead of weekly plans.
  - Recovery protection: Body Battery < 50 or Sleep Score < 60 triggers low-intensity recommendations.
  - Intensity baseline is goal-dependent, e.g. Strength Focus -> 9/10, Endurance -> 6-8/10.
- **Coach cache:** recommendations are cached for 6 hours; `--refresh` forces a new request.
- **Notifications:** new model recommendations can be sent via Discord DM per user.
- **Streamlit dashboard:**
  - **Verification gate:** users must register with Discord ID and verify a code.
  - **Dashboard tab:** fitness data, coach recommendation with intensity and reasoning, recent activities.
  - **Data Sources tab:** Garmin OAuth stub and manual health/activity entry forms.
  - Session-based profile management: mobility, training goal, and other considerations.
  - Discord DM options are configurable in the sidebar.
- **Persistent storage:**
  - `data/users.json` - users and verification state.
  - `data/daily_stats.json` - last 7 days of fitness data.
  - `data/activities.json` - last 7 activities.
  - `data/user_profile.json` - dashboard preferences per login.

**Environment Variables (.env)**
```env
# Garmin data
GARMIN_EMAIL=your_email@example.com
GARMIN_PASSWORD=your_password

# Groq LLM (coach engine)
GROQ_CLOUD_KEY=your_groq_api_key

# Discord (verification and notifications)
DISCORD_BOT_TOKEN=your_bot_token_here
DISCORD_SERVER_INVITE=https://discord.gg/YOUR_SERVER_CODE

# Optional: Garmin OAuth (future)
# GARMIN_CLIENT_ID=your_client_id
# GARMIN_REDIRECT_URI=http://localhost:8501/callback
```

**Quick Start**

1. **Install dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

2. **Create a `.env` file in the repo root** with Garmin credentials, `GROQ_CLOUD_KEY`, and `DISCORD_BOT_TOKEN`.

3. **Start the Streamlit dashboard:**
   ```bash
   streamlit run web/app.py
   ```

4. **Register and verify:**
   - Enter your Discord ID.
   - Click "Register & send code" -> the code is delivered by Discord DM.
   - Enter the code and verify it.
   - After sign-in, the dashboard and data source options become available.

5. **Enter data:**
   - **Garmin:** use "Refresh Garmin data" in the sidebar or the Data Sources tab.
   - **Manual:** enter health data and activities in the Data Sources tab.

6. **Optional: run the coach directly:**
   ```bash
   python core/coach_agent.py --run-model
   ```

**Tests**

Unit tests for registration and verification:
```bash
python tests/test_user_management.py
```

**Architecture**

```
PersonalGarminAICoach/
├── core/
│   ├── coach_agent.py          # Coach logic, LLM integration, fallbacks
│   ├── fetch_garmin_data.py    # Fetch Garmin data
│   ├── data_persistence.py     # JSON storage
│   ├── user_management.py      # Registration, verification, user store
│   ├── notification_service.py # Discord DM delivery
│   └── data_entry.py           # Garmin OAuth stubs, manual entry forms
├── web/
│   └── app.py                  # Streamlit dashboard
├── data/
│   ├── users.json              # User profiles
│   ├── daily_stats.json        # Daily fitness data
│   ├── activities.json         # Activities
│   └── user_profile.json       # Dashboard preferences
├── tests/
│   └── test_user_management.py # Unit tests
├── requirements.txt            # Dependencies
└── README.md                   # This file
```

**Notes**

- Verification is Discord-based: a bot must be invited and allowed to send DMs.
- Garmin OAuth is currently a stub and will be implemented later.
- Manual entries do not automatically overwrite Garmin data; both sources can be used in parallel.
- The coach prefers the Groq LLM; without `GROQ_CLOUD_KEY` the system falls back to deterministic recommendations.

**Note:** Frequent Garmin requests can be rate limited by IP; the script uses backoff, but avoid calling it too often.
