# GitLab Time Tracker

Desktop Python app with a Kanban board for GitLab issues, time tracking, and reports.

## Features

### Board and issues

- Loads **open issues** from configured GitLab projects (with assignee/reviewer filtering when `USER_ID` is set).
- Separate **Review** board: open merge requests where you are a **reviewer**.
- Kanban columns are driven by issue **labels**; issues without a column label go to the `open` column.
- **Drag and drop** between columns; labels are updated in GitLab.
- Double-click a card to open the issue in the browser.
- Context menu: open in GitLab, start/stop work, time summary, **work sessions**.
- **Local tasks** on the board (no GitLab): time is stored only locally.
- Add/remove columns; reorder columns with left/right buttons.
- Multiple **boards** by label category (e.g. `TT:Bugfix`, `W:OnReview`) and a board selector.

### Time tracking

- One **active** task at a time (GitLab or local); timer and title appear above the board.
- **Stop current** button and **Stop work** in the context menu stop tracking and record time.
- For GitLab: sends spent time via the API (**add spent time**); on network failure the event is **queued** and synced later.
- For MRs linked to an issue, time may be posted to the linked issue when GitLab exposes that relationship.
- **Today total** shows today’s sum (GitLab from the background scan + local events); it updates after you stop the timer without requiring Refresh.
- **Work sessions** on an issue: adjust session length/bounds and sync to GitLab (incremental updates when possible, avoiding a full reset of all spent time).

### Reports

- **Daily Report** — totals for a chosen day for board items plus issues/MRs updated in GitLab on or after that day (including closed), plus local tasks.
- **Period Report** — totals for a date range, grouped by project tags.

### Data on disk

- UI state, tracking, local tasks, the offline event queue, and the “today” cache live in SQLite **`tracker.db`** (next to the exe, or project root when running from source).

## Dependencies

| Package      | Role                                |
|-------------|-------------------------------------|
| **PySide6** | GUI (Qt for Python)                 |
| **requests**| HTTP client for the GitLab REST API |

The Python standard library (including `sqlite3`) is not installed via pip.

### Installing dependencies for development

From the repository root:

```bash
pip install -r requirements.txt
```

For building the Windows exe, also install PyInstaller into the **same** Python environment you use for the app:

```bash
pip install pyinstaller
```

Using a virtual environment is recommended:

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

On Linux/macOS: `source .venv/bin/activate`.

**Python 3.10+** is required (modern type annotation syntax).

## Configuring `gitlab_access.ini`

The file lives in the repository root; the PyInstaller build bundles it next to `Tracker.exe` (or falls back to `_internal`).

`[gitlab]` section:

| Key           | Required | Description |
|---------------|----------|-------------|
| `GITLAB_URL`  | yes      | Base URL without a trailing `/`, e.g. `https://gitlab.example.com` |
| `TOKEN`       | yes      | Personal access token with API access to the projects |
| `PROJECT_IDS` | yes      | Comma-separated numeric project IDs, e.g. `2,14,49` |
| `USER_ID`     | no       | GitLab user id: filters assignee/reviewer when loading issues and when parsing spent-time system notes |

## Run from source

From the **repository root**:

```bash
python main.py
```

This adds `source/` to the import path and runs `source/main.py`.

## Building the exe (Windows, PyInstaller)

1. Install runtime dependencies and PyInstaller (see above).
2. Fill in `gitlab_access.ini`, or edit the copy under `dist/Tracker/` after the build.
3. From the **repository root** run (use this form so Windows finds PyInstaller even when `pyinstaller.exe` is not on `PATH`):

```bash
python -m PyInstaller tracker.spec
```

4. Output: **`dist/Tracker/`** with `Tracker.exe`, dependencies, and assets. Launch `Tracker.exe`.

If you see `pyinstaller` is not recognized in PowerShell, you either skipped `pip install pyinstaller` for this Python, or the `Scripts` folder is not on `PATH`—`python -m PyInstaller` avoids that as long as `python` is the interpreter where PyInstaller was installed.

`tracker.spec` uses **onedir** layout (exe plus `_internal`, etc.) with **no console window** (`console=False`).

If you add modules under `source/board/`, register them in `hiddenimports` in `tracker.spec` if PyInstaller misses them.

## Project layout

- `main.py` — root entrypoint.
- `source/main.py` — Qt bootstrap and config load.
- `source/board/` — main window, board widgets, reports, refresh/sync threads.
- `source/gitlab_client.py` — GitLab API calls.
- `source/storage.py` — SQLite persistence.
- `source/config.py` — INI parsing.
- `tracker.spec` — PyInstaller spec.
