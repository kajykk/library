"""DocumentQuality 模型与迁移测试（B1-01）"""

import sys
import pathlib

SERVER_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SERVER_ROOT))

import uuid
import pytest
from sqlalchemy import inspect

from app.database import Base, engine, SessionLocal
from app.models import Document, DocumentQuality, utcnow

# 测试库使用 Base.metadata.create_all（conftest 设置了 KB_AUTO_CREATE_TABLES=1，
# 但直接测模型时不会触发 app 启动建表）
Base.metadata.create_all(engine)


class TestDocumentQualityModel:
    def test_table_exists(self):
        inspector = inspect(engine)
        assert "document_quality" in inspector.get_table_names()

    def test_columns(self):
        inspector = inspect(engine)
        cols = {c["name"]: c for c in inspector.get_columns("document_quality")}
        assert "document_id" in cols
        assert "content_chars" in cols
        assert "file_exists" in cols
        assert "cover_exists" in cols
        assert "needs_ocr" in cols
        assert "duplicate_group_size" in cols
        assert "issues" in cols
        assert "scan_error" in cols
        assert "checked_at" in cols

    def test_indexes(self):
        inspector = inspect(engine)
        idx_names = {i["name"] for i in inspector.get_indexes("document_quality")}
        assert "ix_document_quality_needs_ocr" in idx_names
        assert "ix_document_quality_duplicate_group_size" in idx_names
        assert "ix_document_quality_checked_at" in idx_names

    def test_cascade_delete(self):
        """删除文档会级联删除质量行"""
        db = SessionLocal()
        try:
            doc_id = uuid.uuid4()
            doc = Document(
                id=doc_id, type="book", title="测试书", content="",
                created_at=utcnow(), updated_at=utcnow(),
            )
            db.add(doc)
            db.flush()
            quality = DocumentQuality(
                document_id=doc_id, content_chars=100,
                file_exists=True, cover_exists=True,
                needs_ocr=False, duplicate_group_size=0,
                issues=[], checked_at=utcnow(),
            )
            db.add(quality)
            db.commit()
            # 确认质量行存在
            assert db.query(DocumentQuality).filter_by(document_id=doc_id).first() is not None
            # 删除文档
            db.delete(doc)
            db.commit()
            # 质量行应级联删除
            assert db.query(DocumentQuality).filter_by(document_id=doc_id).first() is None
        finally:
            db.close()


class TestQualityFilter:
    """B1-04: apply_quality_filter 的列表/计数一致性"""

    def test_empty_content_filter_finds_unaudited(self):
        """未审计但正文为空的书能被 empty_content 筛出"""
        db = SessionLocal()
        try:
            doc = Document(
                id=uuid.uuid4(), type="book", title="空正文书", content="",
                created_at=utcnow(), updated_at=utcnow(),
            )
            db.add(doc)
            db.commit()

            from app.routers.documents.filters import apply_quality_filter
            from sqlalchemy import select, func as sa_func

            stmt = select(Document).where(Document.deleted_at.is_(None))
            stmt = apply_quality_filter(stmt, db, "empty_content")
            results = db.execute(stmt).scalars().all()
            assert any(d.id == doc.id for d in results)

            # count 与 list 一致
            count_stmt = select(sa_func.count()).select_from(Document).where(Document.deleted_at.is_(None))
            count_stmt = apply_quality_filter(count_stmt, db, "empty_content")
            count = db.execute(count_stmt).scalar()
            assert count >= 1

            # 清理
            db.delete(doc)
            db.commit()
        finally:
            db.close()

    def test_invalid_quality_returns_400(self):
        from fastapi import HTTPException
        from app.routers.documents.filters import apply_quality_filter
        from sqlalchemy import select

        db = SessionLocal()
        try:
            stmt = select(Document)
            with pytest.raises(HTTPException) as exc_info:
                apply_quality_filter(stmt, db, "bogus_filter")
            assert exc_info.value.status_code == 400
        finally:
            db.close()

    def test_not_audited_filter(self):
        """not_audited 在有审计行后正确减少"""
        db = SessionLocal()
        try:
            from app.routers.documents.filters import apply_quality_filter
            from sqlalchemy import select, func as sa_func

            # 创建未审计文档
            doc = Document(
                id=uuid.uuid4(), type="book", title="未审计", content="x" * 1000,
                created_at=utcnow(), updated_at=utcnow(),
            )
            db.add(doc)
            db.commit()

            # 计数 not_audited 应包含它
            stmt = select(sa_func.count()).select_from(Document).where(Document.deleted_at.is_(None))
            stmt = apply_quality_filter(stmt, db, "not_audited")
            count_before = db.execute(stmt).scalar()

            # 审计它
            q = DocumentQuality(
                document_id=doc.id, content_chars=1000,
                file_exists=True, cover_exists=False,
                needs_ocr=False, duplicate_group_size=0,
                issues=["no_cover"], checked_at=utcnow(),
            )
            db.add(q)
            db.commit()

            # not_audited 计数应减少
            stmt2 = select(sa_func.count()).select_from(Document).where(Document.deleted_at.is_(None))
            stmt2 = apply_quality_filter(stmt2, db, "not_audited")
            count_after = db.execute(stmt2).scalar()
            assert count_after < count_before

            # 清理
            db.delete(q)
            db.delete(doc)
            db.commit()
        finally:
            db.close()
