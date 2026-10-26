import pytest

from mas_optimiser.benchmarks.enterpriseops import runner


def test_config_names_resolve_to_files_in_configs():
    path = runner.config_path("experiments", "smoke")
    assert path == runner.CONFIGS / "experiments" / "smoke.yaml"
    assert path.exists()
    assert runner.config_path("models", "haiku-5-5").exists()


def test_config_paths_are_used_as_given(tmp_path):
    given = tmp_path / "mine.yaml"
    assert runner.config_path("experiments", str(given)) == given
    assert runner.config_path("experiments", "sub/other.yaml") == runner.Path(
        "sub/other.yaml"
    )


def test_unknown_config_name_lists_the_known_ones():
    with pytest.raises(SystemExit, match="smoke"):
        runner.config_path("experiments", "nope")


def test_experiment_file_defaults_and_overrides():
    no_overrides = dict.fromkeys(runner.EXPERIMENT_DEFAULTS)
    settings = runner.load_experiment("smoke", no_overrides)
    assert settings["model"] == "haiku-5-5"
    assert (settings["domain"], settings["mode"], settings["limit"]) == (
        "itsm",
        "oracle",
        5,
    )
    assert settings["trace"] is True and settings["thinking_summary"] is False

    overridden = runner.load_experiment(
        "smoke", no_overrides | {"limit": 1, "trace": False}
    )
    assert (overridden["limit"], overridden["trace"]) == (1, False)
    assert overridden["domain"] == "itsm"


def test_experiment_file_is_validated(tmp_path):
    no_overrides = dict.fromkeys(runner.EXPERIMENT_DEFAULTS)
    typo = tmp_path / "typo.yaml"
    typo.write_text("model: haiku-5-5\ndomain: itsm\nlimti: 3\n")
    with pytest.raises(SystemExit, match="limti"):
        runner.load_experiment(str(typo), no_overrides)

    no_domain = tmp_path / "no_domain.yaml"
    no_domain.write_text("model: haiku-5-5\n")
    with pytest.raises(SystemExit, match="domain"):
        runner.load_experiment(str(no_domain), no_overrides)
    # The command line can supply what the file leaves out.
    assert (
        runner.load_experiment(str(no_domain), no_overrides | {"domain": "hr"})[
            "domain"
        ]
        == "hr"
    )

    bad_mode = tmp_path / "bad_mode.yaml"
    bad_mode.write_text("model: haiku-5-5\ndomain: itsm\nmode: all_tools\n")
    with pytest.raises(SystemExit, match="mode"):
        runner.load_experiment(str(bad_mode), no_overrides)


def test_servers_that_do_not_answer_stop_the_run_with_the_compose_command():
    with pytest.raises(SystemExit, match="docker compose"):
        runner.check_servers({"http://127.0.0.1:9"})
