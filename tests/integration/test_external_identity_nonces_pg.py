"""Real-PostgreSQL contract for the external identity replay store.

The replay store's reason to exist is cross-worker atomicity: two workers
consuming the same ``(issuer, nonce)`` must never both succeed. That is only
observable on a real database, so these tests run against the ``pg_db``
fixture and are marked ``postgres`` (CI's default lane excludes them; the
``postgres-test`` lane executes them).
"""

import threading

import pytest

from app.modules.workspace.external_identity import DelegationDenied, ReplayStore

pytestmark = pytest.mark.postgres


class TestReplayStorePostgres:
    def test_concurrent_consume_accepts_exactly_one(self, pg_db):
        store = ReplayStore(pg_db.get_connection, "%s")
        now = 1_800_000_000
        results = []
        barrier = threading.Barrier(4)

        def consume():
            barrier.wait()
            try:
                store.consume("issuer-pg", "nonce-pg", now)
                results.append("accepted")
            except DelegationDenied:
                results.append("rejected")

        threads = [threading.Thread(target=consume) for _ in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        assert results.count("accepted") == 1
        assert results.count("rejected") == 3

        count = pg_db.execute("SELECT COUNT(*) AS c FROM external_identity_nonces").fetchone()["c"]
        assert count == 1

    def test_expired_nonce_cleanup_only_removes_expired(self, pg_db):
        store = ReplayStore(pg_db.get_connection, "%s")
        now = 1_800_000_000
        store.consume("issuer-pg", "expired", now - 240)
        store.consume("issuer-pg", "live", now)
        # A later consume cleans only rows already past their 120s window.
        store.consume("issuer-pg", "next", now + 60)
        rows = [
            row["nonce"]
            for row in pg_db.execute(
                "SELECT nonce FROM external_identity_nonces ORDER BY nonce"
            ).fetchall()
        ]
        assert rows == ["live", "next"]
