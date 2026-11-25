"""The planner page's starting values against the package's own defaults."""

import re
from pathlib import Path

import yaml

from mas_optimiser.benchmarks.enterpriseops import runner, splits

PAGE = Path(__file__).parents[2] / "docs" / "_static" / "planner.html"


def test_the_planner_starts_from_the_packages_defaults():
    # Each row of the page's DESIGN list: key, label, least, most, step, default.
    rows = re.findall(
        r'\["(\w+)", "[^"]+", [\d.]+, [\d.]+, [\d.]+, ([\d.]+), "\w*"\]',
        PAGE.read_text(),
    )
    start = {key: float(value) for key, value in rows}
    training, validation, _ = splits.DEFAULT_PROPORTIONS
    defaults = runner.EXPERIMENT_DEFAULTS
    assert start == start | {
        "shareTraining": 100 * training,
        "shareValidation": 100 * validation,
        "attempts": defaults["attempts"],
        "rounds": defaults["rounds"],
        "candidates": defaults["candidates"],
        "filter": defaults["filter_share"],
        "pool": defaults["pool_share"],
        "alpha": defaults["alpha"],
    }
    # Every control of the page was read, so a row written another way is caught.
    assert len(start) == 13


def test_the_planner_shows_every_setting_as_a_control_or_in_its_tables():
    text = PAGE.read_text()

    def listed(name):
        start = text.index(f"const {name} = [")
        return text[start : text.index("];", start)]

    # The controls that are settings, and the settings with no control.
    controls = dict(re.findall(r'\["(\w+)", "(\w+)"\]', listed("FILE")))
    rows = re.findall(r'\["[^"]+", "(\w+)", "[^"]+", "([^"]+)"\]', listed("OTHER"))
    sliders = re.findall(r'\["(\w+)", "[^"]+", [\d.]+,', listed("DESIGN"))
    assert set(controls) <= set(sliders)
    defaults = runner.EXPERIMENT_DEFAULTS
    names = [name for name, _ in rows]
    assert sorted([*controls.values(), *names]) == sorted(defaults)
    for name, shown in rows:
        if defaults[name] is None:
            assert shown == "not set"
        else:
            assert yaml.safe_load(shown) == defaults[name]


def test_the_planner_needs_nothing_from_outside_its_file():
    text = PAGE.read_text()
    assert not re.search(r'(src|href)="https?://', text)
    assert "<script src" not in text and "@import" not in text
