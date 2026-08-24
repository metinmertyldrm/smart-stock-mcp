import os
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

from security import (CsrfError, SessionError, SessionStore, csrf_token_hash,
                      parse_bearer_token, parse_session_token,
                      session_token_hash)


class SessionTokenTest(unittest.TestCase):
    def test_bearer_scheme_is_required(self):
        with self.assertRaises(SessionError):
            parse_bearer_token(None)
        with self.assertRaises(SessionError):
            parse_bearer_token("Basic abcdefghijklmnopqrstuvwxyz0123456789")
        with self.assertRaises(SessionError):
            parse_bearer_token("Bearer short")

    def test_direct_session_token_is_validated_without_transport(self):
        token = "a" * 48
        self.assertEqual(parse_session_token(f"  {token}  "), token)
        with self.assertRaises(SessionError):
            parse_session_token(None)
        with self.assertRaises(SessionError):
            parse_session_token("short")

    def test_hash_is_deterministic_but_not_plaintext(self):
        token = "a" * 48
        digest = session_token_hash(token)
        self.assertEqual(digest, session_token_hash(token))
        self.assertNotIn(token, digest)
        self.assertEqual(len(digest), 64)


class SessionStoreTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.directory.name, "sessions.db")
        self.store = SessionStore(self.path, ttl_seconds=3600)

    def tearDown(self):
        self.directory.cleanup()

    def test_created_token_resolves_to_stable_owner(self):
        session = self.store.create()
        owner_a = self.store.owner_for_authorization(f"Bearer {session['token']}")
        owner_b = self.store.owner_for_authorization(f"Bearer {session['token']}")

        self.assertEqual(owner_a, owner_b)
        self.assertTrue(session["expiresAt"])

    def test_direct_token_and_bearer_resolve_same_principal(self):
        session = self.store.create(user_id="user-123")
        direct = self.store.principal_for_token(session["token"])
        bearer = self.store.principal_for_authorization(f"Bearer {session['token']}")
        self.assertEqual(direct, bearer)
        self.assertEqual(direct.conversation_owner_id, "user-123")

    def test_user_bound_session_resolves_to_stable_user_owner(self):
        session_a = self.store.create(user_id="user-123")
        session_b = self.store.create(user_id="user-123")
        principal_a = self.store.principal_for_authorization(f"Bearer {session_a['token']}")
        principal_b = self.store.principal_for_authorization(f"Bearer {session_b['token']}")

        self.assertNotEqual(principal_a.owner_id, principal_b.owner_id)
        self.assertEqual(principal_a.user_id, "user-123")
        self.assertEqual(principal_b.user_id, "user-123")
        self.assertEqual(principal_a.conversation_owner_id, "user-123")
        self.assertEqual(self.store.owner_for_authorization(f"Bearer {session_b['token']}"), "user-123")

    def test_csrf_enabled_session_validates_only_matching_value(self):
        session = self.store.create(user_id="user-123", with_csrf=True)
        self.store.validate_csrf(session["token"], session["csrfToken"])
        with self.assertRaises(CsrfError):
            self.store.validate_csrf(session["token"], "x" * 32)
        with self.assertRaises(CsrfError):
            self.store.validate_csrf(session["token"], None)

    def test_non_csrf_session_cannot_pass_csrf_validation(self):
        session = self.store.create()
        with self.assertRaises(CsrfError):
            self.store.validate_csrf(session["token"], "x" * 32)

    def test_revoke_token_invalidates_cookie_style_credential(self):
        session = self.store.create(user_id="user-a", with_csrf=True)
        self.store.revoke_token(session["token"])
        with self.assertRaisesRegex(SessionError, "Unknown session"):
            self.store.principal_for_token(session["token"])

    def test_revoke_user_invalidates_all_user_sessions_only(self):
        user_a = self.store.create(user_id="user-a")
        user_b = self.store.create(user_id="user-a")
        anonymous = self.store.create()
        self.assertEqual(self.store.revoke_user("user-a"), 2)
        for session in (user_a, user_b):
            with self.assertRaisesRegex(SessionError, "Unknown session"):
                self.store.owner_for_authorization(f"Bearer {session['token']}")
        self.assertTrue(self.store.owner_for_authorization(f"Bearer {anonymous['token']}"))

    def test_legacy_session_schema_is_migrated_in_place(self):
        legacy_path = os.path.join(self.directory.name, "legacy.db")
        with sqlite3.connect(legacy_path) as db:
            db.executescript(
                """
                CREATE TABLE sessions (
                  token_hash TEXT PRIMARY KEY,
                  owner_id TEXT NOT NULL UNIQUE,
                  created_at TEXT NOT NULL,
                  expires_at TEXT NOT NULL,
                  last_seen_at TEXT NOT NULL
                );
                """
            )
        SessionStore(legacy_path, ttl_seconds=3600)
        with sqlite3.connect(legacy_path) as db:
            columns = {row[1] for row in db.execute("PRAGMA table_info(sessions)").fetchall()}
        self.assertIn("user_id", columns)
        self.assertIn("csrf_hash", columns)

    def test_plaintext_session_and_csrf_tokens_are_never_persisted(self):
        session = self.store.create(user_id="user-123", with_csrf=True)
        token = session["token"]
        csrf = session["csrfToken"]
        with sqlite3.connect(self.path) as db:
            row = db.execute("SELECT token_hash,csrf_hash FROM sessions").fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row[0], session_token_hash(token))
        self.assertEqual(row[1], csrf_token_hash(csrf))
        self.assertNotEqual(row[0], token)
        self.assertNotEqual(row[1], csrf)
        with open(self.path, "rb") as handle:
            raw = handle.read()
        self.assertNotIn(token.encode("utf-8"), raw)
        self.assertNotIn(csrf.encode("utf-8"), raw)

    def test_unknown_token_is_rejected(self):
        self.store.create()
        with self.assertRaisesRegex(SessionError, "Unknown session"):
            self.store.owner_for_authorization(f"Bearer {'z' * 48}")

    def test_expired_token_is_deleted_and_rejected(self):
        session = self.store.create()
        digest = session_token_hash(session["token"])
        expired = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
        with sqlite3.connect(self.path) as db:
            db.execute("UPDATE sessions SET expires_at=? WHERE token_hash=?", (expired, digest))

        with self.assertRaisesRegex(SessionError, "Session expired"):
            self.store.owner_for_authorization(f"Bearer {session['token']}")

        with sqlite3.connect(self.path) as db:
            count = db.execute("SELECT COUNT(*) FROM sessions WHERE token_hash=?", (digest,)).fetchone()[0]
        self.assertEqual(count, 0)


if __name__ == "__main__":
    unittest.main()
