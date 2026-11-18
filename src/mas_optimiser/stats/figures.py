"""The figures of a comparison and of a calibration, drawn from their data files.

``draw`` reads ``tests.csv``, ``rules.csv`` and ``differences.csv`` from a
comparison's folder and writes four figures into its ``figures/`` folder,
each as PDF and PNG, each with one thing to show:

- ``gains``: the gain on all tasks and in each domain, with its interval;
- ``differences``: how many tasks rose, fell and stayed the same;
- ``detectable``: the chance that a real gain of each size is accepted under
  each decision rule, with the minimum detectable effects and the gain observed;
- ``decisions``: the observed statistic against the bar of each rule.

``draw_calibration`` does the same for a calibration's folder
(``scores.csv``, ``pulls.csv``, ``calibration.csv``) with three figures:
``scatter``, each task's score in one run against the other;
``distributions``, what the test assumes of its statistic; and
``calibration``, the statistic of many no-change splits against that
assumption, with the pull of each bin.

Nothing is computed here that the data files do not hold, except the curves
of theory (``gate.power``, Student's t and its noncentral form). A figure carries
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
from scipy import stats  # noqa: E402

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
    that rule's minimum detectable effect, which is marked and given in the legend. The
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
        min(0.0, observed),
        1.6 * rules["minimum_detectable_effect_percentage_points"].max(),
        120,
    )
    with plt.style.context(STYLE):
        fig, ax = plt.subplots(figsize=(3.4, 3.1))
        ax.axhline(80, color=REFERENCE, linewidth=0.8)
        for (_, rule), colour in zip(
            rules.iterrows(), RAMP(np.linspace(0, 1, len(rules))), strict=True
        ):
            level = rule["false_acceptances_allowed_percent"] / 100
            effect = rule["minimum_detectable_effect_percentage_points"]
            ax.plot(
                sizes,
                [100 * gate.power(se, n, size, level) for size in sizes],
                color=colour,
                label=f"{rule['rule']}: {effect:.1f}",
            )
            ax.plot(effect, 80, "o", color=colour)
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
        ax.legend(
            loc="upper center",
            bbox_to_anchor=(0.5, -0.24),
            ncol=1,
            title="rule: minimum detectable effect [percentage points]",
        )
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


def scatter(scores: pd.DataFrame, out: Path) -> None:
    """Each task's score in one run against its score in the other.

    Tasks with the same pair of scores share a dot, whose area grows with
    their number. On the diagonal a task scored the same in both runs.

    Args:
        scores: The per-task scores, as a calibration's ``scores.csv`` holds them.
        out: The folder to write ``scatter.pdf`` and ``scatter.png`` into.
    """
    outcome = scores["outcome"].iloc[0]
    points = scores.groupby(
        ["first_run_percent", "second_run_percent"], as_index=False
    ).size()
    with plt.style.context(STYLE):
        fig, ax = plt.subplots(figsize=(3.4, 3.4))
        ax.plot([0, 100], [0, 100], color=REFERENCE, linewidth=0.8)
        ax.scatter(
            points["first_run_percent"],
            points["second_run_percent"],
            s=8 * points["size"],
            color=INK,
            alpha=0.75,
            linewidths=0,
        )
        for count in sorted({1, int(points["size"].max())}):
            ax.scatter(
                [],
                [],
                s=8 * count,
                color=INK,
                alpha=0.75,
                linewidths=0,
                label=f"{count} task" + ("s" if count > 1 else ""),
            )
        ax.set_aspect("equal")
        ax.set_xlim(-4, 104)
        ax.set_ylim(-4, 104)
        ax.set_xlabel(f"{outcome} in the first run [%]")
        ax.set_ylabel(f"{outcome} in the second run [%]")
        ax.legend(loc="upper left")
    _save(fig, out, "scatter")


def distributions(n: int, alpha: float, out: Path) -> None:
    """What the test assumes of its statistic, with and without a real gain.

    Student's t with ``n - 1`` degrees of freedom when nothing changed, and
    the noncentral t when the true gain equals the minimum detectable effect, with the bar
    between them. Theory only: the tail of the first curve beyond the bar is
    the share of false acceptances allowed, the part of the second below it
    the share of real gains missed.

    Args:
        n: The number of tasks.
        alpha: The Type-I error level of the decision.
        out: The folder to write ``distributions.pdf`` and ``distributions.png`` into.
    """
    bar = gate.bar(n, alpha)
    shift = gate.detectable(1.0, n, alpha)
    x = np.linspace(-4, shift + 4, 300)
    null = stats.t.pdf(x, n - 1)
    gain = stats.nct.pdf(x, n - 1, shift)
    with plt.style.context(STYLE):
        fig, ax = plt.subplots()
        ax.plot(x, null, color=THEORY, label="nothing changed")
        # Both curves are theory, so both wear its colour; the dashes tell
        # them apart.
        ax.plot(
            x,
            gain,
            color=THEORY,
            linestyle="--",
            label="true gain equal to the minimum detectable effect",
        )
        ax.fill_between(x, null, where=x >= bar, color=THEORY, alpha=0.35, linewidth=0)
        ax.fill_between(x, gain, where=x <= bar, color=THEORY, alpha=0.15, linewidth=0)
        ax.axvline(bar, color=REFERENCE, linewidth=0.8)
        ax.text(
            bar,
            1.01,
            f"bar {bar:.2f}",
            transform=ax.get_xaxis_transform(),
            ha="center",
            va="bottom",
            color=INK,
            fontsize=7,
        )
        # The two shaded shares, named where they lie.
        ax.text(
            bar + 0.45,
            stats.t.pdf(bar + 0.45, n - 1) + 0.015,
            f"{100 * alpha:g}%",
            color=INK,
            fontsize=7,
        )
        ax.text(
            bar - 0.75,
            0.4 * stats.nct.pdf(bar - 0.75, n - 1, shift),
            f"{100 * stats.nct.cdf(bar, n - 1, shift):.0f}%",
            color=INK,
            fontsize=7,
            ha="center",
        )
        ax.set_ylim(0, None)
        ax.set_xlabel("T = gain / standard error")
        ax.set_ylabel("probability density of T")
        ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.24), ncol=1)
    _save(fig, out, "distributions")


def calibration(pulls: pd.DataFrame, table: pd.DataFrame, n: int, out: Path) -> None:
    """The statistic of many no-change splits against the curve the test assumes.

    Top, on a logarithmic scale: the number of splits in each bin of T as
    points with their counting errors, and the number Student's t with
    ``n - 1`` degrees of freedom expects as a line; the bars of the decision
    rules are marked, and the T of the two runs as they were run. Bottom: the
    pull of each bin, its count minus the expected one in units of the
    expected count's error, for the bins expecting at least five splits.

    Args:
        pulls: The statistic of every split, as ``pulls.csv`` holds it.
        table: The calibration, as ``calibration.csv`` holds it.
        n: The number of tasks.
        out: The folder to write ``calibration.pdf`` and ``calibration.png`` into.
    """
    t = pulls["t"].to_numpy()
    edges = np.arange(-4.5, 4.75, 0.25)
    centres = (edges[:-1] + edges[1:]) / 2
    counts = np.histogram(t, bins=edges)[0]
    expected = len(t) * np.diff(stats.t.cdf(edges, n - 1))
    seen, enough = counts > 0, expected >= 5
    rules = table[table["bar"].notna()]
    as_run = table.loc[
        table["quantity"] == "T of the two runs as they were run", "measured"
    ].iloc[0]
    with plt.style.context(STYLE):
        fig, (top, bottom) = plt.subplots(
            2, 1, sharex=True, figsize=(3.4, 4.4), height_ratios=(3, 1)
        )
        top.plot(centres, expected, color=THEORY, label="Student's t (theory)")
        top.errorbar(
            centres[seen],
            counts[seen],
            yerr=np.sqrt(counts[seen]),
            fmt="o",
            markersize=2.5,
            color=INK,
            elinewidth=0.8,
            label="random splits (data)",
        )
        for (_, rule), colour in zip(
            rules.iterrows(), RAMP(np.linspace(0, 1, len(rules))), strict=True
        ):
            name = (
                rule["quantity"]
                .removeprefix("splits accepted at ")
                .removesuffix(" (%)")
            )
            top.axvline(
                rule["bar"],
                ymax=0.35,
                color=colour,
                label=f"{name}: bar {rule['bar']:.2f}",
            )
        if not np.isnan(as_run):
            top.axvline(
                as_run,
                color=OBSERVED,
                linewidth=1.2,
                label=f"the two runs as run: T {as_run:+.2f}",
            )
        top.set_yscale("log")
        top.set_ylabel("splits per bin")
        bottom.axhline(0, color=REFERENCE, linewidth=0.8)
        bottom.errorbar(
            centres[enough],
            (counts[enough] - expected[enough]) / np.sqrt(expected[enough]),
            yerr=1,
            fmt="o",
            markersize=2.5,
            color=INK,
            elinewidth=0.8,
        )
        bottom.set_ylim(-4, 4)
        bottom.set_ylabel("pull")
        bottom.set_xlabel("T of a split in which nothing changed")
        fig.legend(loc="outside lower center", ncol=1)
    _save(fig, out, "calibration")


def draw_calibration(folder: Path) -> list[str]:
    """Draw a calibration's figures from the data files in its folder.

    Args:
        folder: The calibration's folder, holding ``scores.csv``,
            ``pulls.csv`` and ``calibration.csv``; the figures go into its
            ``figures/``.

    Returns:
        The names of the figures drawn.
    """
    scores = pd.read_csv(folder / "scores.csv")
    table = pd.read_csv(folder / "calibration.csv")
    # The first rule is one decision on its own, at the level the rest share.
    alpha = table.loc[table["bar"].notna(), "expected"].iloc[0] / 100
    scatter(scores, folder / "figures")
    distributions(len(scores), alpha, folder / "figures")
    calibration(
        pd.read_csv(folder / "pulls.csv"), table, len(scores), folder / "figures"
    )
    return ["calibration", "scatter", "distributions"]
