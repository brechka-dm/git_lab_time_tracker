"""Network error helpers for sync and retry logic."""

from __future__ import annotations

import requests


def is_retryable_network_error(error: Exception) -> bool:
    """Return True for transient network errors that should be retried later."""
    if isinstance(error, requests.RequestException):
        if error.response is None:
            return True
        return error.response.status_code >= 500
    return False
