"""Unit tests of ``listing``: an experiment's evaluations as a table."""

import argparse
import csv

import pytest

from mas_optimiser.benchmarks.enterpriseops import listing

ROWS = [
    {
        "evaluation": "csm-2026-11-03-0826",
        "started": "2026-11-03T08:26:42",
        "domain": "csm",
        "mode": "oracle",
        "model": "claude-haiku-5-5",
        "label": "",
        "successes": "81",
        "total_attempts": "309",
        "checks_passed": "700",
        "checks_total": "1200",
        "cost_usd": "3.1",
    },
    {
        "evaluation": "itsm-2026-11-03-0803",
        "started": "2026-11-03T08:03:28",
        "domain": "itsm",
        "mode": "oracle",
        "model": "claude-haiku-5-5",
        "label": "",
        "successes": "58",
        "total_attempts": "306",
        "checks_passed": "530",
        "checks_total": "1131",
        "cost_usd": "2.58",
    },
]


def write_index(folder):
    folder.mkdir()
    with open(folder / "index.csv", "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(ROWS[0]))
        writer.writeheader()
        writer.writerows(ROWS)
    return folder


def test_table_lists_evaluations_in_start_order(tmp_path):
    folder = write_index(tmp_path / "exp")
    lines = listing.table(folder).splitlines()
    assert lines[0].startswith("| evaluation | started | domain |")
    assert lines[2].startswith(
        "| itsm-2026-11-03-0803 | 2026-11-03 08:03 | itsm | oracle |"
    )
    assert "58 of 306 | 530 of 1131 | 2.58 |" in lines[2]
    assert lines[3].startswith("| csm-2026-11-03-0826 |")
    assert listing.table(folder, domain="csm").count("\n") == 2


def test_run_refuses_a_folder_without_an_index(tmp_path):
    with pytest.raises(SystemExit, match="no index.csv"):
        listing.run(argparse.Namespace(experiment=str(tmp_path), domain=None))
