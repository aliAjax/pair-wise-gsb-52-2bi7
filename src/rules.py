"""特殊教育支持计划合规领域规则与状态转换。"""
import re
from datetime import date, datetime
from typing import Any, Dict, Iterable, Optional, Tuple

from .domain import Actor, Conflict, ValidationError, boolean, choice, integer, number, text, text_list


REVIEW_DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def parse_review_date(value: Any) -> Optional[date]:
    """解析YYYY-MM-DD格式的复查日期，无效时返回None。"""
    if not isinstance(value, str):
        return None
    value = value.strip()
    if not REVIEW_DATE_PATTERN.match(value):
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        return None


INITIAL_STATE = "draft"
CREATE_ROLES = {'case_manager'}
ACTION_ROLES = {'consent': {'parent_rep'}, 'activate': {'case_manager'}, 'log_service': {'case_manager', 'specialist'}, 'review': {'administrator'}, 'amend': {'case_manager'}, 'close': {'administrator'}}
TRANSITIONS = {'consent': {'draft': 'consented'}, 'activate': {'consented': 'active'}, 'log_service': {'active': 'active'}, 'review': {'active': 'under_review'}, 'amend': {'under_review': 'active'}, 'close': {'active': 'closed', 'under_review': 'closed'}}


class DomainRules:
    INITIAL_STATE = INITIAL_STATE

    def known_role(self, role: str) -> bool:
        all_roles = set(CREATE_ROLES)
        for roles in ACTION_ROLES.values():
            all_roles.update(roles)
        return role == "admin" or role in all_roles

    def role_can_create(self, role: str) -> bool:
        return role == "admin" or role in CREATE_ROLES

    def role_can_action(self, role: str, action: str) -> bool:
        return role == "admin" or role in ACTION_ROLES.get(action, set())

    def validate_create(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        p = dict(payload)
        text(p, "student_id")
        text(p, "disability")
        integer(p, "service_minutes", 1)
        integer(p, "delivered_minutes", 0)
        integer(p, "review_due_days", 0)
        integer(p, "goals_count", 1)
        boolean(p, "consent")
        if p["delivered_minutes"] > p["service_minutes"]:
            raise ValidationError("已提供服务不能超过计划服务")
        return p

    def is_review_overdue(self, payload: Dict[str, Any], today: date = None) -> bool:
        """复查日期未设置、无效或早于今天都视为逾期。"""
        parsed = parse_review_date(payload.get("next_review_date"))
        if parsed is None:
            return True
        return parsed < (today or date.today())

    def prepare_create(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        p = self.validate_create(payload)
        p["missing_minutes"] = max(0, int(p["service_minutes"]) - int(p["delivered_minutes"]))
        p["compliance_rate"] = round(int(p["delivered_minutes"]) / int(p["service_minutes"]) * 100, 2)
        p["review_overdue"] = self.is_review_overdue(p)
        p["plan_status"] = "draft"
        return p

    def check_create_conflicts(self, payload: Dict[str, Any], existing: Iterable[Dict[str, Any]]) -> None:
        for item in existing:
            if item["state"] in {"active", "under_review", "consented"} and item["payload"].get("student_id") == payload.get("student_id"):
                raise Conflict("该学生已有有效的支持计划")

    def require_transition(self, record: Dict[str, Any], action: str) -> str:
        allowed = TRANSITIONS.get(action, {}).get(record["state"])
        if allowed is None:
            raise Conflict("当前状态不允许执行%s" % action)
        return allowed

    def apply_action(self, record: Dict[str, Any], action: str, data: Dict[str, Any]) -> Tuple[str, Dict[str, Any], str]:
        new_state = self.require_transition(record, action)
        data = dict(data or {})
        p = dict(record["payload"])
        changes: Dict[str, Any] = {}
        summary = ""
        if action == "consent":
            if not boolean(data, "guardian_confirmed"):
                raise ValidationError("监护人尚未确认")
            if not text(data, "consent_scope"):
                raise ValidationError("同意范围不能为空")
            changes["consent"] = True
            changes["consent_scope"] = data["consent_scope"]
            summary = "监护人同意已记录"
        elif action == "activate":
            if not p.get("consent"):
                raise ValidationError("缺少有效同意")
            if int(p["goals_count"]) <= 0:
                raise ValidationError("计划必须包含目标")
            changes["plan_status"] = "active"
            summary = "支持计划生效"
        elif action == "log_service":
            session = integer(data, "session_minutes", 1)
            delivered = int(p["delivered_minutes"])
            planned = int(p["service_minutes"])
            if session + delivered > planned:
                raise ValidationError("记录服务超过计划分钟数，还剩%s分钟" % (planned - delivered))
            changes["delivered_minutes"] = delivered + session
            changes["last_provider"] = text(data, "provider")
            changes["missing_minutes"] = planned - changes["delivered_minutes"]
            changes["compliance_rate"] = round(changes["delivered_minutes"] / planned * 100, 2)
            summary = "服务记录已登记"
        elif action == "review":
            changes["progress_note"] = text(data, "progress_note")
            next_review_date = text(data, "next_review_date")
            parsed = parse_review_date(next_review_date)
            if parsed is None:
                raise ValidationError("next_review_date必须是YYYY-MM-DD格式的有效日期")
            changes["next_review_date"] = next_review_date
            changes["review_overdue"] = parsed < date.today()
            summary = "进入计划复查"
        elif action == "amend":
            changes["amendment_reason"] = text(data, "amendment_reason")
            changes["updated_goals"] = text_list(data, "updated_goals", 1)
            changes["goals_count"] = len(changes["updated_goals"])
            changes["plan_status"] = "active"
            summary = "计划已修订"
        elif action == "close":
            if not boolean(data, "review_complete"):
                raise ValidationError("复查尚未完成")
            changes["plan_status"] = "closed"
            summary = "支持计划结束"
        p.update(changes)
        return new_state, p, summary or ("已执行%s" % action)
