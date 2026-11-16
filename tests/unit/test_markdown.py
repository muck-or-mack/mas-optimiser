"""Unit tests of ``markdown``: tables padded so the source reads as a table."""

from mas_optimiser.markdown import align_tables

TEXT = """# Title

Some text with a | pipe that is no table.

| set | tasks | attempts passed |
|---|---|--:|
| all | 125 | 97 of 375 |
| itsm | 42 | 34 of 126 |

After the table.
"""


def test_tables_are_padded_to_the_widest_cell_and_other_text_is_untouched():
    lines = align_tables(TEXT).split("\n")
    assert lines[:4] == TEXT.split("\n")[:4]
    assert lines[4] == "| set  | tasks | attempts passed |"
    assert lines[5] == "|------|-------|----------------:|"
    assert lines[6] == "| all  | 125   | 97 of 375       |"
    assert lines[7] == "| itsm | 42    | 34 of 126       |"
    assert lines[8:] == TEXT.split("\n")[8:]


def test_aligning_twice_changes_nothing():
    once = align_tables(TEXT)
    assert align_tables(once) == once


def test_an_escaped_pipe_stays_in_its_cell_and_short_rows_are_filled():
    text = "| a | b |\n|---|---|\n| x \\| y | z |\n| only |"
    assert align_tables(text).split("\n") == [
        "| a      | b   |",
        "|--------|-----|",
        "| x \\| y | z   |",
        "| only   |     |",
    ]
