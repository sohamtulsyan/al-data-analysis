"""Logged-in user detection from play UIDs.

India Mini (and related WordPress embeds) mint logged-in play UIDs with the
``imgl`` prefix. Anonymous / guest UIDs use other formats (e.g. hex UUIDs).
"""

from __future__ import annotations

LOGGED_IN_UID_PREFIX = "imgl"


def is_logged_in_uid(uid: str) -> bool:
    """True when the play uid belongs to a logged-in user (``imgl…``)."""
    return uid.strip().lower().startswith(LOGGED_IN_UID_PREFIX)
