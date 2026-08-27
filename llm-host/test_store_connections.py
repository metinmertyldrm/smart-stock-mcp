"""SQLite tabanlı store'lar her işlemden sonra bağlantıyı kapatmalı.

Regresyon: `with sqlite3.connect(...) as db:` yalnızca işlemi (commit/rollback)
yönetir, bağlantıyı KAPATMAZ. Kapanmayan bağlantı dosya kilidini tutuyordu;
Windows'ta geçici veritabanı silinemediği için 25 test tearDown'da
`PermissionError: [WinError 32]` veriyordu (27.08 koşumu, Python 3.14).

Bu testler işletim sisteminden bağımsızdır: gerçek `sqlite3.connect` sarılıp
açılan her bağlantı kaydediliyor, işlem bittikten sonra hepsinin kapalı olduğu
doğrulanıyor. Kapalı bağlantıya erişim `sqlite3.ProgrammingError` fırlatır.
"""
import ast
import io
import os
import sqlite3
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

from approval_audit import DraftApprovalAuditStore
from identity import IdentityStore, ROLE_OPERATOR
from security import SessionStore


class ConnectionTracker:
    """Store'un açtığı gerçek bağlantıları toplar."""

    def __init__(self):
        self.opened = []
        self._real_connect = sqlite3.connect

    def connect(self, *args, **kwargs):
        connection = self._real_connect(*args, **kwargs)
        self.opened.append(connection)
        return connection


class StoreConnectionHygieneTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)

    def path(self, name):
        return os.path.join(self.directory.name, name)

    def assert_all_closed(self, tracker):
        self.assertTrue(tracker.opened, "hiç bağlantı açılmadı; test bir şey ölçmüyor")
        for index, connection in enumerate(tracker.opened):
            with self.subTest(connection=index):
                # Kapalı bağlantı üzerinde çalışmak ProgrammingError verir.
                with self.assertRaises(sqlite3.ProgrammingError):
                    connection.execute("SELECT 1")

    def run_tracked(self, operation):
        tracker = ConnectionTracker()
        with mock.patch("sqlite3.connect", tracker.connect):
            operation()
        return tracker

    def test_draft_audit_store_closes_every_connection(self):
        store = DraftApprovalAuditStore(self.path("audit.db"))
        identity = SimpleNamespace(id="operator-1", username="operator", role="OPERATOR")

        tracker = self.run_tracked(lambda: store.record_created(1, identity))

        self.assert_all_closed(tracker)

    def test_identity_store_closes_every_connection(self):
        store = IdentityStore(self.path("identity.db"))

        tracker = self.run_tracked(lambda: store.create_user(
            "operator.one", "very-long-operator-password",
            display_name="Operatör", role=ROLE_OPERATOR))

        self.assert_all_closed(tracker)

    def test_session_store_closes_every_connection(self):
        store = SessionStore(self.path("sessions.db"), ttl_seconds=3600)

        tracker = self.run_tracked(store.create)

        self.assert_all_closed(tracker)

    def test_schema_setup_also_closes_its_connection(self):
        """Kurulum (__init__) da bağlantı açıyor; o da kapanmalı."""
        tracker = self.run_tracked(lambda: SessionStore(self.path("setup.db")))

        self.assert_all_closed(tracker)

    def test_failed_operation_still_closes_the_connection(self):
        """Hata yolunda kilidi bırakmamak, sonraki isteği 'database is locked' yapar."""
        store = IdentityStore(self.path("identity.db"))
        store.create_user("operator.one", "very-long-operator-password",
                          display_name="Operatör", role=ROLE_OPERATOR)

        def duplicate():
            try:
                store.create_user("operator.one", "very-long-operator-password",
                                  display_name="Operatör", role=ROLE_OPERATOR)
            except Exception:
                pass

        tracker = self.run_tracked(duplicate)

        self.assert_all_closed(tracker)

    def test_database_file_can_be_removed_after_use(self):
        """Windows'taki asıl belirti: açık tutulan dosya silinemiyordu."""
        path = self.path("removable.db")
        store = DraftApprovalAuditStore(path)
        store.record_created(1, SimpleNamespace(id="u", username="u", role="OPERATOR"))

        os.remove(path)

        self.assertFalse(os.path.exists(path))


class SqliteIdiomGuardTest(unittest.TestCase):
    """`with sqlite3.connect(...)` deyimi kaynak dosyalarda hiç kalmamalı.

    Aynı hata iki kez yapıldı: önce store'larda (25 tearDown hatası), sonra
    testlerin kendi doğrulama bağlantılarında (4 hata). Kural okunarak değil,
    ölçülerek korunmalı — bu yüzden AST üzerinden bakıyoruz.

    İzin verilen kullanımlar:
      * closing(sqlite3.connect(...))       -> kapanışı garanti eder
      * self.db = sqlite3.connect(...)      -> uzun ömürlü, close() metodu var
    """

    HERE = os.path.dirname(os.path.abspath(__file__))

    @staticmethod
    def is_sqlite_connect(node):
        return (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "connect"
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "sqlite3")

    def offenders(self, tree):
        found = []
        for node in ast.walk(tree):
            if not isinstance(node, (ast.With, ast.AsyncWith)):
                continue
            for item in node.items:
                if self.is_sqlite_connect(item.context_expr):
                    found.append(node.lineno)
        return found

    def test_no_source_file_opens_sqlite_in_a_bare_with(self):
        problems = []
        for name in sorted(os.listdir(self.HERE)):
            if not name.endswith(".py"):
                continue
            source = io.open(os.path.join(self.HERE, name), encoding="utf-8").read()
            for line in self.offenders(ast.parse(source)):
                problems.append(f"{name}:{line}")

        self.assertEqual(
            problems, [],
            "Bu satırlar bağlantıyı kapatmıyor; closing(sqlite3.connect(...)) kullan: "
            + ", ".join(problems))

    def test_guard_detects_the_bare_form(self):
        """Koruma ters yönde de çalışmalı, yoksa sessizce hep geçer."""
        tree = ast.parse("import sqlite3\nwith sqlite3.connect('x') as db:\n    db.execute('SELECT 1')\n")

        self.assertEqual(self.offenders(tree), [2])

    def test_guard_accepts_the_closing_form(self):
        tree = ast.parse("import sqlite3\nfrom contextlib import closing\n"
                         "with closing(sqlite3.connect('x')) as db, db:\n    db.execute('SELECT 1')\n")

        self.assertEqual(self.offenders(tree), [])


if __name__ == "__main__":
    unittest.main()
