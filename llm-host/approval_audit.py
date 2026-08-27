"""Persistent audit records for cross-user purchase draft approvals.

Purchase drafts live in the central stock-service database, while authenticated
user identities live in the LLM host.  This small SQLite store links both sides
without duplicating or weakening the stock-service transaction boundary.
"""
from __future__ import annotations

import os
from contextlib import contextmanager
import sqlite3
from datetime import datetime, timezone


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


class DraftApprovalAuditStore:
    """Record the authenticated creator and first successful approver of a draft."""

    def __init__(self, path: str):
        self.path = path
        parent = os.path.dirname(os.path.abspath(path))
        if parent:
            os.makedirs(parent, exist_ok=True)
        with self._connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS draft_approval_audit (
                  draft_id INTEGER PRIMARY KEY,
                  created_by_user_id TEXT,
                  created_by_username TEXT,
                  created_by_role TEXT,
                  creator_recorded_at TEXT,
                  approved_by_user_id TEXT,
                  approved_by_username TEXT,
                  approved_by_role TEXT,
                  approved_at TEXT,
                  order_id INTEGER
                );
                CREATE INDEX IF NOT EXISTS draft_approval_audit_order
                  ON draft_approval_audit(order_id);
                """
            )

    @contextmanager
    def _connect(self):
        """Her islem icin baglanti acar ve KAPATIR.

        `with sqlite3.connect(...) as db` yalnizca islemi (commit/rollback)
        yonetir, baglantiyi kapatmaz. Kapanmayan baglanti dosya kilidini
        tutuyor; Windows'ta veritabani dosyasi silinemiyor (WinError 32) ve
        es zamanli istekte "database is locked" riski doguyor.
        """
        connection = sqlite3.connect(self.path, timeout=5)
        connection.row_factory = sqlite3.Row
        try:
            with connection:          # islem siniri: commit ya da rollback
                yield connection
        finally:
            connection.close()        # dosya kilidi birakilir

    def record_created(self, draft_id: int, identity) -> dict:
        timestamp = now()
        with self._connect() as db:
            db.execute(
                """
                INSERT INTO draft_approval_audit(
                  draft_id, created_by_user_id, created_by_username,
                  created_by_role, creator_recorded_at
                ) VALUES(?,?,?,?,?)
                ON CONFLICT(draft_id) DO UPDATE SET
                  created_by_user_id=COALESCE(
                    draft_approval_audit.created_by_user_id,
                    excluded.created_by_user_id
                  ),
                  created_by_username=COALESCE(
                    draft_approval_audit.created_by_username,
                    excluded.created_by_username
                  ),
                  created_by_role=COALESCE(
                    draft_approval_audit.created_by_role,
                    excluded.created_by_role
                  ),
                  creator_recorded_at=COALESCE(
                    draft_approval_audit.creator_recorded_at,
                    excluded.creator_recorded_at
                  )
                """,
                (
                    int(draft_id),
                    identity.id,
                    identity.username,
                    identity.role,
                    timestamp,
                ),
            )
        return self.get(draft_id)

    def record_approved(self, draft_id: int, identity, order_id: int | None) -> dict:
        timestamp = now()
        with self._connect() as db:
            db.execute(
                """
                INSERT INTO draft_approval_audit(
                  draft_id, approved_by_user_id, approved_by_username,
                  approved_by_role, approved_at, order_id
                ) VALUES(?,?,?,?,?,?)
                ON CONFLICT(draft_id) DO UPDATE SET
                  approved_by_user_id=COALESCE(
                    draft_approval_audit.approved_by_user_id,
                    excluded.approved_by_user_id
                  ),
                  approved_by_username=COALESCE(
                    draft_approval_audit.approved_by_username,
                    excluded.approved_by_username
                  ),
                  approved_by_role=COALESCE(
                    draft_approval_audit.approved_by_role,
                    excluded.approved_by_role
                  ),
                  approved_at=COALESCE(
                    draft_approval_audit.approved_at,
                    excluded.approved_at
                  ),
                  order_id=COALESCE(
                    draft_approval_audit.order_id,
                    excluded.order_id
                  )
                """,
                (
                    int(draft_id),
                    identity.id,
                    identity.username,
                    identity.role,
                    timestamp,
                    int(order_id) if order_id is not None else None,
                ),
            )
        return self.get(draft_id)

    def get(self, draft_id: int) -> dict:
        with self._connect() as db:
            row = db.execute(
                "SELECT * FROM draft_approval_audit WHERE draft_id=?",
                (int(draft_id),),
            ).fetchone()
        if row is None:
            return {"draftId": int(draft_id), "createdBy": None, "approvedBy": None}
        return {
            "draftId": int(row["draft_id"]),
            "createdBy": self._actor(row, "created_by"),
            "creatorRecordedAt": row["creator_recorded_at"],
            "approvedBy": self._actor(row, "approved_by"),
            "approvedAt": row["approved_at"],
            "orderId": row["order_id"],
        }

    @staticmethod
    def _actor(row: sqlite3.Row, prefix: str) -> dict | None:
        user_id = row[f"{prefix}_user_id"]
        if user_id is None:
            return None
        return {
            "userId": str(user_id),
            "username": str(row[f"{prefix}_username"]),
            "role": str(row[f"{prefix}_role"]),
        }
