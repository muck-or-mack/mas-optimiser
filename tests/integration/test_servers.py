"""Checks against the domain servers started by `docker/compose.yaml`.

Skipped by default; run with `uv run pytest -m integration` once the servers
are up.
"""

import asyncio

import pytest

from mas_optimiser.benchmarks.enterpriseops import GYM_ROOT

from benchmark.mcp_client import MCPClient, create_database_from_file, delete_database

# The host ports are fixed by the task configs' `mcp_server_url` values.
SERVERS = {
    "itsm": "http://localhost:8006",
    "csm": "http://localhost:8001",
    "hr": "http://localhost:8008",
}
pytestmark = [pytest.mark.integration, pytest.mark.parametrize("domain", SERVERS)]


def test_server_lists_its_tools(domain):
    client = MCPClient(SERVERS[domain])
    assert asyncio.run(client.connect()), f"no {domain} server; is compose up?"
    tools = asyncio.run(client.list_tools())
    assert tools
    assert all("name" in tool and "inputSchema" in tool for tool in tools)


def test_server_seeds_and_deletes_a_database(domain):
    dbs = GYM_ROOT / "Domain Wise DBs and Task-DB Mappings" / domain / "dbs"
    seed = next(dbs.glob("*.sql"))
    database_id = create_database_from_file(SERVERS[domain], str(seed))
    assert database_id
    assert delete_database(SERVERS[domain], database_id)
