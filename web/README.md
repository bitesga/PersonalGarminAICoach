# Streamlit Dashboard

This directory contains the dashboard for Personal Garmin AI Coach.

## Start

```bash
streamlit run web/app.py
```

## Features
- Displays the latest fitness data from `data/daily_stats.json`
- Displays the latest activities from `data/activities.json`
- Configurable preferences for mobility, goal, and training style
- Shows the current coach recommendation with a 6-hour cache
- Copyable message for Discord, push, or later integrations
- Button to refresh Garmin fitness data directly from the dashboard
- Optional delivery of new model recommendations via Discord DM

## Notes
- The current recommendation is fetched through `core/coach_agent.py` and the cache helps save tokens.
- Notifications are only sent for new model recommendations (`source=model`), not for cache hits.

## Notification Setup
- Discord DM:
  - Set `DISCORD_BOT_TOKEN` in `.env`
  - Enter the recipient's `Discord user ID` in the dashboard
