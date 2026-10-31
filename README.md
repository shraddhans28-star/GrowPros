# GrowPros

GrowPros is a student productivity prototype by ctrl+CREATE. It brings study goals, Pomodoro focus sessions, badges, shared streaks, a friends leaderboard, and Deshika, an optional AI study coach, into one app.

## Run locally

Requirements: Python 3.10 or newer.

Windows PowerShell:

    py -3 -m venv .venv
    .\.venv\Scripts\Activate.ps1
    python -m pip install -r requirements.txt
    python app.py

Then open http://127.0.0.1:5001. Create a test account from the sign-in screen.

macOS or Linux:

    python3 -m venv .venv
    source .venv/bin/activate
    python -m pip install -r requirements.txt
    python app.py

The app creates its SQLite database beside app.py. Do not commit that database; it is excluded by .gitignore.

## Optional AI coach

Deshika uses OpenAI only when the server has an OPENAI_API_KEY environment variable. Without it, the app uses its built-in demo replies. Keep API keys out of source files, screenshots, chat, and GitHub. Add a key through the hosting provider's secret settings. API usage is billed separately from ChatGPT.

## Team collaboration

See CONTRIBUTING.md for a simple branch-and-pull-request workflow for ctrl+CREATE.

## Publish a stable public demo on Render

This repository includes `render.yaml` for a public GrowPros web service. In Render, choose **New → Blueprint**, connect `shraddhans28-star/GrowPros`, and review the service and disk before applying it. Render assigns a stable `onrender.com` address that can be shared without a custom domain.

The Blueprint uses a paid always-on web service and a 1 GB persistent disk so the SQLite user database survives restarts and redeploys. Render currently lists the smallest web service at $7/month and persistent disks at $0.25/GB/month; usage, taxes, and pricing can change. Review the final estimate in Render before creating it. A free web service sleeps when idle and loses local files, so it is not configured here for ongoing user accounts.

Render creates a secret session key automatically. The optional `OPENAI_API_KEY` is not included in GitHub; add it in the Render service's Environment settings only if you want to enable Deshika's live AI responses. Without it, Deshika stays in demo mode.
