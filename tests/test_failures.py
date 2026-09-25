import tempfile
import unittest
from pathlib import Path

from app import build_service
from src.domain import Actor, Conflict, PermissionDenied, ValidationError


CREATE_DATA = {'student_id': 'S-100', 'disability': 'hearing', 'service_minutes': 600, 'delivered_minutes': 120, 'review_due_days': 15, 'goals_count': 4, 'consent': False}
FLOW = [('consent', 'parent_rep', {'guardian_confirmed': True, 'consent_scope': '个别化服务'}, 'consented'), ('activate', 'case_manager', {}, 'active'), ('log_service', 'specialist', {'request_id': 'REQ-1', 'session_minutes': 60, 'provider': 'SP-3'}, 'active'), ('review', 'administrator', {'progress_note': '阶段复盘', 'next_review_date': '2099-01-01'}, 'under_review'), ('amend', 'case_manager', {'amendment_reason': '调整目标', 'updated_goals': ['目标A', '目标B']}, 'active'), ('close', 'administrator', {'review_complete': True}, 'closed')]


class FailureTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.service = build_service(str(Path(self.temp.name) / "test.db"))

    def tearDown(self):
        self.temp.cleanup()

    def _create_active(self):
        record = self.service.create(Actor("creator", "case_manager"), "IEP-28001", CREATE_DATA)
        record = self.service.act(Actor("operator", "parent_rep"), record["id"], record["version"], "consent", FLOW[0][2])
        return self.service.act(Actor("operator", "case_manager"), record["id"], record["version"], "activate", {})

    def test_permission_and_duplicate(self):
        with self.assertRaises(PermissionDenied):
            self.service.create(Actor("outsider", "outsider"), "IEP-28001", CREATE_DATA)
        self.service.create(Actor("creator", "case_manager"), "IEP-28001", CREATE_DATA)
        with self.assertRaises(Conflict):
            self.service.create(Actor("creator", "case_manager"), "IEP-28001", CREATE_DATA)

    def test_stale_version_is_rejected(self):
        record = self.service.create(Actor("creator", "case_manager"), "IEP-28001", CREATE_DATA)
        first = FLOW[0]
        record = self.service.act(Actor("operator", first[1]), record["id"], record["version"], first[0], first[2])
        second = FLOW[1]
        with self.assertRaises(Conflict):
            self.service.act(Actor("operator", second[1]), record["id"], record["version"] - 1, second[0], second[2])

    def test_backfill_without_request_id_rejected(self):
        record = self._create_active()
        with self.assertRaises(ValidationError):
            self.service.act(Actor("operator", "specialist"), record["id"], record["version"], "log_service", {'session_minutes': 60, 'provider': 'SP-3'})

    def test_over_limit_backfill_keeps_record_and_reports_remaining(self):
        record = self._create_active()
        data = {'request_id': 'REQ-X', 'session_minutes': 481, 'provider': 'SP-3'}
        with self.assertRaises(ValidationError) as ctx:
            self.service.act(Actor("operator", "specialist"), record["id"], record["version"], "log_service", data)
        self.assertIn("480", str(ctx.exception))
        fresh = self.service.get_record(Actor("creator", "case_manager"), record["id"])
        # 当前记录原样保留：分钟不增加，版本不前进
        self.assertEqual(fresh["payload"]["delivered_minutes"], 120)
        self.assertEqual(fresh["version"], record["version"])
        # 被拒绝的编号未登记，同编号再次提交仍会正常尝试（仍超限）
        with self.assertRaises(ValidationError):
            self.service.act(Actor("operator", "specialist"), record["id"], record["version"], "log_service", data)

    def test_review_without_or_with_invalid_date_rejected(self):
        record = self._create_active()
        with self.assertRaises(ValidationError):
            self.service.act(Actor("operator", "administrator"), record["id"], record["version"], "review", {'progress_note': '复盘'})
        with self.assertRaises(ValidationError):
            self.service.act(Actor("operator", "administrator"), record["id"], record["version"], "review", {'progress_note': '复盘', 'next_review_date': '待定'})
        fresh = self.service.get_record(Actor("creator", "case_manager"), record["id"])
        self.assertEqual(fresh["state"], "active")
        self.assertNotIn("next_review_date", fresh["payload"])
