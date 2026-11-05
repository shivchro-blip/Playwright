"""Data set CSV / Excel import and export."""
from __future__ import annotations

import io
import zipfile

import pytest

from utils.data_sets import DataSetError
from utils.data_table import DEFAULTS_ROW, from_table, read_table, read_xlsx, to_csv

DATA = {"description": "people", "defaults": {"Country": "United States"},
        "sets": {"austin": {"City": "Austin", "Zip": "78701"}, "dallas": {"City": "Dallas, TX", "Zip": "75201"}}}


def xlsx(rows: list[list[str]], shared: bool = True) -> bytes:
    letters = "ABCDEFGH"
    strings: list[str] = []
    sheet_rows = []
    for r, row in enumerate(rows, 1):
        cells = []
        for c, value in enumerate(row):
            ref = f"{letters[c]}{r}"
            if value.replace(".", "").isdigit() and not value.startswith("0"):
                cells.append(f'<c r="{ref}"><v>{value}</v></c>')
            elif shared:
                strings.append(value)
                cells.append(f'<c r="{ref}" t="s"><v>{len(strings) - 1}</v></c>')
            else:
                cells.append(f'<c r="{ref}" t="inlineStr"><is><t>{value}</t></is></c>')
        sheet_rows.append(f'<row r="{r}">{"".join(cells)}</row>')
    ns = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as z:
        z.writestr("xl/worksheets/sheet1.xml", f'<worksheet {ns}><sheetData>{"".join(sheet_rows)}</sheetData></worksheet>')
        z.writestr("xl/sharedStrings.xml", f'<sst {ns}>{"".join(f"<si><t>{s}</t></si>" for s in strings)}</sst>')
    return out.getvalue()


def test_csv_round_trip_keeps_defaults_values_and_commas():
    text = to_csv(DATA)
    assert text.splitlines()[0] == "set,Country,City,Zip"
    assert text.splitlines()[1].startswith(DEFAULTS_ROW + ",United States")
    rows = read_table("people.csv", ("﻿" + text).encode("utf-8"))
    assert from_table(rows, {"description": "people"}) == DATA


def test_semicolon_and_tab_files_are_understood():
    rows = read_table("x.csv", b"set;City;Zip\r\naustin;Austin;78701\r\n")
    assert from_table(rows)["sets"] == {"austin": {"City": "Austin", "Zip": "78701"}}
    rows = read_table("x.csv", b"set\tCity\naustin\tAustin\n")
    assert from_table(rows)["sets"]["austin"] == {"City": "Austin"}


def test_empty_cells_mean_not_set_and_blank_rows_are_ignored():
    rows = [["set", "A", "B"], ["one", "x", ""], ["", "", ""], ["two", "", "y"]]
    assert from_table(rows)["sets"] == {"one": {"A": "x"}, "two": {"B": "y"}}


@pytest.mark.parametrize("rows, message", [
    ([["set", "A"]], "at least one row"),
    ([["set", "bad key"], ["a", "1"]], "Column header"),
    ([["set", "A", "A"], ["a", "1", "2"]], "same key"),
    ([["set", "A"], ["bad name", "1"]], "Row 2"),
    ([["set", "A"], ["a", "1"], ["a", "2"]], "appears twice"),
    ([["set", "A"], [DEFAULTS_ROW, "1"]], "no set rows"),
])
def test_bad_tables_say_where(rows, message):
    with pytest.raises(DataSetError, match=message):
        from_table(rows)


def test_excel_files_are_read_with_text_numbers_and_inline_strings():
    rows = [["set", "City", "Zip"], ["austin", "Austin", "78701"], ["dallas", "Dallas", "75201.0"]]
    for shared in (True, False):
        data = from_table(read_xlsx(xlsx(rows, shared)))
        assert data["sets"]["austin"] == {"City": "Austin", "Zip": "78701"}
        assert data["sets"]["dallas"]["Zip"] == "75201"


def test_a_file_that_is_not_what_it_claims_is_refused():
    with pytest.raises(DataSetError, match="Not an Excel"):
        read_table("x.xlsx", b"just text")
    with pytest.raises(DataSetError, match=".csv or .xlsx"):
        read_table("x.json", b"{}")


def test_an_oversize_sheet_is_refused(monkeypatch):
    import utils.data_table as dt
    monkeypatch.setattr(dt, "MAX_SHEET_BYTES", 50)
    with pytest.raises(DataSetError, match="too large"):
        dt.read_xlsx(xlsx([["set", "A"], ["a", "1"]]))
