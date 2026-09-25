# 特殊教育支持计划合规

纯Python标准库实现的特殊教育支持计划合规原型，使用SQLite持久化，HTTP接口由`http.server`提供。

## 模块结构

- `app.py`：命令行参数、依赖组装和服务启动。
- `src/domain.py`：领域数据类型、错误和基础校验。
- `src/rules.py`：状态转换、同意、服务履约、复查期限和计划版本和冲突检查。
- `src/repository.py`：SQLite建表、事务和查询。
- `src/service.py`：用例编排、权限检查、乐观并发和审计。
- `src/http_api.py`：HTTP路由与统一错误响应。
- `src/audit.py`：事件时间线。
- `static/index.html`：最小演示页面。
- `tests/`：完整流程、规则计算和失败场景测试。

## 启动

```bash
python3 app.py --db ./data.db --port 8328
```

默认端口为`8328`，默认数据库位于项目目录。服务启动时自动建表。

## 主要接口

- `GET /health`：健康检查。
- `GET /`：演示页面。
- `GET /api/records`：记录列表，可带`state`和`limit`参数。
- `GET /api/records/{id}`：记录详情。
- `GET /api/records/{id}/audit`：审计时间线。
- `GET /api/stats`：统计，返回`{"by_state":{状态:数量},"overdue":逾期数,"normal":正常数}`。
- `POST /api/records`：创建记录，请求体为`{"reference":"...","data":{...}}`。
- `POST /api/records/{id}/actions/{action}`：执行业务动作，请求体为`{"expected_version":1,"data":{...}}`。

## 补录幂等与复查期限

- `log_service`（补录）的`data`必须携带`request_id`请求编号。同一记录同一编号重复提交（如网络重试）直接返回第一次的结果，不重复增加已服务分钟，也不新增审计时间线。不同编号各自正常登记。
- 补录分钟数超过计划剩余分钟时返回校验错误，错误信息注明还可登记多少分钟；当前记录（分钟数与版本）原样保留。
- `review`（管理员完成复查）的`data`必须填写`next_review_date`（格式`YYYY-MM-DD`，下一次复查日期），缺失或格式非法都会被拒绝。
- 列表与详情中的`payload.review_overdue`在读取时根据`next_review_date`实时计算：日期尚未设置、格式无效、或早于当天的计划均算逾期；当天或未来日期算正常。统计接口按同一规则汇总`overdue`/`normal`。

除`/health`和`/`外，请求需提供`X-User-Id`、`X-Role`，可选`X-Org`。

## 测试

```bash
python3 -m unittest discover -s tests -v
```

测试覆盖完整流程、规则计算、重复引用、权限拒绝和版本冲突。
