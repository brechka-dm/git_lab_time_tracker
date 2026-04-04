"""Application configuration loader."""

from __future__ import annotations

import sys
from configparser import ConfigParser
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class AppConfig:
    """Runtime settings used by the tracker application."""

    gitlab_url: str
    project_ids: list[int]
    token: str
    user_id: int | None


def resolve_gitlab_config_paths(exe_dir: Path) -> tuple[Path, Path]:
    """Resolve base gitlab_access.ini and path for optional gitlab_access.ini.user.

    Base: next to the executable / project root, else bundled template when frozen.
    User layer: always exe_dir/gitlab_access.ini.user (never bundled).
    """
    local_base = exe_dir / "gitlab_access.ini"
    if local_base.is_file():
        base = local_base
    elif getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        bundled = Path(sys._MEIPASS) / "gitlab_access.ini"  # noqa: SLF001
        base = bundled if bundled.is_file() else local_base
    else:
        base = local_base
    user_path = exe_dir / "gitlab_access.ini.user"
    return base, user_path


def load_config(base_path: Path, user_path: Path) -> AppConfig:
    """Load merged config: base template then optional user override (same keys win in user file)."""
    if not base_path.is_file():
        raise FileNotFoundError(
            f"Base config file not found:\n{base_path}\n\n"
            "Add gitlab_access.ini from the repository (template without secrets)."
        )

    paths: list[Path] = [base_path]
    user_exists = user_path.is_file()
    if user_exists:
        paths.append(user_path)

    parser = ConfigParser()
    parser.read(paths, encoding="utf-8")

    if "gitlab" not in parser:
        raise ValueError(
            "Missing [gitlab] section in gitlab_access.ini.\n"
            "Fix the base file or add [gitlab] to gitlab_access.ini.user."
        )

    section = parser["gitlab"]
    gitlab_url = section.get("GITLAB_URL", "").strip().rstrip("/")
    token = section.get("TOKEN", "").strip()
    project_ids_raw = section.get("PROJECT_IDS", "").strip()
    user_id_raw = section.get("USER_ID", "").strip()

    user_file_hint = (
        f"\n\nCreate or edit:\n{user_path}\n"
        "(this file is private — listed in .gitignore; not shipped in the repo)."
    )

    if not gitlab_url:
        raise ValueError(
            "GITLAB_URL is empty after loading config.\n"
            "Set GITLAB_URL in gitlab_access.ini (team default) or gitlab_access.ini.user."
            + ("" if user_exists else user_file_hint)
        )
    if not token:
        msg = (
            "TOKEN is empty. GitLab API requires a personal access token.\n"
            "Set TOKEN in gitlab_access.ini.user (recommended) or in gitlab_access.ini."
        )
        if not user_exists:
            msg += user_file_hint
        raise ValueError(msg)
    if not project_ids_raw:
        raise ValueError(
            "PROJECT_IDS is empty.\n"
            "Set PROJECT_IDS in gitlab_access.ini (shared list) or gitlab_access.ini.user."
            + ("" if user_exists else user_file_hint)
        )

    project_ids: list[int] = []
    for raw_value in project_ids_raw.split(","):
        value = raw_value.strip()
        if not value:
            continue
        try:
            project_ids.append(int(value))
        except ValueError as error:
            raise ValueError(
                "PROJECT_IDS must be comma-separated integers (e.g. 2,14,49)."
            ) from error
    if not project_ids:
        raise ValueError("PROJECT_IDS must contain at least one project id.")

    user_id: int | None = None
    if user_id_raw:
        try:
            user_id = int(user_id_raw)
        except ValueError as error:
            raise ValueError("USER_ID must be an integer when set.") from error

    return AppConfig(
        gitlab_url=gitlab_url,
        project_ids=project_ids,
        token=token,
        user_id=user_id,
    )
