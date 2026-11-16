<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import { getJSON, postJSON } from '../api'
import { POLICY, packStatusLabel } from '../status'

const packs = ref([])
const releases = ref([])
const packId = ref(null)
const reason = ref('')
const last = ref(null)
const err = ref('')
const busy = ref(false)
const checklist = ref([])
const policy = ref('reject_all')

const selected = computed(() => packs.value.find(p => p.id === Number(packId.value)) || null)

// 放行阻断判定与后端同源：未通过项 或 未解决挂起
const blocking = computed(() => {
  const failed = checklist.value.filter(r => !r.passed)
  const openSuspended = checklist.value.filter(r => r.item_state === 'suspended' && !r.resolved)
  if (!failed.length && !openSuspended.length) return null
  const names = [...failed, ...openSuspended].map(r => r.check_title)
  return { failed, openSuspended, names: [...new Set(names)] }
})

async function loadPolicy() {
  const s = await getJSON('/api/settings')
  policy.value = s.partial_fail_policy || 'reject_all'
}

async function loadChecklist() {
  if (!packId.value) { checklist.value = []; return }
  checklist.value = await getJSON(`/api/checklist?pack_id=${packId.value}`)
}

async function refresh() {
  packs.value = await getJSON('/api/packs')
  releases.value = await getJSON('/api/releases')
  if (!packId.value && packs.value.length) packId.value = packs.value[0].id
  await loadChecklist()
}

onMounted(async () => {
  try { await loadPolicy(); await refresh() } catch (e) { err.value = String(e.message || e) }
})
watch(packId, async () => { try { await loadChecklist() } catch (e) { err.value = String(e.message || e) } })

async function decide(decision) {
  err.value = ''
  busy.value = true
  try {
    last.value = await postJSON('/api/releases/decide', {
      pack_id: Number(packId.value),
      decision,
      reason: reason.value || null,
    })
    reason.value = ''
    await refresh()
  } catch (e) {
    err.value = String(e.message || e)
  } finally {
    busy.value = false
  }
}

function decisionLabel(d) {
  return d === 'pass' ? '通过' : d === 'reject' ? '驳回' : d
}
</script>
<template>
  <div class="page">
    <h1>放行台</h1>
    <p class="muted">
      部分失败命运策略：<span class="tag">{{ POLICY[policy] || policy }}</span>
      。仍有未通过项或未解决挂起时禁止放行；驳回须填写原因并留痕。
    </p>
    <p v-if="err" class="tag danger">{{ err }}</p>
    <div class="panel">
      <h3>做出决策</h3>
      <div class="row">
        <div class="field">
          <label>证据包</label>
          <select v-model="packId">
            <option v-for="p in packs" :key="p.id" :value="p.id">
              {{ p.pack_no }}（{{ packStatusLabel(p.status) }}）
            </option>
          </select>
        </div>
        <div class="field" style="flex:1;min-width:14rem">
          <label>原因（驳回必填）</label>
          <input v-model="reason" style="width:100%" placeholder="例如：键重复，暂缓放行" />
        </div>
        <button :disabled="busy || !packId || !!blocking" @click="decide('pass')">放行通过</button>
        <button class="danger" :disabled="busy || !packId" @click="decide('reject')">驳回</button>
      </div>

      <div v-if="selected" class="panel" style="margin:0.5rem 0;background:#fff">
        <p style="margin:0 0 0.5rem">
          当前包状态：<span class="tag" :class="blocking ? 'danger' : 'ok'">{{ packStatusLabel(selected.status) }}</span>
        </p>
        <p v-if="!checklist.length" class="muted">该包尚未跑校验清单，请先到「校验清单」执行。</p>
        <p v-else-if="blocking" class="tag danger" style="font-size:0.85rem">
          禁止放行：{{ blocking.names.join('、') }}
          {{ policy === 'reject_all' ? '未通过项保留，只能驳回留因或修复后复检' : '为未解决挂起，须逐项复检或豁免（留因）' }}
        </p>
        <p v-else class="tag ok">清单无未通过项且无未解决挂起，可放行</p>
      </div>

      <p v-if="last">
        决策
        <span class="tag" :class="last.decision === 'pass' ? 'ok' : 'danger'">
          {{ decisionLabel(last.decision) }}
        </span>
        · {{ last.reason }}
      </p>
    </div>
    <div class="panel">
      <h3>决策留痕</h3>
      <table>
        <thead>
          <tr>
            <th>包号</th>
            <th>决策</th>
            <th>原因</th>
            <th>包状态</th>
            <th>时间</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="r in releases" :key="r.id">
            <td class="mono">{{ r.pack_no }}</td>
            <td>
              <span class="tag" :class="r.decision === 'pass' ? 'ok' : 'danger'">
                {{ decisionLabel(r.decision) }}
              </span>
            </td>
            <td>{{ r.reason }}</td>
            <td><span class="tag">{{ packStatusLabel(r.pack_status) }}</span></td>
            <td class="mono">{{ r.created_at }}</td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>
