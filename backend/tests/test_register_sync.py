"""[SCRUM-99] The test register exists twice: docs/TEST_CASES.md and docs/TEST_CASES.csv.

These tests read the two files and stop them drifting apart. They need no database.
"""
import csv
import html
import io
import re
from collections import Counter
from pathlib import Path

DOCS = Path(__file__).resolve().parents[2] / "docs"
COLUMNS = 15
ROW_START = re.compile(r"\| (?:\*\*)?TC-\d+(?:\*\*)? \|")

csv.field_size_limit(10_000_000)


def read_text(name):
    # newline="" then a plain replace, so a CRLF checkout and CI's LF read the same.
    with open(DOCS / name, encoding="utf-8", newline="") as handle:
        return handle.read().replace("\r\n", "\n")


def markdown_lines():
    return [line for line in read_text("TEST_CASES.md").split("\n") if ROW_START.match(line)]


def markdown_rows():
    rows = []
    for line in markdown_lines():
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        cells[0] = cells[0].replace("**", "")
        rows.append(cells)
    return rows


def csv_rows():
    """Every record after the header. A blank line comes back as an empty record."""
    return list(csv.reader(io.StringIO(read_text("TEST_CASES.csv"), newline="")))[1:]


def repeated(ids):
    return [row_id for row_id, count in Counter(ids).items() if count > 1]


def ids_by_story(rows):
    grouped = {}
    for row in rows:
        grouped.setdefault(row[1], []).append(row[0])
    return grouped


def test_tc9903_both_files_hold_the_same_ids_in_the_same_order_within_each_story():
    # The register helper groups the Markdown by story but writes the CSV in rows-file order.
    # So a rows file with mixed stories orders the stories differently, and that is not a mismatch.
    md_rows = markdown_rows()
    rows = [row for row in csv_rows() if row]
    md_ids = [row[0] for row in md_rows]
    csv_ids = [row[0] for row in rows]
    assert sorted(set(md_ids) - set(csv_ids)) == []
    assert sorted(set(csv_ids) - set(md_ids)) == []
    assert ids_by_story(md_rows) == ids_by_story(rows)


def test_tc9928_order_is_compared_story_by_story():
    """TC-9928 | Workflow Variation | A rows file with mixed stories must not fail; a swap inside a story must."""
    markdown = [["TC-1", "SCRUM-1"], ["TC-2", "SCRUM-1"], ["TC-3", "SCRUM-2"]]
    stories_mixed = [["TC-1", "SCRUM-1"], ["TC-3", "SCRUM-2"], ["TC-2", "SCRUM-1"]]
    swapped_inside_a_story = [["TC-2", "SCRUM-1"], ["TC-1", "SCRUM-1"], ["TC-3", "SCRUM-2"]]
    assert ids_by_story(stories_mixed) == ids_by_story(markdown)
    assert ids_by_story(swapped_inside_a_story) != ids_by_story(markdown)


def test_tc9904_every_csv_row_has_fifteen_columns():
    wrong = [(row[0] if row else "(blank line)", len(row)) for row in csv_rows() if len(row) != COLUMNS]
    assert wrong == []


def test_tc9905_no_id_appears_twice_in_a_file():
    assert repeated(row[0] for row in markdown_rows()) == []
    assert repeated(row[0] for row in csv_rows() if row) == []


def test_tc9906_each_markdown_row_is_on_its_own_line():
    # Three blocks were once pasted onto single lines, which hid 114 rows from the CSV.
    joined = [line[:30] for line in markdown_lines() if len(re.findall(r"\| \| (?:\*\*)?TC-\d+", line)) > 0]
    assert joined == []
    assert [len(row) for row in markdown_rows() if len(row) != COLUMNS] == []


def test_tc9907_every_cell_says_the_same_in_both_files():
    from_csv = {row[0]: row for row in csv_rows() if row}
    different = []
    for row in markdown_rows():
        expected = [html.unescape(cell.replace("<br>", "\n")) for cell in row]
        if from_csv.get(row[0]) != expected:
            different.append(row[0])
    assert different == []
