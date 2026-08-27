import os
import sqlite3
from contextlib import closing
import tempfile
import unittest

from identity import (
    IdentityError,
    IdentityStore,
    ROLE_ADMIN,
    ROLE_MANAGER,
    ROLE_OPERATOR,
    ROLE_VIEWER,
    hash_password,
    normalize_username,
    verify_password,
)


class PasswordPrimitiveTest(unittest.TestCase):
    def test_password_hash_is_salted_and_verifiable(self):
        salt_a, digest_a = hash_password("correct horse battery staple")
        salt_b, digest_b = hash_password("correct horse battery staple")
        self.assertNotEqual(salt_a, salt_b)
        self.assertNotEqual(digest_a, digest_b)
        self.assertTrue(verify_password("correct horse battery staple", salt_a, digest_a))
        self.assertFalse(verify_password("wrong password value", salt_a, digest_a))

    def test_username_is_normalized_and_bounded(self):
        self.assertEqual(normalize_username("  Alice.Admin  "), "alice.admin")
        with self.assertRaises(IdentityError):
            normalize_username("a")
        with self.assertRaises(IdentityError):
            normalize_username("spaces are not allowed")


class IdentityStoreTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.directory.name, "identity.db")
        self.store = IdentityStore(self.path)

    def tearDown(self):
        self.directory.cleanup()

    def test_user_is_stable_across_authentication(self):
        created = self.store.create_user(
            "operator.one",
            "very-long-operator-password",
            display_name="Operatör Bir",
            role=ROLE_OPERATOR,
        )
        authenticated = self.store.authenticate("OPERATOR.ONE", "very-long-operator-password")
        self.assertEqual(created.id, authenticated.id)
        self.assertEqual(authenticated.role, ROLE_OPERATOR)
        self.assertTrue(authenticated.has("draft"))
        self.assertFalse(authenticated.has("confirm"))

    def test_plaintext_password_is_not_persisted(self):
        password = "never-store-this-password"
        self.store.create_user("viewer.one", password, role=ROLE_VIEWER)
        with closing(sqlite3.connect(self.path)) as db, db:
            row = db.execute("SELECT password_salt,password_hash FROM users").fetchone()
        self.assertIsNotNone(row)
        self.assertNotIn(password, row)
        with open(self.path, "rb") as handle:
            self.assertNotIn(password.encode("utf-8"), handle.read())

    def test_duplicate_username_is_rejected_case_insensitively(self):
        self.store.create_user("manager.one", "manager-password-123", role=ROLE_MANAGER)
        with self.assertRaisesRegex(IdentityError, "zaten"):
            self.store.create_user("MANAGER.ONE", "another-password-123", role=ROLE_MANAGER)

    def test_disabled_user_cannot_authenticate(self):
        created = self.store.create_user("viewer.two", "viewer-password-123", role=ROLE_VIEWER)
        self.store.set_enabled(created.id, False)
        with self.assertRaisesRegex(IdentityError, "devre dışı"):
            self.store.authenticate("viewer.two", "viewer-password-123")

    def test_role_changes_are_persisted(self):
        created = self.store.create_user("operator.two", "operator-password-123", role=ROLE_OPERATOR)
        updated = self.store.set_role(created.id, ROLE_MANAGER)
        self.assertEqual(updated.role, ROLE_MANAGER)
        self.assertTrue(updated.has("confirm"))
        self.assertFalse(updated.has("users"))

    def test_bootstrap_admin_is_created_only_on_empty_store(self):
        admin = self.store.bootstrap_admin("admin", "bootstrap-admin-password")
        self.assertIsNotNone(admin)
        self.assertEqual(admin.role, ROLE_ADMIN)
        self.assertTrue(admin.has("users"))
        self.assertEqual(self.store.count_enabled_admins(), 1)
        self.assertIsNone(self.store.bootstrap_admin("other", "another-admin-password"))
        self.assertEqual(self.store.count_users(), 1)

    def test_bootstrap_requires_credentials_when_empty(self):
        with self.assertRaisesRegex(IdentityError, "İlk yönetici"):
            self.store.bootstrap_admin(None, None)

    def test_last_active_admin_cannot_be_demoted_or_disabled(self):
        admin = self.store.bootstrap_admin("admin", "bootstrap-admin-password")
        with self.assertRaisesRegex(IdentityError, "Son aktif yönetici rolü"):
            self.store.set_role(admin.id, ROLE_MANAGER)
        with self.assertRaisesRegex(IdentityError, "Son aktif yönetici devre dışı"):
            self.store.set_enabled(admin.id, False)
        self.assertEqual(self.store.get_user(admin.id).role, ROLE_ADMIN)
        self.assertTrue(self.store.get_user(admin.id).enabled)

    def test_one_admin_can_change_when_another_active_admin_remains(self):
        first = self.store.bootstrap_admin("admin.one", "bootstrap-admin-password")
        second = self.store.create_user("admin.two", "second-admin-password", role=ROLE_ADMIN)
        self.assertEqual(self.store.count_enabled_admins(), 2)
        demoted = self.store.set_role(first.id, ROLE_MANAGER)
        self.assertEqual(demoted.role, ROLE_MANAGER)
        self.assertEqual(self.store.count_enabled_admins(), 1)
        with self.assertRaisesRegex(IdentityError, "Son aktif yönetici devre dışı"):
            self.store.set_enabled(second.id, False)

    def test_one_of_two_active_admins_can_be_disabled(self):
        first = self.store.bootstrap_admin("admin.one", "bootstrap-admin-password")
        self.store.create_user("admin.two", "second-admin-password", role=ROLE_ADMIN)
        disabled = self.store.set_enabled(first.id, False)
        self.assertFalse(disabled.enabled)
        self.assertEqual(self.store.count_enabled_admins(), 1)


if __name__ == "__main__":
    unittest.main()
