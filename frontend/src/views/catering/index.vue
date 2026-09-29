<template>
  <section class="page" data-module="catering">
    <header class="page-head">
      <div>
        <h2>航食配餐管理</h2>
        <p class="page-desc">
          按航站楼与餐食类型的配餐阈值校验单次配送：车辆额度不足或超阈值一律拦下达；
          配餐数据缺失按待确认处理；同航班同批次以最近一次为准。
        </p>
      </div>
      <div class="page-actions">
        <button class="btn primary" type="button" @click="openCreate">登记配餐任务</button>
        <button class="btn" type="button" @click="exportRows">导出配餐清单</button>
      </div>
    </header>

    <div class="stat-row">
      <article v-for="item in statCards" :key="item.label" class="stat-card" :class="item.danger ? 'danger' : ''">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value">{{ item.value }}</strong>
      </article>
    </div>

    <nav class="tabs">
      <button
        v-for="tab in tabs"
        :key="tab.key"
        class="tab"
        :class="{ active: activeTab === tab.key }"
        type="button"
        @click="switchTab(tab.key)"
      >
        {{ tab.label }}
        <em v-if="tab.badge" class="tab-badge">{{ tab.badge }}</em>
      </button>
    </nav>

    <!-- 配餐台账 -->
    <template v-if="activeTab === 'ledger'">
      <form class="filter-bar" @submit.prevent="reloadLedger">
        <label class="filter-item">
          <span>配餐编号/航班</span>
          <input v-model="ledgerQuery.keyword" placeholder="按编号或航班检索" />
        </label>
        <label class="filter-item">
          <span>状态</span>
          <select v-model="ledgerQuery.status">
            <option value="">全部</option>
            <option v-for="s in statuses" :key="s" :value="s">{{ s }}</option>
          </select>
        </label>
        <button class="btn" type="submit">查询</button>
        <button class="btn ghost" type="button" @click="resetLedgerFilter">重置条件</button>
        <button class="btn ghost" type="button" @click="showRules = !showRules">
          {{ showRules ? '收起配餐标准与额度' : '查看配餐标准与额度' }}
        </button>
      </form>

      <div v-if="showRules" class="rules-panel">
        <div class="rules-col">
          <h4>各航站楼配餐标准（单次上限 = 座位数 × 系数）</h4>
          <table class="data-table compact">
            <thead>
              <tr><th>航站楼</th><th>餐食类型</th><th>餐食系数</th></tr>
            </thead>
            <tbody>
              <tr v-for="std in standards" :key="String(std.id)">
                <td>{{ std.航站楼 }}</td>
                <td>{{ std.餐食类型 }}</td>
                <td>{{ std.餐食系数 }}</td>
              </tr>
            </tbody>
          </table>
          <form class="inline-form" @submit.prevent="saveStandard">
            <input v-model="standardForm.航站楼" placeholder="航站楼，如 T3" />
            <input v-model="standardForm.餐食类型" placeholder="餐食类型，如 正餐" />
            <input v-model="standardForm.餐食系数" placeholder="系数，如 1.0" />
            <button class="btn" type="submit">新增/更新标准</button>
          </form>
        </div>
        <div class="rules-col">
          <h4>配送车辆额度（占用实时取自配送中配送单）</h4>
          <table class="data-table compact">
            <thead>
              <tr><th>车辆</th><th>总额度</th><th>已占用</th><th>剩余</th></tr>
            </thead>
            <tbody>
              <tr v-for="q in quotas" :key="String(q.id)">
                <td>{{ q.配送车辆 }}</td>
                <td>{{ q.车辆额度 }}</td>
                <td>{{ q.已占用额度 }}</td>
                <td :class="q.剩余额度 < 0 ? 'danger-text' : ''">{{ q.剩余额度 }}</td>
              </tr>
            </tbody>
          </table>
          <form class="inline-form" @submit.prevent="saveQuota">
            <input v-model="quotaForm.配送车辆" placeholder="车辆编号" />
            <input v-model="quotaForm.车辆额度" placeholder="额度（份）" />
            <button class="btn" type="submit">新增/更新额度</button>
          </form>
        </div>
      </div>

      <table class="data-table">
        <thead>
          <tr>
            <th>配餐编号</th><th>对应航班</th><th>航站楼</th><th>批次号</th>
            <th>餐食类型</th><th>餐食数量</th><th>座位数</th><th>单次上限</th>
            <th>配送车辆</th><th>车辆余量</th><th>状态</th><th>可执行动作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in ledger" :key="String(row.id)" :class="{ 'row-danger': row.超限, 'row-warn': row.status === '待确认' }">
            <td>{{ row['配餐编号'] }}</td>
            <td>{{ row['对应航班'] }}</td>
            <td>{{ row['航站楼'] || '—' }}</td>
            <td>{{ row['批次号'] || '—' }}</td>
            <td>{{ row['餐食类型'] || '—' }}</td>
            <td>{{ row['餐食数量'] ?? '—' }}</td>
            <td>{{ row['航班座位数'] ?? '—' }}</td>
            <td>{{ row['规则校验']?.['配餐上限'] ?? '—' }}</td>
            <td>{{ row['配送车辆'] || '—' }}</td>
            <td>{{ row['规则校验']?.['车辆剩余额度'] ?? '—' }}</td>
            <td>
              {{ row.status }}
              <span v-if="row.超限" class="tag danger">超限</span>
              <span v-if="row.status === '待确认'" class="tag warn">待确认</span>
            </td>
            <td class="row-actions">
              <button
                v-if="row.status === '待配送' || row.status === '配送中'"
                class="link"
                type="button"
                @click="runAction('下达配送', row)"
              >下达配送</button>
              <button
                v-if="row.status === '配送中'"
                class="link"
                type="button"
                @click="runAction('确认送达', row)"
              >确认送达</button>
              <button
                v-if="row.status !== '已送达' && row.status !== '已作废' && row.status !== '已变更'"
                class="link"
                type="button"
                @click="runAction('变更餐食', row)"
              >变更餐食</button>
              <button
                v-if="row.status === '待确认'"
                class="link"
                type="button"
                @click="openConfirm(row)"
              >补录确认</button>
            </td>
          </tr>
          <tr v-if="!ledger.length">
            <td colspan="12" class="empty-state">暂无配餐任务，可先登记</td>
          </tr>
        </tbody>
      </table>
      <footer class="page-foot">
        <span>共 {{ ledgerTotal }} 条配餐任务</span>
        <span v-if="noticeMessage" class="error-text">{{ noticeMessage }}</span>
      </footer>
    </template>

    <!-- 配送席待办 -->
    <template v-else-if="activeTab === 'orders'">
      <form class="filter-bar" @submit.prevent="reloadOrders">
        <label class="filter-item">
          <span>配送单号/航班</span>
          <input v-model="orderQuery.keyword" placeholder="按单号或航班检索" />
        </label>
        <label class="filter-item">
          <span>状态</span>
          <select v-model="orderQuery.status">
            <option value="">全部</option>
            <option value="配送中">配送中</option>
            <option value="已送达">已送达</option>
            <option value="已作废">已作废</option>
          </select>
        </label>
        <button class="btn" type="submit">查询</button>
      </form>
      <table class="data-table">
        <thead>
          <tr>
            <th>配送单号</th><th>配餐编号</th><th>对应航班</th><th>航站楼</th><th>批次号</th>
            <th>餐食类型</th><th>餐食数量</th><th>配送车辆</th><th>配送人员</th>
            <th>下达时间</th><th>状态</th><th>操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in orders" :key="String(row.id)" :class="{ 'row-danger': row.超限, 'row-void': row.status === '已作废' }">
            <td>{{ row['配送单号'] }}<span v-if="row.超限" class="tag danger">超限</span></td>
            <td>{{ row['配餐编号'] }}</td>
            <td>{{ row['对应航班'] }}</td>
            <td>{{ row['航站楼'] }}</td>
            <td>{{ row['批次号'] }}</td>
            <td>{{ row['餐食类型'] }}</td>
            <td>{{ row['餐食数量'] }}</td>
            <td>{{ row['配送车辆'] }}</td>
            <td>{{ row['配送人员'] }}</td>
            <td>{{ row['下达时间'] }}</td>
            <td>{{ row.status }}<span v-if="row.作废原因" class="void-reason">（{{ row.作废原因 }}）</span></td>
            <td>
              <button v-if="row.status === '配送中'" class="link" type="button" @click="deliver(row)">
                确认送达
              </button>
            </td>
          </tr>
          <tr v-if="!orders.length">
            <td colspan="12" class="empty-state">配送席暂无待办配送单</td>
          </tr>
        </tbody>
      </table>
      <footer class="page-foot">
        <span>共 {{ orderTotal }} 张配送单，未消除超限 {{ summary['超限条数'] ?? 0 }} 条（与台账同一口径）</span>
        <span v-if="noticeMessage" class="error-text">{{ noticeMessage }}</span>
      </footer>
    </template>

    <!-- 超限告警 -->
    <template v-else>
      <form class="filter-bar" @submit.prevent="reloadAlerts">
        <label class="filter-item">
          <span>告警状态</span>
          <select v-model="alertStatus">
            <option value="">全部</option>
            <option value="未消除">未消除</option>
            <option value="已消除">已消除</option>
          </select>
        </label>
        <button class="btn" type="submit">查询</button>
      </form>
      <table class="data-table">
        <thead>
          <tr>
            <th>告警编号</th><th>配餐编号</th><th>对应航班</th><th>航站楼</th>
            <th>批次号</th><th>告警原因</th><th>告警时间</th><th>状态</th><th>处置说明</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in alerts" :key="String(row.id)" :class="{ 'row-danger': row.状态 === '未消除' }">
            <td>{{ row['告警编号'] }}</td>
            <td>{{ row['配餐编号'] }}</td>
            <td>{{ row['对应航班'] }}</td>
            <td>{{ row['航站楼'] }}</td>
            <td>{{ row['批次号'] }}</td>
            <td>{{ row['告警原因'] }}</td>
            <td>{{ row['告警时间'] }}</td>
            <td>{{ row['状态'] }}</td>
            <td>{{ row['处置说明'] || '—' }}</td>
          </tr>
          <tr v-if="!alerts.length">
            <td colspan="9" class="empty-state">暂无超限告警</td>
          </tr>
        </tbody>
      </table>
      <footer class="page-foot">
        <span>未消除超限 {{ summary['超限条数'] ?? 0 }} 条；超限条数统一取自告警台账</span>
      </footer>
    </template>

    <!-- 登记弹窗 -->
    <div v-if="createOpen" class="modal-mask" @click.self="createOpen = false">
      <div class="modal">
        <h3>登记配餐任务</h3>
        <p class="modal-tip">数据缺失的任务会落「待确认」，补齐确认前不许下达配送。</p>
        <div class="form-grid">
          <label v-for="f in createFields" :key="f.key" class="form-item">
            <span>{{ f.label }}{{ f.required ? ' *' : '' }}</span>
            <input v-model="createForm[f.key]" :placeholder="f.placeholder" />
          </label>
        </div>
        <div class="modal-actions">
          <button class="btn ghost" type="button" @click="createOpen = false">取消</button>
          <button class="btn primary" type="button" @click="submitCreate">登记</button>
        </div>
      </div>
    </div>

    <!-- 补录确认弹窗 -->
    <div v-if="confirmTarget" class="modal-mask" @click.self="confirmTarget = null">
      <div class="modal">
        <h3>补录确认 · {{ confirmTarget['配餐编号'] }}</h3>
        <p class="modal-tip">待确认项：{{ (confirmTarget['待确认项'] || []).join('、') || '无' }}</p>
        <div class="form-grid">
          <label v-for="f in confirmFields" :key="f.key" class="form-item">
            <span>{{ f.label }}</span>
            <input v-model="confirmForm[f.key]" :placeholder="f.placeholder" />
          </label>
        </div>
        <div class="modal-actions">
          <button class="btn ghost" type="button" @click="confirmTarget = null">取消</button>
          <button class="btn primary" type="button" @click="submitConfirm">提交确认</button>
        </div>
      </div>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'

import { request } from '@/api/client'

type Row = Record<string, any>

const ENDPOINT = '/api/catering'
const statuses = ['待确认', '待配送', '配送中', '已送达', '已变更', '已作废']

const activeTab = ref<'ledger' | 'orders' | 'alerts'>('ledger')
const noticeMessage = ref('')
const showRules = ref(false)

const summary = ref<Record<string, number>>({})
const ledger = ref<Row[]>([])
const ledgerTotal = ref(0)
const orders = ref<Row[]>([])
const orderTotal = ref(0)
const alerts = ref<Row[]>([])
const standards = ref<Row[]>([])
const quotas = ref<Row[]>([])

const ledgerQuery = reactive({ keyword: '', status: '' })
const orderQuery = reactive({ keyword: '', status: '' })
const alertStatus = ref('')

const tabs = computed(() => [
  { key: 'ledger' as const, label: '配餐台账', badge: 0 },
  { key: 'orders' as const, label: '配送席待办', badge: summary.value['配送席待办'] || 0 },
  { key: 'alerts' as const, label: '超限告警', badge: summary.value['超限条数'] || 0 },
])

const statCards = computed(() => [
  { label: '待确认', value: summary.value['待确认'] ?? 0, danger: false },
  { label: '待配送', value: summary.value['待配送'] ?? 0, danger: false },
  { label: '配送中', value: summary.value['配送中'] ?? 0, danger: false },
  { label: '配送席待办', value: summary.value['配送席待办'] ?? 0, danger: false },
  { label: '超限条数（统一口径）', value: summary.value['超限条数'] ?? 0, danger: (summary.value['超限条数'] ?? 0) > 0 },
])

const createFields = [
  { key: '配餐编号', label: '配餐编号', required: true, placeholder: 'CATE-0104' },
  { key: '对应航班', label: '对应航班', required: true, placeholder: 'CA1201' },
  { key: '航站楼', label: '航站楼', required: false, placeholder: 'T1 / T2' },
  { key: '航班座位数', label: '航班座位数', required: false, placeholder: '160' },
  { key: '批次号', label: '批次号', required: false, placeholder: 'B20260929-04' },
  { key: '餐食类型', label: '餐食类型', required: false, placeholder: '正餐 / 点心 / 特餐' },
  { key: '餐食数量', label: '餐食数量', required: false, placeholder: '150' },
  { key: '配送车辆', label: '配送车辆', required: false, placeholder: 'CATE-CAR-01' },
  { key: '配送人员', label: '配送人员', required: false, placeholder: '配送员姓名' },
  { key: '预计送达', label: '预计送达', required: false, placeholder: '2026-09-29 10:30' },
]
const createOpen = ref(false)
const createForm = reactive<Record<string, string>>({})

const confirmFields = createFields.filter((f) =>
  ['航站楼', '航班座位数', '批次号', '餐食类型', '餐食数量', '配送车辆', '配送人员', '预计送达'].includes(f.key),
)
const confirmTarget = ref<Row | null>(null)
const confirmForm = reactive<Record<string, string>>({})
const standardForm = reactive({ 航站楼: '', 餐食类型: '', 餐食系数: '' })
const quotaForm = reactive({ 配送车辆: '', 车辆额度: '' })

function switchTab(key: 'ledger' | 'orders' | 'alerts') {
  activeTab.value = key
  noticeMessage.value = ''
  if (key === 'orders') void reloadOrders()
  if (key === 'alerts') void reloadAlerts()
}

async function readJson(response: Response) {
  const payload = await response.json().catch(() => ({}))
  if (!response.ok || payload.ok === false) {
    throw new Error(payload.message || payload.detail || '操作未生效，请稍后重试')
  }
  return payload
}

async function loadSummary() {
  try {
    summary.value = await (await request(`${ENDPOINT}/summary`)).json()
  } catch {
    /* 统计拉取失败不阻塞列表 */
  }
}

async function reloadLedger() {
  noticeMessage.value = ''
  const params = new URLSearchParams()
  if (ledgerQuery.keyword) params.set('keyword', ledgerQuery.keyword)
  if (ledgerQuery.status) params.set('status', ledgerQuery.status)
  try {
    const payload = await readJson(await request(`${ENDPOINT}?${params.toString()}`))
    ledger.value = payload.items ?? []
    ledgerTotal.value = payload.total ?? 0
  } catch (error) {
    noticeMessage.value = error instanceof Error ? error.message : '配餐台账读取失败'
  }
  void loadSummary()
}

function resetLedgerFilter() {
  ledgerQuery.keyword = ''
  ledgerQuery.status = ''
  void reloadLedger()
}

async function reloadOrders() {
  noticeMessage.value = ''
  const params = new URLSearchParams()
  if (orderQuery.keyword) params.set('keyword', orderQuery.keyword)
  if (orderQuery.status) params.set('status', orderQuery.status)
  try {
    const payload = await readJson(await request(`${ENDPOINT}/orders?${params.toString()}`))
    orders.value = payload.items ?? []
    orderTotal.value = payload.total ?? 0
  } catch (error) {
    noticeMessage.value = error instanceof Error ? error.message : '配送席待办读取失败'
  }
  void loadSummary()
}

async function reloadAlerts() {
  const params = new URLSearchParams()
  if (alertStatus.value) params.set('status', alertStatus.value)
  try {
    const payload = await readJson(await request(`${ENDPOINT}/alerts?${params.toString()}`))
    alerts.value = payload.items ?? []
  } catch (error) {
    noticeMessage.value = error instanceof Error ? error.message : '超限告警读取失败'
  }
  void loadSummary()
}

async function reloadRules() {
  const [std, q] = await Promise.all([
    request(`${ENDPOINT}/standards`),
    request(`${ENDPOINT}/quotas`),
  ])
  standards.value = ((await std.json()).items ?? [])
  quotas.value = ((await q.json()).items ?? [])
}

async function runAction(action: string, row: Row) {
  noticeMessage.value = ''
  try {
    const payload = await readJson(
      await request(`${ENDPOINT}/${row.id}/actions`, {
        method: 'POST',
        body: JSON.stringify({ values: { action } }),
      }),
    )
    noticeMessage.value = payload.message
  } catch (error) {
    // 拦截类信息（额度不足/超限/待确认）同样要让配餐席看到原因
    noticeMessage.value = error instanceof Error ? error.message : '操作失败'
  }
  await Promise.all([reloadLedger(), reloadOrders(), reloadRules()])
}

async function deliver(row: Row) {
  noticeMessage.value = ''
  try {
    const payload = await readJson(await request(`${ENDPOINT}/orders/${row.id}/deliver`, { method: 'POST' }))
    noticeMessage.value = payload.message
  } catch (error) {
    noticeMessage.value = error instanceof Error ? error.message : '确认送达失败'
  }
  await Promise.all([reloadOrders(), reloadLedger()])
}

function openCreate() {
  Object.keys(createForm).forEach((key) => delete createForm[key])
  createOpen.value = true
}

async function submitCreate() {
  try {
    const payload = await readJson(
      await request(ENDPOINT, { method: 'POST', body: JSON.stringify({ values: { ...createForm } }) }),
    )
    createOpen.value = false
    noticeMessage.value = payload.message
    await reloadLedger()
  } catch (error) {
    noticeMessage.value = error instanceof Error ? error.message : '登记失败'
  }
}

function openConfirm(row: Row) {
  confirmTarget.value = row
  Object.keys(confirmForm).forEach((key) => delete confirmForm[key])
}

async function submitConfirm() {
  if (!confirmTarget.value) return
  try {
    const payload = await readJson(
      await request(`${ENDPOINT}/${confirmTarget.value.id}/confirm`, {
        method: 'POST',
        body: JSON.stringify({ values: { ...confirmForm } }),
      }),
    )
    noticeMessage.value = payload.message
    confirmTarget.value = null
    await reloadLedger()
  } catch (error) {
    noticeMessage.value = error instanceof Error ? error.message : '补录确认失败'
  }
}

async function saveStandard() {
  try {
    await readJson(
      await request(`${ENDPOINT}/standards`, {
        method: 'POST',
        body: JSON.stringify({ values: { ...standardForm } }),
      }),
    )
    standardForm.航站楼 = standardForm.餐食类型 = standardForm.餐食系数 = ''
    await reloadRules()
  } catch (error) {
    noticeMessage.value = error instanceof Error ? error.message : '标准维护失败'
  }
}

async function saveQuota() {
  try {
    await readJson(
      await request(`${ENDPOINT}/quotas`, {
        method: 'POST',
        body: JSON.stringify({ values: { ...quotaForm } }),
      }),
    )
    quotaForm.配送车辆 = quotaForm.车辆额度 = ''
    await reloadRules()
  } catch (error) {
    noticeMessage.value = error instanceof Error ? error.message : '额度维护失败'
  }
}

function exportRows() {
  window.open(`${ENDPOINT}/export`, '_blank')
}

onMounted(async () => {
  await Promise.all([reloadLedger(), reloadRules()])
})
</script>
