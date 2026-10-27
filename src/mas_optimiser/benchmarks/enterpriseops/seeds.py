"""Fetch each domain's table schema from its server and build local copies of the seed databases.

A task's seed file holds INSERT statements only; the tables' columns, types
and keys live in the domain server's image, and each attempt's database is
built there and deleted after verification. A local SQLite copy of a seeded
database, built from the fetched schema and the seed file, lets check
predicates be tested against a task's starting state without the servers and
without running an agent.

``mas-optimiser schema`` seeds a database on each domain's server from every
seed file the domain's tasks use, reads the CREATE statements back from
``sqlite_master``, checks that every seed file of the domain gets the same
schema, writes it to ``data/schemas/<domain>.sql`` and deletes the server-side
databases. ``build_seed_database`` then creates a local database from a schema
and a seed file. Usage::

    mas-optimiser schema
    mas-optimiser schema --domain itsm

``python -m mas_optimiser.benchmarks.enterpriseops.seeds`` takes the same arguments.
"""

import argparse
import sqlite3
from pathlib import Path

import httpx

from mas_optimiser.benchmarks.enterpriseops import runner

from benchmark.mcp_client import create_database_from_file, delete_database

SCHEMAS_DIR = Path("data") / "schemas"
# SQLite keeps AUTOINCREMENT counters in its own sqlite_sequence table, which
# a database creates for itself and refuses to have created by a script.
SCHEMA_QUERY = (
    "SELECT sql FROM sqlite_master WHERE type = 'table' AND sql IS NOT NULL "
    "AND name NOT LIKE 'sqlite_%' ORDER BY name"
)
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
        RuntimeError: If the server reports the query failed.
    """
    response = httpx.post(
        f"{server_url.rstrip('/')}/api/sql-runner",
        json={"query": query, "database_id": database_id},
        headers={"Content-Type": "application/json", "x-database-id": database_id},
        timeout=60,
    )
    body = response.json()
    if response.status_code != 200 or not body.get("success"):
        raise RuntimeError(f"query failed on {server_url}: {body}")
    return body.get("data") or []


def fetch_schema(server_url: str, seed_path: Path) -> str:
    """Seed a database on the server, read its CREATE TABLE statements, delete it.

    Args:
        server_url: The domain server to seed.
        seed_path: The seed file, as named in the task's server config.

    Returns:
        The CREATE statements, one per table in name order, each ending in a
        semicolon.

    Raises:
        RuntimeError: If the server cannot build a database from the file.
    """
    database_id = create_database_from_file(server_url, str(seed_path))
    if database_id is None:
        raise RuntimeError(f"{server_url} could not seed a database from {seed_path}")
    try:
        rows = run_sql(server_url, database_id, SCHEMA_QUERY)
    finally:
        delete_database(server_url, database_id)
    return "\n\n".join(f"{row['sql']};" for row in rows) + "\n"


def seed_statements(seed_path: Path) -> str:
    """The seed file's SQL with its comment lines removed."""
    lines = seed_path.read_text(encoding="utf-8").splitlines()
    return "\n".join(line for line in lines if not line.lstrip().startswith("--"))


def build_seed_database(
    seed_path: Path, schema_sql: str, path: str | Path = ":memory:"
) -> sqlite3.Connection:
    """Create a local SQLite database with the server's schema and a seed file's rows.

    Args:
        seed_path: The seed file to load.
        schema_sql: The CREATE statements, as ``fetch_schema`` returns them.
        path: Where to create the database; in memory by default.

    Returns:
        An open connection to the seeded database.
    """
    connection = sqlite3.connect(path)
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
        schema = None
        for server_url, seed_paths in domain_seeds(domain).items():
            runner.check_servers({server_url})
            for seed_path in sorted(seed_paths):
                fetched = fetch_schema(server_url, seed_path)
                if schema is None:
                    schema = fetched
                elif fetched != schema:
                    raise SystemExit(
                        f"{domain}: {seed_path.name} gives a different schema from "
                        "the first seed file; one schema per domain was assumed"
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
