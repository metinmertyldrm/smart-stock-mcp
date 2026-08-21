import os
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

from security import (SessionError, SessionStore, parse_bearer_token,
                      session_token_hash)


class SessionTokenTest(unittest.TestCase):
    def test_bearer_scheme_is_required(self):
        with self.assertRaises(SessionError):
            parse_bearer_token(None)
        with self.assertRaises(SessionError):
            parse_bearer_token("Basic abcdefghijklmnopqrstuvwxyz0123456789")
        with self.assertRaises(SessionError):
            parse_bearer_token("Bearer short")

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

    def test_plaintext_token_is_never_persisted(self):
        session = self.store.create()
        token = session["token"]
        with sqlite3.connect(self.path) as db:
            row = db.execute("SELECT token_hash FROM sessions").fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row[0], session_token_hash(token))
        self.assertNotEqual(row[0], token)
        with open(self.path, "rb") as handle:
            self.assertNotIn(token.encode("utf-8"), handle.read())

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
