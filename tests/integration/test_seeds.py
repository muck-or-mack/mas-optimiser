"""Schema fetch against the running domain servers; skipped by default."""

import pytest

from mas_optimiser.benchmarks.enterpriseops import seeds

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("domain", ["itsm", "csm", "hr"])
def test_every_seed_file_of_a_domain_loads_locally_with_the_fetched_schema(domain):
    for server_url, seed_paths in seeds.domain_seeds(domain).items():
        schema = seeds.fetch_schema(server_url, sorted(seed_paths)[0])
        assert "CREATE TABLE" in schema
        for seed_path in seed_paths:
            db = seeds.build_seed_database(seed_path, schema)
            tables = db.execute(
                "SELECT COUNT(*) FROM sqlite_master WHERE type = 'table'"
            )
            assert tables.fetchone()[0] > 0
