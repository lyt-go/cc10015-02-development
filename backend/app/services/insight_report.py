"""洞察报告服务：聚合 AB 结果与异常检测信号，沉淀可追溯的洞察结论。

核心约束（V002）：
- 结论行必须能回查同一实验、指标口径与证据来源（证据快照 + 报告版本同时写入）。
- 按章节生成，生成中断后只续写未完成章节，重试不覆盖上一份完整结论。
- 结论表按章节处理时若发现未清残留，V002 下触发事务回滚并留档核对。
- 同一实验同时存在 AB 与异常信号（版本冲突）时，支持两种收敛策略：
  按证据时间收敛为单条综合发现，或按报告章节保留两项发现并统一报告版本。
- 指标定义模块通过结论表中的指标标识补齐口径版本（跨模块联动 V002）。
"""
from __future__ import annotations

import copy
from typing import Any

from app.store import store

MODULE = "insight_report"
CONCLUSION_MODULE = "insight_conclusion"
CONCLUSION_VERSION = "V002"
REQUIRED_FIELDS = ["报告编号", "分析主题", "数据来源"]
STATUS_ORDER = ["草稿", "生成中", "已完成", "已归档"]
ACTION_RULES = {"开始生成": "生成中", "确认完成": "已完成", "归档报告": "已归档"}
NEGATIVE_ACTIONS: list[str] = []

# 结论表字段：每条结论都带实验、指标口径、证据来源与报告版本，保证可回查。
CONCLUSION_FIELDS = [
    "结论编号", "报告编号", "报告版本", "章节", "实验编号", "指标标识", "口径版本",
    "关键发现", "证据类型", "证据来源", "证据时间", "收敛策略", "证据快照", "章节状态",
]
# 报告生成时按顺序产出的章节；冲突章节仅在检测到同实验双来源时才生成。
BASE_CHAPTERS = ["AB结果分析", "异常检测信号"]
CONFLICT_CHAPTER = "跨来源综合研判"


class TransactionRollbackError(RuntimeError):
    """模拟事务回滚：结论表残留未清时抛出，整批结论不允许部分落库。"""


class InsightReportService:
    # ------------------------------------------------------------------ 读取
    def list_reports(self) -> list[dict[str, Any]]:
        """报告列表（工作台数据来源，固定值，不再每次随机）。"""
        return [dict(row) for row in store.rows(MODULE)]

    def list_results(self) -> list[dict[str, Any]]:
        """兼容旧接口名：与工作台/详情同源，返回相同业务数据。"""
        return self.list_reports()

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        return store.find(MODULE, entry_id)

    def get_report_by_code(self, report_code: str) -> dict[str, Any] | None:
        return store.find_by_field(MODULE, "报告编号", report_code)

    def list_conclusions(self, report_id: int | None = None) -> list[dict[str, Any]]:
        """洞察结论数据表；可按报告 id 过滤，详情页与工作台共用同一份数据。"""
        rows = store.rows(CONCLUSION_MODULE)
        if report_id is None:
            return [dict(row) for row in rows]
        report = self.get_entry(report_id)
        if report is None:
            return []
        code = report.get("报告编号")
        return [dict(row) for row in rows if row.get("报告编号") == code]

    def get_conclusion(self, conclusion_id: int) -> dict[str, Any] | None:
        return store.find(CONCLUSION_MODULE, conclusion_id)

    def conclusion_overview(self, report_id: int | None = None) -> dict[str, Any]:
        """结论表概览：总量、章节分布、证据来源分布、口径版本覆盖情况。"""
        rows = self.list_conclusions(report_id)
        by_chapter: dict[str, int] = {}
        by_evidence: dict[str, int] = {}
        metric_with_caliber = 0
        for row in rows:
            chapter = str(row.get("章节", ""))
            by_chapter[chapter] = by_chapter.get(chapter, 0) + 1
            evidence = str(row.get("证据类型", ""))
            by_evidence[evidence] = by_evidence.get(evidence, 0) + 1
            if row.get("口径版本"):
                metric_with_caliber += 1
        return {
            "结论总数": len(rows),
            "章节分布": by_chapter,
            "证据来源分布": by_evidence,
            "口径版本覆盖": f"{metric_with_caliber}/{len(rows)}",
            "结论表版本": CONCLUSION_VERSION,
        }

    # ----------------------------------------------------------- 证据收集
    def _collect_evidence(self) -> tuple[dict[str, list[dict[str, Any]]], dict[str, list[dict[str, Any]]]]:
        """读取 AB 对比结果表与异常检测信号表，按实验编号归并证据。"""
        ab_by_experiment: dict[str, list[dict[str, Any]]] = {}
        for row in store.rows("ab_result"):
            experiment = str(row.get("实验编号") or f"AB实验-{row.get('id')}")
            ab_by_experiment.setdefault(experiment, []).append(dict(row))

        anomaly_by_experiment: dict[str, list[dict[str, Any]]] = {}
        for row in store.rows("anomaly_detect"):
            # 异常表没有直接的实验字段，按监控指标与 AB 表的分析指标对齐，
            # 对齐不上时挂到首个 AB 实验，保证每条信号仍可回查。
            metric = str(row.get("监控指标", ""))
            experiment = self._match_experiment_by_metric(metric, ab_by_experiment)
            anomaly_by_experiment.setdefault(experiment, []).append(dict(row))
        return ab_by_experiment, anomaly_by_experiment

    @staticmethod
    def _match_experiment_by_metric(
        metric: str, ab_by_experiment: dict[str, list[dict[str, Any]]]
    ) -> str:
        for experiment, rows in ab_by_experiment.items():
            for row in rows:
                if metric and metric in str(row.get("分析指标", "")):
                    return experiment
        if ab_by_experiment:
            return next(iter(ab_by_experiment))
        return f"ANOM实验-{metric or '未关联'}"

    # ----------------------------------------------------------- 章节计划
    def _plan_chapters(
        self,
        ab_by_experiment: dict[str, list[dict[str, Any]]],
        anomaly_by_experiment: dict[str, list[dict[str, Any]]],
    ) -> list[str]:
        chapters = list(BASE_CHAPTERS)
        conflicts = set(ab_by_experiment) & set(anomaly_by_experiment)
        if conflicts:
            chapters.append(CONFLICT_CHAPTER)
        return chapters

    # ----------------------------------------------------------- 生成主流程
    def generate_conclusions(
        self,
        report_id: int,
        *,
        strategy: str = "chapter",
        simulate_interrupt: bool = False,
        simulate_residual: bool = False,
    ) -> dict[str, Any]:
        """为指定报告生成洞察结论数据表。

        strategy:
          - "time"    按证据时间收敛：同实验双来源合并为单条综合发现，保留来源时间；
          - "chapter" 按报告章节收敛：同实验保留两项发现，统一报告版本（默认）。
        simulate_interrupt: 在最后一个章节写入前模拟生成中断（不回滚，留待续写）。
        simulate_residual:  模拟结论表存在未清残留，V002 下必须整批回滚并留档。
        """
        report = self.get_entry(report_id)
        if report is None:
            return {"ok": False, "message": f"洞察报告 {report_id} 不存在", "rolled_back": False}
        if strategy not in ("time", "chapter"):
            return {"ok": False, "message": f"未知收敛策略：{strategy}", "rolled_back": False}

        report_code = str(report.get("报告编号"))
        existing = store.filter_by_field(CONCLUSION_MODULE, "报告编号", report_code)
        completed_chapters = {str(row.get("章节")) for row in existing if row.get("章节状态") == "已完成"}
        planned = self._plan_chapters(*self._collect_evidence())

        # 残留检测优先于完整性判断：只要本报告存在未完成脏行，
        # V002 下一律事务回滚留档，不允许带着残留进入续写或新版本流程。
        dirty = [row for row in store.rows(CONCLUSION_MODULE)
                 if row.get("报告编号") == report_code and row.get("章节状态") != "已完成"]
        if dirty or simulate_residual:
            # 模拟事务上下文：先快照，回滚时只清除本报告的未清残留（已完成结论保留）。
            snapshot = copy.deepcopy(store.rows(CONCLUSION_MODULE))
            exc = TransactionRollbackError(
                f"结论表按章节处理时发现 {len(dirty) or 1} 条未清残留"
            )
            store._tables[CONCLUSION_MODULE] = [
                row for row in snapshot
                if not (row.get("报告编号") == report_code and row.get("章节状态") != "已完成")
            ]
            store.bump_version(CONCLUSION_MODULE)
            report["status"] = "草稿"
            report["pending"] = True
            report["abnormal"] = True
            self._append_audit(
                report_code, "事务回滚",
                f"V002 回滚：{exc}；本批结论未写入，残留已清除并按 V002 留档核对",
            )
            return {
                "ok": False,
                "rolled_back": True,
                "message": f"V002 事务回滚：{exc}，残留已按 V002 留档核对",
            }

        # 已有一份完整结论时，重试不覆盖，而是开启新报告版本（r2、r3…）。
        if existing and completed_chapters >= set(planned):
            new_version = self._next_report_version(report)
            self._append_audit(
                report_code, "拒绝覆盖",
                f"上一份完整结论已存在，重试改写为新版本 {new_version}（不覆盖历史结论）",
            )
            report["报告版本"] = new_version
            report["status"] = "草稿"
            report["pending"] = True
            return {
                "ok": True,
                "message": f"上一份完整结论已保留，已开启新版本 {new_version}，请重新生成",
                "new_version": new_version,
                "rolled_back": False,
            }

        report_version = str(report.get("报告版本") or CONCLUSION_VERSION)
        if not report_version.startswith(CONCLUSION_VERSION):
            report_version = CONCLUSION_VERSION
            report["报告版本"] = report_version

        ab_by_experiment, anomaly_by_experiment = self._collect_evidence()
        chapters = self._plan_chapters(ab_by_experiment, anomaly_by_experiment)

        # 事务开始：先快照结论表，任何一步失败都整体还原，杜绝“页面完成、证据缺失”。
        snapshot = copy.deepcopy(store.rows(CONCLUSION_MODULE))
        try:
            report["status"] = "生成中"
            report["pending"] = True
            # 单调 id 分配器：store.next_id 只取 max+1 不递增，
            # 同一批 staged 行必须靠这个游标避免 id/结论编号撞车。
            id_cursor = store.next_id(CONCLUSION_MODULE)

            def alloc_id() -> int:
                nonlocal id_cursor
                allocated = id_cursor
                id_cursor += 1
                return allocated

            staged: list[dict[str, Any]] = []
            interrupted = False
            for index, chapter in enumerate(chapters):
                if chapter in completed_chapters:
                    # 断点续写：已完成章节保持不动，只补未完成章节。
                    continue
                if simulate_interrupt and index == len(chapters) - 1:
                    interrupted = True
                    break
                staged.extend(
                    self._build_chapter_rows(
                        chapter, report, report_version, strategy,
                        ab_by_experiment, anomaly_by_experiment, alloc_id,
                    )
                )

            # 证据与报告版本同写：任一结论缺证据或缺版本都判定事务失败。
            for row in staged:
                if not row.get("证据快照") or not row.get("报告版本") or not row.get("证据来源"):
                    raise TransactionRollbackError("存在证据行或报告版本缺失的结论，拒绝落库")
            store.rows(CONCLUSION_MODULE).extend(staged)
            store.bump_version(CONCLUSION_MODULE)

            if interrupted:
                report["status"] = "生成中"
                self._append_audit(
                    report_code, "生成中断",
                    f"章节 {chapters[-1]} 未完成，已保留 {len(staged)} 条结论，等待断点续写",
                )
                return {
                    "ok": True,
                    "interrupted": True,
                    "message": "报告生成中断，仅未完成章节待续写，已完成结论未受影响",
                    "completed_chapters": sorted(completed_chapters | {r["章节"] for r in staged}),
                    "pending_chapters": [c for c in chapters if c not in completed_chapters
                                         and c not in {r["章节"] for r in staged}],
                    "rolled_back": False,
                }

            report["status"] = "已完成"
            report["pending"] = False
            report["abnormal"] = False
            self._append_audit(
                report_code, "生成完成",
                f"版本 {report_version} 按{('证据时间' if strategy == 'time' else '报告章节')}"
                f"收敛写入 {len(staged)} 条结论，证据与版本同写",
            )
            return {
                "ok": True,
                "interrupted": False,
                "message": f"报告 {report_code} 已生成 {len(staged)} 条可追溯洞察结论",
                "report_version": report_version,
                "chapters": chapters,
                "rolled_back": False,
            }
        except TransactionRollbackError as exc:
            # 事务回滚：结论表还原到事务前快照，并按 V002 留档核对。
            store._tables[CONCLUSION_MODULE] = snapshot
            store.bump_version(CONCLUSION_MODULE)
            report["status"] = "草稿"
            report["pending"] = True
            report["abnormal"] = True
            self._append_audit(
                report_code, "事务回滚",
                f"V002 回滚：{exc}；本批结论未写入，结论表已还原并留档核对",
            )
            return {
                "ok": False,
                "rolled_back": True,
                "message": f"V002 事务回滚：{exc}，残留已按 V002 留档核对",
            }

    # ----------------------------------------------------------- 章节构建
    def _build_chapter_rows(
        self,
        chapter: str,
        report: dict[str, Any],
        report_version: str,
        strategy: str,
        ab_by_experiment: dict[str, list[dict[str, Any]]],
        anomaly_by_experiment: dict[str, list[dict[str, Any]]],
        alloc_id,
    ) -> list[dict[str, Any]]:
        if chapter == "AB结果分析":
            return [
                self._make_row(
                    report, report_version, chapter, experiment, ab_row, alloc_id,
                    evidence_type="AB对比结果",
                    finding=(
                        f"实验 {experiment} 的指标「{ab_row.get('分析指标')}」"
                        f"效果方向为 {ab_row.get('效果方向')}，"
                        f"实验组均值 {ab_row.get('实验组均值')} 对比对照组 {ab_row.get('对照组均值')}"
                    ),
                )
                for experiment, rows in ab_by_experiment.items() for ab_row in rows
            ]
        if chapter == "异常检测信号":
            return [
                self._make_row(
                    report, report_version, chapter, experiment, anomaly_row, alloc_id,
                    evidence_type="异常检测信号",
                    finding=(
                        f"实验 {experiment} 关联指标「{anomaly_row.get('监控指标')}」"
                        f"触发 {anomaly_row.get('告警级别')} 级别告警"
                        f"（算法：{anomaly_row.get('检测算法')}，阈值：{anomaly_row.get('波动阈值')}）"
                    ),
                )
                for experiment, rows in anomaly_by_experiment.items() for anomaly_row in rows
            ]

        # 冲突章节：同一实验同时有 AB 结果与异常信号。
        conflict_experiments = sorted(set(ab_by_experiment) & set(anomaly_by_experiment))
        rows: list[dict[str, Any]] = []
        for experiment in conflict_experiments:
            ab_row = ab_by_experiment[experiment][0]
            anomaly_row = anomaly_by_experiment[experiment][0]
            if strategy == "time":
                # 按证据时间收敛：生成单条综合发现，保留两个来源时间。
                evidence_time = f"{ab_row.get('生成时间')} / {anomaly_row.get('静默时段')}"
                rows.append(self._make_row(
                    report, report_version, chapter, experiment,
                    {"ab": ab_row, "anomaly": anomaly_row}, alloc_id,
                    evidence_type="AB+异常综合",
                    finding=(
                        f"实验 {experiment} 同时存在 AB 效果信号与异常告警，"
                        f"按证据时间收敛为单条综合发现：{ab_row.get('效果方向')}，"
                        f"异常级别 {anomaly_row.get('告警级别')}"
                    ),
                    evidence_time=evidence_time,
                    strategy_label="按证据时间收敛",
                ))
            else:
                # 按报告章节收敛：保留两项发现，统一报告版本。
                for source_row, evidence_type, finding in (
                    (ab_row, "AB对比结果",
                     f"实验 {experiment} 章节化发现（AB）：{ab_row.get('效果方向')}"),
                    (anomaly_row, "异常检测信号",
                     f"实验 {experiment} 章节化发现（异常）：{anomaly_row.get('告警级别')} 级告警"),
                ):
                    rows.append(self._make_row(
                        report, report_version, chapter, experiment, source_row, alloc_id,
                        evidence_type=evidence_type, finding=finding,
                        strategy_label="按报告章节收敛",
                    ))
        return rows

    def _make_row(
        self,
        report: dict[str, Any],
        report_version: str,
        chapter: str,
        experiment: str,
        evidence_row: dict[str, Any],
        alloc_id,
        *,
        evidence_type: str,
        finding: str,
        evidence_time: str | None = None,
        strategy_label: str = "单来源",
    ) -> dict[str, Any]:
        metric_code = self._metric_identifier(evidence_row)
        evidence_time = evidence_time or str(
            evidence_row.get("生成时间") or evidence_row.get("静默时段") or ""
        )
        next_id = alloc_id()
        row = {
            "id": next_id,
            "结论编号": f"INSC-{next_id:04d}",
            "报告编号": report.get("报告编号"),
            "报告版本": report_version,
            "章节": chapter,
            "实验编号": experiment,
            "指标标识": metric_code,
            "口径版本": "",  # 由指标定义模块 V002 联动补齐
            "关键发现": finding,
            "证据类型": evidence_type,
            "证据来源": self._evidence_source(evidence_type, evidence_row),
            "证据时间": evidence_time,
            "收敛策略": strategy_label,
            # 证据快照随结论同写，后续即使源表变化也能回查当时证据。
            "证据快照": copy.deepcopy(evidence_row),
            "章节状态": "已完成",
        }
        return row

    @staticmethod
    def _metric_identifier(evidence_row: dict[str, Any]) -> str:
        if "ab" in evidence_row:  # 综合证据
            return str(evidence_row["ab"].get("分析指标", ""))
        return str(evidence_row.get("分析指标") or evidence_row.get("监控指标") or "")

    @staticmethod
    def _evidence_source(evidence_type: str, evidence_row: dict[str, Any]) -> str:
        if evidence_type.startswith("AB"):
            key = "报告编号"
        else:
            key = "规则编号"
        if "ab" in evidence_row:
            return f"ab_result:{evidence_row['ab'].get('报告编号')}+anomaly_detect:{evidence_row['anomaly'].get('规则编号')}"
        return f"{'ab_result' if evidence_type.startswith('AB') else 'anomaly_detect'}:{evidence_row.get(key, evidence_row.get('id', ''))}"

    # ----------------------------------------------------------- 版本与审计
    def _next_report_version(self, report: dict[str, Any]) -> str:
        current = str(report.get("报告版本") or CONCLUSION_VERSION)
        base = current.split("-r")[0] or CONCLUSION_VERSION
        suffix = 2
        existing_versions = {str(r.get("报告版本")) for r in store.rows(MODULE)}
        while f"{base}-r{suffix}" in existing_versions:
            suffix += 1
        return f"{base}-r{suffix}"

    def _append_audit(self, report_code: str, action: str, detail: str) -> None:
        log = store._tables.setdefault("insight_conclusion_audit", [])
        log.append({
            "id": len(log) + 1,
            "报告编号": report_code,
            "动作": action,
            "明细": detail,
            "结论表版本": CONCLUSION_VERSION,
        })

    def list_audit_logs(self) -> list[dict[str, Any]]:
        return [dict(row) for row in store._tables.get("insight_conclusion_audit", [])]

    # ----------------------------------------------------------- 跨模块 V002
    def enrich_metric_caliber(self) -> dict[str, Any]:
        """指标定义 V002 联动：读取结论表的指标标识，为关键发现补齐口径版本。

        匹配顺序：指标定义.指标名称 → 指标定义.指标编号；匹配不上保留空值并计数。
        """
        metric_rows = store.rows("metric_def")
        updated = 0
        unmatched: list[str] = []
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
            "message": f"V002 跨模块联动：已为 {updated} 条结论补齐指标口径版本",
        }

    # ----------------------------------------------------------- 兼容旧看板
    def run_analysis(self, _params: dict[str, Any]) -> list[dict[str, Any]]:
        """保留“运行分析”看板能力：返回最新一版结论表供图表与表格渲染。"""
        rows = self.list_conclusions()
        if rows:
            return rows
        # 结论尚未生成时给出空态提示行，前端表格仍可正常渲染。
        return [{
            "id": 0,
            "分组": "暂无结论",
            "指标值": 0,
            "对比基准": 0,
            "差异": 0,
            "提示": "请先在洞察结论页生成结论",
        }]
