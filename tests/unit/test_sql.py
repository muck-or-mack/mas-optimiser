"""Unit tests of ``sql``: a check query's shape, tables, conditions and columns."""

from mas_optimiser.benchmarks.enterpriseops import sql

COLUMNS = {
    "incident": ["incident_id", "channel", "priority", "status", "caller"],
    "users": ["user_id", "email", "name"],
    "user": ["id", "name"],
}


def test_conditions_are_cut_at_top_level_ands_only():
    source, conditions = sql.clauses(
        "SELECT COUNT(*) FROM t WHERE a = 1 AND (b = 2 OR c = 3) AND "
        "d = 'x AND y' AND e IN (1, 2) AND f BETWEEN 1 AND 2 AND "
        "CASE WHEN g = 1 AND h = 2 THEN 1 END = 1"
    )
    assert source == "t"
    assert conditions == [
        "a = 1",
        "(b = 2 OR c = 3)",
        "d = 'x AND y'",
        "e IN (1, 2)",
        "f BETWEEN 1 AND 2",
        "CASE WHEN g = 1 AND h = 2 THEN 1 END = 1",
    ]
    # A BETWEEN inside a CASE does not swallow the AND after the CASE.
    assert sql.clauses(
        "SELECT COUNT(*) FROM t WHERE CASE WHEN a BETWEEN 1 AND 2 THEN 1 END = 1 AND b = 2"
    ) == ("t", ["CASE WHEN a BETWEEN 1 AND 2 THEN 1 END = 1", "b = 2"])


def test_the_where_clause_ends_before_a_tail_a_semicolon_or_a_comment():
    for tail in (" ORDER BY a LIMIT 1", ";", " -- a note", "; -- a note"):
        assert sql.clauses(f"SELECT COUNT(*) FROM t WHERE a = 1 AND b = 'x'{tail}") == (
            "t",
            ["a = 1", "b = 'x'"],
        )


def test_a_top_level_or_or_no_where_gives_no_conditions():
    assert sql.clauses("SELECT COUNT(*) FROM t WHERE a = 1 OR b = 2") is None
    assert sql.clauses("SELECT COUNT(*) FROM t WHERE a = 1 AND b = 2 OR c = 3") is None
    assert sql.clauses("SELECT COUNT(*) FROM t") is None
    assert sql.clauses("SELECT COUNT(*) FROM (SELECT 1 FROM t WHERE a = 1) x") is None
    assert sql.clauses("SELECT 'unterminated FROM t WHERE a = 1") is None


def test_a_count_parses_with_its_table_alias_source_and_conditions():
    parsed = sql.parse(
        "SELECT COUNT(*) AS n FROM incident i JOIN users u ON i.caller = u.user_id "
        "WHERE i.incident_id = 'INC_024' AND u.email = 'a@b.c'",
        COLUMNS,
    )
    assert parsed == {
        "kind": "count",
        "table": "incident",
        "alias": "i",
        "main": "incident i",
        "source": "incident i JOIN users u ON i.caller = u.user_id",
        "conditions": [
            {
                "text": "i.incident_id = 'INC_024'",
                "left": "i.incident_id",
                "joined": False,
            },
            {"text": "u.email = 'a@b.c'", "left": "u.email", "joined": True},
        ],
    }


def test_a_count_parses_through_shapes_a_pattern_would_miss():
    # An alias without AS, a quoted table name, a subquery with its own ORDER
    # BY and LIMIT, a function inside a function.
    parsed = sql.parse(
        'SELECT COUNT(*) n FROM "user" WHERE id IN (SELECT caller FROM incident '
        "ORDER BY incident_id LIMIT 1) AND LOWER(TRIM(name)) = 'ann'",
        COLUMNS,
    )
    assert parsed["table"] == "user"
    assert parsed["main"] == '"user" user'
    assert parsed["source"] == '"user"'
    assert [c["left"] for c in parsed["conditions"]] == ["id", "LOWER(TRIM(name))"]


def test_which_table_a_condition_reads_follows_the_schema():
    parsed = sql.parse(
        "SELECT COUNT(*) FROM incident i JOIN users u ON i.caller = u.user_id "
        "WHERE status = \"new\" AND email = 'a@b.c' AND "
        "(name LIKE 'A%' OR i.priority = 'high') AND "
        "caller IN (SELECT user_id FROM users WHERE name = 'Ann')",
        COLUMNS,
    )
    assert [(c["text"][:6], c["joined"]) for c in parsed["conditions"]] == [
        # A column of incident; "new" is no column of either table, so a value.
        ("status", False),
        # A column of users alone.
        ("email ", True),
        ("(name ", True),
        # The names inside the subquery belong to the subquery.
        ("caller", False),
    ]
    assert [c["left"] for c in parsed["conditions"]] == [
        "status",
        "email",
        None,
        "caller",
    ]


def test_a_single_value_parses_and_other_shapes_do_not():
    assert sql.parse("SELECT status FROM incident WHERE incident_id = 1", COLUMNS) == {
        "kind": "value",
        "table": "incident",
        "column": "status",
    }
    for query in (
        "SELECT COUNT(*) FROM incident WHERE a = 1 OR b = 2",
        "SELECT COUNT(*) FROM incident WHERE a = 1 GROUP BY b HAVING COUNT(*) > 1",
        "SELECT COUNT(*) FROM incident WHERE a = 1 ORDER BY b LIMIT 1",
        "WITH x AS (SELECT 1) SELECT COUNT(*) FROM x WHERE 1 = 1",
        "SELECT COUNT(*) FROM (SELECT 1 FROM incident) x WHERE 1 = 1",
        "SELECT COUNT(*) FROM incident",
        "SELECT a, b FROM incident WHERE a = 1",
        "SELECT u.email FROM incident i JOIN users u ON i.caller = u.user_id WHERE i.a = 1",
        "DELETE FROM incident WHERE a = 1",
        "SELECT COUNT(*) FROM",
        "",
    ):
        assert sql.parse(query, COLUMNS) is None, query


def test_the_left_side_is_left_out_where_no_column_is_tested():
    parsed = sql.parse(
        "SELECT COUNT(*) FROM incident WHERE CASE WHEN status = 'new' THEN 1 END = 1 "
        "AND 'new' = status AND NOT priority = 'high' AND EXISTS (SELECT 1 FROM users)",
        COLUMNS,
    )
    assert [c["left"] for c in parsed["conditions"]] == [None, None, "priority", None]


def test_the_column_of_a_condition_is_read_through_functions_and_negation():
    assert sql.column_of("i.status = 'new'") == "status"
    assert sql.column_of("COALESCE(notes, '') = ''") == "notes"
    assert sql.column_of("strftime('%Y', opened) = '2025'") == "opened"
    assert sql.column_of("LOWER(TRIM(name)) = 'x'") == "name"
    assert sql.column_of("channel NOT IN ('chat', 'email')") == "channel"
    assert sql.column_of("opened BETWEEN '2025-01-01' AND '2025-12-31'") == "opened"
    assert sql.column_of("closed IS NOT NULL") == "closed"
    assert sql.column_of('"status" = "new"') == "status"


def test_a_condition_with_no_single_column_has_none():
    assert sql.column_of("(a = 1 OR b = 2)") == ""
    assert sql.column_of("NOT EXISTS (SELECT 1 FROM t WHERE t.a = b)") == ""
    assert sql.column_of("'new' = 'new'") == ""
    assert sql.column_of("unbalanced ((") == ""
    assert sql.column_of("") == ""


def test_the_tables_of_a_query_include_joins_and_subqueries_but_not_its_own_names():
    assert sql.tables(
        'SELECT COUNT(*) FROM Incident i JOIN "user" u ON i.caller = u.id '
        "WHERE i.kind IN (SELECT kind FROM kinds)"
    ) == {"incident", "user", "kinds"}
    assert sql.tables(
        "WITH recent AS (SELECT * FROM incident) SELECT COUNT(*) FROM recent"
    ) == {"incident"}
    # The word FROM inside a value or a function names no table.
    assert sql.tables(
        "SELECT COUNT(*) FROM notes WHERE body = 'reply FROM support' "
        "AND TRIM(BOTH ' ' FROM title) = 'x'"
    ) == {"notes"}
    assert sql.tables("SELECT COUNT(*) FROM") == set()
