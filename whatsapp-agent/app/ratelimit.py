import logging
import time
from typing import Dict, List

logger = logging.getLogger(__name__)

# Shared by both channels: {identity (phone or email): [timestamp, ...]}
RATE_LIMIT_TRACKER: Dict[str, List[float]] = {}
MAX_MESSAGES_PER_MINUTE = 20


def is_rate_limited(identity: str) -> bool:
    """Rate limit per sender to prevent spam and cost spikes (max 20 msgs/min)."""
    now = time.time()
    timestamps = RATE_LIMIT_TRACKER.get(identity, [])
    # Keep timestamps within the last 60 seconds
    valid_timestamps = [ts for ts in timestamps if now - ts < 60]
    RATE_LIMIT_TRACKER[identity] = valid_timestamps

    if len(valid_timestamps) >= MAX_MESSAGES_PER_MINUTE:
        return True

    valid_timestamps.append(now)
    return False
