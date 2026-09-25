import unittest
from datetime import date

from src.domain import Actor, ValidationError
from src.rules import DomainRules, parse_review_date


CREATE_DATA = {'student_id': 'S-100', 'disability': 'hearing', 'service_minutes': 600, 'delivered_minutes': 120, 'review_due_days': 15, 'goals_count': 4, 'consent': False}
FLOW = [('consent', 'parent_rep', {'guardian_confirmed': True, 'consent_scope': '个别化服务'}, 'consented'), ('activate', 'case_manager', {}, 'active'), ('log_service', 'specialist', {'request_id': 'REQ-1', 'session_minutes': 60, 'provider': 'SP-3'}, 'active'), ('review', 'administrator', {'progress_note': '阶段复盘', 'next_review_date': '2099-01-01'}, 'under_review'), ('amend', 'case_manager', {'amendment_reason': '调整目标', 'updated_goals': ['目标A', '目标B']}, 'active'), ('close', 'administrator', {'review_complete': True}, 'closed')]


class RulesTest(unittest.TestCase):
    def setUp(self):
        self.rules = DomainRules()

    def test_prepare_create(self):
        prepared = self.rules.prepare_create(CREATE_DATA)
        self.assertEqual(prepared["missing_minutes"], 480)
        self.assertEqual(prepared["compliance_rate"], 20.0)
        # 尚未设置下次复查日期的计划算逾期
        self.assertTrue(self.rules.is_review_overdue(prepared))

    def test_action_calculation(self):
        action, role, data, expected_state = FLOW[0]
        record = {"id": 1, "state": self.rules.INITIAL_STATE, "payload": self.rules.prepare_create(CREATE_DATA)}
        state, payload, summary = self.rules.apply_action(record, action, data)
        self.assertEqual(state, expected_state)
        self.assertTrue(payload["consent"])

    def test_invalid_input(self):
        invalid = dict(CREATE_DATA)
        invalid["goals_count"] = 0
        with self.assertRaises(ValidationError):
            self.rules.prepare_create(invalid)

    def test_log_service_requires_request_id(self):
        record = {"id": 1, "state": "active", "payload": self.rules.prepare_create(CREATE_DATA)}
        with self.assertRaises(ValidationError):
            self.rules.apply_action(record, "log_service", {"session_minutes": 60, "provider": "SP-3"})

    def test_log_service_over_limit_reports_remaining(self):
        record = {"id": 1, "state": "active", "payload": self.rules.prepare_create(CREATE_DATA)}
        data = {"request_id": "REQ-2", "session_minutes": 481, "provider": "SP-3"}
        with self.assertRaises(ValidationError) as ctx:
            self.rules.apply_action(record, "log_service", data)
        # 已服务120/600，需说明还可登记480分钟，且payload原样保留
        self.assertIn("480", str(ctx.exception))
        self.assertEqual(record["payload"]["delivered_minutes"], 120)

    def test_review_requires_valid_next_review_date(self):
        record = {"id": 1, "state": "active", "payload": self.rules.prepare_create(CREATE_DATA)}
        with self.assertRaises(ValidationError):
            self.rules.apply_action(record, "review", {"progress_note": "复盘", "next_review_date": "下个月"})
        with self.assertRaises(ValidationError):
            self.rules.apply_action(record, "review", {"progress_note": "复盘"})

    def test_review_sets_next_review_date(self):
        record = {"id": 1, "state": "active", "payload": self.rules.prepare_create(CREATE_DATA)}
        _, payload, _ = self.rules.apply_action(record, "review", {"progress_note": "复盘", "next_review_date": "2099-01-01"})
        self.assertEqual(payload["next_review_date"], "2099-01-01")
        self.assertFalse(self.rules.is_review_overdue(payload, today=date(2026, 9, 25)))

    def test_overdue_determination(self):
        overdue = self.rules.is_review_overdue({"next_review_date": "2026-09-24"}, today=date(2026, 9, 25))
        on_due_date = self.rules.is_review_overdue({"next_review_date": "2026-09-25"}, today=date(2026, 9, 25))
        future = self.rules.is_review_overdue({"next_review_date": "2026-09-26"}, today=date(2026, 9, 25))
        self.assertTrue(overdue)
        self.assertFalse(on_due_date)
        self.assertFalse(future)
        # 未设置或无效日期都算逾期
        self.assertTrue(self.rules.is_review_overdue({}))
        self.assertTrue(self.rules.is_review_overdue({"next_review_date": ""}))
        self.assertTrue(self.rules.is_review_overdue({"next_review_date": "bad-date"}))
        self.assertIsNone(parse_review_date(None))
