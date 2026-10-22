"""BUG-RAG-18: metadata.file_type заполняется для txt/docx/pptx/excel/rtf.

Парсер для всех форматов кроме pdf и изображений кладёт тип только в
``parsed["confidence_info"]["file_type"]``. Сервисы берут его через
``document_parser.parsed_file_type`` (верхний уровень → confidence_info), поэтому
в metadata документов (kb / project / memory) и в списке документов проекта
``file_type`` больше не пустой.
"""

import pytest

from app.services.document_parser import parsed_file_type

from .fakes import CATS

pytestmark = pytest.mark.unit


async def _kb_meta(kb):
    res = await kb.index_document(CATS, "cats.txt")
    return kb.doc_repo.docs[res["document_id"]].metadata


async def _memory_meta(memory):
    res = await memory.index_document(CATS, "cats.txt")
    return memory.doc_repo.docs[res["document_id"]].metadata


async def _project_meta(project):
    res = await project.index_document(CATS, "cats.txt", "p1")
    return project.doc_repo.rows[res["document_id"]]["metadata"]


@pytest.mark.parametrize("fixture, getter", [("kb", _kb_meta), ("memory", _memory_meta), ("project", _project_meta)])
class TestFileTypeInMetadata:
    async def test_txt_upload_records_file_type(self, request, fixture, getter):
        meta = await getter(request.getfixturevalue(fixture))
        assert meta["file_type"] == "txt"


async def test_project_list_shows_file_type(project):
    await project.index_document(CATS, "cats.txt", "p1")
    assert (await project.list_documents("p1"))[0]["file_type"] == "txt"


class TestParsedFileType:
    def test_top_level_wins(self):
        assert parsed_file_type({"file_type": "pdf", "confidence_info": {"file_type": "x"}}) == "pdf"

    def test_falls_back_to_confidence_info(self):
        assert parsed_file_type({"confidence_info": {"file_type": "docx"}}) == "docx"

    @pytest.mark.parametrize("parsed", [None, {}, {"confidence_info": None}, {"confidence_info": {}}, "x"])
    def test_missing_gives_empty_string(self, parsed):
        assert parsed_file_type(parsed) == ""
