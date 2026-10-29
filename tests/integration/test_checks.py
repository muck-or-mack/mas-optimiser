"""Every check file's queries against the domain servers' own seeded databases; skipped by default.

A requirement's ``seed_count`` was worked out on a local copy of the task's
starting database. Scoring runs the queries on the servers, which can refuse
a query the local copy accepts, so each query is run on a database the
server seeded itself and must give the recorded count there too. Failures
name the task and requirement only.
"""

from collections import defaultdict
from pathlib import Path

import pytest

from mas_optimiser.benchmarks.enterpriseops import checks, runner, seeds

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("domain", runner.DOMAINS)
def test_every_requirement_gives_its_seed_count_on_the_server(domain):
    files = checks.load_checks()
    by_seed = defaultdict(list)
    for task_id, config in runner.load_tasks(domain, "oracle").items():
        (server,) = config["gym_servers_config"]
        key = (server["mcp_server_url"], Path(server["seed_database_file"]))
        by_seed[key].append(task_id)
    missing = [t for ts in by_seed.values() for t in ts if t not in files]
    assert not missing, f"{len(missing)} {domain} tasks have no check file"
    wrong = []
    for (server_url, seed_path), task_ids in sorted(by_seed.items()):
        with seeds.server_database(server_url, seed_path) as database_id:
            for task_id in task_ids:
                for i, requirement in enumerate(files[task_id]["requirements"]):
                    where = f"{task_id} requirements[{i}]"
                    try:
                        (row,) = seeds.run_sql(
                            server_url, database_id, requirement["sql"]
                        )
                    except (RuntimeError, ValueError):
                        wrong.append(f"{where}: the server refused the query")
                        continue
                    (count,) = row.values()
                    if count != requirement["seed_count"]:
                        wrong.append(
                            f"{where}: {count} on the server, seed_count "
                            f"{requirement['seed_count']}"
                        )
    assert not wrong, "\n".join(wrong)
