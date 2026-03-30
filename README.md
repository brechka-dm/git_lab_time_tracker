# GitLab Time Tracker

Desktop app on Python with Kanban interface for opened GitLab issues.

## Features

- Loads opened issues from GitLab project.
- Builds Kanban columns from issue labels.
- Places issues without labels into `open` column.
- Supports drag and drop movement between columns.
- Updates issue labels in GitLab after move.
- Opens issue page in browser on double click.
- Allows adding and deleting columns from UI.
- Allows reordering columns with left/right buttons.
- Persists UI state in SQLite database `tracker.db`.
- Supports multiple boards by label categories (e.g. `TT:Bugfix`, `W:OnReview`).
- Allows switching active board from the board selector.
- Supports time tracking with one active task at a time.
- Shows current task and live timer above the board.
- On stop, sends exact spent seconds to GitLab issue time tracking.
- Context menu can show total spent time and work sessions (loaded from GitLab and local state).
- Stores UI state and offline event queue in SQLite (`tracker.db`).
- Queues events while offline and auto-syncs when connection returns.

## Setup

1. Install dependencies:

   ```bash
   pip install -r requirements.txt
   ```

2. Fill `gitlab_access.ini`:
   - `GITLAB_URL` - base URL (for example: `https://gitlab.example.com`)
  - `PROJECT_IDS` - comma-separated project ids (for example: `2,5,8`)
   - `TOKEN` - personal access token
   - `USER_ID` - optional assignee id filter

## Run

```bash
python main.py
```
