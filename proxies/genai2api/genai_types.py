from __future__ import annotations

import uuid

_CURRENT_CHAT_GROUP_ID: str | None = None


def new_chat_group_id() -> str:
    """Return the current persistent chatGroupId, creating one if needed.

    Previously generated a new ID per request, which created a new dialog
    on the GenAI web interface for every HTTP request. Now reuses a single
    ID per proxy process lifetime to avoid dialog spam on the web UI.
    """
    global _CURRENT_CHAT_GROUP_ID
    if _CURRENT_CHAT_GROUP_ID is None:
        _CURRENT_CHAT_GROUP_ID = f"CG{uuid.uuid4().hex[:22]}"
    return _CURRENT_CHAT_GROUP_ID


def reset_chat_group_id() -> str:
    """Force a new chatGroupId, starting a fresh dialog on the web UI."""
    global _CURRENT_CHAT_GROUP_ID
    _CURRENT_CHAT_GROUP_ID = f"CG{uuid.uuid4().hex[:22]}"
    return _CURRENT_CHAT_GROUP_ID
