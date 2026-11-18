"""洞察报告接口：报告列表、洞察结论数据表、生成/续写/回滚与跨模块口径补齐。"""
from __future__ import annotations

import csv
import io
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse

from app.schemas import EntryPayload
from app.services.insight_report import CONCLUSION_FIELDS, InsightReportService

router = APIRouter(prefix="/api/insight_report", tags=["洞察报告"])
service = InsightReportService()
LIST_FIELDS = ["报告编号", "分析主题", "数据来源", "关键发现", "建议措施", "影响范围", "生成时间", "报告版本"]
STATUSES = ["草稿", "生成中", "已完成", "已归档"]


def csv_cell(value: Any) -> str:
    text = str(value) if value is not None else ""
    if text and text[0] in "=+-@":
        text = "'" + text
    return '"' + text.replace('"', '""') + '"'


@router.get("/summary")
def get_summary() -> dict[str, Any]:
    """看板 KPI：基于真实结论表统计，不再随机造数。"""
    overview = service.conclusion_overview()
    reports = service.list_reports()
    return {"metrics": [
        {"label": "洞察报告数", "value": len(reports),
         "trend": sum(1 for r in reports if r.get("status") == "已完成")},
        {"label": "可追溯结论数", "value": overview["结论总数"],
         "trend": overview["证据来源分布"].get("AB+异常综合", 0)},
        {"label": "回滚/异常报告", "value": sum(1 for r in reports if r.get("abnormal")),
         "trend": -sum(1 for r in reports if r.get("abnormal"))},
    ]}


@router.get("")
def list_results() -> dict[str, Any]:
    """报告工作台列表：与详情页共用同一份业务数据。"""
    return {"items": service.list_results()}


@router.get("/conclusions/overview")
def conclusions_overview(
    report_id: int | None = Query(default=None, description="可选：只统计某份报告"),
) -> dict[str, Any]:
    """洞察结论数据表概览：总量、章节/证据分布、口径版本覆盖率。"""
    return service.conclusion_overview(report_id)


@router.get("/conclusions/audit")
def conclusions_audit() -> dict[str, Any]:
    """V002 留档核对：事务回滚、中断续写、版本拒绝覆盖等审计记录。"""
    return {"items": service.list_audit_logs()}


@router.get("/conclusions")
def list_conclusions(
    report_id: int | None = Query(default=None, description="可选：按报告 id 过滤"),
) -> dict[str, Any]:
    """洞察结论数据表；工作台与详情页都读这里，保证业务数据一致。"""
    return {"items": service.list_conclusions(report_id)}


@router.get("/conclusions/{conclusion_id}")
def get_conclusion(conclusion_id: int) -> dict[str, Any]:
    row = service.get_conclusion(conclusion_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"洞察结论 {conclusion_id} 不存在")
    return row


@router.post("/{entry_id}/conclusions/generate")
def generate_conclusions(entry_id: int, payload: EntryPayload) -> dict[str, Any]:
    """生成/续写洞察结论。

    values 支持：
      strategy: time（按证据时间收敛）或 chapter（按报告章节收敛，默认）
      simulate_interrupt: "true" 模拟生成中断，验证断点续写
      simulate_residual: "true" 模拟未清残留，验证 V002 事务回滚
    """
    values = payload.values or {}
    result = service.generate_conclusions(
        entry_id,
        strategy=str(values.get("strategy", "chapter")),
        simulate_interrupt=str(values.get("simulate_interrupt", "")).lower() == "true",
        simulate_residual=str(values.get("simulate_residual", "")).lower() == "true",
    )
    return result


@router.post("/conclusions/enrich-caliber")
def enrich_caliber() -> dict[str, Any]:
    """指标定义 V002 跨模块联动：按结论表指标标识补齐口径版本。"""
    return service.enrich_metric_caliber()


@router.post("/run")
def run_analysis(payload: EntryPayload) -> dict[str, Any]:
    results = service.run_analysis(payload.values)
    return {"items": results}


@router.get("/export")
def export_entries() -> StreamingResponse:
    items = service.list_results()
    buf = io.StringIO(newline="")
    writer = csv.writer(buf)
    all_keys = ["id"]
    if items:
        all_keys = list(items[0].keys())
    writer.writerow(all_keys)
    for item in items:
        writer.writerow([csv_cell(item.get(k, "")) for k in all_keys])
    buf.seek(0)
    return StreamingResponse(iter([buf.getvalue()]), media_type="text/csv; charset=utf-8",
                             headers={"Content-Disposition": "attachment; filename=insight_report.csv"})


@router.get("/conclusions/export/csv")
def export_conclusions() -> StreamingResponse:
    """导出洞察结论数据表（证据快照序列化后落 CSV，保留追溯链路）。"""
    items = service.list_conclusions()
    buf = io.StringIO(newline="")
    writer = csv.writer(buf)
    writer.writerow(["id", *CONCLUSION_FIELDS])
    for item in items:
        row = []
        for key in ["id", *CONCLUSION_FIELDS]:
            value = item.get(key, "")
            if isinstance(value, (dict, list)):
                value = str(value)
            row.append(csv_cell(value))
        writer.writerow(row)
    buf.seek(0)
    return StreamingResponse(iter([buf.getvalue()]), media_type="text/csv; charset=utf-8",
                             headers={"Content-Disposition": "attachment; filename=insight_conclusion.csv"})


@router.get("/{entry_id}")
def get_entry(entry_id: int) -> dict:
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"洞察报告 {entry_id} 不存在")
    return entry
