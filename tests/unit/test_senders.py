"""Unit tests for sender validation utility."""

from app.utils.senders import get_sender_filter_sql, is_valid_sender


class TestIsValidSender:
    """Tests for is_valid_sender function."""

    def test_is_valid_sender_normal_name(self):
        assert is_valid_sender("alice") is True

    def test_is_valid_sender_chinese_name(self):
        assert is_valid_sender("张三") is True

    def test_is_valid_sender_empty(self):
        assert is_valid_sender("") is False

    def test_is_valid_sender_none_like(self):
        # Function accepts str, but empty string should be False
        assert is_valid_sender("") is False

    def test_is_valid_sender_ou_short(self):
        """Short ou_ names (length <= 10) should be valid."""
        assert is_valid_sender("ou_abc") is True  # length 6
        assert is_valid_sender("ou_1234567") is True  # length 10
        assert is_valid_sender("ou_") is True  # length 3

    def test_is_valid_sender_ou_long(self):
        """Long ou_ names (length > 10) should be invalid (Feishu Open IDs)."""
        assert is_valid_sender("ou_1234567890") is False  # length 13
        assert is_valid_sender("ou_3e479c7f81f8674741d778e8f838f8ed") is False

    def test_is_valid_sender_ou_exactly_10(self):
        """Edge case: ou_ prefix with total length exactly 10 should be valid."""
        # "ou_1234567" = 10 chars
        assert is_valid_sender("ou_1234567") is True

    def test_is_valid_sender_ou_exactly_11(self):
        """Edge case: ou_ prefix with total length 11 should be invalid."""
        # "ou_12345678" = 11 chars
        assert is_valid_sender("ou_12345678") is False

    def test_is_valid_sender_webui_format(self):
        """WebUI format (system_account-hostname-tool) should be valid."""
        assert is_valid_sender("user1-host1-claude") is True


class TestIsValidSenderPlaceholderValues:
    """Tests for placeholder value filtering (Issue #828, #834)."""

    def test_is_valid_sender_null(self):
        """'null' string should be filtered out."""
        assert is_valid_sender("null") is False

    def test_is_valid_sender_none_string(self):
        """'None' string should be filtered out."""
        assert is_valid_sender("None") is False

    def test_is_valid_sender_undefined(self):
        """'undefined' string should be filtered out."""
        assert is_valid_sender("undefined") is False

    def test_is_valid_sender_na(self):
        """'N/A' string should be filtered out."""
        assert is_valid_sender("N/A") is False

    def test_is_valid_sender_unknown_uppercase(self):
        """'Unknown' string should be filtered out."""
        assert is_valid_sender("Unknown") is False

    def test_is_valid_sender_unknown_lowercase(self):
        """'unknown' string should be filtered out."""
        assert is_valid_sender("unknown") is False

    def test_is_valid_sender_placeholder_format_unknown(self):
        """'<unknown>' placeholder format should be filtered out."""
        assert is_valid_sender("<unknown>") is False

    def test_is_valid_sender_placeholder_format_none(self):
        """'<None>' placeholder format should be filtered out."""
        assert is_valid_sender("<None>") is False

    def test_is_valid_sender_placeholder_format_generic(self):
        """Generic placeholder format <...> should be filtered out."""
        assert is_valid_sender("<placeholder>") is False
        assert is_valid_sender("<invalid>") is False

    def test_is_valid_sender_valid_name_not_exact_placeholder(self):
        """Names that don't match exact placeholder format should be valid."""
        assert is_valid_sender("alice<bob>") is True  # Not exact <...> format
        assert is_valid_sender("<a") is True  # Doesn't end with >
        assert is_valid_sender("a>") is True  # Doesn't start with <


class TestGetSenderFilterSql:
    """Tests for get_sender_filter_sql function (SQL-layer filtering)."""

    def test_get_sender_filter_sql_default_column(self):
        """Default column name should be 'sender_name'."""
        sql = get_sender_filter_sql()
        assert "sender_name" in sql
        # %% is escaped % for psycopg2/SQLite compatibility
        assert "LIKE 'ou_%%'" in sql
        assert "LENGTH(sender_name)" in sql

    def test_get_sender_filter_sql_custom_column(self):
        """Custom column name should be used."""
        sql = get_sender_filter_sql("ds.sender_name")
        assert "ds.sender_name" in sql
        assert "LENGTH(ds.sender_name)" in sql

    def test_get_sender_filter_sql_placeholder_values(self):
        """SQL should include placeholder values filter."""
        sql = get_sender_filter_sql()
        assert "NOT IN" in sql
        assert "'null'" in sql
        assert "'None'" in sql
        assert "'undefined'" in sql
        assert "'N/A'" in sql
        assert "'Unknown'" in sql
        assert "'unknown'" in sql

    def test_get_sender_filter_sql_placeholder_format(self):
        """SQL should include placeholder format filter."""
        sql = get_sender_filter_sql()
        # %% is escaped % for psycopg2/SQLite compatibility
        assert "NOT LIKE '<%%>'" in sql

    def test_get_sender_filter_sql_feishu_id_filter(self):
        """SQL should include Feishu ID filter."""
        sql = get_sender_filter_sql()
        assert "NOT (" in sql
        # %% is escaped % for psycopg2/SQLite compatibility
        assert "LIKE 'ou_%%'" in sql
        assert "> 10" in sql
