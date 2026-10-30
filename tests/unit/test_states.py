import random
import sqlite3

import pytest

from mas_optimiser.benchmarks.enterpriseops import states

SCHEMA = """
CREATE TABLE incident (
    incident_id VARCHAR(16) NOT NULL, number VARCHAR(16), caller_id VARCHAR(16),
    priority VARCHAR(16), assigned_to INTEGER, state VARCHAR(16), notes TEXT,
    PRIMARY KEY (incident_id)
);
CREATE UNIQUE INDEX ix_number ON incident (number);
CREATE TABLE user_role (
    user_id INTEGER NOT NULL, role_id INTEGER NOT NULL, PRIMARY KEY (user_id, role_id)
);
"""
ROWS = """
INSERT INTO incident VALUES
('INC_001', 'N1', 'USER_1', 'low', NULL, 'new', '{"tag": "vpn"}'),
('INC_002', 'N2', 'USER_2', 'high', 7, 'new', NULL),
('INC_003', 'N3', 'USER_2', 'low', 4, 'closed', NULL);
INSERT INTO user_role VALUES (1, 1), (1, 2), (2, 1);
"""


@pytest.fixture
def seed():
    connection = sqlite3.connect(":memory:")
    connection.executescript(SCHEMA + ROWS)
    return connection


def state_of(connection):
    """Every row of a local database, as ``read_state`` gives a server's."""
    return {
        table: [
            dict(zip(states.columns_of(connection, table), row, strict=True))
            for row in connection.execute(f'SELECT * FROM "{table}"')
        ]
        for table in states.primary_keys(connection)
    }


def rows_of(connection):
    """Each table's rows, order aside: a rebuild writes changed rows back last."""
    return {
        table: sorted(map(repr, rows)) for table, rows in state_of(connection).items()
    }


def copy_of(seed):
    after = sqlite3.connect(":memory:")
    seed.backup(after)
    return after


def test_primary_keys_keep_composite_keys_in_order(seed):
    assert states.primary_keys(seed) == {
        "incident": ["incident_id"],
        "user_role": ["user_id", "role_id"],
    }


def test_a_table_without_a_primary_key_is_refused(seed):
    seed.execute("CREATE TABLE note (text TEXT)")
    with pytest.raises(ValueError, match="note has no primary key"):
        states.primary_keys(seed)


def test_diff_lists_inserted_deleted_and_updated_rows(seed):
    after = copy_of(seed)
    after.execute("UPDATE incident SET priority = 'high' WHERE incident_id = 'INC_001'")
    after.execute("DELETE FROM user_role WHERE user_id = 2")
    after.execute(
        "INSERT INTO incident VALUES ('INC_009', 'N9', 'U', 'low', NULL, 'new', NULL)"
    )
    keys = states.primary_keys(seed)
    assert states.diff(seed, state_of(after), keys) == {
        "incident": {
            "inserted": [
                {
                    "incident_id": "INC_009",
                    "number": "N9",
                    "caller_id": "U",
                    "priority": "low",
                    "assigned_to": None,
                    "state": "new",
                    "notes": None,
                }
            ],
            "deleted": [],
            "updated": [
                {
                    "key": {"incident_id": "INC_001"},
                    "changes": {"priority": ["low", "high"]},
                }
            ],
        },
        "user_role": {
            "inserted": [],
            "deleted": [{"user_id": 2, "role_id": 1}],
            "updated": [],
        },
    }


def test_diff_and_rebuild_give_back_the_final_state_exactly(seed):
    rng = random.Random(0)
    keys = states.primary_keys(seed)
    for trial in range(50):
        after = copy_of(seed)
        for _ in range(rng.randint(1, 5)):
            action = rng.choice(["update", "delete", "insert", "role"])
            row = rng.choice(["INC_001", "INC_002", "INC_003"])
            try:
                if action == "update":
                    column = rng.choice(["priority", "assigned_to", "notes", "state"])
                    value = rng.choice([None, 0, "x", f"t{trial}"])
                    after.execute(
                        f"UPDATE incident SET {column} = ? WHERE incident_id = ?",
                        [value, row],
                    )
                elif action == "delete":
                    after.execute("DELETE FROM incident WHERE incident_id = ?", [row])
                elif action == "insert":
                    after.execute(
                        "INSERT INTO incident VALUES (?, ?, 'U', 'low', NULL, 'new', NULL)",
                        [f"INC_{trial}", f"N{trial}"],
                    )
                else:
                    after.execute("INSERT INTO user_role VALUES (?, ?)", [trial, trial])
            except sqlite3.IntegrityError:
                pass
        rebuilt = states.rebuild(seed, states.diff(seed, state_of(after), keys), keys)
        assert rows_of(rebuilt) == rows_of(after)


def test_rebuild_survives_two_rows_swapping_a_unique_value(seed):
    after = copy_of(seed)
    after.execute("UPDATE incident SET number = 'tmp' WHERE incident_id = 'INC_001'")
    after.execute("UPDATE incident SET number = 'N1' WHERE incident_id = 'INC_002'")
    after.execute("UPDATE incident SET number = 'N2' WHERE incident_id = 'INC_001'")
    keys = states.primary_keys(seed)
    rebuilt = states.rebuild(seed, states.diff(seed, state_of(after), keys), keys)
    assert rows_of(rebuilt) == rows_of(after)


def test_diff_leaves_out_skipped_columns_and_reads_booleans_as_stored(seed):
    keys = states.primary_keys(seed)
    state = state_of(seed)
    state["incident"][0] = state["incident"][0] | {"state": "stamped"}
    state["user_role"][0] = {"user_id": True, "role_id": 1}
    assert states.diff(seed, state, keys, skip=frozenset({"state"})) == {}


def test_read_state_reads_each_table_a_page_at_a_time(seed, monkeypatch):
    queries = []

    def run_sql(server_url, database_id, query):
        queries.append(query)
        limit, offset = (int(n) for n in query.split("LIMIT ")[1].split(" OFFSET "))
        table = query.split('FROM "')[1].split('"')[0]
        rows = state_of(seed)[table]
        return rows[offset : offset + limit]

    monkeypatch.setattr(states.seeds, "run_sql", run_sql)
    monkeypatch.setattr(states, "PAGE", 2)
    keys = states.primary_keys(seed)
    columns = {table: states.columns_of(seed, table) for table in keys}
    state = states.read_state("http://server", "db", columns, keys)
    assert state == state_of(seed)
    # Three rows in each table: a full page, then a short one that ends the read.
    assert len(queries) == 4
    assert queries[0] == (
        'SELECT "incident_id", "number", "caller_id", "priority", "assigned_to", '
        '"state", "notes" FROM "incident" ORDER BY "incident_id" LIMIT 2 OFFSET 0'
    )


def test_summary_counts_changes_by_table_and_names_tables_no_check_mentions():
    changes = {
        "incident": {
            "inserted": [{"incident_id": "I1"}],
            "deleted": [],
            "updated": [
                {
                    "key": {"incident_id": "I0"},
                    "changes": {
                        "status": ["new", "closed"],
                        "priority": ["low", "high"],
                    },
                }
            ],
        },
        "notification": {
            "inserted": [{"id": 1}],
            "deleted": [{"id": 2}],
            "updated": [],
        },
    }
    queries = [
        "SELECT COUNT(*) FROM incident i JOIN users u ON i.caller = u.user_id "
        "WHERE i.status = 'closed'"
    ]
    assert states.summary(changes, queries) == [
        "incident: 1 inserted, 1 updated (priority, status)",
        "notification: 1 inserted, 1 deleted",
        "no check of this task mentions: notification",
    ]
    assert states.summary({}, queries) == ["no rows changed"]
