from docx import Document

from work_with_word_docs import WordDocumentHandler


def _create_docx(path, text):
    document = Document()
    document.add_paragraph(text)
    document.save(path)


def test_list_files_uses_explicit_directory_and_filters_word_documents(tmp_path):
    documents_dir = tmp_path / "documents"
    documents_dir.mkdir()
    _create_docx(documents_dir / "letter.docx", "Letter")
    (documents_dir / "legacy.doc").write_bytes(b"legacy")
    (documents_dir / "notes.txt").write_text("ignore", encoding="utf-8")

    files = WordDocumentHandler().list_files(str(documents_dir))

    assert set(files) == {"letter.docx", "legacy.doc"}


def test_extract_text_reads_docx_from_full_path(tmp_path):
    document_path = tmp_path / "example.docx"
    _create_docx(document_path, "Document body")

    text = WordDocumentHandler().extract_text(str(document_path))

    assert text == "Document body"


def test_prepare_preview_uses_filename_and_truncates_to_requested_length(tmp_path):
    document_path = tmp_path / "meeting_notes.docx"
    _create_docx(document_path, "abcdefghij")

    title, preview = WordDocumentHandler().prepare_preview(str(document_path), max_chars=5)

    assert title == "Meeting notes"
    assert preview == "abcde..."
