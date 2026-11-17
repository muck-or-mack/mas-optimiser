"""Unit tests of ``markdown``: tables written so that the source reads as a table."""

from mas_optimiser.markdown import amount, table


def cells(line):
    """The cells of one table line, without their padding."""
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def test_columns_are_padded_and_cells_are_written_as_given():
    lines = table(
        ["set", "gain", "attempts passed"],
        [["all", "+3.69", "97 of 375"], ["itsm", "-0.22", "34 of 126"]],
    ).split("\n")
    assert len(lines) == 4
    assert len({len(line) for line in lines}) == 1
    assert set(lines[1]) == {"|", "-"}
    # A signed number stays as written: number parsing is off.
    assert "| +3.69 " in lines[2] and "| -0.22 " in lines[3]


def test_a_table_without_rows_keeps_its_header():
    lines = table(["task", "passed"], []).split("\n")
    assert len(lines) == 2 and lines[0].startswith("| task")


def test_a_small_amount_keeps_two_significant_figures():
    assert [amount(x) for x in (12.26, 1, 0.566, 0.004026, 0.00447)] == [
        "12.26",
        "1.00",
        "0.57",
        "0.0040",
        "0.0045",
    ]
