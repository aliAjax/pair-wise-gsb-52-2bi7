"""业务用例编排、权限检查与审计。"""
from typing import Any, Dict, List, Optional

from .audit import AuditRecorder
from .domain import Actor, PermissionDenied, ValidationError, text
from .repository import Repository
from .rules import DomainRules


class Service:
    def __init__(self, repository: Repository, rules: DomainRules, audit: AuditRecorder = None) -> None:
        self.repository = repository
        self.rules = rules
        self.audit = audit or AuditRecorder(repository)

    @staticmethod
    def _actor(actor: Actor) -> Actor:
        if actor is None or not actor.user_id.strip() or not actor.role.strip():
            raise PermissionDenied("缺少调用身份")
        return actor

    def _ensure_known_role(self, actor: Actor) -> None:
        if not self.rules.known_role(actor.role):
            raise PermissionDenied("角色无权访问该服务")

    def _with_review_status(self, record: Dict[str, Any]) -> Dict[str, Any]:
        """读时根据next_review_date计算review_overdue，覆盖旧的人工标记。"""
        item = dict(record)
        payload = dict(record["payload"])
        payload["review_overdue"] = self.rules.is_review_overdue(payload)
        item["payload"] = payload
        return item

    def create(self, actor: Actor, reference: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        actor = self._actor(actor)
        self._ensure_known_role(actor)
        if not self.rules.role_can_create(actor.role):
            raise PermissionDenied("角色无权创建记录")
        reference = text({"reference": reference}, "reference")
        prepared = self.rules.prepare_create(payload or {})
        self.rules.check_create_conflicts(prepared, self.repository.list_records(limit=500))
        return self.repository.create(reference, self.rules.INITIAL_STATE, prepared, actor.user_id)

    def list_records(self, actor: Actor, state: Optional[str] = None, limit: int = 100) -> List[Dict[str, Any]]:
        actor = self._actor(actor)
        self._ensure_known_role(actor)
        records = self.repository.list_records(state=state, limit=limit)
        return [self._with_review_status(record) for record in records]

    def get_record(self, actor: Actor, record_id: int) -> Dict[str, Any]:
        actor = self._actor(actor)
        self._ensure_known_role(actor)
        return self._with_review_status(self.repository.get(record_id))

    def act(self, actor: Actor, record_id: int, expected_version: int, action: str, data: Dict[str, Any]) -> Dict[str, Any]:
        actor = self._actor(actor)
        self._ensure_known_role(actor)
        action = text({"action": action}, "action")
        if not self.rules.role_can_action(actor.role, action):
            raise PermissionDenied("角色无权执行该操作")
        if data is None:
            data = {}
        if not isinstance(data, dict):
            raise ValidationError("data必须是对象")
        request_id = None
        if action == "log_service":
            # 每次补录必须带请求编号；同一编号重发直接返回首次结果，不重复累计分钟，也不新增时间线
            request_id = text(data, "request_id")
            cached = self.repository.find_request_result(record_id, request_id)
            if cached is not None:
                return self._with_review_status(cached)
        record = self.repository.get(record_id)
        self.rules.require_transition(record, action)
        new_state, new_payload, summary = self.rules.apply_action(record, action, data)
        result = self.repository.mutate(
            record_id=record_id,
            expected_version=int(expected_version),
            state=new_state,
            payload=new_payload,
            actor_id=actor.user_id,
            action=action,
            details={"summary": summary, "input": data, "from": record["state"], "to": new_state},
            request_id=request_id,
        )
        return self._with_review_status(result)

    def timeline(self, actor: Actor, record_id: int) -> List[Dict[str, Any]]:
        actor = self._actor(actor)
        self._ensure_known_role(actor)
        return self.audit.timeline(record_id)

    def stats(self, actor: Actor) -> Dict[str, Any]:
        actor = self._actor(actor)
        self._ensure_known_role(actor)
        records = self.repository.list_records(limit=500)
        by_state: Dict[str, int] = {}
        overdue = 0
        for record in records:
            by_state[record["state"]] = by_state.get(record["state"], 0) + 1
            if self.rules.is_review_overdue(record["payload"]):
                overdue += 1
        return {"by_state": by_state, "overdue": overdue, "normal": len(records) - overdue}
