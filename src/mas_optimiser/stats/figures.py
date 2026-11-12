"""Figures for a comparison, each saved with the numbers it plots.

Every function writes a PDF and a PNG of the figure and a CSV of the plotted
values beside them, so that a figure can be redrawn or checked without
recomputing the comparison. The style is ``figures.mplstyle`` in this
package; drawing uses the Agg backend and needs no display.

Sources: ``docs/how-it-works.md``, section 6, the rows for ``stats``.
"""

import csv
from pathlib import Path

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
from scipy import stats  # noqa: E402

from mas_optimiser.stats.gate import MeanTest, detectable  # noqa: E402
from mas_optimiser.stats.outcomes import Comparison  # noqa: E402

STYLE = Path(__file__).with_name("figures.mplstyle")


def _save(fig: plt.Figure, out: Path, name: str, header: list[str], rows: list) -> Path:
    """Write a figure as PDF and PNG and its numbers as CSV.

    Args:
        fig: The figure to save.
        out: The folder to write into, created if missing.
        name: The file name without extension.
        header: The CSV's column names.
        rows: The CSV's rows.

    Returns:
        The path of the PDF.
    """
    out.mkdir(parents=True, exist_ok=True)
    fig.savefig(out / f"{name}.pdf")
    fig.savefig(out / f"{name}.png")
    plt.close(fig)
    with open(out / f"{name}.csv", "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(header)
        writer.writerows(rows)
    return out / f"{name}.pdf"


def differences(cmp: Comparison, out: Path) -> Path:
    """The distribution of the per-task differences.

    A bar per distinct value when there are few (task success with a few
    attempts gives a handful), a histogram otherwise.

    Args:
        cmp: The comparison.
        out: The folder to write into.

    Returns:
        The path of the PDF ``differences.pdf``.
    """
    d = cmp.d
    values, counts = np.unique(np.round(d, 6), return_counts=True)
    with plt.style.context(STYLE):
        fig, ax = plt.subplots()
        if len(values) <= 15:
            width = 0.8 * (np.diff(values).min() if len(values) > 1 else 0.1)
            ax.bar(values, counts, width=width)
            rows = [[float(v), int(c)] for v, c in zip(values, counts, strict=True)]
            header = ["difference", "tasks"]
        else:
            counts, edges = np.histogram(d, bins=20)
            ax.bar(edges[:-1], counts, width=np.diff(edges), align="edge")
            rows = [
                [float(lo), float(hi), int(c)]
                for lo, hi, c in zip(edges[:-1], edges[1:], counts, strict=True)
            ]
            header = ["bin_low", "bin_high", "tasks"]
        ax.axvline(0, color="black", linewidth=0.8)
        ax.axvline(d.mean(), color="C3", linewidth=1.2, label=f"mean {d.mean():+.3f}")
        ax.set_xlabel(f"per-task difference, candidate minus incumbent ({cmp.outcome})")
        ax.set_ylabel("tasks")
        ax.legend()
    return _save(fig, out, "differences", header, rows)


def threshold(test: MeanTest, out: Path, gain: float | None = None) -> Path:
    """The null and alternative distributions of the statistic, with the threshold.

    The null distribution is the standard normal; the alternative is the same
    bell centred on ``gain / se``, by default the minimum detectable gain at
    power 0.8. The shaded tail beyond ``x0`` under the null is the Type-I
    error, the shaded part below ``x0`` under the alternative the Type-II
    error; the observed statistic is marked.

    Args:
        test: The test, for its standard error, threshold and statistic.
        out: The folder to write into.
        gain: The true gain the alternative is drawn at, in the score's
            units; the minimum detectable gain when ``None``.

    Returns:
        The path of the PDF ``threshold.pdf``.
    """
    if gain is None:
        gain = detectable(test.se, test.alpha)
    centre = gain / test.se
    x = np.linspace(-4, centre + 4, 400)
    null = stats.norm.pdf(x)
    alternative = stats.norm.pdf(x, loc=centre)
    with plt.style.context(STYLE):
        fig, ax = plt.subplots()
        ax.plot(x, null, label="no real gain")
        ax.plot(x, alternative, label=f"true gain {gain * 100:.1f} points")
        ax.fill_between(
            x, null, where=x >= test.x0, alpha=0.3, label=f"alpha {test.alpha:g}"
        )
        ax.fill_between(
            x, alternative, where=x <= test.x0, alpha=0.3, label="beta (missed gain)"
        )
        ax.axvline(
            test.x0, color="black", linewidth=0.8, label=f"threshold x0 {test.x0:.2f}"
        )
        ax.axvline(test.t, color="C3", linewidth=1.2, label=f"observed t {test.t:+.2f}")
        ax.set_xlabel("statistic t, the gain in standard errors")
        ax.set_ylabel("density")
        ax.legend()
    rows = [
        [float(a), float(b), float(c)]
        for a, b, c in zip(x, null, alternative, strict=True)
    ]
    return _save(
        fig, out, "threshold", ["t", "null_density", "alternative_density"], rows
    )


def power_curve(se: float, alpha: float, out: Path) -> Path:
    """The power of the test against the true gain, with the detectable gain marked.

    Args:
        se: The standard error of the mean difference.
        alpha: The Type-I error level of the decision.
        out: The folder to write into.

    Returns:
        The path of the PDF ``power.pdf``.
    """
    mde = detectable(se, alpha)
    gains = np.linspace(0, 2.5 * mde, 200)
    x0 = stats.norm.ppf(1 - alpha)
    powers = stats.norm.sf(x0 - gains / se)
    with plt.style.context(STYLE):
        fig, ax = plt.subplots()
        ax.plot(gains * 100, powers)
        ax.axhline(0.8, color="black", linewidth=0.8, linestyle="--")
        ax.axvline(
            mde * 100,
            color="C3",
            linewidth=1.2,
            label=f"detectable gain {mde * 100:.1f} points",
        )
        ax.set_xlabel("true gain (points)")
        ax.set_ylabel("power, the chance of detecting it")
        ax.set_ylim(0, 1)
        ax.legend()
    rows = [[float(g * 100), float(p)] for g, p in zip(gains, powers, strict=True)]
    return _save(fig, out, "power", ["gain_points", "power"], rows)


def domains(cmp: Comparison, out: Path) -> Path:
    """The mean score of each side, per domain and pooled.

    Args:
        cmp: The comparison.
        out: The folder to write into.

    Returns:
        The path of the PDF ``domains.pdf``.
    """
    names = sorted(set(cmp.domains)) + ["pooled"]
    parts = [cmp.domain(name) for name in names[:-1]] + [cmp]
    a = [part.a.mean() for part in parts]
    b = [part.b.mean() for part in parts]
    positions = np.arange(len(names))
    with plt.style.context(STYLE):
        fig, ax = plt.subplots()
        ax.bar(positions - 0.2, a, width=0.4, label="incumbent")
        ax.bar(positions + 0.2, b, width=0.4, label="candidate")
        ax.set_xticks(positions, names)
        ax.set_ylabel(f"mean score ({cmp.outcome})")
        ax.legend()
    rows = [
        [
            name,
            len(part.tasks),
            float(part.a.mean()),
            float(part.b.mean()),
            float(part.d.mean()),
        ]
        for name, part in zip(names, parts, strict=True)
    ]
    return _save(
        fig,
        out,
        "domains",
        ["set", "tasks", "incumbent", "candidate", "difference"],
        rows,
    )
