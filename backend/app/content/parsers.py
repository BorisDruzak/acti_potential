"""Bounded digital-text parsers. No Office automation, macro execution or OCR."""

import io
import json
import logging
import re
import subprocess
import sys
import tempfile
import zipfile
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path, PurePosixPath
from typing import Protocol

MAX_INPUT_BYTES = 25 * 1024 * 1024
MAX_ZIP_EXPANDED = 32 * 1024 * 1024
MAX_XML_BYTES = 8 * 1024 * 1024
MAX_TEXT_BYTES = 2 * 1024 * 1024
MAX_BLOCKS = 5000
MAX_PAGES = 1000
MAX_CELLS = 20000
W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


class OCRProvider(Protocol):
    """Future adapter only; no OCR provider is installed or invoked by these parsers."""

    def extract(self, data: bytes, *, mime: str) -> dict: ...


class ParseFailure(Exception):
    def __init__(self, code, state="failed"):
        self.code, self.state = code, state


def parser_identity(name: str, mime: str = "") -> tuple[str, str]:
    suffix = Path(name.replace("\\", "/")).suffix.lower()
    if suffix == ".pdf":
        try:
            dependency = version("pypdf")
        except PackageNotFoundError:
            dependency = "unavailable"
        return "pdf", "acti-1+pypdf-" + dependency
    if suffix == ".docx":
        return "docx", "acti-ooxml-1"
    if suffix == ".doc":
        return "doc", "acti-1+antiword-0.37-16"
    return "unsupported", "acti-1"


def empty_result(name, mime, state, error_code=None):
    parser, parser_version = parser_identity(name, mime)
    return {
        "state": state,
        "parser": parser,
        "parser_version": parser_version,
        "full_text": "",
        "blocks": [],
        "warnings": [],
        "error_code": error_code,
    }


def clean_text(value):
    value = value.replace("\r\n", "\n").replace("\r", "\n")
    value = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", value)
    return value.encode("utf-8", errors="replace").decode("utf-8").strip()


class Blocks:
    def __init__(self):
        self.items, self.text = [], []
        self.text_bytes = 0

    def add(self, kind, text, anchor, *, parent=None, page=None, table=None):
        text = clean_text(text)
        # Count every representation, including table text, to bound the JSON result too.
        self.text_bytes += len(text.encode("utf-8"))
        if len(self.items) >= MAX_BLOCKS or self.text_bytes > MAX_TEXT_BYTES:
            raise ParseFailure("limit_exceeded")
        item = {"kind": kind, "ordinal": len(self.items), "text": text, "anchor": anchor}
        if parent is not None:
            item["parent_ordinal"] = parent
        if page is not None:
            item["page"] = page
        if table is not None:
            item["table"] = table
        self.items.append(item)
        if text and kind != "table":
            self.text.append(text)
        return item["ordinal"]

    def result(self):
        return "\n\n".join(self.text), self.items


def _xml(data):
    from defusedxml import ElementTree
    from defusedxml.common import DefusedXmlException

    if len(data) > MAX_XML_BYTES:
        raise ParseFailure("limit_exceeded")
    try:
        return ElementTree.fromstring(
            data, forbid_dtd=True, forbid_entities=True, forbid_external=True
        )
    except DefusedXmlException:
        raise ParseFailure("unsafe_xml") from None
    except ElementTree.ParseError:
        raise ParseFailure("malformed_xml") from None


def _paragraph(element):
    parts = []
    for child in element.iter():
        if child.tag == W + "t":
            parts.append(child.text or "")
        elif child.tag == W + "tab":
            parts.append("\t")
        elif child.tag in {W + "br", W + "cr"}:
            parts.append("\n")
    return "".join(parts)


def parse_docx(data):
    blocks = Blocks()
    warnings = []
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise ParseFailure("malformed_archive") from None
    with archive:
        members = archive.infolist()
        if len(members) > 2000 or sum(info.file_size for info in members) > MAX_ZIP_EXPANDED:
            raise ParseFailure("limit_exceeded")
        names = set()
        for info in members:
            name = info.filename
            target = PurePosixPath(name)
            if name in names or target.is_absolute() or ".." in target.parts or "\\" in name:
                raise ParseFailure("unsafe_archive")
            names.add(name)
            if info.flag_bits & 1:
                raise ParseFailure("encrypted_archive", "encrypted")
            if info.file_size > 1024 * 1024 and info.file_size > max(info.compress_size, 1) * 200:
                raise ParseFailure("limit_exceeded")
            if "vbaproject" in name.lower() or name.lower().endswith((".bin", ".exe", ".js")):
                raise ParseFailure("active_content", "unsupported")
            if name.lower().endswith(".rels"):
                relationships = _xml(archive.read(info))
                if any(node.get("TargetMode") == "External" for node in relationships.iter()):
                    warnings.append("external_relationships_ignored")
        if "[Content_Types].xml" not in names or "word/document.xml" not in names:
            raise ParseFailure("malformed_archive")
        types = _xml(archive.read("[Content_Types].xml"))
        if any(
            "macroenabled" in value.lower()
            for node in types.iter()
            for value in node.attrib.values()
        ):
            raise ParseFailure("active_content", "unsupported")
        document = _xml(archive.read("word/document.xml"))
        body = document.find(W + "body")
        if body is None:
            raise ParseFailure("malformed_document")
        paragraph_index, table_index, cell_count = 0, 0, 0
        for element in body:
            if element.tag == W + "p":
                paragraph_index += 1
                text = _paragraph(element)
                if not clean_text(text):
                    continue
                style = element.find(W + "pPr/" + W + "pStyle")
                heading = style is not None and bool(
                    re.search(r"heading|заголовок", style.get(W + "val", ""), re.I)
                )
                point = element.find(W + "pPr/" + W + "numPr") is not None
                blocks.add(
                    "heading" if heading else "point" if point else "paragraph",
                    text,
                    f"docx:p:{paragraph_index}",
                )
            elif element.tag == W + "tbl":
                table_index += 1
                rows = []
                for row in element.findall(W + "tr"):
                    cells = []
                    for cell in row.findall(W + "tc"):
                        cell_count += 1
                        if cell_count > MAX_CELLS:
                            raise ParseFailure("limit_exceeded")
                        cells.append(
                            clean_text("\n".join(_paragraph(p) for p in cell.findall(W + "p")))
                        )
                        if cell.find(W + "tbl") is not None:
                            warnings.append("nested_table_omitted")
                    rows.append(cells)
                anchor = f"docx:table:{table_index}"
                parent = blocks.add(
                    "table", "\n".join("\t".join(row) for row in rows), anchor, table=rows
                )
                for index, row in enumerate(rows, 1):
                    blocks.add("table_row", "\t".join(row), f"{anchor}:row:{index}", parent=parent)
        return *blocks.result(), sorted(set(warnings))


def parse_pdf(data):
    from pypdf import PdfReader

    blocks = Blocks()
    if not data.startswith(b"%PDF-"):
        raise ParseFailure("malformed_pdf")
    reader = PdfReader(io.BytesIO(data), strict=True)
    if reader.is_encrypted:
        raise ParseFailure("encrypted_pdf", "encrypted")
    if len(reader.pages) > MAX_PAGES:
        raise ParseFailure("limit_exceeded")
    for number, page in enumerate(reader.pages, 1):
        content = page.get_contents()
        if content is not None and len(content.get_data()) > MAX_XML_BYTES:
            raise ParseFailure("limit_exceeded")
        text = page.extract_text() or ""
        if clean_text(text):
            blocks.add("page", text, f"pdf:page:{number}", page=number)
    return *blocks.result(), []


def parse_doc(data):
    if not data.startswith(bytes.fromhex("D0CF11E0A1B11AE1")):
        raise ParseFailure("malformed_doc")
    with tempfile.TemporaryDirectory(prefix="acti-doc-") as directory:
        source = Path(directory) / "input.doc"
        source.write_bytes(data)
        source.chmod(0o600)
        try:
            result = subprocess.run(
                ["/usr/bin/antiword", "-m", "UTF-8.txt", str(source)],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=20,
                shell=False,
                check=False,
            )
        except FileNotFoundError:
            raise ParseFailure("converter_unavailable", "unsupported") from None
        except subprocess.TimeoutExpired:
            raise ParseFailure("timeout") from None
        if result.returncode:
            if b"encrypt" in getattr(result, "stderr", b"").lower():
                raise ParseFailure("encrypted_doc", "encrypted")
            raise ParseFailure("malformed_doc")
        if len(result.stdout) > MAX_TEXT_BYTES:
            raise ParseFailure("limit_exceeded")
        blocks = Blocks()
        for index, paragraph in enumerate(
            result.stdout.decode("utf-8", errors="replace").splitlines(), 1
        ):
            if clean_text(paragraph):
                blocks.add("paragraph", paragraph, f"doc:paragraph:{index}")
        return *blocks.result(), ["legacy_doc_layout_flattened"]


def parse_bytes(data: bytes, name: str, mime: str = "") -> dict:
    parser, _ = parser_identity(name, mime)
    result = empty_result(name, mime, "unsupported", "unsupported_format")
    if len(data) > MAX_INPUT_BYTES:
        return empty_result(name, mime, "failed", "limit_exceeded")
    if parser == "unsupported":
        return result
    try:
        text, blocks, warnings = {"pdf": parse_pdf, "docx": parse_docx, "doc": parse_doc}[parser](
            data
        )
        result.update(
            state="completed" if text else "no_text",
            full_text=text,
            blocks=blocks,
            warnings=warnings,
            error_code=None,
        )
    except ParseFailure as failure:
        return empty_result(name, mime, failure.state, failure.code)
    except (MemoryError, RecursionError):
        return empty_result(name, mime, "failed", "limit_exceeded")
    except Exception:
        return empty_result(name, mime, "failed", "malformed_document")
    return result


def main():
    logging.disable(logging.CRITICAL)
    name, mime = sys.argv[1:3]
    data = sys.stdin.buffer.read(MAX_INPUT_BYTES + 1)
    result = parse_bytes(data, name, mime)
    sys.stdout.write(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
