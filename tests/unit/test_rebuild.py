"""Unit tests of ``rebuild``: an attempt's final database written from its saved changes."""

import json
import sqlite3

from mas_optimiser.benchmarks.enterpriseops import rebuild, runner, states


def test_the_rebuilt_database_holds_the_seed_with_the_attempt_changes(
    tmp_path, monkeypatch
):
    seed = sqlite3.connect(":memory:")
    seed.execute("CREATE TABLE incident (incident_id PRIMARY KEY, status)")
    seed.execute("INSERT INTO incident VALUES ('INC_1', 'new')")
    seed.commit()
    monkeypatch.setattr(
        runner,
        "load_tasks",
        lambda domain, mode: {
            "task_x": {"gym_servers_config": [{"seed_database_file": "seed.sql"}]}
        },
    )
    monkeypatch.setattr(
        states,
        "seed_copy",
        lambda path, domain: (seed, {"keys": {"incident": ["incident_id"]}}),
    )
    evaluation = tmp_path / "e"
    folder = evaluation / "tasks" / "task_x"
    folder.mkdir(parents=True)
    (evaluation / "evaluation.json").write_text(
        json.dumps({"domain": "itsm", "mode": "oracle"})
    )
    changes = {
        "incident": {
            "inserted": [{"incident_id": "INC_2", "status": "new"}],
            "updated": [
                {
                    "key": {"incident_id": "INC_1"},
                    "changes": {"status": ["new", "closed"]},
                }
            ],
            "deleted": [],
        }
    }
    (folder / "attempt-1.changes.json").write_text(json.dumps({"changes": changes}))
    path = rebuild.rebuild_attempt(evaluation, "task_x", 1)
    assert path == folder / "attempt-1.sqlite"
    rows = sqlite3.connect(path).execute("SELECT * FROM incident ORDER BY 1").fetchall()
    assert rows == [("INC_1", "closed"), ("INC_2", "new")]
