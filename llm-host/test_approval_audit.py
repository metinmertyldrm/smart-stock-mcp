import os
import tempfile
import unittest
from types import SimpleNamespace

from approval_audit import DraftApprovalAuditStore


class DraftApprovalAuditStoreTest(unittest.TestCase):
    def setUp(self):
        handle, self.path = tempfile.mkstemp(prefix="smart-stock-draft-audit-", suffix=".db")
        os.close(handle)
        self.store = DraftApprovalAuditStore(self.path)

    def tearDown(self):
        try:
            os.remove(self.path)
        except FileNotFoundError:
            pass

    def test_creator_and_approver_are_persisted_without_being_overwritten(self):
        creator = SimpleNamespace(id="operator-1", username="operator", role="OPERATOR")
        other_creator = SimpleNamespace(id="operator-2", username="other", role="OPERATOR")
        approver = SimpleNamespace(id="manager-1", username="manager", role="MANAGER")
        other_approver = SimpleNamespace(id="admin-1", username="admin", role="ADMIN")

        self.store.record_created(42, creator)
        self.store.record_created(42, other_creator)
        self.store.record_approved(42, approver, 99)
        audit = self.store.record_approved(42, other_approver, 100)

        self.assertEqual(audit["createdBy"]["userId"], "operator-1")
        self.assertEqual(audit["approvedBy"]["userId"], "manager-1")
        self.assertEqual(audit["orderId"], 99)

    def test_legacy_draft_can_record_approver_without_known_creator(self):
        approver = SimpleNamespace(id="manager-1", username="manager", role="MANAGER")

        audit = self.store.record_approved(7, approver, 12)

        self.assertIsNone(audit["createdBy"])
        self.assertEqual(audit["approvedBy"]["role"], "MANAGER")
        self.assertEqual(audit["orderId"], 12)


if __name__ == "__main__":
    unittest.main()
