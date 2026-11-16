<script setup>
import { onMounted, ref } from 'vue'
import { getJSON, postJSON } from '../api'
import { ITEM_STATE, POLICY, itemStateLabel, packStatusLabel } from '../status'

const packs = ref([])
const packId = ref(null)
const result = ref(null)
const history = ref([])
const actions = ref([])
const policy = ref('reject_all')
const exemptReason = ref({})
const err = ref('')
const busy = ref(false)

async function loadPolicy() {
  const s = await getJSON('/api/settings')
  policy.value = s.partial_fail_policy || 'reject_all'
}

async function refresh() {
  packs.value = await getJSON('/api/packs')
  if (!packId.value && packs.value.length) packId.value = packs.value[0].id
  if (packId.value) await onPackChange()
}

async function onPackChange() {
  history.value = await getJSON(`/api/checklist?pack_id=${packId.value}`)
  actions.value = await getJSON(`/api/checklist/actions?pack_id=${packId.value}`)
  result.value = null
}

onMounted(async () => {
  try { await loadPolicy(); await refresh() } catch (e) { err.value = String(e.message || e) }
})

async function run() {
  err.value = ''
  busy.value = true
  try {
    result.value = await postJSON('/api/checklist/run', { pack_id: Number(packId.value) })
    history.value = result.value.results || []
    actions.value = result.value.actions || []
    packs.value = await getJSON('/api/packs')
  } catch (e) {
    err.value = String(e.message || e)
  } finally {
    busy.value = false
  }
}

function applyOut(out) {
  history.value = out.results || []
  actions.value = out.actions || []
  result.value = out
  packs.value = packs.value.map(p => p.id === out.pack_id ? { ...p, status: out.pack_status } : p)
}

async function recheck(code) {
  err.value = ''
  busy.value = true
  try {
    applyOut(await postJSON('/api/checklist/recheck', { pack_id: Number(packId.value), check_code: code }))
  } catch (e) {
    err.value = String(e.message || e)
  } finally {
    busy.value = false
  }
}

async function exempt(code) {
  err.value = ''
  const reason = (exemptReason.value[code] || '').trim()
  if (!reason) { err.value = '豁免须填写原因'; return }
  busy.value = true
  try {
    applyOut(await postJSON('/api/checklist/exempt', {
      pack_id: Number(packId.value), check_code: code, reason,
    }))
    exemptReason.value[code] = ''
  } catch (e) {
    err.value = String(e.message || e)
  } finally {
    busy.value = false
  }
}

function tagClass(r) {
  if (r.item_state === 'suspended') return r.resolved ? 'ok' : 'warn'
  return r.passed ? 'ok' : 'danger'
}
</script>
<template>
  <div class="page">
    <h1>校验清单</h1>
    <p class="muted">
      基础项：条数一致、键唯一、必填非空。当前部分失败命运策略：
      <span class="tag">{{ POLICY[policy] || policy }}</span>
    </p>
    <p v-if="err" class="tag danger">{{ err }}</p>
    <div class="panel">
      <h3>执行校验</h3>
      <div class="row">
        <div class="field">
          <label>证据包</label>
          <select v-model="packId" @change="onPackChange">
            <option v-for="p in packs" :key="p.id" :value="p.id">
              {{ p.pack_no }}（{{ p.item_count }} 条 · {{ packStatusLabel(p.status) }}）
            </option>
          </select>
        </div>
        <button :disabled="busy || !packId" @click="run">跑校验清单</button>
      </div>
      <p v-if="result">
        包状态 <strong>{{ packStatusLabel(result.pack_status) }}</strong>
        ·
        <span class="tag" :class="result.all_passed ? 'ok' : 'danger'">
          {{ result.all_passed ? '全部通过' : '存在未通过项' }}
        </span>
        <template v-if="!result.all_passed">
          · 未通过 <strong>{{ result.summary.failed_count }}</strong>
          · 未解决挂起 <strong>{{ result.summary.open_suspension_count }}</strong>
          · 已闭环 <strong>{{ result.summary.resolved_count }}</strong>
        </template>
      </p>
    </div>

    <div class="panel">
      <h3>清单结果（与包状态同源）</h3>
      <table>
        <thead>
          <tr>
            <th>校验项</th><th>命运</th><th>结果</th><th>说明</th><th>处置留痕</th>
            <th v-if="policy === 'suspend_items'">逐项处置</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="r in history" :key="r.id || r.check_code">
            <td>{{ r.check_title }}</td>
            <td><span class="tag" :class="tagClass(r)">{{ itemStateLabel(r.item_state) }}{{ r.resolved ? '·已闭环' : '' }}</span></td>
            <td>
              <span class="tag" :class="r.passed ? 'ok' : 'danger'">
                {{ r.passed ? '通过' : '未通过' }}
              </span>
            </td>
            <td>{{ r.detail }}</td>
            <td class="muted">{{ r.resolve_note || '—' }}</td>
            <td v-if="policy === 'suspend_items'">
              <template v-if="r.item_state === 'suspended' && !r.resolved">
                <button class="small" :disabled="busy" @click="recheck(r.check_code)">复检</button>
                <input
                  v-model="exemptReason[r.check_code]"
                  class="small"
                  style="width:11rem;margin:0 0.4rem"
                  placeholder="豁免原因（必填）"
                />
                <button class="small danger" :disabled="busy" @click="exempt(r.check_code)">豁免</button>
              </template>
              <span v-else class="muted">—</span>
            </td>
          </tr>
        </tbody>
      </table>
      <p v-if="policy === 'reject_all'" class="muted" style="margin-top:0.75rem">
        整包驳回口径下未通过项只可保留并驳回留因，或修复数据后重跑清单（整包复检），不支持逐项处置。
      </p>
    </div>

    <div class="panel" v-if="actions.length">
      <h3>挂起处置留痕</h3>
      <table>
        <thead><tr><th>校验项</th><th>动作</th><th>豁免原因</th><th>结果</th><th>时间</th></tr></thead>
        <tbody>
          <tr v-for="a in actions" :key="a.id">
            <td>{{ a.check_code }}</td>
            <td><span class="tag">{{ a.action === 'recheck' ? '复检' : '豁免' }}</span></td>
            <td>{{ a.reason || '—' }}</td>
            <td>{{ ITEM_STATE[a.result_state] ? itemStateLabel(a.result_state) : (a.result_state === 'exempted' ? '已豁免' : a.result_state) }}</td>
            <td class="mono">{{ a.created_at }}</td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>
