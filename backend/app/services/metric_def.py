"""指标定义业务规则：状态流转、字段校验与筛选口径都收在这里。"""
from __future__ import annotations

from typing import Any

from app.store import store

MODULE = "metric_def"
LIST_FIELDS = ["指标编号", "指标名称", "计算公式", "统计粒度", "时间窗口", "基准取值", "精度位数", "口径版本"]
REQUIRED_FIELDS = ["指标编号", "指标名称", "计算公式"]
STATUS_ORDER = ["草稿", "待校验", "已生效", "已废弃"]
ACTION_RULES = {"提交校验": "待校验", "标记生效": "已生效", "标记废弃": "已废弃"}
NEGATIVE_ACTIONS = []


class MetricDefService:
    def list_entries(
        self,
        *,
        filters: dict[str, str] | None = None,
        keyword: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = store.rows(MODULE)
        for field, expected in (filters or {}).items():
            rows = [row for row in rows if expected.casefold() in str(row.get(field, "")).casefold()]
        if keyword:
            rows = [row for row in rows if keyword.casefold() in str(row.get("指标编号", "")).casefold()]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        total = len(rows)
        start = (page - 1) * size
        return rows[start:start + size], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        return store.find(MODULE, entry_id)

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing
        rows = store.rows(MODULE)
        entry = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        entry.update({field: values.get(field) for field in LIST_FIELDS})
        entry["status"] = STATUS_ORDER[0]
        entry["pending"] = True
        entry["abnormal"] = False
        rows.append(entry)
        return entry, []

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"指标定义 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于指标定义可执行范围"
        target = ACTION_RULES[action]
        if target not in STATUS_ORDER:
            return None, f"目标状态「{target}」不在允许的状态序列里"
        entry["status"] = target
        entry["pending"] = target != STATUS_ORDER[-1]
        entry["abnormal"] = action in NEGATIVE_ACTIONS
        return entry, f"指标定义已{action}"

    def sync_conclusion_caliber(self) -> dict[str, Any]:
        """跨模块联动（V002）：指标定义模块读取洞察结论数据表中的指标标识，
        为关键发现补齐口径版本。

        结论表是口径版本的接收方：本方法按指标名称/指标编号匹配指标定义，
        把已生效的口径版本回写到结论行；匹配不上的标识单独返回便于核对。
        """
        # 延迟导入避免模块间循环依赖。
        from app.services.insight_report import CONCLUSION_MODULE

        updated = 0
        unmatched: list[str] = []
        metric_rows = store.rows(MODULE)
        for conclusion in store.rows(CONCLUSION_MODULE):
            if conclusion.get("口径版本"):
                continue
            identifier = str(conclusion.get("指标标识", ""))
            matched = next(
                (row for row in metric_rows
                 if identifier and (identifier in str(row.get("指标名称", ""))
                                    or identifier == str(row.get("指标编号", "")))),
                None,
            )
            if matched is None:
                if identifier:
                    unmatched.append(identifier)
                continue
            conclusion["口径版本"] = matched.get("口径版本", "")
            updated += 1
        if updated:
            store.bump_version(CONCLUSION_MODULE)
        return {
            "ok": True,
            "updated": updated,
            "unmatched": sorted(set(unmatched)),
            "message": f"指标定义V002联动：已为 {updated} 条关键发现补齐口径版本",
        }
