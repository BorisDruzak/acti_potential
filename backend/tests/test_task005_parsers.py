import io
import zipfile

import pytest

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def docx(body, extra=None):
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "[Content_Types].xml",
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>',
        )
        archive.writestr(
            "word/document.xml",
            (extra or {}).get(
                "word/document.xml",
                f'<w:document xmlns:w="{W}"><w:body>{body}</w:body></w:document>',
            ),
        )
        for name, content in (extra or {}).items():
            if name != "word/document.xml":
                archive.writestr(name, content)
    return output.getvalue()


def pdf(text=None, encrypted=False):
    from pypdf import PdfWriter
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

    writer = PdfWriter()
    page = writer.add_blank_page(width=595, height=842)
    if text:
        font = DictionaryObject(
            {
                NameObject("/Type"): NameObject("/Font"),
                NameObject("/Subtype"): NameObject("/Type1"),
                NameObject("/BaseFont"): NameObject("/Helvetica"),
            }
        )
        page[NameObject("/Resources")] = DictionaryObject(
            {NameObject("/Font"): DictionaryObject({NameObject("/F1"): writer._add_object(font)})}
        )
        stream = DecodedStreamObject()
        stream.set_data(f"BT /F1 12 Tf 30 800 Td ({text}) Tj ET".encode())
        page[NameObject("/Contents")] = writer._add_object(stream)
    if encrypted:
        writer.encrypt("synthetic-test-password")
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


def test_docx_preserves_heading_paragraph_table_rows_and_stable_anchors():
    from app.content.parsers import parse_bytes

    data = docx(
        '<w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr><w:r><w:t>Русский заголовок</w:t></w:r></w:p><w:p><w:r><w:t>Проверка цифрового содержания.</w:t></w:r></w:p><w:tbl><w:tr><w:tc><w:p><w:r><w:t>Тема</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>Значение</w:t></w:r></w:p></w:tc></w:tr></w:tbl>'
    )
    result = parse_bytes(data, "synthetic.docx", "application/octet-stream")
    assert result["state"] == "completed"
    assert [b["kind"] for b in result["blocks"]] == ["heading", "paragraph", "table", "table_row"]
    assert result["blocks"][2]["table"] == [["Тема", "Значение"]]
    assert result["blocks"][3]["parent_ordinal"] == result["blocks"][2]["ordinal"]
    assert result["full_text"].count("Тема") == 1
    assert len({b["anchor"] for b in result["blocks"]}) == 4
    assert result == parse_bytes(data, "synthetic.docx", "application/octet-stream")


def test_docx_rejects_macros_traversal_and_entity_expansion():
    from app.content.parsers import parse_bytes

    for extra in ({"word/vbaProject.bin": b"macro"}, {"../escape": b"escape"}):
        result = parse_bytes(docx("<w:p/>", extra), "unsafe.docx", "")
        assert result["state"] in {"failed", "unsupported"}
        assert result["full_text"] == ""
    entity = b'<!DOCTYPE x [<!ENTITY secret SYSTEM "file:///etc/passwd">]><x>&secret;</x>'
    result = parse_bytes(docx("", {"word/document.xml": entity}), "entities.docx", "")
    assert result["state"] == "failed"
    assert result["error_code"] == "unsafe_xml"


def test_docx_resource_limits_fail_explicitly_and_do_not_return_partial_text(monkeypatch):
    from app.content import parsers

    monkeypatch.setattr(parsers, "MAX_BLOCKS", 2)
    result = parsers.parse_bytes(docx("<w:p><w:r><w:t>x</w:t></w:r></w:p>" * 3), "many.docx", "")
    assert result["state"] == "failed" and result["error_code"] == "limit_exceeded"
    assert result["full_text"] == "" and result["blocks"] == []
    monkeypatch.setattr(parsers, "MAX_ZIP_EXPANDED", 100)
    assert parsers.parse_bytes(docx("<w:p/>"), "bomb.docx", "")["state"] == "failed"


def test_pdf_digital_blank_encrypted_and_corrupt_are_distinct():
    from app.content.parsers import parse_bytes

    result = parse_bytes(pdf("Synthetic digital phrase"), "digital.pdf", "")
    assert result["state"] == "completed"
    assert "Synthetic digital phrase" in result["full_text"]
    assert result["blocks"][0]["page"] == 1
    assert result["blocks"][0]["anchor"] == "pdf:page:1"
    assert parse_bytes(pdf(), "scan.pdf", "")["state"] == "no_text"
    assert parse_bytes(pdf(encrypted=True), "encrypted.pdf", "")["state"] == "encrypted"
    assert parse_bytes(b"%PDF-corrupted", "broken.pdf", "")["state"] == "failed"
    assert parse_bytes(b"plain text", "not-supported.exe", "")["state"] == "unsupported"


def test_input_and_output_limits_are_enforced_before_or_during_parsing(monkeypatch):
    from app.content import parsers

    monkeypatch.setattr(parsers, "MAX_INPUT_BYTES", 4)
    assert parsers.parse_bytes(b"12345", "large.pdf", "")["error_code"] == "limit_exceeded"
    monkeypatch.setattr(parsers, "MAX_INPUT_BYTES", 100000)
    monkeypatch.setattr(parsers, "MAX_TEXT_BYTES", 3)
    assert (
        parsers.parse_bytes(
            docx("<w:p><w:r><w:t>Большой текст</w:t></w:r></w:p>"), "large.docx", ""
        )["error_code"]
        == "limit_exceeded"
    )


def test_old_doc_converter_is_argument_list_utf8_and_bounded(monkeypatch):
    from types import SimpleNamespace

    from app.content import parsers

    calls = []

    def converter(args, **kwargs):
        calls.append((args, kwargs))
        return SimpleNamespace(returncode=0, stdout="Старый документ\nВторой абзац".encode())

    monkeypatch.setattr(parsers.subprocess, "run", converter)
    result = parsers.parse_bytes(bytes.fromhex("D0CF11E0A1B11AE1") + b"\0" * 64, "old.doc", "")
    assert result["state"] == "completed"
    assert len(result["blocks"]) == 2
    assert calls[0][0][:3] == ["/usr/bin/antiword", "-m", "UTF-8.txt"]
    assert calls[0][1]["shell"] is False
    assert calls[0][1]["timeout"] <= 20


def test_parser_identity_is_fingerprintable_and_does_not_trust_mime_alone():
    from app.content.parsers import parser_identity

    assert parser_identity("text.pdf", "application/pdf")[0] == "pdf"
    assert parser_identity("text.docx", "application/octet-stream")[0] == "docx"
    assert (
        parser_identity("macro.docm", "application/vnd.ms-word.document.macroEnabled.12")[0]
        == "unsupported"
    )
    assert parser_identity("renamed.exe", "application/pdf")[0] == "unsupported"
    assert "6.20.1" in parser_identity("text.pdf", "")[1]


def test_sandbox_rejects_bad_child_payload_and_redacts_internal_failure():
    from app.content.sandbox import validate_result

    with pytest.raises(ValueError):
        validate_result({"state": "completed", "full_text": "x", "blocks": "wrong"})
    with pytest.raises(ValueError):
        validate_result(
            {
                "state": "completed",
                "full_text": "x",
                "blocks": [{"kind": "paragraph", "text": "x", "anchor": "../secret", "ordinal": 0}],
                "parser": "docx",
                "parser_version": "1",
                "warnings": [],
            }
        )


@pytest.mark.skipif(__import__("sys").platform != "linux", reason="Linux resource sandbox")
def test_real_sandbox_reads_original_without_modification_and_checks_fingerprint(tmp_path):
    import hashlib

    from app.content.sandbox import extract_file

    data = docx(
        "<w:p><w:r><w:t>Изолированная обработка</w:t></w:r></w:p><w:tbl><w:tr><w:tc><w:p><w:r><w:t>Table cell</w:t></w:r></w:p></w:tc></w:tr></w:tbl>"
    )
    original = tmp_path / "opaque-object"
    original.write_bytes(data)
    original.chmod(0o400)
    digest = hashlib.sha256(data).hexdigest()
    result = extract_file(tmp_path, original.name, "source.docx", "", digest)
    assert result["state"] == "completed", result
    assert "Изолированная обработка" in result["full_text"]
    assert [block["kind"] for block in result["blocks"]] == ["paragraph", "table", "table_row"]
    assert original.read_bytes() == data
    assert (
        extract_file(tmp_path, original.name, "source.docx", "", "0" * 64)["error_code"]
        == "integrity_mismatch"
    )
    assert (
        extract_file(tmp_path, "../opaque-object", "source.docx", "", digest)["error_code"]
        == "storage_unavailable"
    )


@pytest.mark.skipif(__import__("sys").platform != "linux", reason="Linux resource sandbox")
def test_real_sandbox_terminates_with_tiny_wall_budget(tmp_path):
    import hashlib

    from app.content.sandbox import extract_file

    data = pdf("Synthetic wall time limit")
    original = tmp_path / "opaque-object"
    original.write_bytes(data)
    result = extract_file(
        tmp_path,
        original.name,
        "source.pdf",
        "",
        hashlib.sha256(data).hexdigest(),
        timeout_seconds=0.00001,
    )
    assert result["state"] == "failed"
    assert result["error_code"] == "timeout"


def test_old_doc_encryption_is_explicit_and_numbered_docx_points_are_structural(monkeypatch):
    from types import SimpleNamespace

    from app.content import parsers

    monkeypatch.setattr(
        parsers.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=1, stdout=b"", stderr=b"Encrypted Word document cannot be read"
        ),
    )
    result = parsers.parse_bytes(
        bytes.fromhex("D0CF11E0A1B11AE1") + b"\0" * 64, "encrypted.doc", ""
    )
    assert result["state"] == "encrypted"
    result = parsers.parse_bytes(
        docx(
            '<w:p><w:pPr><w:numPr><w:numId w:val="1"/></w:numPr></w:pPr><w:r><w:t>Первый пункт</w:t></w:r></w:p>'
        ),
        "points.docx",
        "",
    )
    assert result["blocks"][0]["kind"] == "point"
