# Contributing to GrowPros

Welcome, ctrl+CREATE. Keep the main branch in a working state and make changes on short-lived feature branches.

## Workflow

1. Clone the repository and create a branch from main, for example feature/focus-timer or docs/presentation.
2. Make one focused change and commit it with a clear message.
3. Push your branch and open a pull request into main.
4. Ask one teammate to review the change. Merge after the reviewer approves it and the change is ready to show.
5. Pull the updated main branch before starting the next change.

Avoid having two people edit the same part of app.py at once. Split work by feature or documentation.

## Keep private values out of Git

Never commit API keys, passwords, user databases, or session secrets. The repository ignores the local SQLite files, .env files, and session secret file. If an API key is accidentally committed, revoke it in the provider dashboard immediately; deleting the line in a later commit does not remove it from Git history.

## Demo accounts

Use fresh test accounts and unique test passwords. Do not add real student records or personal study data to the repository.
