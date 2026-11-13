"""Unit tests of ``listing``: an experiment's runs as a table."""

import argparse
import csv

import pytest

from mas_optimiser.benchmarks.enterpriseops import listing

FIELDS = [
    "evaluation",
    "run",
    "started",
    "domain",
    "mode",
    "model",
    "label",
    "successes",
    "total_attempts",
    "errors",
    "checks_passed",
    "checks_total",
    "cost_usd",
]


def row(evaluation, run, started, domain, successes, total, checks, checks_total, cost):
    return dict(
        zip(
            FIELDS,
            [
                evaluation,
                run,
                started,
                domain,
                "oracle",
                "claude-haiku-5-5",
                "",
                successes,
                total,
                "0",
                checks,
                checks_total,
                cost,
            ],
            strict=True,
        )
    )


ROWS = [
    row(
        "2026-11-03-0803/csm",
        "2026-11-03-0803",
        "2026-11-03T08:26:42",
        "csm",
        "81",
        "309",
        "700",
        "1200",
        "3.1",
    ),
    row(
        "2026-11-03-0803/itsm",
        "2026-11-03-0803",
        "2026-11-03T08:03:28",
        "itsm",
        "58",
        "306",
        "530",
        "1131",
        "2.58",
    ),
    row(
        "20261027-052820-itsm",
        "",
        "2026-10-27T05:28:20",
        "itsm",
        "59",
        "306",
        "541",
        "1131",
        "2.58",
    ),
]


def write_index(folder):
    folder.mkdir()
    with open(folder / "index.csv", "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(ROWS)
    return folder


def test_table_groups_rows_by_run_with_a_summed_row_first(tmp_path):
    lines = listing.table(write_index(tmp_path / "exp")).splitlines()
    assert lines[0].startswith("| run ")
    # The older row has no run name and stands alone under its day.
    assert lines[2].startswith("| 2026-10-27      | itsm   |")
    assert "59 of 306" in lines[2]
    # The named run: the summed row, then its domains in start order.
    assert lines[3].startswith("| 2026-11-03-0803 | all    |")
    assert (
        "139 of 615" in lines[3] and "1230 of 2331" in lines[3] and "5.68" in lines[3]
    )
    assert lines[4].startswith("| 2026-11-03-0803 | itsm   |")
    assert lines[5].startswith("| 2026-11-03-0803 | csm    |")
    assert len({len(line) for line in lines}) == 1


def test_table_keeps_one_domain_when_asked(tmp_path):
    text = listing.table(write_index(tmp_path / "exp"), domain="csm")
    assert text.count("\n") == 2 and "| csm" in text and "| all" not in text


def test_run_refuses_a_folder_without_an_index(tmp_path):
    with pytest.raises(SystemExit, match="no index.csv"):
        listing.run(argparse.Namespace(experiment=str(tmp_path), domain=None))
