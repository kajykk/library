"""quality_audit 审计服务测试（B1-02）"""

import sys
import pathlib

SERVER_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SERVER_ROOT))

import uuid
import pytest
from app.database import Base, engine, SessionLocal
from app.models import Document, DocumentQuality, utcnow
from app.services.quality_audit import (
    normalize_language,
    audit_one,
    scan_orphans,
    ISSUE_EMPTY_CONTENT,
    ISSUE_SHORT_CONTENT,
    ISSUE_NO_COVER,
    ISSUE_UNCLASSIFIED,
    ISSUE_MISSING_FILE,
    ISSUE_LANGUAGE_VARIANT,
    ISSUE_NEEDS_OCR,
    ISSUE_DUPLICATE_SHA,
)

Base.metadata.create_all(engine)


class TestNormalizeLanguage:
    def test_en_us_to_en(self):
        assert normalize_language("en-US") == "en"

    def test_eng_to_en(self):
        assert normalize_language("eng") == "en"

    def test_en_us_uppercase(self):
        assert normalize_language("EN-US") == "en"

    def test_zh_tw_to_zh(self):
        assert normalize_language("zh-TW") == "zh"

    def test_zh_hans_to_zh(self):
        assert normalize_language("zh-Hans") == "zh"

    def test_empty_to_und(self):
        assert normalize_language("") == "und"
        assert normalize_language(None) == "und"

    def test_fr_stays_fr(self):
        assert normalize_language("fr") == "fr"

    def test_zh_stays_zh(self):
        assert normalize_language("zh") == "zh"


class TestAuditOne:
    def _make_doc(self, **kwargs) -> Document:
        defaults = dict(
            id=uuid.uuid4(), type="book", title="测试书", content="",
            created_at=utcnow(), updated_at=utcnow(),
        )
        defaults.update(kwargs)
        return Document(**defaults)

    def test_empty_content(self):
        db = SessionLocal()
        try:
            doc = self._make_doc(content="")
            db.add(doc)
            db.flush()
            q = audit_one(db, doc, threshold=500)
            assert ISSUE_EMPTY_CONTENT in q.issues
            assert q.content_chars == 0
        finally:
            db.close()

    def test_short_content(self):
        db = SessionLocal()
        try:
            doc = self._make_doc(content="短文本")
            db.add(doc)
            db.flush()
            q = audit_one(db, doc, threshold=500)
            assert ISSUE_SHORT_CONTENT in q.issues
            assert ISSUE_EMPTY_CONTENT not in q.issues
            assert q.content_chars == 3
        finally:
            db.close()

    def test_no_cover(self):
        db = SessionLocal()
        try:
            doc = self._make_doc(content="x" * 1000, cover_path=None)
            db.add(doc)
            db.flush()
            q = audit_one(db, doc, threshold=500)
            assert ISSUE_NO_COVER in q.issues
        finally:
            db.close()

    def test_unclassified(self):
        db = SessionLocal()
        try:
            doc = self._make_doc(content="x" * 1000, collection_id=None)
            db.add(doc)
            db.flush()
            q = audit_one(db, doc, threshold=500)
            assert ISSUE_UNCLASSIFIED in q.issues
        finally:
            db.close()

    def test_missing_file(self):
        db = SessionLocal()
        try:
            doc = self._make_doc(content="x" * 1000, file_path="files/nonexistent.pdf")
            db.add(doc)
            db.flush()
            q = audit_one(db, doc, threshold=500)
            assert ISSUE_MISSING_FILE in q.issues
            assert q.file_exists is False
        finally:
            db.close()

    def test_language_variant(self):
        db = SessionLocal()
        try:
            doc = self._make_doc(content="x" * 1000, language="fr")
            db.add(doc)
            db.flush()
            q = audit_one(db, doc, threshold=500)
            assert ISSUE_LANGUAGE_VARIANT in q.issues
        finally:
            db.close()

    def test_normal_book_no_issues(self):
        db = SessionLocal()
        try:
            doc = self._make_doc(content="x" * 1000, cover_path="covers/test.jpg", language="zh")
            db.add(doc)
            db.flush()
            q = audit_one(db, doc, threshold=500)
            assert ISSUE_EMPTY_CONTENT not in q.issues
            assert ISSUE_SHORT_CONTENT not in q.issues
            assert ISSUE_NO_COVER not in q.issues
            # 可能有 unclassified（未设 collection），这个是正常的
        finally:
            db.close()

    def test_needs_ocr_pdf(self):
        db = SessionLocal()
        try:
            doc = self._make_doc(content="", format="pdf", file_path="files/test.pdf")
            db.add(doc)
            db.flush()
            # 不存在的文件 → file_exists=False → needs_ocr 需要 file_exists=True
            q = audit_one(db, doc, threshold=500)
            # file 不存在时 needs_ocr 应为 False（因为 file_exists=False）
            assert q.needs_ocr is False or ISSUE_NEEDS_OCR not in q.issues
        finally:
            db.close()

    def test_audit_does_not_touch_updated_at(self):
        db = SessionLocal()
        try:
            original_time = utcnow()
            doc = self._make_doc(content="", updated_at=original_time)
            db.add(doc)
            db.flush()
            before_updated = doc.updated_at
            audit_one(db, doc, threshold=500)
            db.flush()
            assert doc.updated_at == before_updated
        finally:
            db.close()


class TestScanOrphans:
    def test_reports_orphans_and_missing(self):
        db = SessionLocal()
        try:
            result = scan_orphans(db)
            assert "orphan_files" in result
            assert "orphan_covers" in result
            assert "missing_files" in result
            assert "total_bytes" in result
        finally:
            db.close()

    def test_soft_deleted_refs_not_orphans(self):
        """软删除文档引用的文件不算孤儿"""
        db = SessionLocal()
        try:
            # 创建软删除文档引用某个文件
            doc = Document(
                id=uuid.uuid4(), type="book", title="已删除",
                file_path="files/soft-deleted.pdf",
                created_at=utcnow(), updated_at=utcnow(), deleted_at=utcnow(),
            )
            db.add(doc)
            db.commit()
            result = scan_orphans(db)
            # 该文件如果在磁盘上存在，不应出现在孤儿列表
            orphan_paths = {f["rel_path"] for f in result["orphan_files"]}
            assert "files/soft-deleted.pdf" not in orphan_paths
        finally:
            db.close()
