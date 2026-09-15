"""OS-account uid pinning across container recreation (Issue #3390).

On container recreation (image upgrade, ``compose up -d --force-recreate``)
the container starts from a fresh /etc/passwd while the volume directories
keep their numeric owners. The entrypoint's DB sync used to re-useradd only
the ACTIVE users without a uid pin, so useradd's sequential 1001..N
assignment necessarily landed a DEACTIVATED user's old uid on some active
account — numerically handing that account the deactivated user's 0700
/home/<user> and /workspace/<user> (#3374's "A cannot enter B's private
area", triggered by every image upgrade).

The fix (issue option 1 + placeholder accounts):
- ``users.system_uid`` records each account's OS uid (Alembic migration
  20260911_002; recorded by ``ensure_system_user`` and the entrypoint sync);
- re-creation passes the pin to ``useradd -u`` so uids are stable;
- deactivated/soft-deleted users get nologin placeholder accounts that
  reserve the recorded uid (issue option 2, cheap because the sync already
  knows the pin);
- a pinned uid owned by a DIFFERENT account name fails LOUDLY (skip + warn)
  on the CREATE path — renumbering would silently move the file-ownership
  boundary between two users, so it is an administrator action, never
  automation. For an account that already exists, a stale pin is drift and
  the record converges to the account's actual uid instead (review on
  #3390).

Coverage here:
- app side: ``ensure_system_user`` records/pins/converges uids, refuses
  collisions without renumbering, upgrades reactivated placeholder shells;
  the SQL helpers scope reads/writes to the active row (partial-index
  semantics) against a real in-memory SQLite;
- entrypoint side: the embedded sync python is EXTRACTED from
  docker-entrypoint.sh (unescaping the ``python3 -c "..."`` quoting) and
  executed FUNCTIONALLY under fake pwd/subprocess/psycopg2 — following the
  extraction harness precedent of test_shared_namespace_provisioning_3379
  (its review round 2 showed pure text assertions survive mutations);
  ordering (pinned actives -> placeholders -> legacy unpinned actives) is
  asserted both functionally and textually.
"""

import sqlite3
import subprocess
import sys
import types
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from app.utils import workspace as ws

pytestmark = [pytest.mark.regression, pytest.mark.issue(3390), pytest.mark.security]

ROOT = __file__.rsplit("/tests/", 1)[0]
ENTRYPOINT = f"{ROOT}/docker-entrypoint.sh"


class _FakeProc:
    def __init__(self, rc: int, out: str = "", err: str = ""):
        self.returncode = rc
        self.stdout = out
        self.stderr = err


# ============================================================================
# App side: ensure_system_user uid pinning
# ============================================================================


class TestEnsureSystemUidPinning:
    """Functional tests over ensure_system_user with stubbed OS/DB edges."""

    @pytest.fixture()
    def pin_env(self, monkeypatch, tmp_path):
        """Docker multi-user mode on Linux, wrapper disabled, stubbed OS+DB."""
        monkeypatch.setenv("WORKSPACE_BASE_DIR", str(tmp_path))
        monkeypatch.setattr(ws, "_is_docker_multi_user_mode", lambda: True)
        monkeypatch.setattr(ws.platform, "system", lambda: "Linux")
        monkeypatch.setattr(ws, "_is_wrapper_available", lambda w: False)
        monkeypatch.setattr(ws, "add_user_to_shared_group", lambda acc, tenant_id=None: True)

        state = {
            "recorded": {},  # system_account -> recorded uid (the DB pin)
            "record_calls": [],  # (account, uid) write-backs
            "uid_owner": {},  # uid -> owning account name (NSS)
            "uid_of": {"acealice": 1042, "acebob": 1003},  # id -u answers
            "exists": set(),  # accounts for which `id <name>` succeeds
            "cmds": [],  # run_as_root_if_needed commands
            # Review on #3390 (⚪): answers for the auto-assign exclusion
            # set. db_pins None = DB read failure (fail-soft -> plain
            # useradd, the fixture default so the pre-review assertions
            # below keep pinning that fallback).
            "db_pins": None,  # _recorded_pin_uids answer (set, or None)
            "passwd_uids": {0, 999, 1000},  # _passwd_uids answer (or None)
        }
        monkeypatch.setattr(ws, "get_recorded_system_uid", lambda acc: state["recorded"].get(acc))
        monkeypatch.setattr(
            ws,
            "record_system_uid",
            lambda acc, uid: state["record_calls"].append((acc, uid))
            or state["recorded"].__setitem__(acc, uid),
        )
        monkeypatch.setattr(ws, "_lookup_uid_owner", lambda uid: state["uid_owner"].get(uid))
        monkeypatch.setattr(ws, "_recorded_pin_uids", lambda acc: state["db_pins"])
        monkeypatch.setattr(ws, "_passwd_uids", lambda: state["passwd_uids"])
        monkeypatch.setattr(
            ws,
            "run_as_root_if_needed",
            lambda cmd: state["cmds"].append(tuple(cmd)) or _FakeProc(0, ""),
        )

        def fake_run(cmd, **kw):
            if cmd[:2] == ["id", "-u"] or cmd[:2] == ["id", "-g"]:
                name = cmd[2]
                uid = state["uid_of"].get(name, 1042)
                return _FakeProc(0, str(uid))
            if cmd[0] == "id":
                return _FakeProc(0 if cmd[1] in state["exists"] else 1, "")
            return _FakeProc(0, "")

        monkeypatch.setattr(ws.subprocess, "run", fake_run)
        return state

    def test_creation_records_assigned_uid(self, pin_env):
        state = pin_env
        assert ws.ensure_system_user("acealice") is True
        useradd = [c for c in state["cmds"] if c[0] == "useradd"]
        assert useradd == [("useradd", "-m", "-s", "/bin/bash", "acealice")]
        assert state["record_calls"] == [
            ("acealice", 1042)
        ], "assigned uid must be written back to users.system_uid (#3390)"

    # ── Review on #3390 (⚪): the auto-assign exclusion set ──────────────
    # The collision guard only sees the CURRENT container's /etc/passwd; a
    # boot-sync failure (#3399) leaves pinned accounts missing from it, and
    # an unpinned account's useradd could then land on a recorded pin.

    def test_auto_assign_skips_db_recorded_pins(self, pin_env):
        """The pinned accounts are missing from /etc/passwd (boot sync
        failed) but recorded in the DB — the auto uid must skip them."""
        state = pin_env
        state["db_pins"] = {1001, 1002, 1003}
        assert ws.ensure_system_user("acealice") is True
        useradd = [c for c in state["cmds"] if c[0] == "useradd"]
        assert ("-u", "1004") in zip(useradd[0], useradd[0][1:]), (
            "auto-assign must pass an explicit -u outside the recorded pin "
            "set — a plain useradd here would grab 1001 (someone's pin)"
        )

    def test_auto_assign_skips_passwd_uids_too(self, pin_env):
        state = pin_env
        state["db_pins"] = set()
        state["passwd_uids"] = {0, 999, 1000, 1001}
        assert ws.ensure_system_user("acealice") is True
        useradd = [c for c in state["cmds"] if c[0] == "useradd"]
        assert ("-u", "1002") in zip(useradd[0], useradd[0][1:])

    def test_auto_assign_failsoft_on_db_read_failure(self, pin_env):
        """DB unreadable -> plain unpinned useradd (pre-review behavior)."""
        state = pin_env
        state["db_pins"] = None
        assert ws.ensure_system_user("acealice") is True
        useradd = [c for c in state["cmds"] if c[0] == "useradd"]
        assert useradd == [("useradd", "-m", "-s", "/bin/bash", "acealice")]

    def test_auto_assign_failsoft_on_passwd_read_failure(self, pin_env):
        state = pin_env
        state["db_pins"] = {1001}
        state["passwd_uids"] = None
        assert ws.ensure_system_user("acealice") is True
        useradd = [c for c in state["cmds"] if c[0] == "useradd"]
        assert useradd == [("useradd", "-m", "-s", "/bin/bash", "acealice")]

    def test_recreation_pins_recorded_uid(self, pin_env):
        state = pin_env
        state["recorded"]["acebob"] = 1003
        assert ws.ensure_system_user("acebob") is True
        useradd = [c for c in state["cmds"] if c[0] == "useradd"]
        assert useradd == [
            ("useradd", "-m", "-s", "/bin/bash", "-u", "1003", "acebob")
        ], "recorded pin must flow to useradd -u so the uid survives recreation"

    def test_explicit_uid_wins_over_recorded(self, pin_env):
        state = pin_env
        state["recorded"]["acebob"] = 1003
        assert ws.ensure_system_user("acebob", uid=1010) is True
        useradd = [c for c in state["cmds"] if c[0] == "useradd"]
        assert "-u 1010" in " ".join(useradd[0])

    def test_recorded_uid_collision_fails_loudly_without_renumbering(self, pin_env, caplog):
        state = pin_env
        state["recorded"]["acebob"] = 1003
        state["uid_owner"][1003] = "acemallory"  # pin owned by a DIFFERENT account
        with caplog.at_level("ERROR"):
            assert ws.ensure_system_user("acebob") is False
        assert state["cmds"] == [], "collision must not create or renumber anything"
        assert "1003" in caplog.text and "acemallory" in caplog.text

    def test_explicit_uid_collision_also_guarded(self, pin_env):
        state = pin_env
        state["uid_owner"][1003] = "acemallory"
        assert ws.ensure_system_user("acealice", uid=1003) is False
        assert state["cmds"] == []

    def test_reserved_uid_still_rejected(self, pin_env):
        assert ws.ensure_system_user("acealice", uid=999) is False

    def test_corrupt_recorded_pin_below_1000_rejected(self, pin_env):
        state = pin_env
        state["recorded"]["acealice"] = 42  # tampered row: pins are >= 1000
        assert ws.ensure_system_user("acealice") is False
        assert state["cmds"] == [], "must not create a system-range account"

    def test_existing_account_converges_missing_pin(self, pin_env):
        state = pin_env
        state["exists"].add("acealice")  # `id acealice` succeeds
        assert ws.ensure_system_user("acealice") is True
        assert state["record_calls"] == [
            ("acealice", 1042)
        ], "an unpinned existing account must get its actual uid recorded"
        # Second run with the pin now recorded: no further writes.
        state["record_calls"].clear()
        state["recorded"]["acealice"] = 1042
        assert ws.ensure_system_user("acealice") is True
        assert state["record_calls"] == []

    def test_existing_account_drift_updates_record_with_warning(self, pin_env, caplog):
        state = pin_env
        state["exists"].add("acealice")
        state["recorded"]["acealice"] = 1001  # stale pin; actual uid is 1042
        with caplog.at_level("WARNING"):
            assert ws.ensure_system_user("acealice") is True
        assert state["record_calls"] == [("acealice", 1042)]
        assert "1001" in caplog.text

    def test_existing_account_with_colliding_stale_pin_still_converges(self, pin_env, caplog):
        """Review on #3390 NIT: the collision guard is CREATE-path only. For
        an EXISTING account a stale pin owned by another account is drift —
        blocking here (the guard's old position) wedged the account on a pin
        the OS no longer honors; converging to the actual uid resolves it."""
        state = pin_env
        state["exists"].add("acealice")
        state["recorded"]["acealice"] = 1001  # stale, and...
        state["uid_owner"][1001] = "acedave"  # ...1001 now belongs to someone else
        with caplog.at_level("WARNING"):
            assert ws.ensure_system_user("acealice") is True
        assert state["record_calls"] == [
            ("acealice", 1042)
        ], "exists-path must converge the record instead of failing"
        assert "1042" in caplog.text

    def test_reactivated_placeholder_gets_login_shell_back(self, pin_env, monkeypatch):
        state = pin_env
        state["exists"].add("acebob")
        state["recorded"]["acebob"] = 1003
        # /etc/passwd still carries the nologin placeholder shell from the
        # deactivated period — reactivation must restore /bin/bash.
        import pwd as real_pwd

        entry = SimpleNamespace(pw_name="acebob", pw_uid=1003, pw_shell="/usr/sbin/nologin")
        monkeypatch.setattr(real_pwd, "getpwnam", lambda name: entry)
        assert ws.ensure_system_user("acebob") is True
        assert ("usermod", "-s", "/bin/bash", "acebob") in state["cmds"]


# ============================================================================
# App side: SQL helpers (real in-memory SQLite, partial-index scoping)
# ============================================================================


class TestSystemUidSqlHelpers:
    @pytest.fixture()
    def sqlite_conn(self, monkeypatch):
        conn = sqlite3.connect(":memory:")
        conn.execute("""
            CREATE TABLE users (
                id INTEGER PRIMARY KEY,
                username TEXT,
                system_account TEXT,
                system_uid INTEGER,
                is_active INTEGER,
                deleted_at TIMESTAMP
            )
            """)
        conn.executemany(
            "INSERT INTO users (id, username, system_account, system_uid, is_active, deleted_at)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            [
                (1, "acealice", "acealice", None, 1, None),  # active, unpinned
                (2, "acebob", "acebob", 1003, 0, None),  # deactivated WITH pin
                (3, "acebob2", "acebob", 1009, 1, None),  # active row reusing the name
                (4, "acecarol", "acecarol", 1004, 1, "2026-09-01"),  # soft-deleted
            ],
        )
        conn.commit()

        import app.repositories.database as dbmod

        monkeypatch.setattr(dbmod, "adapt_sql", lambda q: q)

        @contextmanager
        def fake_get_db_connection():
            yield conn

        monkeypatch.setattr(dbmod, "get_db_connection", fake_get_db_connection)
        return conn

    def test_read_scoped_to_active_row(self, sqlite_conn):
        assert ws.get_recorded_system_uid("acealice") is None  # NULL pin
        assert (
            ws.get_recorded_system_uid("acebob") == 1009
        ), "the ACTIVE row's pin wins; the deactivated row keeps its own"
        assert ws.get_recorded_system_uid("nonexistent") is None

    def test_record_updates_only_active_row(self, sqlite_conn):
        assert ws.record_system_uid("acealice", 1042) is True
        assert (
            ws.record_system_uid("acecarol", 2000) is False
        ), "soft-deleted rows are out of scope for recording"
        rows = dict(sqlite_conn.execute("SELECT id, system_uid FROM users").fetchall())
        assert rows == {1: 1042, 2: 1003, 3: 1009, 4: 1004}, (
            "the deactivated user's pin (1003) must never be overwritten by "
            "the active row's recording — it is what the placeholder restores"
        )

    def test_recorded_pin_uids_all_states_and_excluding_self(self, sqlite_conn):
        """Review round 3 (PR #3400 🟠): the auto-assign exclusion set must
        cover the pins of DEACTIVATED and soft-deleted rows too — those are
        exactly the uids the entrypoint's nologin placeholders reserve (the
        #3390 inheritance boundary), and a #3399-shaped sync failure leaves
        precisely those accounts absent from /etc/passwd while the DB still
        records their pins. Minus the caller's own row(s)."""
        sqlite_conn.execute(
            "INSERT INTO users (id, username, system_account, system_uid, is_active, deleted_at)"
            " VALUES (5, 'acedave', 'acedave', 1010, 1, NULL)"
        )
        sqlite_conn.commit()
        # ALL recorded pins regardless of account state: 1009 (active row),
        # 1003 (deactivated), 1004 (soft-deleted); NULL (acealice) never
        # enters; the caller's own row (by system_account) is excluded.
        assert ws._recorded_pin_uids("acedave") == {1009, 1003, 1004}
        assert ws._recorded_pin_uids("acebob") == {1004, 1010}, (
            "exclusion is by system_account — BOTH acebob rows are the caller's own"
        )
        assert ws._recorded_pin_uids("stranger") == {1009, 1003, 1004, 1010}


# ============================================================================
# Review on #3390 (🔴): PostgreSQL row shapes (RealDictCursor)
# ============================================================================

try:  # RealDictRow is a dict subclass; keep the fake faithful when present
    from psycopg2.extras import RealDictRow as _RealDictRow
except ImportError:  # psycopg2 not installed in the unit-test env

    class _RealDictRow(dict):
        """Stand-in: RealDictRow subclasses OrderedDict (a dict)."""


class _PgShapeCursor:
    """Cursor over a PG-shaped row: fetchone/fetchall return RealDictRow."""

    def __init__(self, row):
        self._row = row

    def execute(self, sql, params=()):
        return self

    def fetchone(self):
        return self._row

    def fetchall(self):
        return [] if self._row is None else [self._row]


class _PgShapeConn:
    def __init__(self, row):
        self._row = row

    def cursor(self):
        return _PgShapeCursor(self._row)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class TestRecordedUidPgRowShapes:
    """On PostgreSQL get_db_connection wraps every connection with
    cursor_factory=RealDictCursor (app/repositories/database.py), so
    fetchone() yields a dict — positional row[0] raised KeyError(0), the
    except below the query swallowed it, and the pin ALWAYS read as None on
    the only deployment shape that runs ensure_system_user (compose is PG)
    while record_system_uid kept overwriting the correct pin. CI's SQLite
    matrix returns tuples and cannot see this class of bug."""

    @pytest.fixture()
    def pg_shape(self, monkeypatch):
        import app.repositories.database as dbmod

        holder = {}
        monkeypatch.setattr(dbmod, "adapt_sql", lambda q: q)

        @contextmanager
        def fake_get_db_connection():
            yield _PgShapeConn(holder["row"])

        monkeypatch.setattr(dbmod, "get_db_connection", fake_get_db_connection)
        return holder

    def test_reads_pin_from_real_dict_row(self, pg_shape):
        pg_shape["row"] = _RealDictRow(system_uid=1003)
        assert ws.get_recorded_system_uid("acebob") == 1003

    def test_reads_null_pin_from_real_dict_row(self, pg_shape):
        pg_shape["row"] = _RealDictRow(system_uid=None)
        assert ws.get_recorded_system_uid("acealice") is None

    def test_missing_row_is_none(self, pg_shape):
        pg_shape["row"] = None
        assert ws.get_recorded_system_uid("nobody") is None

    def test_recorded_pin_uids_reads_real_dict_rows(self, pg_shape):
        pg_shape["row"] = _RealDictRow(system_uid=1005)
        assert ws._recorded_pin_uids("acebob") == {1005}

    def test_the_fake_row_is_faithfully_pg_shaped(self):
        """Guard the guard: positional access must REALLY break on this row
        shape (a plain dict fake that tolerated row[0] would pass the tests
        above against broken code)."""
        row = _RealDictRow(system_uid=1003)
        assert isinstance(row, dict)
        with pytest.raises(KeyError):
            row[0]


# ============================================================================
# Entrypoint: functional execution of the extracted embedded sync
# ============================================================================


def _extract_sync_python() -> str:
    """Pull the embedded ``python3 -u -c "..."`` user-sync source from the
    entrypoint and undo the bash double-quote escaping (\" -> ")."""
    content = open(ENTRYPOINT, encoding="utf-8").read()
    anchor = content.index("Syncing workspace users from database...")
    start = content.index('python3 -u -c "', anchor) + len('python3 -u -c "')
    end = content.index('" 2>&1 | tee /app/logs/open-ace-user-sync.log', start)
    py = content[start:end].replace('\\"', '"')
    compile(py, "entrypoint-user-sync", "exec")
    return py


class _FakeCursor:
    def __init__(self, scenario_rows, updates, project_rows=(), fail_on_sql=None):
        self._scenario_rows = scenario_rows
        self._project_rows = list(project_rows)
        self._fail_on_sql = fail_on_sql
        self.updates = updates
        self._rows = []

    def execute(self, sql, params=()):
        if self._fail_on_sql is not None and sql.startswith(self._fail_on_sql):
            raise RuntimeError(f"simulated failure: {sql[:30]}...")
        if sql.startswith("SELECT id, username"):
            self._rows = self._scenario_rows
        elif sql.startswith("SELECT path FROM projects"):
            self._rows = self._project_rows
        elif sql.startswith("UPDATE users"):
            self.updates.append((sql, params))
        return self

    def fetchall(self):
        return self._rows


class _FakeConn:
    def __init__(self, scenario_rows, updates, project_rows=(), fail_on_sql=None):
        self.scenario_rows = scenario_rows
        self.updates = updates
        self.project_rows = project_rows
        self.fail_on_sql = fail_on_sql
        self.commits = 0
        self.closed = False

    def cursor(self):
        return _FakeCursor(self.scenario_rows, self.updates, self.project_rows, self.fail_on_sql)

    def commit(self):
        self.commits += 1

    def close(self):
        self.closed = True


class _FakePasswd:
    """In-memory /etc/passwd: name -> (uid, shell), with reverse lookup.

    ``allocator`` picks the auto-uid strategy for useradd calls WITHOUT -u:
    - "max" (default): next above the highest ever assigned — lenient, lets
      phase-ordering bugs hide (an unpinned useradd never lands on a pin).
    - "lowest": first free uid from 1000 — what shadow useradd actually does
      on a fresh container, so a phase-3 useradd WOULD grab an unreserved
      pin if the placeholder phase ran too late.
    Note the fakes do NOT simulate useradd failing on an occupied uid (the
    collision guard is expected to fire first); the tests assert useradd's
    ARGUMENTS as the contract instead.
    """

    def __init__(self, accounts=None, allocator="max"):
        self.accounts = dict(accounts or {})  # name -> [uid, shell]
        self.next_uid = 1000
        self.allocator = allocator

    def _add(self, name, uid, shell):
        self.accounts[name] = [uid, shell]

    def getpwnam(self, name):
        uid, shell = self.accounts[name]
        return SimpleNamespace(pw_name=name, pw_uid=uid, pw_shell=shell)

    def getpwuid(self, uid):
        for name, (nuid, _shell) in self.accounts.items():
            if nuid == uid:
                return SimpleNamespace(pw_name=name, pw_uid=uid)
        raise KeyError(f"uid {uid} not found")

    def next_free_uid(self):
        # 0/999 system range; 1000 is the image's default `open-ace` user
        # (uid 1000 per the Dockerfile), so normal allocation starts at 1001
        # — matching what shadow useradd does inside the real container.
        used = {uid for uid, _ in self.accounts.values()} | {0, 999, 1000}
        if self.allocator == "lowest":
            uid = 1000
            while uid in used:
                uid += 1
            return uid
        uid = self.next_uid
        while uid in used:
            uid += 1
        self.next_uid = uid + 1
        return uid


def _run_sync(
    monkeypatch,
    tmp_path,
    db_rows,
    passwd_accounts=None,
    allocator="max",
    project_rows=(),
    workspace_base=None,
    fail_on_sql=None,
):
    """Execute the extracted entrypoint sync with fakes; returns the log of
    useradd/usermod calls, the UPDATE statements, captured stdout, and the
    block's exit code (nonzero when the missing-actives verification or the
    outermost exception handler fires).

    ``fail_on_sql`` makes the fake cursor raise on matching SQL prefixes —
    the mid-stage-exception scenario (UPDATE failure) the outermost
    ``except Exception`` must convert into a nonzero exit."""
    fake_pwd = _FakePasswd(passwd_accounts, allocator=allocator)
    user_cmds: list[tuple] = []
    conn_log = {"commits": 0, "updates": []}
    exit_code = 0

    def fake_run(cmd, **kw):
        if cmd[0] == "id":
            if len(cmd) == 2:
                name = cmd[1]
                ok = name in fake_pwd.accounts
                return _FakeProc(0 if ok else 1, "")
            if cmd[1] == "-u":
                return _FakeProc(0, str(fake_pwd.accounts[cmd[2]][0]))
            return _FakeProc(0, "1000")
        if cmd[0] == "useradd":
            user_cmds.append(tuple(cmd))
            uid = None
            shell = "/bin/bash"
            args = cmd[1:]
            i = 0
            while i < len(args) - 1:
                if args[i] == "-u":
                    uid = int(args[i + 1])
                if args[i] == "-s":
                    shell = args[i + 1]
                i += 1
            name = args[-1]
            if uid is None:
                uid = fake_pwd.next_free_uid()
            fake_pwd._add(name, uid, shell)
            return _FakeProc(0, "")
        if cmd[0] == "usermod":
            user_cmds.append(tuple(cmd))
            if len(cmd) >= 4 and cmd[1] == "-s":
                fake_pwd.accounts[cmd[3]][1] = cmd[2]
            return _FakeProc(0, "")
        return _FakeProc(0, "")

    fake_subprocess = SimpleNamespace(run=fake_run)
    conn_holder = {}

    def fake_connect(url):
        conn = _FakeConn(db_rows, conn_log["updates"], project_rows, fail_on_sql)
        conn_holder["conn"] = conn
        return conn

    fake_psycopg2 = SimpleNamespace(connect=fake_connect)

    monkeypatch.setenv("DATABASE_URL", "postgresql://fake")
    monkeypatch.setenv("WORKSPACE_BASE_DIR", workspace_base or str(tmp_path))
    monkeypatch.setitem(sys.modules, "subprocess", fake_subprocess)
    monkeypatch.setitem(sys.modules, "pwd", fake_pwd)
    monkeypatch.setitem(sys.modules, "psycopg2", fake_psycopg2)

    import io
    from contextlib import redirect_stdout

    buf = io.StringIO()
    with redirect_stdout(buf):
        try:
            exec(compile(_extract_sync_python(), "entrypoint-user-sync", "exec"), {})
        except SystemExit as exc:  # missing-actives abort / outermost handler
            exit_code = int(exc.code or 0)
    conn_log["commits"] = conn_holder["conn"].commits
    if exit_code == 0:
        assert conn_holder["conn"].closed, "clean runs must reach conn.close()"
    return {
        "user_cmds": user_cmds,
        "updates": conn_log["updates"],
        "commits": conn_log["commits"],
        "stdout": buf.getvalue(),
        "passwd": fake_pwd,
        "exit_code": exit_code,
    }


def _useradds(log):
    return [c for c in log["user_cmds"] if c[0] == "useradd"]


RECREATE_ROWS = [
    # (id, username, system_account, system_uid, is_active, deleted_at)
    (1, "aceadmin", None, 1001, True, None),
    (2, "acealice", "acealice", 1002, True, None),
    (3, "acebob", "acebob", 1003, False, None),  # DEACTIVATED with a pin
    (4, "acecarol", "acecarol", 1004, True, None),
    (5, "acedave", None, 1005, True, None),
    (6, "aceerin", "aceerin", None, True, None),  # legacy active, no pin yet
    (7, "acefrank", "acefrank", 1007, True, "2026-09-01"),  # soft-deleted with a pin
]


class TestEntrypointSyncFunctional:
    def test_recreate_pins_actives_and_reserves_deactivated_uids(self, monkeypatch, tmp_path):
        """The issue's exact repro: fresh /etc/passwd, volume uids recorded."""
        log = _run_sync(monkeypatch, tmp_path, RECREATE_ROWS)
        useradds = _useradds(log)
        # Phase 1 — active users WITH pins, deterministic (ORDER BY id):
        assert ("useradd", "-m", "-s", "/bin/bash", "-u", "1001", "aceadmin") in useradds
        assert ("useradd", "-m", "-s", "/bin/bash", "-u", "1002", "acealice") in useradds
        assert ("useradd", "-m", "-s", "/bin/bash", "-u", "1004", "acecarol") in useradds
        assert ("useradd", "-m", "-s", "/bin/bash", "-u", "1005", "acedave") in useradds
        # Phase 2 — placeholders for deactivated AND soft-deleted pins:
        assert (
            "useradd",
            "-u",
            "1003",
            "-s",
            "/usr/sbin/nologin",
            "acebob",
        ) in useradds, "deactivated bob's uid must be reserved by a nologin placeholder"
        assert ("useradd", "-u", "1007", "-s", "/usr/sbin/nologin", "acefrank") in useradds
        # Phase 3 — legacy unpinned active gets NO -u and cannot displace a pin:
        erin = [c for c in useradds if c[-1] == "aceerin"]
        assert erin == [("useradd", "-m", "-s", "/bin/bash", "aceerin")]
        # The legacy account's auto-assigned uid is recorded back (bootstrap):
        erin_uid = log["passwd"].accounts["aceerin"][0]
        assert erin_uid not in (1001, 1002, 1003, 1004, 1005, 1007)
        # (system_uid, id) parameter order matches the UPDATE statement
        assert [params for _sql, params in log["updates"]] == [
            (erin_uid, 6)
        ], "only the unpinned row gets a record-back UPDATE"
        assert log["commits"] >= 1, "pins must be committed before project sync"
        # Final state: every uid from the volume era is owned by its own name.
        assert log["passwd"].accounts["acebob"][0] == 1003
        assert log["exit_code"] == 0, "all active users got accounts — clean run"

    def test_legacy_useradd_cannot_steal_a_pin_before_placeholder_phase(
        self, monkeypatch, tmp_path
    ):
        """Review on #3390: the phase ORDER is load-bearing, and the default
        fake allocator (max+1) cannot show it. With the realistic allocator
        (first free uid from 1000, what shadow useradd does on a fresh
        container), an unpinned phase-3 useradd WOULD land on bob's
        unreserved 1003 if placeholders ran after legacy actives — inheriting
        bob's dirs and recording the stolen uid as erin's pin."""
        log = _run_sync(monkeypatch, tmp_path, RECREATE_ROWS, allocator="lowest")
        assert log["exit_code"] == 0
        # erin's auto-assign must skip every reserved pin...
        assert (
            log["passwd"].accounts["aceerin"][0] == 1006
        ), "erin must get the first free uid OUTSIDE the pinned set"
        # ...because bob's placeholder claimed 1003 before phase 3 ran.
        assert log["passwd"].accounts["acebob"] == [1003, "/usr/sbin/nologin"]

    def test_placeholder_collision_skips_loudly_without_renumbering(self, monkeypatch, tmp_path):
        # uid 1003 already owned by an unrelated account -> no renumber, warn.
        log = _run_sync(
            monkeypatch,
            tmp_path,
            RECREATE_ROWS,
            passwd_accounts={"acemallory": [1003, "/bin/bash"]},
        )
        useradds = _useradds(log)
        assert not any(
            c[-1] == "acebob" for c in useradds
        ), "colliding placeholder must be skipped, never renumbered"
        assert "acebob" in log["stdout"] and "acemallory" in log["stdout"]
        assert "not renumbering" in log["stdout"]
        # bob is deactivated, so he is NOT in the missing-actives set — the
        # sync still completes cleanly (exit 0) despite the skipped
        # placeholder; the loud WARNING line above is his signal.
        assert log["exit_code"] == 0

    def test_active_pin_collision_skips_and_keeps_pin(self, monkeypatch, tmp_path):
        rows = list(RECREATE_ROWS) + [(8, "acezoe", "acezoe", 1003, True, None)]
        log = _run_sync(
            monkeypatch,
            tmp_path,
            rows,
            passwd_accounts={"acemallory": [1003, "/bin/bash"]},
        )
        useradds = _useradds(log)
        assert not any(c[-1] == "acezoe" for c in useradds), "collision -> skip, no renumber"
        assert not any(
            params[0] == 8 for _sql, params in log["updates"]
        ), "a skipped account must NOT get a wrong auto uid recorded"
        assert "acezoe" in log["stdout"]
        # Review on #3390: an ACTIVE user left without an OS account must
        # not be a silent failure — the block exits nonzero (the entrypoint's
        # pipefail wrapper turns that into the WARNING line).
        assert log["exit_code"] == 1
        assert "WITHOUT an OS account" in log["stdout"] and "acezoe" in log["stdout"]

    def test_missing_active_still_syncs_project_dirs_then_exits_nonzero(
        self, monkeypatch, tmp_path
    ):
        """Review round 2 on #3390: the missing-actives abort moved AFTER the
        project-dir pass — one failed account (here: zoe's pin collides with
        acemallory) must not block every other user's project-dir sync on
        every boot. The block still exits nonzero overall."""
        import os as real_os

        made = []
        monkeypatch.setattr(real_os, "makedirs", lambda path, **kw: made.append(path))
        monkeypatch.setattr(real_os.path, "exists", lambda path: False)
        rows = list(RECREATE_ROWS) + [(8, "acezoe", "acezoe", 1003, True, None)]
        log = _run_sync(
            monkeypatch,
            tmp_path,
            rows,
            passwd_accounts={"acemallory": [1003, "/bin/bash"], "acealice": [1002, "/bin/bash"]},
            project_rows=[("/workspace/acealice/proj",)],
            workspace_base="/workspace",
        )
        assert (
            "/workspace/acealice/proj" in made
        ), "the project-dir pass must run despite the missing active account"
        assert "Created project directory: /workspace/acealice/proj" in log["stdout"]
        assert "WITHOUT an OS account" in log["stdout"] and "acezoe" in log["stdout"]
        assert log["exit_code"] == 1, "nonzero overall — the failure is not silenced"

    def test_midstage_exception_exits_nonzero(self, monkeypatch, tmp_path):
        """Review round 2 on #3390: the outermost ``except Exception`` used to
        print the error and END WITH EXIT 0, so the pipefail wrapper never
        fired the WARNING — the same silence class as #3399 (a mid-stage
        UPDATE failure, connection drop, or makedirs PermissionError also
        skipped conn.commit() and the missing-actives verification). Any
        Python exception must exit nonzero."""
        log = _run_sync(monkeypatch, tmp_path, RECREATE_ROWS, fail_on_sql="UPDATE users")
        assert "Error syncing users and projects" in log["stdout"]
        assert log["exit_code"] == 1

    def test_reactivated_placeholder_upgraded_to_bash(self, monkeypatch, tmp_path):
        # bob is ACTIVE again; the account exists as his old nologin placeholder.
        rows = [list(r) for r in RECREATE_ROWS]
        rows[2][4] = True  # bob reactivated, pin 1003 intact
        log = _run_sync(
            monkeypatch,
            tmp_path,
            rows,
            passwd_accounts={"acebob": [1003, "/usr/sbin/nologin"]},
        )
        assert ("usermod", "-s", "/bin/bash", "acebob") in log["user_cmds"]
        assert not any(c[0] == "useradd" and c[-1] == "acebob" for c in _useradds(log))
        assert log["passwd"].accounts["acebob"][0] == 1003, "pin preserved"
        assert log["exit_code"] == 0

    def test_second_run_is_idempotent(self, monkeypatch, tmp_path):
        # DB state as the first run left it (every row pinned) + accounts in
        # /etc/passwd already on those pins -> nothing to do anywhere.
        accounts = {
            name: [uid, "/bin/bash"] for _id, name, _acct, uid, _a, _d in RECREATE_ROWS if uid
        }
        accounts["acebob"] = [1003, "/usr/sbin/nologin"]
        accounts["acefrank"] = [1007, "/usr/sbin/nologin"]
        accounts["aceerin"] = [1006, "/bin/bash"]
        rows = [list(r) for r in RECREATE_ROWS]
        rows[5][3] = 1006  # aceerin's pin recorded by the first run
        log = _run_sync(monkeypatch, tmp_path, rows, passwd_accounts=accounts)
        assert _useradds(log) == [], "fully-synced container must not re-useradd"
        assert log["updates"] == [], "no record churn when pins already match"


class TestEntrypointSyncTextual:
    """Ordering/wiring markers that the functional scenarios cannot see."""

    def test_useradd_u_wiring_present(self):
        content = open(ENTRYPOINT, encoding="utf-8").read()
        assert "cmd.extend(['-u', str(uid)])" in content
        assert (
            "'SELECT id, username, system_account, system_uid, is_active, deleted_at '" in content
        )
        assert "ORDER BY id" in content

    def test_placeholder_uses_pinned_uid_and_nologin(self):
        content = open(ENTRYPOINT, encoding="utf-8").read()
        assert (
            "['useradd', '-u', str(uid), '-s', '/usr/sbin/nologin', username]" in content
        ), "placeholders must pin the uid and deny login"

    def test_pinned_then_placeholders_then_legacy_ordering(self):
        content = open(ENTRYPOINT, encoding="utf-8").read()
        phase1 = content.index("Updated recorded uid")  # pinned-actives loop
        phase2 = content.index("create_placeholder_user(account, uid)")
        phase3 = content.index("pin bootstrap")  # legacy unpinned loop
        assert phase1 < phase2 < phase3, (
            "pins must be claimed before placeholders, placeholders before any "
            "auto-assigned useradd — otherwise a legacy useradd can steal a pin"
        )

    def test_sync_pipeline_does_not_mask_python_death(self):
        """Review on #3390: plain `python3 -c ... | tee LOG || echo` masked
        python's exit status (tee rc=0, no pipefail file-wide) and block-
        buffered stdout — a #3399-style boot-context kill vanished silently.
        The scoped subshell + pipefail + `python3 -u` makes death loud while
        the trailing || keeps set -e from crash-looping the service."""
        content = open(ENTRYPOINT, encoding="utf-8").read()
        assert '( set -o pipefail; python3 -u -c "' in content
        assert "open-ace-user-sync.log ) || echo" in content

    def test_missing_actives_verification_exits_nonzero(self):
        """Review on #3390: a collision skip or useradd failure leaving an
        ACTIVE user without an OS account must abort the block loudly —
        verified functionally in TestEntrypointSyncFunctional; this pins the
        wiring markers: the abort lands AFTER the project-dir pass (review
        round 2) and the pins are committed before it can fire."""
        content = open(ENTRYPOINT, encoding="utf-8").read()
        check = content.index("missing_actives")
        exit_pos = content.index("sys.exit(1)", check)
        assert (
            content.index("conn.commit()") < check
        ), "pins must be committed before the verification can exit"
        assert (
            content.index("Syncing project directories") < exit_pos
        ), "review round 2: the project-dir pass must run before the nonzero abort"

    def test_outermost_exception_handler_exits_nonzero(self):
        """Review round 2 on #3390: the sync's outermost except printed the
        error and ended with exit 0, so the pipefail wrapper never fired the
        WARNING for mid-stage exceptions (functionally proven in
        test_midstage_exception_exits_nonzero); pin the wiring marker.
        The explicit assert keeps the failure semantics visible (and
        satisfies the false-positive scanner's no_assertion gate)."""
        content = open(ENTRYPOINT, encoding="utf-8").read()
        handler = content.index("Error syncing users and projects")
        assert content.index("sys.exit(1)", handler) > handler


class TestSchedulerSyncScopedToActiveUsers:
    """Review on #3390: the scheduler's account sync ran over ALL rows with a
    system_account — including deactivated/soft-deleted ones — and since the
    multi-user compose runs the scheduler as root, ensure_system_user's
    exists-path (_ensure_login_shell) usermod -s /bin/bash'd the entrypoint's
    nologin placeholders at every scheduler boot, silently revoking the uid
    reservation. The query must mirror the entrypoint classification.
    (Textual by necessity: SchedulerWorker drags in APScheduler/metrics
    machinery that the scenario does not need.)"""

    def test_scheduler_query_filters_to_active_non_deleted(self):
        content = open(f"{ROOT}/app/scheduler_worker.py", encoding="utf-8").read()
        anchor = content.index("_sync_system_users")
        # #3396 integration note: the query now also selects tenant_id (for
        # tenant-scoped shared-group enrollment), so the DISTINCT shape from
        # the original #3390 fix is gone — the protective property under
        # test is the active/non-deleted filter, not the DISTINCT.
        query = content.index("SELECT system_account, tenant_id", anchor)
        window = content[query : query + 600]
        assert "deleted_at IS NULL" in window and "is_active = true" in window, (
            "the scheduler must not ensure accounts for deactivated/soft-deleted "
            "users — it would strip their placeholder shells"
        )


# ============================================================================
# Admin PUT /admin/users/<id>: provision after the row write, active only
# ============================================================================


class TestAdminUpdateUserProvisioning:
    """Review on #3390: api_update_user called ensure_system_user BEFORE
    update_user and regardless of active state:
    (1) record_system_uid writes back WHERE system_account = ?, but the NEW
        mapping was not on the row yet — the pin UPDATE hit 0 rows, so a
        container recreation before first login re-assigned the uid while
        /workspace/<account> kept the old owner;
    (2) editing a DEACTIVATED user hit the exists-path and _ensure_login_shell
        usermod'd the nologin placeholder back to /bin/bash (the same leak
        the scheduler fix in this PR removed elsewhere).
    Harness follows test_deactivate_revokes_workspace_3379."""

    _ADMIN = {"id": 1, "username": "admin", "role": "admin", "tenant_id": 1}

    @pytest.fixture()
    def put_env(self, app, client, monkeypatch):
        import app.routes.admin as admin_mod
        from app.services import webui_manager as wm

        events: list = []
        repo = MagicMock()
        repo.get_user_by_id.return_value = {
            "id": 7,
            "username": "bob",
            "tenant_id": 2,
            "role": "user",
            "is_active": True,
        }
        repo.update_user.side_effect = lambda **kw: events.append("update_user") or True
        repo.set_tokens_valid_after.return_value = True
        repo.delete_all_sessions_for_user.return_value = {"sessions": 0}
        monkeypatch.setattr(admin_mod, "user_repo", repo)
        monkeypatch.setattr(admin_mod, "audit_logger", MagicMock())
        monkeypatch.setattr(admin_mod, "_spawn_background", lambda fn: None)
        monkeypatch.setattr(wm, "peek_webui_manager", lambda: None)

        def fake_ensure(system_account, uid=None, tenant_id=None):
            # tenant_id kwarg: PR #3402 (#3396) enrolls into the tenant-
            # scoped shared group; the #3390 provisioning tests don't care.
            events.append(("ensure_system_user", system_account, uid))
            return True

        monkeypatch.setattr(admin_mod, "ensure_system_user", fake_ensure)

        def run(payload):
            client.set_cookie("session_token", "t")
            with (
                patch(
                    "app.auth.decorators._authenticate",
                    return_value=(True, {**self._ADMIN, "user_id": 1}),
                ),
                patch("app.routes.admin.same_tenant_user_required", lambda f: f),
            ):
                return client.put("/api/admin/users/7", json=payload)

        return SimpleNamespace(repo=repo, events=events, run=run)

    def test_new_mapping_provisions_after_row_write(self, put_env):
        env = put_env
        resp = env.run({"system_account": "acenew", "system_uid": 1010})
        assert resp.status_code == 200
        assert env.events == ["update_user", ("ensure_system_user", "acenew", 1010)], (
            "provisioning must happen AFTER update_user succeeds — the uid "
            "write-back matches WHERE system_account = ?, which only the "
            "written row carries"
        )
        assert env.repo.update_user.call_args.kwargs["system_account"] == "acenew"

    def test_deactivated_target_is_not_provisioned(self, put_env):
        put_env.repo.get_user_by_id.return_value = {
            "id": 7,
            "username": "bob",
            "tenant_id": 2,
            "role": "user",
            "is_active": False,
        }
        resp = put_env.run({"system_account": "acenew"})  # no is_active in payload
        assert resp.status_code == 200
        assert put_env.events == ["update_user"], (
            "editing a deactivated user must NOT re-shell their nologin "
            "placeholder or provision an OS account for the new mapping"
        )

    def test_explicit_deactivation_request_skips_provisioning(self, put_env):
        resp = put_env.run({"system_account": "acenew", "is_active": False})
        assert resp.status_code == 200
        assert put_env.events == ["update_user"]

    def test_reactivation_provisions(self, put_env):
        put_env.repo.get_user_by_id.return_value = {
            "id": 7,
            "username": "bob",
            "tenant_id": 2,
            "role": "user",
            "is_active": False,
        }
        resp = put_env.run({"system_account": "acenew", "is_active": True})
        assert resp.status_code == 200
        assert put_env.events == ["update_user", ("ensure_system_user", "acenew", None)], (
            "a user who ends up ACTIVE via this PUT gets provisioned (the "
            "exists-path upgrades their placeholder back to /bin/bash)"
        )
