"""Data set files as a spreadsheet table: export to CSV, import from CSV or Excel (.xlsx).

The table has one row per set and one column per key. The first column is the set name; the first row holds the
keys. A row named #defaults holds the values every set falls back to. An empty cell means "not set here".
Excel files are read with the standard library only (first sheet; text, numbers and shared strings).
"""
from __future__ import annotations

import csv
import io
import re
import zipfile
import xml.etree.ElementTree as ET

from utils.data_sets import KEY_OK, NAME_OK, DataSetError, validate

DEFAULTS_ROW = "#defaults"
MAX_ROWS, MAX_COLUMNS = 5000, 200
MAX_SHEET_BYTES = 20 * 1024 * 1024
NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}


def to_csv(data: dict) -> str:
    """The data file as CSV text (UTF-8 with a BOM is added by the caller so Excel reads accents)."""
    defaults, sets = validate(data, "data")
    keys: dict[str, None] = dict.fromkeys(defaults)
    for values in sets.values():
        keys.update(dict.fromkeys(values))
    columns = list(keys)
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\r\n")
    writer.writerow(["set", *columns])
    cell = lambda values, k: "" if values.get(k) is None else str(values[k])  # noqa: E731
    if defaults:
        writer.writerow([DEFAULTS_ROW, *(cell(defaults, k) for k in columns)])
    for name, values in sets.items():
        writer.writerow([name, *(cell(values, k) for k in columns)])
    return out.getvalue()


def read_csv(content: bytes) -> list[list[str]]:
    text = content.decode("utf-8-sig", errors="replace")
    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    rows = [row for row in csv.reader(io.StringIO(text), dialect)]
    return rows


def _column(ref: str) -> int:
    letters = re.match(r"[A-Z]+", ref or "")
    n = 0
    for ch in letters.group(0) if letters else "A":
        n = n * 26 + ord(ch) - 64
    return n - 1


def read_xlsx(content: bytes) -> list[list[str]]:
    """Rows of the first worksheet as text."""
    try:
        z = zipfile.ZipFile(io.BytesIO(content))
    except zipfile.BadZipFile as e:
        raise DataSetError("Not an Excel (.xlsx) file.") from e
    with z:
        sheets = sorted(n for n in z.namelist() if re.fullmatch(r"xl/worksheets/sheet\d+\.xml", n))
        if not sheets:
            raise DataSetError("The Excel file has no worksheet.")
        first = min(sheets, key=lambda n: int(re.search(r"(\d+)\.xml", n).group(1)))
        shared: list[str] = []
        if "xl/sharedStrings.xml" in z.namelist():
            if z.getinfo("xl/sharedStrings.xml").file_size > MAX_SHEET_BYTES:
                raise DataSetError("The Excel file is too large.")
            for si in ET.fromstring(z.read("xl/sharedStrings.xml")).findall("m:si", NS):
                shared.append("".join(t.text or "" for t in si.iter(f"{{{NS['m']}}}t")))
        if z.getinfo(first).file_size > MAX_SHEET_BYTES:
            raise DataSetError("The Excel file is too large.")
        root = ET.fromstring(z.read(first))
    rows: list[list[str]] = []
    for row in root.iter(f"{{{NS['m']}}}row"):
        values: dict[int, str] = {}
        for c in row.findall("m:c", NS):
            kind, v = c.get("t"), c.find("m:v", NS)
            if kind == "s" and v is not None and (v.text or "").isdigit() and int(v.text) < len(shared):
                value = shared[int(v.text)]
            elif kind == "inlineStr":
                value = "".join(t.text or "" for t in c.iter(f"{{{NS['m']}}}t"))
            else:
                value = (v.text or "") if v is not None else ""
                if re.fullmatch(r"-?\d+\.0+", value):  # 78701.0 -> 78701
                    value = value.split(".")[0]
            values[_column(c.get("r", ""))] = value
            if len(values) > MAX_COLUMNS:
                raise DataSetError(f"More than {MAX_COLUMNS} columns.")
        rows.append([values.get(i, "") for i in range(max(values, default=-1) + 1)])
        if len(rows) > MAX_ROWS + 1:
            raise DataSetError(f"More than {MAX_ROWS} rows.")
    return rows


def read_table(filename: str, content: bytes) -> list[list[str]]:
    name = filename.lower()
    if name.endswith(".xlsx"):
        return read_xlsx(content)
    if name.endswith((".csv", ".txt", ".tsv")):
        return read_csv(content)
    raise DataSetError("Use a .csv or .xlsx file (in Excel: Save As, CSV UTF-8).")


def from_table(rows: list[list[str]], base: dict | None = None) -> dict:
    """The data file for a table. `base` supplies the description and any other top-level fields to keep."""
    rows = [r for r in rows if any(str(c).strip() for c in r)]
    if len(rows) < 2:
        raise DataSetError("The table needs a header row of keys and at least one row of values.")
    if len(rows) > MAX_ROWS + 1:
        raise DataSetError(f"More than {MAX_ROWS} rows.")
    header = [str(c).strip() for c in rows[0]]
    keys = header[1:]
    while keys and not keys[-1]:  # trailing empty header cells
        keys.pop()
    if len(keys) > MAX_COLUMNS:
        raise DataSetError(f"More than {MAX_COLUMNS} columns.")
    for k in keys:
        if not KEY_OK.match(k):
            raise DataSetError(f"Column header {k!r}: letters, digits, '_' or '-', starting with a letter or '_'.")
    if len(set(keys)) != len(keys):
        raise DataSetError("Two columns have the same key.")
    defaults: dict[str, str] = {}
    sets: dict[str, dict[str, str]] = {}
    for number, row in enumerate(rows[1:], start=2):
        name = str(row[0]).strip() if row else ""
        cells = {k: str(row[i + 1]).strip() if i + 1 < len(row) else "" for i, k in enumerate(keys)}
        cells = {k: v for k, v in cells.items() if v != ""}
        if name == DEFAULTS_ROW:
            defaults = cells
        elif not NAME_OK.match(name):
            raise DataSetError(f"Row {number}: set name {name!r} may only have letters, digits, '_' or '-'.")
        elif name in sets:
            raise DataSetError(f"Row {number}: the set {name!r} appears twice.")
        else:
            sets[name] = cells
    if not sets:
        raise DataSetError("The table has no set rows.")
    data = {**(base or {}), "defaults": defaults, "sets": sets}
    data.setdefault("description", "")
    validate(data, "import")
    return data
