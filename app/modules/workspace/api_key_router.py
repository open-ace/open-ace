"""
Open ACE - API Key Router

Multi-key scheduling with priority-based selection and failover support.
"""

from __future__ import annotations
import logging
import random
from typing import Any

logger = logging.getLogger(__name__)


class APIKeyRouter:
    """
    Selects an API key from a pool of candidates using priority + failover.

    Strategy:
    1. Filter out excluded (failed) key IDs
    2. Sort by priority (descending)
    3. Take the highest-priority group
    4. Within that group, select by weighted random sampling
    """

    def select_key(
        self,
        candidates: list[dict[str, Any]],
        exclude_key_ids: set[int] | None = None,
    ) -> dict[str, Any] | None:
        """
        Select the best API key from candidates.

        Args:
            candidates: List of dicts with keys: id, priority, weight,
                        api_key, base_url (and optionally more).
            exclude_key_ids: Key IDs to skip (e.g. previously failed).

        Returns:
            The selected candidate dict, or None if no keys available.
        """
        if not candidates:
            return None

        exclude = exclude_key_ids or set()

        # Filter out excluded keys
        pool = [c for c in candidates if c.get("id") not in exclude]
        if not pool:
            logger.warning("All API keys excluded, no key available")
            return None

        # Sort by priority descending
        pool.sort(key=lambda c: c.get("priority", 0), reverse=True)

        # Take highest priority group
        max_priority = pool[0].get("priority", 0)
        top_group = [c for c in pool if c.get("priority", 0) == max_priority]

        if len(top_group) == 1:
            return top_group[0]

        # Weighted random selection within the top group
        return self._weighted_random(top_group)

    def _weighted_random(self, keys: list[dict[str, Any]]) -> dict[str, Any]:
        """
        Select a key by weighted random sampling.

        Each key's weight determines its probability of being selected.
        All weights default to 100 if not specified.
        """
        weights = [max(k.get("weight", 100), 1) for k in keys]
        return random.choices(keys, weights=weights, k=1)[0]
