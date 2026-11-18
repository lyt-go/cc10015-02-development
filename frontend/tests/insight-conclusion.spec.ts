import { expect, test } from '@playwright/test'

const BASE = 'http://127.0.0.1:5173'

test.describe('洞察报告 V002：洞察结论数据表', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto(`${BASE}/insight_report`)
  })

  test('工作台保留看板交互，KPI与运行分析可用', async ({ page }) => {
    await expect(page.getByTestId('kpi-card').first()).toBeVisible()
    await expect(page.getByTestId('run-analysis')).toBeVisible()
    await page.getByTestId('run-analysis').click()
    // 结论表当前为空时返回空态行，表格或柱状至少不报错
    await expect(page.getByTestId('tab-conclusion')).toBeVisible()
  })

  test('结论页展示概览、生成结论、证据回查与口径补齐', async ({ page }) => {
    await page.getByTestId('tab-conclusion').click()
    await expect(page.getByTestId('conclusion-overview-card').first()).toBeVisible()

    // 选择报告1并按章节策略生成
    await page.getByTestId('report-filter').selectOption({ index: 1 })
    await page.getByTestId('generate-conclusion').click()
    await expect(page.getByTestId('action-message')).toContainText(/已生成|已保留/)
    const rows = page.locator('[data-testid^="conclusion-row-"]')
    await expect(rows.first()).toBeVisible()
    const count = await rows.count()
    expect(count).toBeGreaterThanOrEqual(6)

    // 概览卡片与表格同源
    const overviewText = await page.getByTestId('conclusion-overview-card').first().innerText()
    expect(overviewText).toContain(String(count))

    // V002 口径补齐
    await page.getByTestId('enrich-caliber').click()
    await expect(page.getByTestId('action-message')).toContainText('口径版本')

    // 证据回查弹窗显示快照
    await page.getByTestId(/view-evidence-/).first().click()
    await expect(page.getByTestId('evidence-modal')).toBeVisible()
    const snapshot = await page.getByTestId('evidence-snapshot').innerText()
    expect(snapshot.length).toBeGreaterThan(20)
    await page.getByRole('button', { name: '关闭' }).first().click()

    // 审计留档表存在
    await expect(page.locator('[data-testid^="audit-row-"]').first()).toBeVisible()
  })

  test('模拟中断后续写只补未完成章节，重试不覆盖完整结论', async ({ page }) => {
    await page.getByTestId('tab-conclusion').click()
    await page.getByTestId('report-filter').selectOption({ index: 3 }) // 报告3

    await page.getByTestId('simulate-interrupt').click()
    await expect(page.getByTestId('action-message')).toContainText('中断')
    const firstCount = await page.locator('[data-testid^="conclusion-row-"]').count()
    expect(firstCount).toBeGreaterThan(0)

    // 续写
    await page.getByTestId('generate-conclusion').click()
    await expect(page.getByTestId('action-message')).toContainText('已生成')
    const secondCount = await page.locator('[data-testid^="conclusion-row-"]').count()
    expect(secondCount).toBeGreaterThan(firstCount)

    // 再次重试：开启新版本，不覆盖
    await page.getByTestId('generate-conclusion').click()
    await expect(page.getByTestId('action-message')).toContainText('新版本')
    expect(await page.locator('[data-testid^="conclusion-row-"]').count()).toBe(secondCount)
  })

  test('模拟残留触发V002回滚并留档', async ({ page }) => {
    await page.getByTestId('tab-conclusion').click()
    await page.getByTestId('report-filter').selectOption({ index: 2 }) // 报告2
    await page.getByTestId('simulate-rollback').click()
    await expect(page.getByTestId('action-message')).toContainText('回滚')
    // 回滚审计行出现
    await expect(page.locator('[data-testid^="audit-row-"]').filter({ hasText: '事务回滚' }).first()).toBeVisible()
  })

  test('按证据时间收敛生成单条综合发现（报告2回滚后为干净状态）', async ({ page }) => {
    await page.getByTestId('tab-conclusion').click()
    await page.getByTestId('report-filter').selectOption({ index: 2 }) // 报告2
    await page.getByTestId('strategy-select').selectOption('time')
    await page.getByTestId('generate-conclusion').click()
    await expect(page.getByTestId('action-message')).toContainText('已生成')
    // 冲突章节每行都是“按证据时间收敛”且证据类型为综合
    const conflictRows = page.locator('[data-chapter="跨来源综合研判"]')
    await expect(conflictRows.first()).toBeVisible()
    const texts = await conflictRows.allInnerTexts()
    for (const t of texts) expect(t).toContain('按证据时间收敛')
  })

  test('工作台与详情页业务数据一致', async ({ page }) => {
    // 用一份此前未生成过结论的报告，避免共享后端状态下混入历史版本行。
    await page.getByTestId('tab-conclusion').click()
    await page.getByTestId('report-filter').selectOption({ index: 3 }) // INSI-0003
    await page.getByTestId('generate-conclusion').click()
    await expect(page.getByTestId('action-message')).toContainText(/已生成|新版本/)
    const tableCount = await page.locator('[data-testid^="conclusion-row-"]').count()
    expect(tableCount).toBeGreaterThan(0)
    const selectedReportId = await page.getByTestId('report-filter').inputValue()

    await page.getByTestId('tab-report').click()
    await page
      .locator(`[data-testid="report-row-${selectedReportId}"]`)
      .getByTestId('open-conclusion-panel')
      .click()
    await expect(page.getByTestId('report-detail-modal')).toBeVisible()
    const detailCount = await page.locator('[data-testid^="detail-conclusion-"]').count()
    // 工作台筛选视图与详情弹窗读同一个 report_id 接口，行数必须完全一致。
    expect(detailCount).toBe(tableCount)
  })
})
