import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from app import build_service
from src.domain import Actor, Conflict


CREATE_DATA = {'student_id': 'S-100', 'disability': 'hearing', 'service_minutes': 600, 'delivered_minutes': 120, 'review_due_days': 15, 'goals_count': 4, 'consent': False}
FLOW = [('consent', 'parent_rep', {'guardian_confirmed': True, 'consent_scope': '个别化服务'}, 'consented'), ('activate', 'case_manager', {}, 'active'), ('log_service', 'specialist', {'request_id': 'REQ-1', 'session_minutes': 60, 'provider': 'SP-3'}, 'active'), ('review', 'administrator', {'progress_note': '阶段复盘', 'next_review_date': '2099-01-01'}, 'under_review'), ('amend', 'case_manager', {'amendment_reason': '调整目标', 'updated_goals': ['目标A', '目标B']}, 'active'), ('close', 'administrator', {'review_complete': True}, 'closed')]


class WorkflowTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.service = build_service(str(Path(self.temp.name) / "test.db"))
        self.actor = Actor("creator", "case_manager")

    def tearDown(self):
        self.temp.cleanup()

    def _create_active(self):
        record = self.service.create(self.actor, "IEP-28001", CREATE_DATA)
        record = self.service.act(Actor("operator", "parent_rep"), record["id"], record["version"], "consent", FLOW[0][2])
        record = self.service.act(Actor("operator", "case_manager"), record["id"], record["version"], "activate", {})
        return record

    def test_complete_workflow_and_audit(self):
        record = self.service.create(self.actor, "IEP-28001", CREATE_DATA)
        self.assertEqual(record["state"], "draft")
        for action, role, data, expected_state in FLOW:
            record = self.service.act(Actor("operator", role), record["id"], record["version"], action, data)
            self.assertEqual(record["state"], expected_state)
        timeline = self.service.timeline(self.actor, record["id"])
        self.assertEqual(len(timeline), len(FLOW) + 1)
        self.assertEqual(timeline[-1]["action"], FLOW[-1][0])

    def test_log_service_retry_is_idempotent(self):
        record = self._create_active()
        version = record["version"]
        data = {'request_id': 'REQ-9', 'session_minutes': 60, 'provider': 'SP-3'}
        first = self.service.act(Actor("operator", "specialist"), record["id"], version, "log_service", data)
        self.assertEqual(first["payload"]["delivered_minutes"], 180)
        # 网络重试：同一编号、同样的旧版本，直接返回首次结果
        second = self.service.act(Actor("operator", "specialist"), record["id"], version, "log_service", data)
        self.assertEqual(first, second)
        self.assertEqual(second["payload"]["delivered_minutes"], 180)
        self.assertEqual(second["version"], first["version"])
        timeline = self.service.timeline(self.actor, record["id"])
        self.assertEqual(len([event for event in timeline if event["action"] == "log_service"]), 1)

    def test_distinct_request_ids_both_apply(self):
        record = self._create_active()
        data1 = {'request_id': 'REQ-A', 'session_minutes': 30, 'provider': 'SP-3'}
        data2 = {'request_id': 'REQ-B', 'session_minutes': 20, 'provider': 'SP-3'}
        record = self.service.act(Actor("operator", "specialist"), record["id"], record["version"], "log_service", data1)
        record = self.service.act(Actor("operator", "specialist"), record["id"], record["version"], "log_service", data2)
        self.assertEqual(record["payload"]["delivered_minutes"], 170)

    def test_list_and_stats_follow_review_dates(self):
        actor = self.actor
        # 刚创建：尚未设置复查日期 -> 逾期
        record = self.service.create(actor, "IEP-28001", CREATE_DATA)
        items = self.service.list_records(actor)
        self.assertTrue(items[0]["payload"]["review_overdue"])
        stats = self.service.stats(actor)
        self.assertEqual(stats["overdue"], 1)
        self.assertEqual(stats["normal"], 0)
        self.assertEqual(stats["by_state"]["draft"], 1)
        # 完成复查并设定未来的下次复查日期 -> 正常
        record = self.service.act(Actor("operator", "parent_rep"), record["id"], record["version"], "consent", FLOW[0][2])
        record = self.service.act(Actor("operator", "case_manager"), record["id"], record["version"], "activate", {})
        future = (date.today() + timedelta(days=30)).isoformat()
        record = self.service.act(Actor("operator", "administrator"), record["id"], record["version"], "review", {"progress_note": "阶段复盘", "next_review_date": future})
        self.assertFalse(record["payload"]["review_overdue"])
        self.assertEqual(record["payload"]["next_review_date"], future)
        stats = self.service.stats(actor)
        self.assertEqual(stats["overdue"], 0)
        self.assertEqual(stats["normal"], 1)
        self.assertEqual(stats["by_state"]["under_review"], 1)
