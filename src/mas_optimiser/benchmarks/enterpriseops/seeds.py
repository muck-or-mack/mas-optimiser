"""Fetch each domain's table schema from its server and build local copies of the seed databases.

A task's seed file holds INSERT statements only; the tables' columns, types
and keys live in the domain server's image, and each attempt's database is
built there and deleted after verification. A local SQLite copy of a seeded
database, built from the fetched schema and the seed file, lets check
predicates be tested against a task's starting state without the servers and
without running an agent.

``mas-optimiser schema`` seeds a database on each domain's server from every
seed file the domain's tasks use, reads the CREATE statements of its tables
and indexes back from ``sqlite_master``, checks that every seed file of the
domain gets the same schema, writes it to ``data/schemas/<domain>.sql`` and
deletes the server-side databases. ``build_seed_database`` then creates a
local database from a schema and a seed file; the integration tests check
that it matches the server's database row for row. Usage::

    mas-optimiser schema
    mas-optimiser schema --domain itsm

``python -m mas_optimiser.benchmarks.enterpriseops.seeds`` takes the same arguments.
"""

import argparse
import contextlib
import secrets
import sqlite3
import time
from collections.abc import Iterator
from pathlib import Path

import httpx

from mas_optimiser.benchmarks.enterpriseops import REPO_ROOT, runner

from benchmark.mcp_client import delete_database

SCHEMAS_DIR = REPO_ROOT / "data" / "schemas"
# Tables first, so that the indexes on them can be created after. SQLite
# keeps AUTOINCREMENT counters in its own sqlite_sequence table, which a
# database creates for itself and refuses to have created by a script, and
# the automatic indexes behind PRIMARY KEY and UNIQUE columns have no SQL.
SCHEMA_QUERY = (
    "SELECT sql FROM sqlite_master WHERE type IN ('table', 'index', 'trigger', "
    "'view') AND sql IS NOT NULL AND name NOT LIKE 'sqlite_%' "
    "ORDER BY type != 'table', name"
)
# The server can take minutes to load the largest seed files.
SEED_TIMEOUT = 1200
DESCRIPTION = __doc__.split("\n\n")[0]


def run_sql(server_url: str, database_id: str, query: str) -> list[dict]:
    """Run a query on a server-side database through the benchmark's SQL runner API.

    Args:
        server_url: The domain server, e.g. ``http://localhost:8006``.
        database_id: The database the server created from a seed file.
        query: The SQL to run.

    Returns:
        The rows, each a dict of column name to value.

    Raises:
        RuntimeError: If the server answers with an error status or reports
            that the query failed.
    """
    response = httpx.post(
        f"{server_url.rstrip('/')}/api/sql-runner",
        json={"query": query, "database_id": database_id},
        headers={"Content-Type": "application/json", "x-database-id": database_id},
        timeout=60,
    )
    if response.status_code != 200:
        raise RuntimeError(
            f"query failed on {server_url}: HTTP {response.status_code} "
            f"{response.text[:200]}"
        )
    body = response.json()
    if not body.get("success"):
        raise RuntimeError(f"query failed on {server_url}: {body}")
    return body.get("data") or []


@contextlib.contextmanager
def server_database(server_url: str, seed_path: Path) -> Iterator[str]:
    """Seed a database on the server from a seed file and delete it afterwards.

    The database id is chosen before the server is asked to seed, so a seed
    the server rejects part-way is deleted too.

    Args:
        server_url: The domain server, e.g. ``http://localhost:8006``.
        seed_path: The seed file to load.

    Yields:
        The server-side database id, for ``run_sql``.

    Raises:
        RuntimeError: If the server cannot seed the file, or cannot delete the
            database once the block has finished without an error.
    """
    database_id = f"db_{time.time_ns()}_{secrets.token_hex(4)}"
    try:
        try:
            response = httpx.post(
                f"{server_url.rstrip('/')}/api/seed-database",
                json={
                    "database_id": database_id,
                    "name": f"seed copy {seed_path.name}",
                    "description": f"from {seed_path.name}",
                    "sql_content": seed_path.read_text(encoding="utf-8"),
                },
                timeout=SEED_TIMEOUT,
            )
        except httpx.HTTPError as error:
            raise RuntimeError(f"{server_url} could not be reached: {error}") from error
        if response.status_code != 200:
            raise RuntimeError(
                f"{server_url} could not seed a database from {seed_path.name}: "
                f"HTTP {response.status_code} {response.text[:200]}"
            )
        yield database_id
    finally:
        deleted = delete_database(server_url, database_id)
    if not deleted:
        raise RuntimeError(f"database {database_id} was left on {server_url}")


def fetch_schema(server_url: str, seed_path: Path) -> str:
    """Seed a database on the server, read its CREATE statements, delete it.

    Args:
        server_url: The domain server to seed.
        seed_path: The seed file, as named in the task's server config.

    Returns:
        The CREATE statements, tables in name order and then indexes, each
        ending in a semicolon.

    Raises:
        RuntimeError: As ``server_database`` and ``run_sql`` do.
    """
    with server_database(server_url, seed_path) as database_id:
        rows = run_sql(server_url, database_id, SCHEMA_QUERY)
    return "\n\n".join(f"{row['sql']};" for row in rows) + "\n"


def seed_statements(seed_path: Path) -> str:
    """The seed file's SQL with its comment lines removed.

    Raises:
        ValueError: If a quoted value runs over a line break. The server
            stores such a value with a space where the break was, by a rule
            not visible from outside, so a local copy could differ from the
            server's; no current seed file has one.
    """
    kept = []
    for number, line in enumerate(
        seed_path.read_text(encoding="utf-8").splitlines(), 1
    ):
        if line.lstrip().startswith("--"):
            continue
        # Each line starts outside a value, since none may run over a line
        # break. A doubled quote inside a value is an escaped quote, and a
        # comment starts at "--" only outside a value.
        inside, i = False, 0
        while i < len(line):
            if inside and line.startswith("''", i):
                i += 2
                continue
            if line[i] == "'":
                inside = not inside
            elif not inside and line.startswith("--", i):
                break
            i += 1
        if inside:
            raise ValueError(
                f"{seed_path.name}: line {number} ends inside a quoted value"
            )
        kept.append(line)
    return "\n".join(kept)


def build_seed_database(seed_path: Path, schema_sql: str) -> sqlite3.Connection:
    """Create an in-memory SQLite database with the server's schema and a seed file's rows.

    Args:
        seed_path: The seed file to load.
        schema_sql: The CREATE statements, as ``fetch_schema`` returns them.

    Returns:
        An open connection to the seeded database.
    """
    connection = sqlite3.connect(":memory:")
    connection.executescript(schema_sql)
    connection.executescript(seed_statements(seed_path))
    connection.commit()
    return connection


def domain_seeds(domain: str) -> dict[str, set[Path]]:
    """Each server URL a domain's tasks use to the seed files seeded on it."""
    seeds: dict[str, set[Path]] = {}
    for config in runner.load_tasks(domain, "oracle").values():
        for server in config["gym_servers_config"]:
            seeds.setdefault(server["mcp_server_url"], set()).add(
                Path(server["seed_database_file"])
            )
    return seeds


def add_arguments(parser: argparse.ArgumentParser) -> None:
    """Add the schema options to ``parser``; shared with the ``mas-optimiser`` command."""
    parser.add_argument(
        "--domain",
        nargs="+",
        choices=runner.DOMAINS,
        default=runner.DOMAINS,
        help="domains to fetch (default: all)",
    )


def run(args: argparse.Namespace) -> None:
    """Fetch and save each domain's schema, checking that its seed files agree."""
    SCHEMAS_DIR.mkdir(parents=True, exist_ok=True)
    for domain in args.domain:
        schema = first = None
        for server_url, seed_paths in domain_seeds(domain).items():
            runner.check_servers({server_url})
            for seed_path in sorted(seed_paths):
                fetched = fetch_schema(server_url, seed_path)
                if schema is None:
                    schema, first = fetched, seed_path
                elif fetched != schema:
                    raise SystemExit(
                        f"error: {domain}: {seed_path.name} and {first.name} give "
                        "different schemas; one schema per domain is assumed"
                    )
                tables = (
                    build_seed_database(seed_path, schema)
                    .execute("SELECT COUNT(*) FROM sqlite_master WHERE type = 'table'")
                    .fetchone()[0]
                )
                print(f"{domain}: {seed_path.name} loads locally, {tables} tables")
        path = SCHEMAS_DIR / f"{domain}.sql"
        path.write_text(schema or "")
        print(f"wrote {path}")


def main() -> None:
    """Command-line entry point; see the module docstring for usage."""
    parser = argparse.ArgumentParser(description=DESCRIPTION)
    add_arguments(parser)
    run(parser.parse_args())


if __name__ == "__main__":
    main()
