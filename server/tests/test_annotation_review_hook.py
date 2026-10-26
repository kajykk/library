"""B4-05 标注创建挂钩：复习行自动创建、排除书签/软删文档、失败隔离。"""

import uuid

import pytest

from app.database import SessionLocal
from app.models import Annotation, AnnotationReview, Document, utcnow
from app.routers import annotations as annotations_router
from app.services.review_service import ensure_review_row


def _create_book(title: str = "复习挂钩测试书") -> uuid.UUID:
    with SessionLocal() as db:
        doc = Document(type="book", title=title, author="作者", format="epub")
        db.add(doc)
        db.commit()
        return doc.id


def _review_count(annotation_id: uuid.UUID) -> int:
    with SessionLocal() as db:
        return (
            db.query(AnnotationReview)
            .filter(AnnotationReview.annotation_id == annotation_id)
            .count()
        )


def _create_annotation(document_id: uuid.UUID, ann_type: str = "highlight") -> uuid.UUID:
    with SessionLocal() as db:
        ann = Annotation(
            document_id=document_id,
            type=ann_type,
            anchor={"chapter": 1, "page": 2},
            quote="测试原文",
        )
        db.add(ann)
        db.commit()
        return ann.id


def test_highlight_creates_due_card_immediately(client, auth_headers):
    doc_id = _create_book()
    resp = client.post(
        "/api/annotations",
        json={
            "document_id": str(doc_id),
            "type": "highlight",
            "anchor": {"chapter": 1, "page": 3},
            "quote": "被划线的原文",
            "content": "我的批注",
        },
        headers=auth_headers,
    )
    assert resp.status_code == 201
    ann_id = uuid.UUID(resp.json()["id"])
    assert _review_count(ann_id) == 1

    due = client.get(
        f"/api/reviews/due?document_id={doc_id}", headers=auth_headers
    )
    assert due.status_code == 200
    payload = due.json()
    assert payload["total"] == 1
    assert payload["cards"][0]["annotation_id"] == str(ann_id)
    assert payload["cards"][0]["interval_days"] == 0


def test_bookmark_does_not_create_review_row(client, auth_headers):
    doc_id = _create_book()
    resp = client.post(
        "/api/annotations",
        json={
            "document_id": str(doc_id),
            "type": "bookmark",
            "anchor": {"page": 5},
            "content": "书签",
        },
        headers=auth_headers,
    )
    assert resp.status_code == 201
    assert _review_count(uuid.UUID(resp.json()["id"])) == 0


def test_ensure_review_row_is_idempotent():
    doc_id = _create_book()
    ann_id = _create_annotation(doc_id)
    with SessionLocal() as db:
        ann = db.get(Annotation, ann_id)
        first = ensure_review_row(db, ann)
        db.commit()
        second = ensure_review_row(db, ann)
        assert first is not None and second is not None
        assert first.annotation_id == second.annotation_id
    assert _review_count(ann_id) == 1


def test_soft_deleted_document_does_not_create_row():
    doc_id = _create_book()
    ann_id = _create_annotation(doc_id)
    with SessionLocal() as db:
        doc = db.get(Document, doc_id)
        doc.deleted_at = utcnow()
        db.commit()
        ann = db.get(Annotation, ann_id)
        assert ensure_review_row(db, ann) is None
        db.commit()
    assert _review_count(ann_id) == 0


def test_hook_failure_does_not_break_annotation(client, auth_headers, monkeypatch):
    doc_id = _create_book()

    def boom(*_args, **_kwargs):
        raise RuntimeError("模拟复习行写入失败")

    monkeypatch.setattr(annotations_router, "ensure_review_row", boom)
    resp = client.post(
        "/api/annotations",
        json={
            "document_id": str(doc_id),
            "type": "highlight",
            "anchor": {"chapter": 2, "page": 9},
            "quote": "挂钩失败也要保存",
        },
        headers=auth_headers,
    )
    assert resp.status_code == 201
    ann_id = uuid.UUID(resp.json()["id"])
    assert _review_count(ann_id) == 0

    due = client.get(
        f"/api/reviews/due?document_id={doc_id}", headers=auth_headers
    )
    assert due.status_code == 200
    assert due.json()["total"] == 0


def test_delete_annotation_cascades_review_row(client, auth_headers):
    doc_id = _create_book()
    resp = client.post(
        "/api/annotations",
        json={
            "document_id": str(doc_id),
            "type": "note",
            "anchor": {"page": 1},
            "content": "级联测试",
        },
        headers=auth_headers,
    )
    assert resp.status_code == 201
    ann_id = uuid.UUID(resp.json()["id"])
    assert _review_count(ann_id) == 1

    assert client.delete(f"/api/annotations/{ann_id}", headers=auth_headers).status_code == 204
    assert _review_count(ann_id) == 0


@pytest.mark.parametrize("ann_type", ["bookmark", "highlight", "note"])
def test_reviewable_types_only(client, auth_headers, ann_type):
    doc_id = _create_book()
    resp = client.post(
        "/api/annotations",
        json={
            "document_id": str(doc_id),
            "type": ann_type,
            "anchor": {"page": 4},
            "content": ann_type,
        },
        headers=auth_headers,
    )
    assert resp.status_code == 201
    assert _review_count(uuid.UUID(resp.json()["id"])) == (0 if ann_type == "bookmark" else 1)
