"""Markdown shared by the commands that write reports.

The summaries, the comparison report and the listings are read as files as
often as they are rendered, so their tables are written by ``tabulate`` in
its GitHub format, every column padded to its widest cell.
"""

from tabulate import tabulate


def table(header: list[str], rows: list[list[str]]) -> str:
    r"""A markdown table with its columns aligned in the source.

    The cells are written as given. Number parsing is off, because it would
    right-align numeric columns and drop the sign of a gain such as
    ``+3.69``; and ``tabulate`` does not escape a ``|`` inside a cell, so a
    caller whose cells can hold one writes it as ``\|`` first.

    Args:
        header: The column names.
        rows: The rows, each cell already formatted as text.

    Returns:
        The table, without a trailing newline.
    """
    return tabulate(rows, headers=header, tablefmt="github", disable_numparse=True)


def amount(value: float) -> str:
    """A cost or a duration for a table cell: two decimals from 1 upwards, two significant figures below.

    At two decimals a run that cost ``0.004026`` dollars reads as ``0.00``,
    as if it were free.

    Args:
        value: The amount, not negative.

    Returns:
        ``12.26`` for ``12.26``, ``0.0040`` for ``0.004026``.
    """
    return f"{value:.2f}" if value >= 1 else f"{value:#.2g}"
