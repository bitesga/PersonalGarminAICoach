# Personal Garmin AI Coach

An autonomous Python app that analyzes Garmin fitness data and produces adaptive training recommendations. The system combines Garmin data ingestion, an LLM-backed coach, deterministic fallbacks, and a Streamlit dashboard for configuration, onboarding, and manual data entry.

## Highlights

- AI coach that produces concrete next-session recommendations with safety checks
- Streamlit dashboard for onboarding, profile settings, and data review
- Garmin Connect integration and manual data entry
- Discord DM and email notifications
- Automatic recommendations at two daily times (server-local time)
- Weather-aware recommendations based on user location
- English/German language switch in the UI and generated outputs
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

# Optional Vault OSS (secrets)
VAULT_ADDR=http://127.0.0.1:8200
VAULT_TOKEN=your_vault_token
VAULT_KV_PATH=kv/garmin/default
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
  - Location (latitude/longitude) for weather-aware advice
  - Language switch for English or German

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
  - Garmin credentials (saved locally and, if Vault is configured, also written to Vault on connect)
  - Manual health metrics input
  - Manual activity input
  - Manual weather testing values for recommendation checks
  - Delete manual entries

## Language Settings

The app includes a language selector in the top-right corner of the dashboard.

- Supported languages: English and German.
- The selected language is saved per user profile.
- The coach output, dashboard labels, notifications, and email/Discord messages follow the selected language.
- Automatic recommendations use the same saved language for each user.

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

## Weather Awareness

The coach can adjust recommendations based on current weather using Open-Meteo.

- Location is configured in the sidebar (latitude/longitude) and saved per user.
- Weather is fetched every 10 minutes while the dashboard is open.
- The main recommendation is outdoor only when temperature is between 5°C and 35°C and precipitation is at most 20 mm.
- Otherwise the main recommendation is indoor.
- The recommendation and reasoning explicitly mention the weather values and the indoor/outdoor decision.
- The same weather context is used for manual and automatic recommendations.

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

A sample unit file is included as `personal-garmin-ai-coach.service`. It can also be enabled under the alias `PGAIC.service`:

```
[Unit]
Description=Personal Garmin AI Coach
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=/home/ubuntu/PersonalGarminAICoach
EnvironmentFile=/home/ubuntu/PersonalGarminAICoach/.env
ExecStart=/home/ubuntu/PersonalGarminAICoach/.venv/bin/python -m streamlit run /home/ubuntu/PersonalGarminAICoach/web/app.py --server.port=8080 --server.address=0.0.0.0
Restart=always
RestartSec=20

[Install]
WantedBy=multi-user.target
```

Enable the service and follow the logs:

```bash
sudo systemctl daemon-reload
sudo systemctl enable personal-garmin-ai-coach.service
sudo systemctl start personal-garmin-ai-coach.service
sudo systemctl status personal-garmin-ai-coach.service
sudo journalctl -u personal-garmin-ai-coach.service -f
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

## Security Notes

- Garmin credentials are stored in `garmin_credentials.json` for local use. If you plan to host real users, move secrets into a secure vault (for example, HashiCorp Vault, AWS Secrets Manager, or Azure Key Vault).
- Longer term, prefer an OAuth-based flow if Garmin makes it available for small developers.

## Vault OSS Setup (Optional)

If you run Vault OSS on your Ubuntu server, the app can read Garmin credentials directly from Vault and skip local JSON storage.
When a user clicks **Connect Garmin Account** in the dashboard, the app now writes the credentials to both local JSON and Vault if `VAULT_ADDR` and `VAULT_TOKEN` are configured.

- The dashboard shows a one-time toast when Vault is enabled via env vars.
- Vault access is controlled by `VAULT_ADDR`, `VAULT_TOKEN`, and `VAULT_KV_PATH`.
- The app expects a KV v2 path like `kv/garmin/default` and supports per-user paths via `kv/garmin/{user_id}`.
- If Vault is unavailable, the app falls back to the local JSON credential store.

1. Install Vault OSS on the server.
2. Initialize and unseal Vault.
3. Enable KV v2 at the `kv` mount (or use your own mount name).
4. Store Garmin credentials:
  ```bash
  export VAULT_ADDR=http://127.0.0.1:8200
  export VAULT_TOKEN=your_vault_token
  vault kv put -mount=kv garmin/default email='your_email@example.com' password='your_password'
  ```
5. Set these env vars in `.env` (or your systemd service):
  ```env
  VAULT_ADDR=http://127.0.0.1:8200
  VAULT_TOKEN=your_vault_token
  VAULT_KV_PATH=kv/garmin/default
  ```

Recommended example:

- Shared mount: `kv/garmin/default`
- Per-user mount: `kv/garmin/{user_id}`

Per-user option:

- Use `VAULT_KV_PATH=kv/garmin/{user_id}` and store each user separately:
  ```bash
  vault kv put kv/garmin/discord_123456789 email=user@example.com password=secret
  ```

### Vault Troubleshooting

- `permission denied`: the token does not have read access to the KV path.
- `no handler for route`: the KV v2 mount path is incorrect or KV is not enabled.
- `key not found`: the record does not exist at the provided `VAULT_KV_PATH`.
- `connection refused`: Vault is not reachable at `VAULT_ADDR`.
- To inspect the app logs on systemd, use `journalctl -u personal-garmin-ai-coach.service -f`.

## Operational Notes

- Garmin endpoints can rate-limit if called too frequently; the fetch script uses backoff.
- If `GROQ_CLOUD_KEY` is missing, the coach uses deterministic fallbacks.
- Email requires SMTP credentials; otherwise it is skipped.
- The auto scheduler requires the Streamlit process to remain running.
- The Streamlit service now logs to stderr, so `journalctl -u personal-garmin-ai-coach.service -f` shows app logs and Vault credential save/load messages.

## License

This repository is personal. 
