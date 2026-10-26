import json

import pytest

from mas_optimiser import cli


def test_help_lists_every_command(capsys):
    with pytest.raises(SystemExit) as exit_info:
        cli.main(["--help"])
    assert exit_info.value.code == 0
    out = capsys.readouterr().out
    assert "run" in out and "replay" in out


def test_run_help_shows_the_options(capsys):
    with pytest.raises(SystemExit):
        cli.main(["run", "--help"])
    out = capsys.readouterr().out
    assert "--domain {itsm,csm,hr}" in out and "--thinking-summary" in out


def test_replay_prints_a_record(tmp_path, capsys):
    record = {"runs": [{"run_number": 1, "error": "ConnectionError('server down')"}]}
    path = tmp_path / "task_xyz" / "repeat-2.json"
    path.parent.mkdir()
    path.write_text(json.dumps(record))
    cli.main(["replay", str(path)])
    out = capsys.readouterr().out
    assert "task task_xyz, repeat-2" in out and "server down" in out


def test_unknown_command_fails():
    with pytest.raises(SystemExit) as exit_info:
        cli.main(["evolve"])
    assert exit_info.value.code != 0
