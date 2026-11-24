"""The documentation's references to code, and the line counts it gives."""

import importlib
import re
from collections.abc import Iterator
from pathlib import Path

ROOT = Path(__file__).parents[2]
PACKAGE = ROOT / "src" / "mas_optimiser"
VENDOR = ROOT / "vendor" / "enterpriseops-gym"
PAGES = [
    ROOT / "README.md",
    *(p for p in sorted((ROOT / "docs").rglob("*.md")) if "_build" not in p.parts),
]
# Code whose lines stay where they are under a page: the benchmark's, pinned
# to one commit, and the starting harness.
PINNED = [
    VENDOR / "benchmark",
    VENDOR / "orchestrators",
    ROOT / "harnesses" / "default",
]
LINE = re.compile(r"`(?P<file>[\w/]+\.py):(?P<first>\d+)(?:-(?P<last>\d+))?`")
# A name written as code in a page: ``runner.load_model``, ``stats.gate.mean_test``.
DOTTED = re.compile(r"`([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)+)`")
# What a dotted name ends with when it is a file and not a name of the code.
FILES = {
    "py",
    "md",
    "csv",
    "json",
    "jsonl",
    "yaml",
    "yml",
    "svg",
    "drawio",
    "log",
    "sh",
}


def references() -> Iterator[tuple[str, re.Match, str | None]]:
    """Every reference by line in the pages.

    Yields:
        The page's name, the reference's match, and the last part of the name
        written just before it, as in "`name` (`file.py:12`)", or ``None``.
    """
    for page in PAGES:
        text = page.read_text()
        for match in LINE.finditer(text):
            before = text[max(0, match.start() - 80) : match.start()]
            named = re.search(r"`([A-Za-z_][\w.]*)`\s*(?:\(\s*|,\s+)$", before)
            yield page.name, match, named and named[1].split(".")[-1]


def pinned(file: str) -> list[Path]:
    """The pinned files a reference's file name stands for.

    Args:
        file: The file as the reference writes it.

    Returns:
        The files of that name under ``PINNED``.
    """
    return [path for root in PINNED for path in root.rglob(Path(file).name)]


def test_this_packages_code_is_referred_to_by_name_and_not_by_line():
    # A line number goes out of date with the next change to the file; a
    # name is checked by the documentation build, which fails on one that
    # no longer exists.
    by_line = [
        f"{page}: {match[0]}"
        for page, match, _ in references()
        if not pinned(match["file"]) and list(PACKAGE.rglob(Path(match["file"]).name))
    ]
    assert not by_line


def test_a_reference_by_line_points_at_lines_that_hold_what_it_names():
    wrong = []
    for page, match, name in references():
        found = pinned(match["file"])
        if len(found) != 1:
            wrong.append(f"{page}: {match[0]} stands for {len(found)} pinned files")
            continue
        first, last = int(match["first"]), int(match["last"] or match["first"])
        span = found[0].read_text().splitlines()[first - 1 : last]
        if not span or not span[0].strip():
            wrong.append(f"{page}: {match[0]} is no line of code")
        elif name and not any(
            re.search(rf"\b{re.escape(name)}\b", line) for line in span
        ):
            wrong.append(f"{page}: {match[0]} does not hold {name}")
    # A range of lines with no file beside it cannot be checked at all.
    wrong += [
        f"{page.name}: ({bare}) names no file"
        for page in PAGES
        for bare in re.findall(r"\((\d{2,4}-\d{2,4})\)", page.read_text())
    ]
    assert not wrong


def test_every_name_of_the_package_written_in_a_page_exists():
    # Each module by the ways a page may write it: ``runner``, ``stats.gate``
    # and its path in full.
    modules = {"mas_optimiser": "mas_optimiser"}
    for path in PACKAGE.rglob("*.py"):
        parts = path.relative_to(PACKAGE).with_suffix("").parts
        if parts[-1].startswith("_"):
            parts = parts[:-1]
        for start in range(len(parts)):
            modules[".".join(parts[start:])] = ".".join(("mas_optimiser", *parts))
    gone = []
    for page in PAGES:
        for name in sorted(set(DOTTED.findall(page.read_text()))):
            parts = name.split(".")
            if parts[-1] in FILES:
                continue
            for cut in range(len(parts), 0, -1):
                if ".".join(parts[:cut]) in modules:
                    found = importlib.import_module(modules[".".join(parts[:cut])])
                    try:
                        for part in parts[cut:]:
                            found = getattr(found, part)
                    except AttributeError:
                        gone.append(f"{page.name}: {name}")
                    break
    assert not gone


def test_every_link_by_name_has_a_definition_and_every_definition_is_used():
    # A label with no definition is shown as it is written and gives no
    # warning, so the build cannot catch it.
    for page in PAGES:
        text = page.read_text()
        used = set(re.findall(r"\]\[([A-Za-z_]+\.[\w.]+)\]", text))
        defined = set(re.findall(r"^\[([A-Za-z_]+\.[\w.]+)\]: #", text, re.MULTILINE))
        assert used == defined, (page.name, sorted(used ^ defined))


def test_the_walkthroughs_line_counts_are_near_the_files():
    text = (ROOT / "docs" / "how-it-works.md").read_text()
    table = text[text.index("| Layer | Files | Lines |") :].split("\n\n")[0]
    places = [
        ROOT,
        PACKAGE,
        PACKAGE / "benchmarks" / "enterpriseops",
        ROOT / "harnesses" / "default",
        VENDOR,
        VENDOR / "benchmark",
    ]
    for row in table.splitlines()[2:]:
        layer, files, count = (cell.strip() for cell in row.split("|")[1:4])
        lines = 0
        for name in re.findall(r"`([^`]+)`", files):
            path = next(place / name for place in places if (place / name).exists())
            sources = [path] if path.is_file() else sorted(path.glob("*.py"))
            lines += sum(len(source.read_text().splitlines()) for source in sources)
        documented = int(re.sub(r"\D", "", count))
        assert abs(lines - documented) <= 0.15 * lines, (layer, documented, lines)
