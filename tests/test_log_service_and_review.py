import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from app import build_service
from src.domain import Actor, ValidationError


CREATE_DATA = {'student_id': 'S-100', 'disability': 'hearing', 'service_minutes': 600, 'delivered_minutes': 120, 'review_due_days': 15, 'goals_count': 4, 'consent': False}
MANAGER = Actor("creator", "case_manager")
SPECIALIST = Actor("operator", "specialist")
ADMIN = Actor("reviewer", "administrator")
FUTURE_DATE = (date.today() + timedelta(days=30)).isoformat()
PAST_DATE = (date.today() - timedelta(days=1)).isoformat()


class LogServiceAndReviewTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.service = build_service(str(Path(self.temp.name) / "test.db"))

    def tearDown(self):
        self.temp.cleanup()

    def _active_record(self, reference="IEP-28001", data=None):
        record = self.service.create(MANAGER, reference, data or CREATE_DATA)
        record = self.service.act(Actor("parent", "parent_rep"), record["id"], record["version"], "consent", {"guardian_confirmed": True, "consent_scope": "个别化服务"})
        record = self.service.act(MANAGER, record["id"], record["version"], "activate", {})
        return record

    def test_same_request_id_returns_first_result(self):
        record = self._active_record()
        data = {"session_minutes": 60, "provider": "SP-3", "request_id": "REQ-1"}
        first = self.service.act(SPECIALIST, record["id"], record["version"], "log_service", data)
        self.assertEqual(first["payload"]["delivered_minutes"], 180)
        timeline_before = self.service.timeline(MANAGER, record["id"])
        # 网络重试：同一请求编号、旧版本号，返回第一次结果
        retry = self.service.act(SPECIALIST, record["id"], record["version"], "log_service", data)
        self.assertEqual(retry, first)
        current = self.service.get_record(MANAGER, record["id"])
        self.assertEqual(current["payload"]["delivered_minutes"], 180)
        self.assertEqual(current["version"], first["version"])
        # 不新增时间线
        self.assertEqual(len(self.service.timeline(MANAGER, record["id"])), len(timeline_before))

    def test_different_request_id_adds_minutes(self):
        record = self._active_record()
        record = self.service.act(SPECIALIST, record["id"], record["version"], "log_service", {"session_minutes": 60, "provider": "SP-3", "request_id": "REQ-1"})
        record = self.service.act(SPECIALIST, record["id"], record["version"], "log_service", {"session_minutes": 60, "provider": "SP-3", "request_id": "REQ-2"})
        self.assertEqual(record["payload"]["delivered_minutes"], 240)

    def test_log_service_requires_request_id(self):
        record = self._active_record()
        with self.assertRaises(ValidationError):
            self.service.act(SPECIALIST, record["id"], record["version"], "log_service", {"session_minutes": 60, "provider": "SP-3"})

    def test_exceeding_minutes_keeps_record_and_reports_remaining(self):
        record = self._active_record()
        timeline_before = self.service.timeline(MANAGER, record["id"])
        with self.assertRaises(ValidationError) as ctx:
            self.service.act(SPECIALIST, record["id"], record["version"], "log_service", {"session_minutes": 600, "provider": "SP-3", "request_id": "REQ-1"})
        self.assertIn("还剩480分钟", str(ctx.exception))
        current = self.service.get_record(MANAGER, record["id"])
        self.assertEqual(current["payload"]["delivered_minutes"], 120)
        self.assertEqual(current["version"], record["version"])
        self.assertEqual(len(self.service.timeline(MANAGER, record["id"])), len(timeline_before))

    def test_review_sets_next_review_date(self):
        record = self._active_record()
        record = self.service.act(ADMIN, record["id"], record["version"], "review", {"progress_note": "阶段复盘", "next_review_date": FUTURE_DATE})
        self.assertEqual(record["payload"]["next_review_date"], FUTURE_DATE)
        self.assertFalse(record["payload"]["review_overdue"])

    def test_review_rejects_missing_or_invalid_date(self):
        record = self._active_record()
        with self.assertRaises(ValidationError):
            self.service.act(ADMIN, record["id"], record["version"], "review", {"progress_note": "阶段复盘"})
        with self.assertRaises(ValidationError):
            self.service.act(ADMIN, record["id"], record["version"], "review", {"progress_note": "阶段复盘", "next_review_date": "2026年9月1日"})

    def test_list_and_stats_distinguish_overdue(self):
        overdue_record = self._active_record("IEP-28001")
        other_student = dict(CREATE_DATA, student_id="S-200")
        normal_record = self._active_record("IEP-28002", other_student)
        self.service.act(ADMIN, normal_record["id"], normal_record["version"], "review", {"progress_note": "阶段复盘", "next_review_date": FUTURE_DATE})
        items = {item["id"]: item for item in self.service.list_records(MANAGER)}
        # 未设置复查日期的计划算逾期
        self.assertTrue(items[overdue_record["id"]]["payload"]["review_overdue"])
        self.assertFalse(items[normal_record["id"]]["payload"]["review_overdue"])
        stats = self.service.stats(MANAGER)
        self.assertEqual(stats["review_overdue"], 1)
        self.assertEqual(stats["review_normal"], 1)

    def test_past_review_date_counts_as_overdue(self):
        record = self._active_record()
        self.service.act(ADMIN, record["id"], record["version"], "review", {"progress_note": "阶段复盘", "next_review_date": PAST_DATE})
        item = self.service.get_record(MANAGER, record["id"])
        self.assertTrue(item["payload"]["review_overdue"])
