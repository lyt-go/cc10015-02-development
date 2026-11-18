"""洞察报告 V002：洞察结论数据表的可追溯性、事务回滚、断点续写与跨模块联动测试。"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.services.insight_report import (
    CONCLUSION_FIELDS,
    CONCLUSION_MODULE,
    CONCLUSION_VERSION,
    InsightReportService,
)
from app.services.metric_def import MetricDefService
from app.store import store


def _reset():
    """恢复到种子数据状态，结论表与审计表清空。"""
    from app.seed import SEED_ROWS

    for name, rows in SEED_ROWS.items():
        store._tables[name] = [dict(row) for row in rows]
    store._tables["insight_conclusion_audit"] = []


def _seed_ab_and_anomaly():
    return store.rows("ab_result"), store.rows("anomaly_detect")


def test_generate_conclusions_are_traceable():
    """每条结论都能回查同一实验、指标口径字段与证据来源，证据快照与报告版本同时写入。"""
    _reset()
    svc = InsightReportService()
    result = svc.generate_conclusions(1)
    assert result["ok"] is True
    assert result["rolled_back"] is False

    rows = svc.list_conclusions()
    assert rows, "结论表应当生成结论行"
    for row in rows:
        for field in CONCLUSION_FIELDS:
            assert field in row, f"结论缺少字段 {field}"
        assert row["报告版本"] == CONCLUSION_VERSION
        assert row["实验编号"], "结论必须可回查实验"
        assert row["指标标识"], "结论必须带指标标识"
        assert row["证据来源"], "结论必须带证据来源"
        assert row["证据快照"], "证据快照必须与结论同时写入，不能页面完成而证据缺失"
        assert row["章节状态"] == "已完成"
    # AB 与异常两个基础章节都应出现
    chapters = {row["章节"] for row in rows}
    assert "AB结果分析" in chapters
    assert "异常检测信号" in chapters


def test_same_data_on_workbench_and_detail():
    """工作台与详情页读取同一接口数据，按报告过滤后与全量中该报告的行一致。"""
    _reset()
    svc = InsightReportService()
    svc.generate_conclusions(1)

    all_rows = svc.list_conclusions()
    detail_rows = svc.list_conclusions(report_id=1)
    report_code = svc.get_entry(1)["报告编号"]
    expected = [row for row in all_rows if row["报告编号"] == report_code]
    assert detail_rows == expected
    assert len(detail_rows) > 0


def test_conflict_time_convergence_single_finding():
    """按证据时间收敛：同实验双来源合并为单条综合发现，并保留两个来源时间。"""
    _reset()
    svc = InsightReportService()
    result = svc.generate_conclusions(1, strategy="time")
    assert result["ok"]

    conflict_rows = [
        row for row in svc.list_conclusions()
        if row["报告编号"] == "INSI-0001" and row["章节"] == "跨来源综合研判"
    ]
    experiments = {row["实验编号"] for row in conflict_rows}
    # 每个冲突实验只保留一条综合发现
    for experiment in experiments:
        per_exp = [row for row in conflict_rows if row["实验编号"] == experiment]
        assert len(per_exp) == 1
        assert per_exp[0]["收敛策略"] == "按证据时间收敛"
        assert per_exp[0]["证据类型"] == "AB+异常综合"
        assert "/" in per_exp[0]["证据时间"], "综合发现必须保留两个证据来源时间"


def test_conflict_chapter_convergence_two_findings():
    """按报告章节收敛：同实验保留 AB、异常两项发现，并统一报告版本。"""
    _reset()
    svc = InsightReportService()
    result = svc.generate_conclusions(1, strategy="chapter")
    assert result["ok"]

    conflict_rows = [
        row for row in svc.list_conclusions()
        if row["报告编号"] == "INSI-0001" and row["章节"] == "跨来源综合研判"
    ]
    assert len(conflict_rows) >= 2
    evidence_types = {row["证据类型"] for row in conflict_rows}
    assert "AB对比结果" in evidence_types
    assert "异常检测信号" in evidence_types
    assert {row["报告版本"] for row in conflict_rows} == {CONCLUSION_VERSION}
    assert all(row["收敛策略"] == "按报告章节收敛" for row in conflict_rows)


def test_interrupt_only_resumes_pending_chapter():
    """生成中断后只续写未完成章节，已完成章节与结论不被覆盖。"""
    _reset()
    svc = InsightReportService()

    interrupted = svc.generate_conclusions(2, simulate_interrupt=True)
    assert interrupted["interrupted"] is True
    assert interrupted["pending_chapters"], "应当存在未完成章节"
    first_batch = svc.list_conclusions(report_id=2)
    first_ids = {row["id"] for row in first_batch}
    assert first_batch, "中断前已完成章节的结论应保留"

    resumed = svc.generate_conclusions(2)
    assert resumed["ok"] is True
    assert resumed["interrupted"] is False
    second_batch = svc.list_conclusions(report_id=2)
    # 第一批结论原样保留，续写只新增缺失章节
    assert first_ids <= {row["id"] for row in second_batch}
    assert len(second_batch) > len(first_batch)
    assert svc.get_entry(2)["status"] == "已完成"


def test_retry_does_not_overwrite_complete_conclusions():
    """完整结论重试时不得覆盖上一份，而是开启新报告版本并留档。"""
    _reset()
    svc = InsightReportService()
    svc.generate_conclusions(3)
    rows_before = svc.list_conclusions(report_id=3)
    version_before = svc.get_entry(3)["报告版本"]

    retry = svc.generate_conclusions(3)
    assert retry["ok"] is True
    assert retry["new_version"].startswith(version_before)
    assert retry["new_version"] != version_before
    # 结论表行数与内容保持不变
    assert svc.list_conclusions(report_id=3) == rows_before
    assert any(log["动作"] == "拒绝覆盖" for log in svc.list_audit_logs())


def test_residual_triggers_v002_rollback_and_archive():
    """结论表按章节发现未清残留时，V002 触发事务回滚并留档核对。"""
    _reset()
    svc = InsightReportService()
    # 先造一条本报告的未完成脏行
    store.rows(CONCLUSION_MODULE).append({
        "id": 999, "报告编号": "INSI-0001", "章节状态": "未开始", "章节": "脏章节",
    })
    result = svc.generate_conclusions(1)
    assert result["ok"] is False
    assert result["rolled_back"] is True
    # 残留被清除，且没有写入任何新结论
    remaining = svc.list_conclusions()
    assert all(row.get("id") != 999 for row in remaining)
    assert remaining == []
    # 报告被标记异常，审计留档
    assert svc.get_entry(1)["abnormal"] is True
    audit = svc.list_audit_logs()
    assert any(log["动作"] == "事务回滚" and log["结论表版本"] == CONCLUSION_VERSION for log in audit)
    # 回滚清除残留后，同一份报告可以正常重新生成
    regenerated = svc.generate_conclusions(1)
    assert regenerated["ok"] is True
    assert len(svc.list_conclusions(report_id=1)) > 0


def test_simulated_residual_flag_rolls_back():
    """显式模拟残留参数同样触发回滚，且不污染其他报告的结论。"""
    _reset()
    svc = InsightReportService()
    svc.generate_conclusions(2)
    count_report_2 = len(svc.list_conclusions(report_id=2))

    result = svc.generate_conclusions(1, simulate_residual=True)
    assert result["rolled_back"] is True
    # 报告2 的结论不受影响
    assert len(svc.list_conclusions(report_id=2)) == count_report_2


def test_metric_def_v002_enriches_caliber_version():
    """指标定义 V002 联动：读取结论表指标标识，为关键发现补齐口径版本，且幂等。"""
    _reset()
    svc = InsightReportService()
    svc.generate_conclusions(1)

    before = [row for row in svc.list_conclusions() if row["指标标识"] == "首页点击率"]
    assert before and all(row["口径版本"] == "" for row in before)

    sync_result = MetricDefService().sync_conclusion_caliber()
    assert sync_result["ok"] is True
    assert sync_result["updated"] >= 1

    after = [row for row in svc.list_conclusions() if row["指标标识"] == "首页点击率"]
    assert all(row["口径版本"] == "V002" for row in after)
    # 幂等：再次联动不应重复计数
    again = MetricDefService().sync_conclusion_caliber()
    assert again["updated"] == 0


def test_conclusion_overview_counts():
    """概览统计与结论表实际行数、章节分布一致。"""
    _reset()
    svc = InsightReportService()
    svc.generate_conclusions(1)
    overview = svc.conclusion_overview()
    rows = svc.list_conclusions()
    assert overview["结论总数"] == len(rows)
    assert sum(overview["章节分布"].values()) == len(rows)
    assert overview["结论表版本"] == CONCLUSION_VERSION


def test_unknown_strategy_rejected():
    """非法收敛策略直接拒绝，不写任何结论。"""
    _reset()
    svc = InsightReportService()
    result = svc.generate_conclusions(1, strategy="bogus")
    assert result["ok"] is False
    assert svc.list_conclusions() == []
