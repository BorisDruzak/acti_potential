"""Large real-world Office layouts retain complete text within parser limits."""

import zipfile
from types import SimpleNamespace

import pytest

from app.content import parsers
from app.content.sandbox import validate_result
from tests.test_task005_parsers import docx


def large_body():
    return (
        '<w:p><w:r><w:t>Opening paragraph</w:t></w:r></w:p><w:tbl>'
        + ''.join(
            f'<w:tr><w:tc><w:p><w:r><w:t>Cell {index:03}</w:t></w:r></w:p></w:tc></w:tr>'
            for index in range(80)
        )
        + '</w:tbl><w:p><w:r><w:t>Closing paragraph</w:t></w:r></w:p>'
    )


def test_large_docx_streams_and_coalesces_complete_table_text(monkeypatch):
    monkeypatch.setattr(parsers, 'MAX_XML_BYTES', 512)
    monkeypatch.setattr(parsers, 'MAX_BLOCKS', 2)
    original_read = zipfile.ZipFile.read

    def bounded_read(archive, name, *args, **kwargs):
        assert name != 'word/document.xml', 'Large body must be streamed'
        return original_read(archive, name, *args, **kwargs)

    monkeypatch.setattr(zipfile.ZipFile, 'read', bounded_read)
    data = docx(large_body())
    result = parsers.parse_bytes(data, 'large.docx')
    assert result['state'] == 'completed', result['error_code']
    assert len(result['blocks']) <= 2
    assert 'large_docx_text_flattened' in result['warnings']
    assert result['full_text'].startswith('Opening paragraph')
    assert result['full_text'].endswith('Closing paragraph')
    for index in range(80):
        assert result['full_text'].count(f'Cell {index:03}') == 1
    assert validate_result(result) == result
    assert parsers.parse_bytes(data, 'large.docx') == result


@pytest.mark.parametrize('failure', ['entity', 'malformed', 'depth', 'nodes', 'text', 'bytes'])
def test_large_docx_remains_bounded_and_never_returns_partial_text(monkeypatch, failure):
    monkeypatch.setattr(parsers, 'MAX_XML_BYTES', 512)
    body = large_body()
    xml = None
    expected = 'limit_exceeded'
    if failure == 'entity':
        xml = '<!DOCTYPE document [<!ENTITY leak SYSTEM "file:///etc/passwd">]>' + (
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            f'<w:body>{body}<w:p><w:r><w:t>&leak;</w:t></w:r></w:p></w:body></w:document>'
        )
        expected = 'unsafe_xml'
    elif failure == 'malformed':
        xml = '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">' + body
        expected = 'malformed_xml'
    elif failure == 'depth':
        body = '<w:customXml>' * 300 + body + '</w:customXml>' * 300
    elif failure == 'nodes':
        monkeypatch.setattr(parsers, 'MAX_STREAM_XML_ELEMENTS', 100, raising=False)
    elif failure == 'text':
        monkeypatch.setattr(parsers, 'MAX_TEXT_BYTES', 32)
    else:
        monkeypatch.setattr(parsers, 'MAX_DOCUMENT_XML_BYTES', 1024, raising=False)
    result = parsers.parse_bytes(docx(body, {'word/document.xml': xml} if xml else None), 'large.docx')
    assert result['state'] == 'failed'
    assert result['error_code'] == expected
    assert result['full_text'] == '' and result['blocks'] == []


def test_old_doc_coalesces_wrapped_lines_without_losing_words(monkeypatch):
    raw = '\n\n'.join('\n'.join(f'Line {index} token' for index in range(start, min(start + 100, 5107)))
                       for start in range(0, 5107, 100))
    monkeypatch.setattr(parsers.subprocess, 'run', lambda *args, **kwargs:
                        SimpleNamespace(returncode=0, stdout=raw.encode(), stderr=b''))
    result = parsers.parse_bytes(bytes.fromhex('D0CF11E0A1B11AE1') + b'\0' * 64, 'legacy.doc')
    assert result['state'] == 'completed', result['error_code']
    assert len(result['blocks']) == 52
    assert result['full_text'].split() == raw.split()
    assert 'large_doc_text_coalesced' in result['warnings']
