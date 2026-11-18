<template>
  <section class="page" data-module="insight_report">
    <header class="page-head">
      <div>
        <h2>洞察报告</h2>
        <p class="page-desc">聚合 AB 对比结果与异常检测信号，沉淀可回查实验、指标口径与证据来源的洞察结论（V002）。</p>
      </div>
      <div class="page-actions">
        <button class="btn" type="button" data-testid="tab-report" :class="{ primary: activeTab === 'report' }" @click="switchTab('report')">报告工作台</button>
        <button class="btn" type="button" data-testid="tab-conclusion" :class="{ primary: activeTab === 'conclusion' }" @click="switchTab('conclusion')">洞察结论数据表</button>
      </div>
    </header>

    <!-- ======================= Tab 1：报告工作台（保留看板交互） ======================= -->
    <div v-show="activeTab === 'report'">
      <div class="analysis-summary">
        <article v-for="m in summaryMetrics" :key="m.label" class="analysis-card" data-testid="kpi-card">
          <span style="font-size:12px;color:var(--muted)">{{ m.label }}</span>
          <strong style="font-size:22px;display:block">{{ m.value }}</strong>
          <span :class="m.trend > 0 ? 'trend-up' : 'trend-down'" style="font-size:12px">{{ m.trend > 0 ? '↑' : '↓' }}{{ Math.abs(m.trend) }}%</span>
        </article>
      </div>
      <form class="analysis-form" @submit.prevent="runAnalysis">
        <label v-for="field in paramFields" :key="field"><span>{{ field }}</span>
          <select v-if="field.includes('方法')||field.includes('策略')||field.includes('粒度')" v-model="params[field]"><option v-for="o in options[field]" :key="o" :value="o">{{ o }}</option></select>
          <input v-else v-model="params[field]" :placeholder="`请输入${field}`" />
        </label>
        <button class="btn primary" type="submit" data-testid="run-analysis">运行分析</button>
      </form>
      <div v-if="chartData.length" class="analysis-chart">
        <div v-for="(bar, i) in chartData" :key="i" class="analysis-bar" :style="{ height: barHeight(bar.value) + 'px' }"><span>{{ bar.label }}</span></div>
      </div>

      <h3 style="margin:16px 0 8px">报告列表（与详情页同源）</h3>
      <table class="data-table" v-if="reports.length">
        <thead><tr><th v-for="c in reportColumns" :key="String(c)">{{ c }}</th><th>状态</th><th>操作</th></tr></thead>
        <tbody>
          <tr v-for="row in reports" :key="String(row.id)" :data-testid="`report-row-${row.id}`" :data-status="String(row.status)">
            <td v-for="c in reportColumns" :key="String(c)">{{ row[c] ?? '—' }}</td>
            <td>{{ row.status }}</td>
            <td class="row-actions">
              <button class="link" type="button" data-testid="open-conclusion-panel" @click="openReportDetail(Number(row.id))">查看洞察结论</button>
            </td>
          </tr>
        </tbody>
      </table>
      <p v-if="!reports.length" style="text-align:center;color:var(--muted)">暂无洞察报告</p>
    </div>

    <!-- ======================= Tab 2：洞察结论数据表 ======================= -->
    <div v-show="activeTab === 'conclusion'">
      <div class="stat-row">
        <article v-for="card in overviewCards" :key="card.label" class="stat-card" data-testid="conclusion-overview-card">
          <span class="stat-label">{{ card.label }}</span>
          <strong class="stat-value">{{ card.value }}</strong>
        </article>
      </div>

      <form class="filter-bar" @submit.prevent="reloadConclusions">
        <label class="filter-item"><span>报告编号</span>
          <select data-testid="report-filter" v-model="reportFilter">
            <option value="">全部报告</option>
            <option v-for="r in reports" :key="String(r.id)" :value="String(r.id)">{{ r.报告编号 }}</option>
          </select>
        </label>
        <label class="filter-item"><span>收敛策略</span>
          <select data-testid="strategy-select" v-model="strategy">
            <option value="chapter">按报告章节收敛（保留两项发现，统一版本）</option>
            <option value="time">按证据时间收敛（单条综合发现，保留来源时间）</option>
          </select>
        </label>
        <button class="btn primary" type="button" data-testid="generate-conclusion" @click="generate()">生成/续写结论</button>
        <button class="btn" type="button" data-testid="simulate-interrupt" @click="generate(true)">模拟中断</button>
        <button class="btn" type="button" data-testid="simulate-rollback" @click="generate(false, true)">模拟残留回滚</button>
        <button class="btn" type="button" data-testid="enrich-caliber" @click="enrichCaliber">V002补齐口径版本</button>
        <button class="btn ghost" type="button" data-testid="reload-conclusion" @click="reloadConclusions">刷新</button>
      </form>

      <p v-if="actionMessage" class="error-text" data-testid="action-message">{{ actionMessage }}</p>

      <table class="data-table" v-if="conclusions.length">
        <thead>
          <tr><th v-for="c in conclusionColumns" :key="String(c)">{{ c }}</th><th>操作</th></tr>
        </thead>
        <tbody>
          <tr v-for="row in conclusions" :key="String(row.id)" :data-testid="`conclusion-row-${row.id}`" :data-chapter="String(row.章节)">
            <td v-for="c in conclusionColumns" :key="String(c)">{{ row[c] ?? '—' }}</td>
            <td class="row-actions">
              <button class="link" type="button" :data-testid="`view-evidence-${row.id}`" @click="openConclusionDetail(row)">证据回查</button>
            </td>
          </tr>
          <tr v-if="!conclusions.length">
            <td :colspan="conclusionColumns.length + 1" class="empty-state">暂无洞察结论，请选择报告后点击“生成/续写结论”</td>
          </tr>
        </tbody>
      </table>
      <p v-else style="text-align:center;color:var(--muted)">暂无洞察结论，请选择报告后生成</p>

      <h3 style="margin:16px 0 8px">V002 留档核对（事务回滚 / 中断续写 / 版本保护）</h3>
      <table class="data-table" v-if="auditLogs.length">
        <thead><tr><th>报告编号</th><th>动作</th><th>明细</th><th>结论表版本</th></tr></thead>
        <tbody>
          <tr v-for="log in auditLogs" :key="String(log.id)" :data-testid="`audit-row-${log.id}`">
            <td>{{ log.报告编号 }}</td><td>{{ log.动作 }}</td><td>{{ log.明细 }}</td><td>{{ log.结论表版本 }}</td>
          </tr>
        </tbody>
      </table>
      <p v-else style="text-align:center;color:var(--muted)">暂无留档记录</p>
    </div>

    <!-- ======================= 报告详情：结论与证据回查 ======================= -->
    <div v-if="detailReport" class="modal-backdrop" data-testid="report-detail-modal">
      <div class="modal" style="max-width:960px">
        <h3>报告详情 · {{ detailReport.报告编号 }}（{{ detailReport.报告版本 }}）</h3>
        <p class="page-desc">{{ detailReport.分析主题 }} · 数据来源：{{ detailReport.数据来源 }} · 状态：{{ detailReport.status }}</p>
        <table class="data-table">
          <thead><tr><th v-for="c in conclusionColumns" :key="String(c)">{{ c }}</th></tr></thead>
          <tbody>
            <tr v-for="row in detailConclusions" :key="String(row.id)" :data-testid="`detail-conclusion-${row.id}`">
              <td v-for="c in conclusionColumns" :key="String(c)">{{ row[c] ?? '—' }}</td>
            </tr>
          </tbody>
        </table>
        <p v-if="!detailConclusions.length" class="empty-state">该报告尚未生成洞察结论</p>
        <div class="page-actions">
          <button class="btn primary" type="button" @click="detailReport = null">关闭</button>
        </div>
      </div>
    </div>

    <!-- ======================= 证据回查弹窗 ======================= -->
    <div v-if="evidenceRow" class="modal-backdrop" data-testid="evidence-modal">
      <div class="modal" style="max-width:760px">
        <h3>证据回查 · {{ evidenceRow.结论编号 }}</h3>
        <p class="page-desc">实验：{{ evidenceRow.实验编号 }} ｜ 指标标识：{{ evidenceRow.指标标识 }} ｜ 口径版本：{{ evidenceRow.口径版本 || '待V002补齐' }}</p>
        <p class="page-desc">证据来源：{{ evidenceRow.证据来源 }} ｜ 证据时间：{{ evidenceRow.证据时间 }} ｜ 报告版本：{{ evidenceRow.报告版本 }}</p>
        <pre data-testid="evidence-snapshot" style="background:var(--bg,#f7f8fa);padding:12px;border-radius:8px;overflow:auto;max-height:360px">{{ formatSnapshot(evidenceRow.证据快照) }}</pre>
        <div class="page-actions">
          <button class="btn primary" type="button" @click="evidenceRow = null">关闭</button>
        </div>
      </div>
    </div>

    <footer class="page-foot">
      <span>共 {{ conclusions.length }} 条洞察结论，结论表版本 V002</span>
    </footer>
  </section>
</template>
<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { request } from '@/api/client'

const ENDPOINT = '/api/insight_report'
type Row = Record<string, string | number | null | Record<string, unknown>>

const summaryMetrics = ref<{ label: string; value: number; trend: number }[]>([])
const reports = ref<Row[]>([])
const conclusions = ref<Row[]>([])
const auditLogs = ref<Row[]>([])
const overview = ref<Record<string, any>>({})
const chartData = ref<{ label: string; value: number }[]>([])
const activeTab = ref<'report' | 'conclusion'>('report')
const reportFilter = ref('')
const strategy = ref<'chapter' | 'time'>('chapter')
const actionMessage = ref('')
const params = ref<Record<string, string>>({})
const detailReport = ref<Row | null>(null)
const detailConclusions = ref<Row[]>([])
const evidenceRow = ref<Row | null>(null)

const paramFields = ['报告编号', '分析主题', '数据来源', '关键发现'].slice(0, 4)
const options: Record<string, string[]> = { 统计粒度: ['按日', '按周', '按月'], 分组字段: ['全量', '按类型', '按区域'], 比较方法: ['环比', '同比', '绝对值'], 时间范围: ['最近7天', '最近30天', '最近90天'] }
const reportColumns = ['报告编号', '分析主题', '数据来源', '关键发现', '报告版本']
const conclusionColumns = ['结论编号', '报告版本', '章节', '实验编号', '指标标识', '口径版本', '证据类型', '证据来源', '证据时间', '收敛策略']

const overviewCards = computed(() => [
  { label: '可追溯结论数', value: overview.value['结论总数'] ?? conclusions.value.length },
  { label: 'AB证据', value: overview.value['证据来源分布']?.['AB对比结果'] ?? 0 },
  { label: '异常证据', value: overview.value['证据来源分布']?.['异常检测信号'] ?? 0 },
  { label: '综合证据', value: overview.value['证据来源分布']?.['AB+异常综合'] ?? 0 },
  { label: '口径版本覆盖', value: overview.value['口径版本覆盖'] ?? '0/0' },
])

function barHeight(v: number) {
  const max = Math.max(...chartData.value.map((d) => d.value), 1)
  return Math.max(10, (v / max) * 140)
}

function switchTab(tab: 'report' | 'conclusion') {
  activeTab.value = tab
  if (tab === 'conclusion') void reloadConclusions()
}

async function fetchSummary() {
  try {
    const res = await request(`${ENDPOINT}/summary`)
    summaryMetrics.value = (await res.json()).metrics || []
  } catch {
    summaryMetrics.value = []
  }
}

async function fetchReports() {
  const res = await request(ENDPOINT)
  reports.value = (await res.json()).items ?? []
}

async function reloadConclusions() {
  actionMessage.value = ''
  const query = reportFilter.value ? `?report_id=${reportFilter.value}` : ''
  const [listRes, overviewRes, auditRes] = await Promise.all([
    request(`${ENDPOINT}/conclusions${query}`),
    request(`${ENDPOINT}/conclusions/overview${query}`),
    request(`${ENDPOINT}/conclusions/audit`),
  ])
  conclusions.value = (await listRes.json()).items ?? []
  overview.value = await overviewRes.json()
  auditLogs.value = (await auditRes.json()).items ?? []
}

async function generate(interrupt = false, residual = false) {
  if (!reportFilter.value) {
    actionMessage.value = '请先在“报告编号”下拉中选择一份报告'
    return
  }
  actionMessage.value = ''
  const values: Record<string, string> = { strategy: strategy.value }
  if (interrupt) values.simulate_interrupt = 'true'
  if (residual) values.simulate_residual = 'true'
  const res = await request(`${ENDPOINT}/${reportFilter.value}/conclusions/generate`, {
    method: 'POST',
    body: JSON.stringify({ values }),
  })
  const data = await res.json()
  // 先刷新数据再写消息：reloadConclusions 内部会清空旧消息，顺序反了会把本次提示抹掉。
  await Promise.all([reloadConclusions(), fetchReports(), fetchSummary()])
  actionMessage.value = data.message || (data.ok ? '生成完成' : '生成失败')
}

async function enrichCaliber() {
  const res = await request(`${ENDPOINT}/conclusions/enrich-caliber`, { method: 'POST' })
  const data = await res.json()
  await reloadConclusions()
  actionMessage.value = data.message
}

async function openReportDetail(reportId: number) {
  const reportRes = await request(`${ENDPOINT}/${reportId}`)
  detailReport.value = await reportRes.json()
  const conclusionRes = await request(`${ENDPOINT}/conclusions?report_id=${reportId}`)
  detailConclusions.value = (await conclusionRes.json()).items ?? []
}

function openConclusionDetail(row: Row) {
  evidenceRow.value = row
}

function formatSnapshot(snapshot: unknown): string {
  try {
    return JSON.stringify(snapshot, null, 2)
  } catch {
    return String(snapshot)
  }
}

async function runAnalysis() {
  try {
    const res = await request(`${ENDPOINT}/run`, { method: 'POST', body: JSON.stringify({ values: params.value }) })
    const data = await res.json()
    const items = data.items || []
    chartData.value = items
      .map((r: any) => ({
        label: String(r['结论编号'] ?? r['分组'] ?? Object.values(r)[0]),
        value: Number(r['id'] ?? 0),
      }))
      .slice(0, 6)
  } catch {
    chartData.value = []
  }
}

onMounted(async () => {
  await Promise.all([fetchSummary(), fetchReports()])
})
</script>
