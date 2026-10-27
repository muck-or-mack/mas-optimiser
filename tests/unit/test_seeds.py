import pytest

from mas_optimiser.benchmarks.enterpriseops import seeds

SCHEMA = (
    "CREATE TABLE incident (\n\tincident_id VARCHAR(32) NOT NULL, \n"
    "\tpriority VARCHAR(8), \n\tcost INTEGER, \n\tPRIMARY KEY (incident_id)\n);\n"
)
SEED = """-- Database Seed Data for: test
-- NOTE: comments are skipped

INSERT INTO incident (incident_id, priority, cost) VALUES
('INC_001', 'moderate', 1370),
('INC_002', 'high', NULL);
"""


def test_seed_statements_drop_comment_lines_only(tmp_path):
    seed = tmp_path / "seed.sql"
    seed.write_text(SEED)
    statements = seeds.seed_statements(seed)
    assert "--" not in statements
    assert "INSERT INTO incident" in statements and "('INC_002'" in statements


def test_build_seed_database_applies_schema_then_rows(tmp_path):
    seed = tmp_path / "seed.sql"
    seed.write_text(SEED)
    db = seeds.build_seed_database(seed, SCHEMA)
    assert db.execute("SELECT COUNT(*) FROM incident").fetchone() == (2,)
    # The server's column types decide how literals compare: an INTEGER
    # column matches the unquoted seed value, a VARCHAR column the quoted one.
    assert db.execute("SELECT COUNT(*) FROM incident WHERE cost = 1370").fetchone() == (
        1,
    )
    assert db.execute(
        "SELECT priority FROM incident WHERE incident_id = 'INC_002'"
    ).fetchone() == ("high",)
    columns = [row[1] for row in db.execute("PRAGMA table_info(incident)")]
    assert columns == ["incident_id", "priority", "cost"]


def test_run_sql_raises_on_a_failed_query(monkeypatch):
    class Response:
        status_code = 400

        @staticmethod
        def json():
            return {"detail": "no such column: cost"}

    monkeypatch.setattr(seeds.httpx, "post", lambda *a, **k: Response())
    with pytest.raises(RuntimeError, match="no such column"):
        seeds.run_sql("http://localhost:1", "db_x", "SELECT 1")
