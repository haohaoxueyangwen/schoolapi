"""Context compression reminder for large message arrays.

When total message content exceeds a threshold, inject a reminder
into the response so the user knows to run /compact manually.
No automatic truncation or summary — the user stays in control.
"""

import logging
from typing import Any, Dict, List

logger = logging.getLogger(__name__)

# Thresholds in characters
GENAI_COMPRESS_THRESHOLD = 400_000
DEFAULT_COMPRESS_THRESHOLD = 1_000_000


def _msg_content_len(msg: Dict[str, Any]) -> int:
    if isinstance(msg.get("content"), str):
        return len(msg["content"])
    if isinstance(msg.get("content"), list):
        total = 0
        for block in msg["content"]:
            if isinstance(block, str):
                total += len(block)
            elif isinstance(block, dict):
                total += len(block.get("text", ""))
        return total
    return 0


def total_content_size(messages: List[Dict[str, Any]]) -> int:
    return sum(_msg_content_len(m) for m in messages)


def check_context_size(messages: List[Dict[str, Any]], threshold: int = GENAI_COMPRESS_THRESHOLD) -> bool:
    """Check if context exceeds threshold. Returns True if reminder should be shown."""
    size = total_content_size(messages)
    if size > threshold:
        logger.info(
            "Context size %d chars exceeds threshold %d (%d messages) — suggesting /compact",
            size, threshold, len(messages),
        )
        return True
    return False
