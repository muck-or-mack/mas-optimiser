"""Local seed copies against the domain servers' own databases; skipped by default."""

import pytest

from mas_optimiser.benchmarks.enterpriseops import runner, seeds, states

pytestmark = pytest.mark.integration


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
    # In many rows the HR server replaces these columns with the time it loads
    # the seed, so they are left out of the comparison there.
    skip = states.STAMPED.get(domain, frozenset())
    for server_url, seed_paths in seeds.domain_seeds(domain).items():
        for seed_path in sorted(seed_paths):
            local = seeds.build_seed_database(seed_path, schema)
            keys = states.primary_keys(local)
            columns = {
                table: [c for c in states.columns_of(local, table) if c not in skip]
                for table in keys
            }
            with seeds.server_database(server_url, seed_path) as database_id:
                server_schema = seeds.run_sql(
                    server_url, database_id, seeds.SCHEMA_QUERY
                )
                assert (
                    "\n\n".join(f"{row['sql']};" for row in server_schema) + "\n"
                    == schema
                ), f"{seed_path.name}: the server's schema differs from {domain}.sql"
                remote = states.read_state(server_url, database_id, columns, keys)
            for table, names in columns.items():
                listed = ", ".join(f'"{c}"' for c in names)
                local_rows = local.execute(f'SELECT {listed} FROM "{table}"').fetchall()
                remote_rows = [tuple(row[c] for c in names) for row in remote[table]]
                assert canonical(remote_rows) == canonical(local_rows), (
                    f"{seed_path.name}: table {table} differs from the server"
                )
