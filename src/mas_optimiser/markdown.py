"""Markdown shared by the commands that write reports.

The summaries and the comparison report are read as files as often as they
are rendered, so their tables are padded: every column as wide as its widest
cell, which makes the source read as a table.
"""

import re
from itertools import zip_longest

_SEPARATOR = re.compile(r"^\|(\s*:?-+:?\s*\|)+\s*$")
_PIPE = re.compile(r"(?<!\\)\|")


def align_tables(text: str) -> str:
    r"""Pad every table in a markdown text so that its columns line up in the source.

    A table is a run of consecutive lines starting with ``|`` whose second
    line is a separator row. Each cell is padded to the widest cell of its
    column and the separator row is redrawn to the same widths, keeping any
    alignment colons. Text outside tables is returned unchanged, a pipe
    escaped as ``\|`` stays inside its cell, and aligning twice changes
    nothing.

    Args:
        text: The markdown text.

    Returns:
        The text with the columns of its tables aligned.
    """
    lines = text.split("\n")
    out = []
    i = 0
    while i < len(lines):
        is_table = (
            lines[i].startswith("|")
            and i + 1 < len(lines)
            and _SEPARATOR.match(lines[i + 1])
        )
        if not is_table:
            out.append(lines[i])
            i += 1
            continue
        end = i
        while end < len(lines) and lines[end].startswith("|"):
            end += 1
        rows = []
        for line in lines[i:end]:
            body = line.strip()[1:]
            if body.endswith("|") and not body.endswith("\\|"):
                body = body[:-1]
            rows.append([cell.strip() for cell in _PIPE.split(body)])
        columns = list(zip_longest(*rows, fillvalue=""))
        widths = [
            max(3, *(len(cell) for k, cell in enumerate(column) if k != 1))
            for column in columns
        ]
        for k, row in enumerate(zip(*columns, strict=True)):
            if k == 1:
                rules = [
                    ":" * cell.startswith(":")
                    + "-" * (w + 2 - cell.startswith(":") - cell.endswith(":"))
                    + ":" * cell.endswith(":")
                    for cell, w in zip(row, widths, strict=True)
                ]
                out.append("|" + "|".join(rules) + "|")
            else:
                cells = [cell.ljust(w) for cell, w in zip(row, widths, strict=True)]
                out.append("| " + " | ".join(cells) + " |")
        i = end
    return "\n".join(out)
