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

## Public hosting

A deployment-ready WSGI file is included for a host that supports Python WSGI. Before sharing the public app, set the host's secret key and HTTPS settings, and use a fresh database. The local SQLite database and presentation seed data are intentionally excluded from this repository.
