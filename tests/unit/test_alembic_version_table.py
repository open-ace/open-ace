from migrations.version_table import VERSION_NUM_LENGTH, build_version_table


def test_alembic_version_table_allows_long_revision_ids():
    table = build_version_table("alembic_version", None, True)

    version_col = table.c.version_num

    assert version_col.type.length == VERSION_NUM_LENGTH
    assert version_col.primary_key is True
