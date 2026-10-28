"""Local seed copies against the domain servers' own databases; skipped by default."""

import pytest

from mas_optimiser.benchmarks.enterpriseops import runner, seeds

pytestmark = pytest.mark.integration

# The CSM server returns at most 100 rows unless a query sets its own limit.
PAGE = 1000
# The HR server sets these columns to the time it loads a seed, whatever the
# seed file says, so they are left out of the comparison there.
STAMPED = {"hr": {"created_at", "updated_at"}}


def server_rows(server_url, database_id, table, columns):
    """Every row of a server-side table, read a page at a time."""
    names = ", ".join(f'"{c}"' for c in columns)
    rows, offset = [], 0
    while True:
        page = seeds.run_sql(
            server_url,
            database_id,
            f'SELECT {names} FROM "{table}" ORDER BY rowid LIMIT {PAGE} OFFSET {offset}',
        )
        rows += [tuple(row[c] for c in columns) for row in page]
        if len(page) < PAGE:
            return rows
        offset += PAGE


def canonical(rows):
    """Rows in a fixed order, with booleans as the integers SQLite stores.

    Types are kept, so a number stored as text on one side and as a number on
    the other differs, as it would in a check's comparison.
    """
    return sorted(
        (tuple(int(v) if isinstance(v, bool) else v for v in row) for row in rows),
        key=repr,
    )


@pytest.mark.parametrize("domain", runner.DOMAINS)
def test_every_local_copy_matches_its_server_database_row_for_row(domain):
    schema = (seeds.SCHEMAS_DIR / f"{domain}.sql").read_text()
    for server_url, seed_paths in seeds.domain_seeds(domain).items():
        for seed_path in sorted(seed_paths):
            local = seeds.build_seed_database(seed_path, schema)
            tables = [
                name
                for (name,) in local.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table' "
                    "AND name NOT LIKE 'sqlite_%' ORDER BY name"
                )
            ]
            with seeds.server_database(server_url, seed_path) as database_id:
                server_schema = seeds.run_sql(
                    server_url, database_id, seeds.SCHEMA_QUERY
                )
                assert (
                    "\n\n".join(f"{row['sql']};" for row in server_schema) + "\n"
                    == schema
                ), f"{seed_path.name}: the server's schema differs from {domain}.sql"
                for table in tables:
                    columns = [
                        name
                        for (_, name, *_) in local.execute(
                            f'PRAGMA table_info("{table}")'
                        )
                        if name not in STAMPED.get(domain, set())
                    ]
                    names = ", ".join(f'"{c}"' for c in columns)
                    local_rows = local.execute(
                        f'SELECT {names} FROM "{table}"'
                    ).fetchall()
                    remote = server_rows(server_url, database_id, table, columns)
                    assert canonical(remote) == canonical(local_rows), (
                        f"{seed_path.name}: table {table} differs from the server"
                    )
