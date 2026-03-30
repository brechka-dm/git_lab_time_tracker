"""Application configuration loader."""

from __future__ import annotations

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


def load_config(config_path: Path) -> AppConfig:
    """Load and validate GitLab config from ini file."""
    parser = ConfigParser()
    parser.read(config_path, encoding="utf-8")

    if "gitlab" not in parser:
        raise ValueError("Missing [gitlab] section in config file")

    section = parser["gitlab"]
    gitlab_url = section.get("GITLAB_URL", "").strip().rstrip("/")
    token = section.get("TOKEN", "").strip()
    project_ids_raw = section.get("PROJECT_IDS", "").strip()
    user_id_raw = section.get("USER_ID", "").strip()

    if not gitlab_url:
        raise ValueError("GITLAB_URL is empty")
    if not token:
        raise ValueError("TOKEN is empty")
    if not project_ids_raw:
        raise ValueError("PROJECT_IDS is empty")

    project_ids: list[int] = []
    for raw_value in project_ids_raw.split(","):
        value = raw_value.strip()
        if not value:
            continue
        try:
            project_ids.append(int(value))
        except ValueError as error:
            raise ValueError("PROJECT_IDS must be comma-separated integers") from error
    if not project_ids:
        raise ValueError("PROJECT_IDS must contain at least one project id")

    user_id: int | None = None
    if user_id_raw:
        try:
            user_id = int(user_id_raw)
        except ValueError as error:
            raise ValueError("USER_ID must be integer when specified") from error

    return AppConfig(
        gitlab_url=gitlab_url,
        project_ids=project_ids,
        token=token,
        user_id=user_id,
    )
