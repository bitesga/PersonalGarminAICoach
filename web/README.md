# Streamlit Dashboard

This folder contains the Streamlit dashboard for Personal Garmin AI Coach. It handles authentication, profile settings, data review, and triggers for Garmin refresh + recommendations.

## Entry Point

```bash
streamlit run web/app.py
```

## Key Screens

- Authentication gate
  - Discord or email registration
  - 6-digit verification codes
  - Auth token persistence for returning users

- Dashboard tab
  - Summary metrics (Sleep, Body Battery, Stress, VO2Max, RHR, Acute Load)
  - Current recommendation (title, intensity, alternative, reasoning)
  - Recent activities table

- Data Sources tab
  - Garmin credential capture (writes to local JSON and Vault when configured)
  - Manual health entry
  - Manual activity entry
  - Deletion of manual entries

- Sidebar configuration
  - Mobility and goal selection
  - Coach refresh actions
  - Automatic recommendation times
  - Notification settings

## Automatic Recommendations

The sidebar lets each user enable two daily times. The scheduler runs inside the Streamlit process and performs:

1. Garmin refresh
2. Recommendation generation
3. Notification delivery

Times are interpreted in server local time. The process must remain running for scheduled jobs to trigger.

## Configuration

The dashboard reads all configuration from `.env` in the repo root. Key values:

- `GROQ_CLOUD_KEY`
- `GARMIN_EMAIL` / `GARMIN_PASSWORD`
- `DISCORD_BOT_TOKEN`
- `MAIL_USERNAME` / `MAIL_PASSWORD`
- `VAULT_ADDR`, `VAULT_TOKEN`, `VAULT_KV_PATH` for optional Vault credential storage

## Data Files

Per-user data lives under `data/users/<user_id>/`:

- `user_profile.json` - dashboard settings and auto times
- `daily_stats.json` - latest health metrics
- `activities.json` - recent activities
- `coach_recommendation.json` - cached recommendation

## Developer Notes

- UI logic is in [web/app.py](web/app.py)
- Auth flow is in [web/auth.py](web/auth.py)
- Sidebar config is in [web/sidebar.py](web/sidebar.py)
- Coach logic is in [core/coach_agent.py](../core/coach_agent.py)
- Scheduler is in [core/auto_recommendation.py](../core/auto_recommendation.py)

## Troubleshooting

- If notifications do not send, confirm the relevant env vars are set
- If recommendations are always local, verify `GROQ_CLOUD_KEY`
- If Garmin refresh fails, validate credentials and check rate limits
- If the Streamlit service is running under systemd, check `journalctl -u personal-garmin-ai-coach.service -f` for app logs
