import argparse

import httpx
import pytest

from mas_optimiser.benchmarks.enterpriseops import seeds

SCHEMA = (
    "CREATE TABLE incident (\n\tincident_id VARCHAR(32) NOT NULL, \n"
    "\tpriority VARCHAR(8), \n\tcost INTEGER, \n\tPRIMARY KEY (incident_id)\n);\n"
)
# Real seeds quote integers, carry "--" inside values and end some lines with
# a comment of their own.
SEED = """-- Database Seed Data for: test
-- NOTE: comments are skipped

INSERT INTO incident (incident_id, priority, cost) VALUES
('INC_001', 'moderate', '1370'),
('INC_002', 'high -- see the guide', NULL),  -- Sarah's note
('INC_003', 'O''Brien', 5);
"""


def write(tmp_path, text, name="seed.sql"):
    path = tmp_path / name
    path.write_text(text)
    return path


def test_seed_statements_drop_comment_lines_and_keep_dashes_inside_values(tmp_path):
    statements = seeds.seed_statements(write(tmp_path, SEED))
    lines = statements.splitlines()
    assert not any(line.lstrip().startswith("--") for line in lines)
    assert "'high -- see the guide'" in statements
    assert len(lines) == 5


def test_seed_statements_refuse_a_value_that_runs_over_a_line_break(tmp_path):
    seed = write(
        tmp_path, "INSERT INTO incident VALUES ('INC_9', 'Main site\nfloor 2', 1);"
    )
    with pytest.raises(ValueError, match="line 1 ends inside a quoted value"):
        seeds.seed_statements(seed)


def test_build_seed_database_applies_the_schema_types_to_the_rows(tmp_path):
    db = seeds.build_seed_database(write(tmp_path, SEED), SCHEMA)
    assert db.execute("SELECT COUNT(*) FROM incident").fetchone() == (3,)
    # The quoted '1370' is stored as a number only because the column is
    # declared INTEGER, so the numeric comparison a check makes finds it.
    assert db.execute("SELECT COUNT(*) FROM incident WHERE cost = 1370").fetchone() == (
        1,
    )
    assert db.execute(
        "SELECT priority FROM incident WHERE incident_id = 'INC_002'"
    ).fetchone() == ("high -- see the guide",)
    types = [(row[1], row[2]) for row in db.execute("PRAGMA table_info(incident)")]
    assert types == [
        ("incident_id", "VARCHAR(32)"),
        ("priority", "VARCHAR(8)"),
        ("cost", "INTEGER"),
    ]


class Response:
    def __init__(self, status_code, body=None, text=""):
        self.status_code, self.body, self.text = status_code, body, text

    def json(self):
        if self.body is None:
            raise ValueError("not JSON")
        return self.body


def test_run_sql_raises_on_a_failed_query(monkeypatch):
    failed = Response(400, {"detail": "no such column: cost"}, "no such column: cost")
    monkeypatch.setattr(seeds.httpx, "post", lambda *a, **k: failed)
    with pytest.raises(RuntimeError, match="no such column"):
        seeds.run_sql("http://localhost:1", "db_x", "SELECT 1")


def test_run_sql_reports_an_error_status_without_a_json_body(monkeypatch):
    monkeypatch.setattr(
        seeds.httpx, "post", lambda *a, **k: Response(502, None, "Bad Gateway")
    )
    with pytest.raises(RuntimeError, match="HTTP 502 Bad Gateway"):
        seeds.run_sql("http://localhost:1", "db_x", "SELECT 1")


@pytest.fixture
def server(monkeypatch):
    """A fake domain server recording which databases were deleted."""
    state = {"seed": Response(200, {"success": True}), "delete": True, "deleted": []}

    def post(url, json, timeout):
        state["seeded"] = json["database_id"]
        if isinstance(state["seed"], Exception):
            raise state["seed"]
        return state["seed"]

    def delete(server_url, database_id):
        state["deleted"].append(database_id)
        return state["delete"]

    monkeypatch.setattr(seeds.httpx, "post", post)
    monkeypatch.setattr(seeds, "delete_database", delete)
    return state


def test_server_database_deletes_the_database_after_use(server, tmp_path):
    with seeds.server_database("http://s", write(tmp_path, SEED)) as database_id:
        assert database_id == server["seeded"]
    assert server["deleted"] == [server["seeded"]]


@pytest.mark.parametrize(
    "failure", [Response(500, None, "no such table"), httpx.ConnectError("refused")]
)
def test_server_database_deletes_a_database_the_server_failed_to_seed(
    server, tmp_path, failure
):
    server["seed"] = failure
    with (
        pytest.raises(RuntimeError),
        seeds.server_database("http://s", write(tmp_path, SEED)),
    ):
        pass
    assert server["deleted"] == [server["seeded"]]


def test_server_database_reports_a_database_left_on_the_server(server, tmp_path):
    server["delete"] = False
    with (
        pytest.raises(RuntimeError, match="was left on http://s"),
        seeds.server_database("http://s", write(tmp_path, SEED)),
    ):
        pass


def test_a_failed_delete_does_not_hide_the_error_that_came_first(server, tmp_path):
    server["delete"] = False
    with (
        pytest.raises(KeyError, match="from the block"),
        seeds.server_database("http://s", write(tmp_path, SEED)),
    ):
        raise KeyError("from the block")


@pytest.fixture
def schema_command(monkeypatch, tmp_path):
    """The schema command over two fake seed files, with the server calls replaced."""
    first, second = write(tmp_path, SEED, "a.sql"), write(tmp_path, SEED, "b.sql")
    monkeypatch.setattr(seeds, "SCHEMAS_DIR", tmp_path / "schemas")
    monkeypatch.setattr(seeds, "domain_seeds", lambda d: {"http://s": {first, second}})
    monkeypatch.setattr(seeds.runner, "check_servers", lambda urls: None)
    return argparse.Namespace(domain=["itsm"])


def test_schema_command_writes_the_schema_all_seed_files_share(
    schema_command, monkeypatch
):
    monkeypatch.setattr(seeds, "fetch_schema", lambda url, path: SCHEMA)
    seeds.run(schema_command)
    assert (seeds.SCHEMAS_DIR / "itsm.sql").read_text() == SCHEMA


def test_schema_command_stops_when_two_seed_files_disagree(schema_command, monkeypatch):
    other = SCHEMA.replace("cost INTEGER", "cost REAL")
    monkeypatch.setattr(
        seeds,
        "fetch_schema",
        lambda url, path: SCHEMA if path.name == "a.sql" else other,
    )
    with pytest.raises(SystemExit, match="error: itsm: b.sql and a.sql give different"):
        seeds.run(schema_command)
    assert not (seeds.SCHEMAS_DIR / "itsm.sql").exists()
