import pytest

from mas_optimiser import cli


def test_help_lists_every_command(capsys):
    with pytest.raises(SystemExit) as exit_info:
        cli.main(["--help"])
    assert exit_info.value.code == 0
    out = capsys.readouterr().out
    assert all(name in out for name in ("run", "rescore", "split", "schema"))


def test_run_help_shows_the_options(capsys):
    with pytest.raises(SystemExit):
        cli.main(["run", "--help"])
    out = capsys.readouterr().out
    assert "--domain {itsm,csm,hr}" in out and "--thinking-summary" in out


def test_unknown_command_fails():
    with pytest.raises(SystemExit) as exit_info:
        cli.main(["evolve"])
    assert exit_info.value.code != 0
