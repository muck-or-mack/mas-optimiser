"""Read an attempt's final database from its server, compare it with the seed, and rebuild it locally.

The domain server deletes an attempt's database once the attempt is over, so
its final state is read back first, every table in full. Compared by primary
key with the local copy of the seed, it reduces to the rows the attempt
inserted, the rows it deleted and, for each row it updated, the cells that
changed with their values before and after. That difference is small (a few
kilobytes) and is saved with the attempt; applied to a fresh copy of the seed,
it gives back the final state, on which the attempt can be scored again at any
time.

The local seed copy matches the server's own database row for row (see
``tests/integration/test_seeds.py``) with one exception: the HR server sets
``created_at`` and ``updated_at`` to the time it loads the seed, so those two
columns are left out of the comparison for HR. No check tests either.
"""

import sqlite3

from mas_optimiser.benchmarks.enterpriseops import seeds

# The CSM server returns at most 100 rows to a query without a limit of its
# own, so every table is read a page at a time.
PAGE = 1000
STAMPED = {"hr": frozenset({"created_at", "updated_at"})}


def columns_of(connection: sqlite3.Connection, table: str) -> list[str]:
    """A table's columns, in table order."""
    return [row[1] for row in connection.execute(f'PRAGMA table_info("{table}")')]


def primary_keys(connection: sqlite3.Connection) -> dict[str, list[str]]:
    """Each table's primary key columns, in key order.

    Raises:
        ValueError: If a table has no primary key, since its rows could not
            be matched with the seed's.
    """
    tables = [
        name
        for (name,) in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' "
            "AND name NOT LIKE 'sqlite_%' ORDER BY name"
        )
    ]
    keys = {}
    for table in tables:
        info = connection.execute(f'PRAGMA table_info("{table}")').fetchall()
        keys[table] = [row[1] for row in sorted(info, key=lambda row: row[5]) if row[5]]
        if not keys[table]:
            raise ValueError(f"table {table} has no primary key")
    return keys


def read_state(
    server_url: str, database_id: str, columns: dict, keys: dict
) -> dict[str, list[dict]]:
    """Every row of every table of a server-side database.

    Args:
        server_url: The domain server.
        database_id: The attempt's database on it.
        columns: Each table's columns, as ``columns_of`` gives them for the
            seed copy.
        keys: Each table's primary key columns.

    Returns:
        Each table's rows, as dicts of column to value, in key order.
    """
    state = {}
    for table, key in keys.items():
        names = ", ".join(f'"{c}"' for c in columns[table])
        order = ", ".join(f'"{c}"' for c in key)
        rows, offset = [], 0
        while True:
            page = seeds.run_sql(
                server_url,
                database_id,
                f'SELECT {names} FROM "{table}" ORDER BY {order} '
                f"LIMIT {PAGE} OFFSET {offset}",
            )
            rows += page
            if len(page) < PAGE:
                break
            offset += PAGE
        state[table] = rows
    return state


def as_stored(value: object) -> object:
    """A value as SQLite stores it: booleans are integers."""
    return int(value) if isinstance(value, bool) else value


def diff(
    seed: sqlite3.Connection,
    state: dict[str, list[dict]],
    keys: dict,
    skip: frozenset = frozenset(),
) -> dict:
    """What an attempt changed, table by table, against the seed.

    Args:
        seed: The local copy of the task's seed.
        state: The attempt's final rows, as ``read_state`` gives them.
        keys: Each table's primary key columns.
        skip: Columns whose changes are left out (``STAMPED`` for the domain).

    Returns:
        For each table with a change: ``inserted`` rows (every column),
        ``deleted`` rows (their keys) and ``updated`` rows (``key`` and
        ``changes``, column to ``[before, after]``), each in key order.
    """
    changes = {}
    for table, rows in state.items():
        columns, key = columns_of(seed, table), keys[table]
        names = ", ".join(f'"{c}"' for c in columns)
        before = {
            tuple(row[columns.index(k)] for k in key): dict(
                zip(columns, row, strict=True)
            )
            for row in seed.execute(f'SELECT {names} FROM "{table}"')
        }
        after = {
            tuple(as_stored(row[k]) for k in key): {
                c: as_stored(row[c]) for c in columns
            }
            for row in rows
        }
        inserted = [after[k] for k in sorted(after.keys() - before.keys(), key=repr)]
        deleted = [
            dict(zip(key, k, strict=True))
            for k in sorted(before.keys() - after.keys(), key=repr)
        ]
        updated = []
        for k in sorted(before.keys() & after.keys(), key=repr):
            changed = {
                c: [before[k][c], after[k][c]]
                for c in columns
                if c not in skip and before[k][c] != after[k][c]
            }
            if changed:
                updated.append(
                    {"key": dict(zip(key, k, strict=True)), "changes": changed}
                )
        if inserted or deleted or updated:
            changes[table] = {
                "inserted": inserted,
                "deleted": deleted,
                "updated": updated,
            }
    return changes


def rebuild(seed: sqlite3.Connection, changes: dict, keys: dict) -> sqlite3.Connection:
    """A new in-memory database holding the seed with an attempt's changes applied.

    Every changed row is deleted before any is written back, so a change that
    swaps unique values between rows never collides on the way.
    """
    final = sqlite3.connect(":memory:")
    seed.backup(final)
    for table, change in changes.items():
        key = keys[table]
        match = " AND ".join(f'"{k}" IS ?' for k in key)
        names = columns_of(final, table)
        rewritten = []
        for update in change["updated"]:
            values = [update["key"][k] for k in key]
            row = final.execute(
                f'SELECT * FROM "{table}" WHERE {match}', values
            ).fetchone()
            rewritten.append(
                dict(zip(names, row, strict=True))
                | {c: after for c, (_, after) in update["changes"].items()}
            )
        for row_key in change["deleted"] + [
            update["key"] for update in change["updated"]
        ]:
            final.execute(
                f'DELETE FROM "{table}" WHERE {match}', [row_key[k] for k in key]
            )
        for row in change["inserted"] + rewritten:
            listed = ", ".join(f'"{c}"' for c in row)
            marks = ", ".join("?" for _ in row)
            final.execute(
                f'INSERT INTO "{table}" ({listed}) VALUES ({marks})', list(row.values())
            )
    final.commit()
    return final
