<template>
  <section class="page" data-module="catering">
    <header class="page-head">
      <div>
        <h2>航食配餐管理</h2>
        <p class="page-desc">按餐食类型×航班座位数×航站楼判定单次配送上限，车辆额度不足不许下达并说明差额，超限单独告警；同航班同批次冲突以最近一次为准。</p>
      </div>
      <div class="page-actions">
        <button class="btn primary" type="button" @click="openCreate">登记配餐任务</button>
        <button class="btn" type="button" @click="exportRows">导出航食配餐清单</button>
      </div>
    </header>

    <div class="stat-row">
      <article v-for="item in stats" :key="item.label" class="stat-card">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value" :class="{ 'stat-warn': item.warn }">{{ item.value }}</strong>
      </article>
    </div>

    <form class="filter-bar" @submit.prevent="reload">
      <label class="filter-item">
        <span>关键词</span>
        <input v-model="filters.keyword" placeholder="按配餐编号或航班检索" />
      </label>
      <label class="filter-item">
        <span>配餐状态</span>
        <select v-model="filters.status">
          <option value="">全部</option>
          <option v-for="s in statuses" :key="s" :value="s">{{ s }}</option>
        </select>
      </label>
      <label class="filter-item">
        <span>超限口径</span>
        <select v-model="filters.over_limit">
          <option value="">全部</option>
          <option value="true">仅看超限</option>
          <option value="false">仅看正常</option>
        </select>
      </label>
      <button class="btn" type="submit">查询</button>
      <button class="btn ghost" type="button" @click="resetFilters">重置条件</button>
    </form>

    <table class="data-table">
      <thead>
        <tr>
          <th v-for="column in columns" :key="column">{{ column }}</th>
          <th>可执行动作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="String(row.id)">
          <td v-for="column in columns" :key="column">
            <template v-if="column === '超限'">
              <span v-if="row._评估?.over_limit" class="badge badge-warn">超限 {{ row._评估.over_amount }}</span>
              <span v-else class="badge badge-ok">正常</span>
            </template>
            <template v-else>{{ row[column] ?? '—' }}</template>
          </td>
          <td class="row-actions">
            <button v-for="action in actions" :key="action" class="link" type="button" @click="runAction(action, row)">
              {{ action }}
            </button>
            <button class="link" type="button" @click="openDeliveryNote(row)">配送单</button>
          </td>
        </tr>
        <tr v-if="!rows.length">
          <td :colspan="columns.length + 1" class="empty-state">暂无航食配餐数据，可先登记配餐任务</td>
        </tr>
      </tbody>
    </table>

    <footer class="page-foot">
      <span>共 {{ total }} 条航食配餐记录</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
      <span v-if="infoMessage" class="info-text">{{ infoMessage }}</span>
    </footer>

    <section class="todo-panel">
      <header class="todo-head">
        <h3>配送席待办</h3>
        <button class="link" type="button" @click="loadTodos">刷新</button>
      </header>
      <ul v-if="todos.length" class="todo-list">
        <li v-for="t in todos" :key="t.id" class="todo-item">
          <span class="badge" :class="t.状态 === '已送达' ? 'badge-ok' : 'badge-warn'">{{ t.事项 }}</span>
          <strong>{{ t.配餐编号 }}</strong>
          <span>{{ t.对应航班 }} / {{ t.批次 }}</span>
          <span>{{ t.餐食数量 }} 份 · {{ t.配送车辆 }} · {{ t.配送人员 }}</span>
          <span class="muted">{{ t.写入时间 }}</span>
        </li>
      </ul>
      <p v-else class="empty-state">暂无配送待办，下达配送或确认送达后会同步到这里</p>
    </section>

    <div v-if="showCreate" class="modal-mask" @click.self="showCreate = false">
      <div class="modal">
        <h3>登记配餐任务</h3>
        <form class="modal-form" @submit.prevent="submitCreate">
          <label v-for="f in createFields" :key="f.key" class="modal-field">
            <span>{{ f.label }}</span>
            <select v-if="f.options" v-model="createForm[f.key]">
              <option value="">请选择</option>
              <option v-for="opt in f.options" :key="opt" :value="opt">{{ opt }}</option>
            </select>
            <input v-else v-model="createForm[f.key]" :type="f.type || 'text'" :placeholder="f.placeholder" />
          </label>
          <footer class="modal-foot">
            <button class="btn ghost" type="button" @click="showCreate = false">取消</button>
            <button class="btn primary" type="submit">提交登记</button>
          </footer>
        </form>
      </div>
    </div>

    <div v-if="showNote" class="modal-mask" @click.self="showNote = false">
      <div class="modal">
        <h3>配送单 · {{ note?.配餐编号 }}</h3>
        <div v-if="note" class="note-body">
          <p><span class="muted">对应航班：</span>{{ note.对应航班 }}　<span class="muted">批次：</span>{{ note.批次 }}</p>
          <p><span class="muted">航站楼：</span>{{ note.评估?.terminal }}　<span class="muted">餐食类型：</span>{{ note.评估?.meal_type }}</p>
          <p><span class="muted">航班座位数：</span>{{ note.评估?.seats }}　<span class="muted">单次配送上限：</span><strong>{{ note.评估?.cap }}</strong> 份</p>
          <p><span class="muted">餐食数量：</span>{{ note.评估?.quantity }} 份　<span class="muted">配送车辆额度：</span>{{ note.评估?.quota }} 份</p>
          <p v-if="note.评估?.over_limit" class="error-text">超限：超出 {{ note.评估.over_amount }} 份（台账与配送单同一口径）</p>
          <p v-else class="info-text">未超限</p>
          <p v-if="note.评估?.quota_shortfall > 0" class="error-text">车辆额度不足，还差 {{ note.评估.quota_shortfall }} 份，不许下达</p>
        </div>
        <footer class="modal-foot">
          <button class="btn primary" type="button" @click="showNote = false">关闭</button>
        </footer>
      </div>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'

import { request } from '@/api/client'

type Row = Record<string, any>

const ENDPOINT = '/api/catering'
const columns = ["配餐编号", "对应航班", "批次", "餐食类型", "餐食数量", "航班座位数", "航站楼", "配送车辆", "配送人员", "配餐状态", "超限"]
const actions = ["下达配送", "确认送达", "变更餐食"]
const statuses = ["待确认", "待配送", "配送中", "已送达", "已变更", "已作废"]

const rows = ref<Row[]>([])
const total = ref(0)
const todos = ref<Row[]>([])
const errorMessage = ref('')
const infoMessage = ref('')
const filters = ref<Record<string, string>>({ keyword: '', status: '', over_limit: '' })

const showCreate = ref(false)
const showNote = ref(false)
const note = ref<Row | null>(null)

const createFields = [
  { key: '配餐编号', label: '配餐编号', placeholder: '如 CATE-0010' },
  { key: '对应航班', label: '对应航班', placeholder: '如 CA1234' },
  { key: '批次', label: '批次', placeholder: '如 BATCH-A' },
  { key: '餐食类型', label: '餐食类型', options: ['正餐', '轻食', '特殊餐', '儿童餐'] },
  { key: '餐食数量', label: '餐食数量', type: 'number', placeholder: '份' },
  { key: '航班座位数', label: '航班座位数', type: 'number', placeholder: '座' },
  { key: '航站楼', label: '航站楼', options: ['T1', 'T2', 'T3'] },
  { key: '配送车辆', label: '配送车辆', options: ['VEHI-0001', 'VEHI-0002', 'VEHI-0003'] },
  { key: '配送人员', label: '配送人员', placeholder: '姓名' },
]
const createForm = reactive<Record<string, string>>({})

const stats = computed(() => [
  { label: '待确认', value: rows.value.filter((r) => r.status === '待确认').length },
  { label: '待配送', value: rows.value.filter((r) => r.status === '待配送').length },
  { label: '配送中', value: rows.value.filter((r) => r.status === '配送中').length },
  { label: '超限告警', value: rows.value.filter((r) => r._评估?.over_limit).length, warn: true },
])

function resetFilters() {
  filters.value = { keyword: '', status: '', over_limit: '' }
  void reload()
}

function exportRows() {
  window.open(`${ENDPOINT}/export`, '_blank')
}

function openCreate() {
  Object.keys(createForm).forEach((k) => delete createForm[k])
  showCreate.value = true
}

async function submitCreate() {
  errorMessage.value = ''
  infoMessage.value = ''
  const values: Record<string, string> = {}
  for (const f of createFields) {
    const v = (createForm[f.key] ?? '').toString().trim()
    if (v) values[f.key] = v
  }
  try {
    const response = await request(ENDPOINT, {
      method: 'POST',
      body: JSON.stringify({ values }),
    })
    const payload = await response.json()
    if (!payload.ok) {
      errorMessage.value = payload.message || '配餐任务登记失败'
      return
    }
    infoMessage.value = payload.message || '配餐任务已登记'
    showCreate.value = false
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '配餐任务登记失败'
  }
}

async function runAction(action: string, row: Row) {
  errorMessage.value = ''
  infoMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/${row.id}/actions`, {
      method: 'POST',
      body: JSON.stringify({ values: { action } }),
    })
    const payload = await response.json()
    if (!payload.ok) {
      errorMessage.value = payload.message || '航食配餐动作未生效'
    } else {
      infoMessage.value = payload.message || '操作已生效'
    }
    await reload()
    await loadTodos()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '航食配餐操作失败'
  }
}

async function openDeliveryNote(row: Row) {
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/${row.id}/delivery-note`)
    if (!response.ok) {
      throw new Error('配送单读取失败')
    }
    note.value = await response.json()
    showNote.value = true
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '配送单读取失败'
  }
}

async function loadTodos() {
  try {
    const response = await request(`${ENDPOINT}/todos`)
    if (!response.ok) return
    const payload = await response.json()
    todos.value = payload.items ?? []
  } catch {
    // 待办加载失败不阻断主流程
  }
}

async function reload() {
  errorMessage.value = ''
  infoMessage.value = ''
  const params = new URLSearchParams()
  if (filters.value.keyword) params.set('keyword', filters.value.keyword)
  if (filters.value.status) params.set('status', filters.value.status)
  if (filters.value.over_limit) params.set('over_limit', filters.value.over_limit)
  try {
    const response = await request(`${ENDPOINT}?${params.toString()}`)
    if (!response.ok) {
      throw new Error('配餐任务列表读取失败')
    }
    const payload = await response.json()
    rows.value = payload.items ?? []
    total.value = payload.total ?? rows.value.length
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '航食配餐列表读取失败'
  }
}

onMounted(() => {
  void reload()
  void loadTodos()
})
</script>

<style scoped>
.stat-warn { color: #b45309; }
.info-text { color: #1f6feb; }
.muted { color: #64748b; }
.badge { display: inline-block; padding: 1px 8px; border-radius: 10px; font-size: 12px; }
.badge-ok { background: #e6f4ea; color: #1a7f37; }
.badge-warn { background: #fef3e0; color: #b45309; }
.todo-panel { margin-top: 16px; background: #fff; border: 1px solid #d8dee6; border-radius: 8px; padding: 12px 14px; }
.todo-head { display: flex; justify-content: space-between; align-items: center; }
.todo-head h3 { margin: 0; font-size: 14px; }
.todo-list { list-style: none; margin: 8px 0 0; padding: 0; }
.todo-item { display: flex; flex-wrap: wrap; gap: 10px; align-items: center; padding: 6px 0; border-bottom: 1px dashed #eef1f5; font-size: 13px; }
.modal-mask { position: fixed; inset: 0; background: rgba(16, 24, 40, 0.45); display: flex; align-items: center; justify-content: center; z-index: 50; }
.modal { background: #fff; border-radius: 10px; padding: 18px 20px; width: 560px; max-width: 92vw; }
.modal h3 { margin: 0 0 12px; font-size: 15px; }
.modal-form { display: grid; grid-template-columns: 1fr 1fr; gap: 10px 14px; }
.modal-field { display: flex; flex-direction: column; gap: 4px; font-size: 12px; color: #64748b; }
.modal-field input, .modal-field select { border: 1px solid #d8dee6; border-radius: 6px; padding: 6px 8px; font-size: 13px; color: #1f2937; }
.modal-foot { grid-column: 1 / -1; display: flex; justify-content: flex-end; gap: 8px; margin-top: 6px; }
.note-body { font-size: 13px; line-height: 1.9; }
</style>
