"""The figures of a comparison, drawn from its data files.

``draw`` reads ``tests.csv``, ``rules.csv`` and ``differences.csv`` from a
comparison's folder and writes four figures into its ``figures/`` folder,
each as PDF and PNG, each with one thing to show:

- ``gains``: the gain on all tasks and in each domain, with its interval;
- ``differences``: how many tasks rose, fell and stayed the same;
- ``detectable``: the chance that a real gain of each size is accepted under
  each decision rule, with the noise floors and the gain observed;
- ``decisions``: the observed statistic against the bar of each rule.

Nothing is computed here that the data files do not hold, except the power
curves of ``detectable``, which are theory (``gate.power``). A figure carries
no title: the report gives each one a caption. Data is drawn as points with
error bars or as bars, theory as lines; the gain observed is always the same
accent colour, the rules one hue from light to dark as they grow stricter.
The style is ``figures.mplstyle`` in this package; drawing uses the Agg
backend and needs no display.

Sources: ``docs/how-it-works.md``, section 6, the rows for ``stats``.
"""

import textwrap
from pathlib import Path

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from matplotlib.colors import LinearSegmentedColormap  # noqa: E402

from mas_optimiser.stats import gate  # noqa: E402

STYLE = Path(__file__).with_name("figures.mplstyle")
# Ink for data, a muted grey for labels of reference lines, a lighter one for
# the lines themselves; one blue for theory and counts, one orange for what
# this comparison observed. The rules take one hue, light to dark.
INK, MUTED, REFERENCE = "#0b0b0b", "#898781", "#c3c2b7"
THEORY, OBSERVED = "#2a78d6", "#eb6834"
RAMP = LinearSegmentedColormap.from_list(
    "rules", ["#86b6ef", "#3987e5", "#1c5cab", "#0d366b"]
)


def _save(fig: plt.Figure, out: Path, name: str) -> None:
    """Write a figure as PDF and PNG.

    Args:
        fig: The figure to save.
        out: The folder to write into, created if missing.
        name: The file name without extension.
    """
    out.mkdir(parents=True, exist_ok=True)
    fig.savefig(out / f"{name}.pdf")
    fig.savefig(out / f"{name}.png")
    plt.close(fig)


def gains(tests: pd.DataFrame, outcome: str, out: Path) -> None:
    """The gain on all tasks and in each domain, each with its interval.

    A row's bar spans the gain plus and minus the bar on T times the standard
    error, so a row is accepted when its bar lies wholly to the right of the
    no-gain line. The row for all tasks, which decides, is in the accent.

    Args:
        tests: The tests, as ``tests.csv`` holds them.
        outcome: The outcome to show, as the data files name it.
        out: The folder to write ``gains.pdf`` and ``gains.png`` into.
    """
    rows = tests[tests["outcome"] == outcome].iloc[::-1]
    gain = rows["gain_percentage_points"].to_numpy()
    half = gain - rows["lower_bound_percentage_points"].to_numpy()
    with plt.style.context(STYLE):
        fig, ax = plt.subplots(figsize=(3.4, 0.45 * len(rows) + 0.75))
        ax.axvline(0, color=REFERENCE, linewidth=0.8)
        ax.text(
            0,
            1.01,
            "no gain",
            transform=ax.get_xaxis_transform(),
            ha="center",
            va="bottom",
            color=MUTED,
            fontsize=7,
        )
        for y, (name, value, width) in enumerate(
            zip(rows["set"], gain, half, strict=True)
        ):
            ax.errorbar(
                value,
                y,
                xerr=None if np.isnan(width) else width,
                fmt="o",
                color=OBSERVED if name == "all" else INK,
                capsize=2,
            )
            ax.annotate(
                f"{value:+.1f}",
                (value, y),
                xytext=(0, 5),
                textcoords="offset points",
                ha="center",
                color=INK,
                fontsize=7,
            )
        ax.set_yticks(
            range(len(rows)),
            [
                f"{'all tasks' if name == 'all' else name} ({count})"
                for name, count in zip(rows["set"], rows["tasks"], strict=True)
            ],
        )
        ax.set_ylim(-0.6, len(rows) - 0.3)
        ax.set_xlabel(f"gain in {outcome} [percentage points]")
    _save(fig, out, "gains")


def differences(per_task: pd.DataFrame, outcome: str, out: Path) -> None:
    """How many tasks rose, fell and stayed the same, in bins ten points wide.

    The tasks whose score did not change are one grey bar at zero; the others
    are counted in bins of ten percentage points either side of it. The line
    is the mean of the differences, which is the gain.

    Args:
        per_task: The per-task differences, as ``differences.csv`` holds them.
        outcome: The outcome to show, as the data files name it.
        out: The folder to write ``differences.pdf`` and ``differences.png`` into.
    """
    column = f"{outcome.replace(' ', '_')}_difference_percentage_points"
    d = per_task[column].to_numpy()
    rose, fell, same = d[d > 0], -d[d < 0], int(np.sum(d == 0))
    edges = np.arange(0, 10 * np.ceil(max(np.abs(d).max(), 10) / 10) + 10, 10)
    up, down = np.histogram(rose, bins=edges)[0], np.histogram(fell, bins=edges)[0]
    with plt.style.context(STYLE):
        fig, ax = plt.subplots()
        ax.bar(edges[:-1] + 5, up, width=8, color=THEORY)
        ax.bar(-(edges[:-1] + 5), down, width=8, color=THEORY)
        ax.bar(0, same, width=1.6, color=MUTED)
        ax.axvline(d.mean(), color=OBSERVED, linewidth=1.2)
        top = max(same, up.max(), down.max())
        ax.text(
            -2.5, top, f"{same} unchanged", va="top", ha="right", color=INK, fontsize=7
        )
        ax.text(
            d.mean() + 2.5,
            0.72 * top,
            f"mean {d.mean():+.1f}",
            va="top",
            color=INK,
            fontsize=7,
        )
        ax.text(0.02, 0.5, f"{len(fell)} fell", transform=ax.transAxes, fontsize=7)
        ax.text(
            0.98,
            0.5,
            f"{len(rose)} rose",
            transform=ax.transAxes,
            ha="right",
            fontsize=7,
        )
        ax.set_xlabel(
            f"difference in {outcome} [percentage points]\ncandidate minus current"
        )
        ax.set_ylabel("tasks")
    _save(fig, out, "differences")


def detectable(tests: pd.DataFrame, rules: pd.DataFrame, out: Path) -> None:
    """The chance that a real gain of each size is accepted, under each rule.

    One curve per decision rule, from ``gate.power`` at the standard error of
    the comparison on all tasks: theory, not data. Each curve crosses 80% at
    that rule's noise floor, which is marked and given in the legend. The
    vertical line is the gain this comparison observed.

    Args:
        tests: The tests, as ``tests.csv`` holds them.
        rules: The decision rules, as ``rules.csv`` holds them.
        out: The folder to write ``detectable.pdf`` and ``detectable.png`` into.
    """
    outcome = rules["outcome"].iloc[0]
    row = tests[(tests["outcome"] == outcome) & (tests["set"] == "all")].iloc[0]
    se, n = row["standard_error_percentage_points"], int(row["tasks"])
    observed = row["gain_percentage_points"]
    sizes = np.linspace(
        min(0.0, observed), 1.6 * rules["noise_floor_percentage_points"].max(), 120
    )
    with plt.style.context(STYLE):
        fig, ax = plt.subplots(figsize=(3.4, 3.1))
        ax.axhline(80, color=REFERENCE, linewidth=0.8)
        for (_, rule), colour in zip(
            rules.iterrows(), RAMP(np.linspace(0, 1, len(rules))), strict=True
        ):
            level = rule["false_acceptances_allowed_percent"] / 100
            floor = rule["noise_floor_percentage_points"]
            ax.plot(
                sizes,
                [100 * gate.power(se, n, size, level) for size in sizes],
                color=colour,
                label=f"{rule['rule']}: floor {floor:.1f}",
            )
            ax.plot(floor, 80, "o", color=colour)
        ax.axvline(observed, color=OBSERVED, linewidth=1.2)
        ax.text(
            observed,
            1.01,
            f"observed {observed:+.1f}",
            transform=ax.get_xaxis_transform(),
            ha="center",
            va="bottom",
            color=INK,
            fontsize=7,
        )
        ax.set_ylim(0, 100)
        ax.set_xlabel(f"true gain in {outcome} [percentage points]")
        ax.set_ylabel("chance of acceptance [%]")
        ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.24), ncol=1)
    _save(fig, out, "detectable")


def decisions(tests: pd.DataFrame, rules: pd.DataFrame, out: Path) -> None:
    """The observed statistic against the bar of each decision rule.

    One row per rule with a mark at its bar on T; the vertical line is the T
    observed on all tasks. A rule accepts when its bar lies to the left of
    that line; each row's verdict is written beside it.

    Args:
        tests: The tests, as ``tests.csv`` holds them.
        rules: The decision rules, as ``rules.csv`` holds them.
        out: The folder to write ``decisions.pdf`` and ``decisions.png`` into.
    """
    outcome = rules["outcome"].iloc[0]
    t = tests[(tests["outcome"] == outcome) & (tests["set"] == "all")].iloc[0]["t"]
    rows = rules.iloc[::-1]
    with plt.style.context(STYLE):
        fig, ax = plt.subplots(figsize=(3.4, 0.42 * len(rows) + 0.8))
        ax.axvline(t, color=OBSERVED, linewidth=1.2)
        ax.text(
            t,
            1.01,
            f"T {t:+.2f}",
            transform=ax.get_xaxis_transform(),
            ha="center",
            va="bottom",
            color=INK,
            fontsize=7,
        )
        ax.scatter(
            rows["bar"],
            range(len(rows)),
            marker="|",
            s=140,
            linewidths=2,
            color=RAMP(np.linspace(1, 0, len(rows))),
        )
        for y, (_, rule) in enumerate(rows.iterrows()):
            ax.annotate(
                f"{rule['bar']:.2f}",
                (rule["bar"], y),
                xytext=(4, 4),
                textcoords="offset points",
                color=INK,
                fontsize=7,
            )
            ax.text(
                1.02,
                y,
                rule["verdict"],
                transform=ax.get_yaxis_transform(),
                va="center",
                color=INK,
                fontsize=7,
            )
        ax.set_yticks(
            range(len(rows)), [textwrap.fill(name, 24) for name in rows["rule"]]
        )
        ax.set_ylim(-0.6, len(rows) - 0.4)
        ax.set_xlim(min(0.0, t) - 0.4, max(rows["bar"].max(), t) + 0.5)
        ax.set_xlabel("T = gain / standard error")
    _save(fig, out, "decisions")


def draw(folder: Path) -> list[str]:
    """Draw a comparison's figures from the data files in its folder.

    Args:
        folder: The comparison's folder, holding ``tests.csv``, ``rules.csv``
            and ``differences.csv``; the figures go into its ``figures/``.

    Returns:
        The names of the figures drawn: all four, or only ``gains`` and
        ``differences`` when the test could not be computed.
    """
    tests = pd.read_csv(folder / "tests.csv")
    rules = pd.read_csv(folder / "rules.csv")
    outcome = rules["outcome"].iloc[0]
    gains(tests, outcome, folder / "figures")
    differences(pd.read_csv(folder / "differences.csv"), outcome, folder / "figures")
    if rules["bar"].isna().any():
        return ["gains", "differences"]
    detectable(tests, rules, folder / "figures")
    decisions(tests, rules, folder / "figures")
    return ["gains", "differences", "detectable", "decisions"]
