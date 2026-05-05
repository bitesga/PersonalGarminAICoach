# Personal Garmin AI Coach

An autonomous Python app that analyzes Garmin fitness data and produces adaptive training recommendations. The system combines Garmin data ingestion, an LLM-backed coach, deterministic fallbacks, and a Streamlit dashboard for configuration, onboarding, and manual data entry.

## Highlights

- AI coach that produces concrete next-session recommendations with safety checks
- Streamlit dashboard for onboarding, profile settings, and data review
- Garmin Connect integration and manual data entry
- Discord DM and email notifications
- Automatic recommendations at two daily times (server-local time)
- 6-hour cache to reduce LLM calls
- Dockerfile and systemd service for deployment

## Project Structure

```
PersonalGarminAICoach/
├── core/
│   ├── auto_recommendation.py  # Scheduled Garmin refresh + recommendation + notifications
│   ├── coach_agent.py          # Coach logic, LLM prompt, fallbacks
│   ├── fetch_garmin_data.py    # Garmin Connect data fetch
│   ├── data_entry.py           # Manual data entry forms
│   ├── data_persistence.py     # JSON persistence helpers
│   ├── notification_service.py # Discord + email notifications
│   └── user_management.py      # Registration, verification, users
├── web/
│   ├── app.py                  # Streamlit dashboard
│   └── auth.py                 # Login + verification gate
├── data/
│   ├── daily_stats.json         # 7-day health metrics
│   ├── activities.json          # Last 7 activities
│   ├── coach_recommendation.json# Cached recommendation (global fallback)
│   └── users/                   # Per-user data and profiles
├── images/                      # Logo for emails
├── Dockerfile
├── requirements.txt
└── README.md
```

## Requirements

- Python 3.9+ (3.11 recommended)
- Garmin Connect credentials
- Groq API key (optional but recommended)
- Discord bot token for DM notifications (optional)
- Email SMTP credentials for HTML notifications (optional)

## Environment Variables (.env)

Create a `.env` file in the repo root with the following values:

```env
# Garmin data
GARMIN_EMAIL=your_email@example.com
GARMIN_PASSWORD=your_password

# Groq LLM (coach engine)
GROQ_CLOUD_KEY=your_groq_api_key

# Discord (verification and notifications)
DISCORD_BOT_TOKEN=your_bot_token_here
DISCORD_SERVER_INVITE=https://discord.gg/YOUR_SERVER_CODE

# Optional email notifications
MAIL_USERNAME=your_smtp_username
MAIL_PASSWORD=your_smtp_password
```

## Quick Start (Local)

1. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

2. Start the Streamlit dashboard:
   ```bash
   streamlit run web/app.py
   ```

3. Open the dashboard in your browser:
   - Default Streamlit port is 8501 unless you override it.

4. Register and verify:
   - Enter your Discord ID or email (based on the selected auth path)
   - Request a 6-digit verification code
   - Verify to unlock the dashboard

## Dashboard Features

- Access and Profile
  - Mobility selection
  - Training goal selection
  - Additional notes for the coach

- Coach
  - Refresh Garmin data on demand
  - Request a new AI recommendation
  - View status and last refresh time

- Automatic Recommendations
  - Enable auto mode
  - Choose two daily times (server local time)
  - Auto mode will refresh Garmin data and send a new recommendation

- Accounts and Notifications
  - Discord DM support
  - Email notifications (HTML)

- Data Sources
  - Garmin credentials
  - Manual health metrics input
  - Manual activity input
  - Delete manual entries

## Automatic Recommendations

The scheduler is built into the Streamlit process and runs every 60 seconds. It checks the two configured daily times per user and triggers the following sequence:

1. Refresh Garmin data
2. Generate a new recommendation
3. Send notifications (Discord and/or email)
4. Update last-run timestamps for each configured time

Notes:
- The scheduler only runs while the Streamlit app process is running.
- Times use the server local time.
- Settings are stored per user in `data/users/<user_id>/user_profile.json`.

## Coach Logic Overview

The coach is designed to be concrete and safe:

- Always returns JSON with keys: `title`, `recommendation`, `alternative`, `intensity`, `reasoning`
- Avoids weekly plans or generic guidance
- Uses health data to enforce recovery protections
  - Sleep < 60 or Body Battery < 50 forces low intensity (1-4)
  - Body Battery < 35 forces a Rest Day
- Goal-based intensity baseline
  - Strength Focus: high intensity
  - Endurance Focus: moderate-high
  - Build Strength and Endurance: balanced
- Caching
  - Recommendations are cached for 6 hours per user
  - `refresh=True` bypasses the cache

## Notifications

- Discord
  - Uses `DISCORD_BOT_TOKEN`
  - Sends embeds with metrics and recommendation details

- Email
  - Uses `MAIL_USERNAME` and `MAIL_PASSWORD`
  - Sends HTML and plain text with embedded logo

Notifications are only sent for fresh model recommendations (not cache hits).

## Running the Coach Directly

```bash
python core/coach_agent.py --run-model
```

## Tests

```bash
python tests/test_user_management.py
```

## Docker Deployment (Port 8080)

Build and run the container:

```bash
docker build -t personal-garmin-ai-coach .
docker run -d --name coach --restart unless-stopped --env-file .env -p 8080:8080 personal-garmin-ai-coach
```

Then visit: `http://your-server-ip:8080`

## Systemd Service (Port 8080)

A sample unit file is included as `personal-garmin-ai-coach.service`. Update paths if needed:

```
[Unit]
Description=Personal Garmin AI Coach
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=/home/ubuntu/PersonalGarminAICoach
EnvironmentFile=/home/ubuntu/PersonalGarminAICoach/.env
ExecStart=/usr/bin/python3 -m streamlit run /home/ubuntu/PersonalGarminAICoach/web/app.py --server.port=8080 --server.address=0.0.0.0
Restart=always
RestartSec=20

[Install]
WantedBy=multi-user.target
```

Enable the service:

```bash
sudo systemctl daemon-reload
sudo systemctl enable personal-garmin-ai-coach
sudo systemctl start personal-garmin-ai-coach
sudo systemctl status personal-garmin-ai-coach
```

## Data Files

Per-user data lives under `data/users/<user_id>/`:

- `user_profile.json` - profile, notification settings, auto times
- `daily_stats.json` - last 7 days of health metrics
- `activities.json` - last 7 activities
- `coach_recommendation.json` - cached recommendation
- `garmin_credentials.json` - stored Garmin login

Global fallbacks (if per-user persistence fails):

- `data/daily_stats.json`
- `data/activities.json`
- `data/coach_recommendation.json`

## Operational Notes

- Garmin endpoints can rate-limit if called too frequently; the fetch script uses backoff.
- If `GROQ_CLOUD_KEY` is missing, the coach uses deterministic fallbacks.
- Email requires SMTP credentials; otherwise it is skipped.
- The auto scheduler requires the Streamlit process to remain running.

## License

This repository is private/personal. Add a license if you plan to distribute it.
