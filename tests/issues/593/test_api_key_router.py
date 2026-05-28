"""
Unit tests for APIKeyRouter — multi-key scheduling with priority + failover.

Related Issue: https://github.com/open-ace/open-ace/issues/593
"""

import unittest

from app.modules.workspace.api_key_router import APIKeyRouter


class TestAPIKeyRouter(unittest.TestCase):
    """Tests for the APIKeyRouter priority + weighted random selection."""

    def setUp(self):
        self.router = APIKeyRouter()

    def test_select_single_key(self):
        """With one candidate, always returns that key."""
        candidates = [{"id": 1, "priority": 0, "weight": 100, "api_key": "k1", "base_url": None}]
        result = self.router.select_key(candidates)
        self.assertEqual(result["id"], 1)

    def test_select_empty_returns_none(self):
        """With no candidates, returns None."""
        result = self.router.select_key([])
        self.assertIsNone(result)

    def test_priority_higher_preferred(self):
        """Higher priority key is always selected."""
        candidates = [
            {"id": 1, "priority": 0, "weight": 100, "api_key": "k1", "base_url": None},
            {"id": 2, "priority": 5, "weight": 100, "api_key": "k2", "base_url": None},
            {"id": 3, "priority": 2, "weight": 100, "api_key": "k3", "base_url": None},
        ]
        result = self.router.select_key(candidates)
        self.assertEqual(result["id"], 2)

    def test_exclude_key_ids(self):
        """Excluded keys are skipped."""
        candidates = [
            {"id": 1, "priority": 10, "weight": 100, "api_key": "k1", "base_url": None},
            {"id": 2, "priority": 5, "weight": 100, "api_key": "k2", "base_url": None},
        ]
        result = self.router.select_key(candidates, exclude_key_ids={1})
        self.assertEqual(result["id"], 2)

    def test_exclude_all_returns_none(self):
        """If all keys are excluded, returns None."""
        candidates = [
            {"id": 1, "priority": 0, "weight": 100, "api_key": "k1", "base_url": None},
        ]
        result = self.router.select_key(candidates, exclude_key_ids={1})
        self.assertIsNone(result)

    def test_same_priority_weighted_random(self):
        """With same priority, weighted random selects from the group."""
        candidates = [
            {"id": 1, "priority": 0, "weight": 100, "api_key": "k1", "base_url": None},
            {"id": 2, "priority": 0, "weight": 100, "api_key": "k2", "base_url": None},
        ]
        # Run multiple times to verify both keys can be selected
        selected_ids = set()
        for _ in range(100):
            result = self.router.select_key(candidates)
            selected_ids.add(result["id"])
        self.assertEqual(selected_ids, {1, 2})

    def test_weight_zero_treated_as_one(self):
        """Weight of 0 is treated as 1 to avoid division by zero."""
        candidates = [
            {"id": 1, "priority": 0, "weight": 0, "api_key": "k1", "base_url": None},
        ]
        result = self.router.select_key(candidates)
        self.assertEqual(result["id"], 1)

    def test_mixed_priorities_selects_highest_group(self):
        """Only the highest priority group is considered."""
        candidates = [
            {"id": 1, "priority": 1, "weight": 100, "api_key": "k1", "base_url": None},
            {"id": 2, "priority": 1, "weight": 100, "api_key": "k2", "base_url": None},
            {"id": 3, "priority": 0, "weight": 100, "api_key": "k3", "base_url": None},
        ]
        selected_ids = set()
        for _ in range(100):
            result = self.router.select_key(candidates)
            selected_ids.add(result["id"])
        self.assertEqual(selected_ids, {1, 2})  # id 3 never selected

    def test_failover_excludes_failed_key(self):
        """Simulating failover: exclude failed key and select next."""
        candidates = [
            {"id": 1, "priority": 10, "weight": 100, "api_key": "k1", "base_url": None},
            {"id": 2, "priority": 5, "weight": 100, "api_key": "k2", "base_url": None},
            {"id": 3, "priority": 0, "weight": 100, "api_key": "k3", "base_url": None},
        ]
        # First selection: highest priority
        result1 = self.router.select_key(candidates)
        self.assertEqual(result1["id"], 1)

        # Simulate failover: exclude key 1
        result2 = self.router.select_key(candidates, exclude_key_ids={1})
        self.assertEqual(result2["id"], 2)

        # Second failover: exclude keys 1 and 2
        result3 = self.router.select_key(candidates, exclude_key_ids={1, 2})
        self.assertEqual(result3["id"], 3)

    def test_default_priority_and_weight(self):
        """Missing priority/weight defaults to 0/100."""
        candidates = [{"id": 1, "api_key": "k1", "base_url": None}]
        result = self.router.select_key(candidates)
        self.assertEqual(result["id"], 1)


if __name__ == "__main__":
    unittest.main()
