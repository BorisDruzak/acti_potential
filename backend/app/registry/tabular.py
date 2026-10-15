"""Bounded tabular interchange. Values never become executable spreadsheet cells."""

import csv
import io
from datetime import datetime, time
from itertools import chain
from xml.etree.ElementTree import ParseError
from zipfile import BadZipFile, ZipFile

from defusedxml.common import DefusedXmlException

MAX_IMPORT_BYTES = 2 * 1024 * 1024
MAX_IMPORT_ROWS = 2000
MAX_CELL = 32767


def safe_text(value):
    value = "" if value is None else str(value)
    if len(value) > MAX_CELL or any(ord(c) < 32 and c not in "\t\r\n" for c in value):
        raise ValueError("Cell contains unsupported characters or exceeds 32767 characters")
    return value


def export_file(format, headers, rows):
    if format == "csv":
        output = io.StringIO(newline="")
        writer = csv.writer(output)
        for row in chain([headers], rows):
            values = [safe_text(v) for v in row]
            writer.writerow([
                "'" + v if v.lstrip(" \t\r\n").startswith(("=", "+", "-", "@"))
                or v.startswith(("\t", "\r", "\n")) else v for v in values
            ])
        return output.getvalue().encode("utf-8-sig")
    if format != "xlsx":
        raise ValueError("Unsupported tabular format")
    from openpyxl import Workbook
    from openpyxl.cell import WriteOnlyCell

    book = Workbook(write_only=True)
    sheet = book.create_sheet("ACTI")
    for row in chain([headers], rows):
        cells = []
        for value in row:
            cell = WriteOnlyCell(sheet, value=safe_text(value))
            cell.data_type = "s"
            cell.number_format = "@"
            cells.append(cell)
        sheet.append(cells)
    output = io.BytesIO()
    book.save(output)
    return output.getvalue()


def read_tabular(format, content):
    if len(content) > MAX_IMPORT_BYTES:
        raise ValueError("Import exceeds 2 MiB")
    book = None
    try:
        if format == "csv":
            rows = csv.reader(io.StringIO(content.decode("utf-8-sig"), newline=""))
        elif format == "xlsx":
            from openpyxl import load_workbook

            with ZipFile(io.BytesIO(content)) as archive:
                entries = archive.infolist()
                if len(entries) > 100 or sum(e.file_size for e in entries) > 16 * 1024 * 1024:
                    raise ValueError("Expanded workbook exceeds import limits")
            book = load_workbook(io.BytesIO(content), read_only=True, keep_links=False)
            sheet = book.active
            if (sheet.max_row or 0) > MAX_IMPORT_ROWS + 1 or (sheet.max_column or 0) > 30:
                raise ValueError("Workbook dimensions exceed import limits")

            def excel_rows():
                for row in sheet.iter_rows():
                    if any(c.data_type == "f" for c in row):
                        raise ValueError("Formulas cannot be imported")
                    yield [
                        c.value.date().isoformat()
                        if isinstance(c.value, datetime) and c.value.time() == time()
                        else c.value.isoformat() if isinstance(c.value, datetime) else c.value
                        for c in row
                    ]

            rows = excel_rows()
        else:
            raise ValueError("Unsupported tabular format")
        headers = [safe_text(v).strip() for v in next(rows, [])]
        if not headers or len(headers) > 30 or any(not v for v in headers):
            raise ValueError("Invalid headers")
        if len(headers) != len(set(headers)):
            raise ValueError("Duplicate headers")
        result = []
        for row in rows:
            if not any(v is not None and v != "" for v in row):
                continue
            if len(row) != len(headers) or len(result) >= MAX_IMPORT_ROWS:
                raise ValueError("Invalid row length or more than 2000 rows")
            result.append(dict(zip(headers, [safe_text(v) for v in row], strict=True)))
        return result
    except (BadZipFile, UnicodeError, csv.Error, KeyError, OSError, ParseError,
            DefusedXmlException) as error:
        raise ValueError("Invalid tabular file") from error
    finally:
        if book:
            book.close()
