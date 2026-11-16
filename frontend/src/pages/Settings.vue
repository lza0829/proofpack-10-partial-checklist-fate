<script setup>
import { onMounted, ref } from 'vue'
import { getJSON, putJSON } from '../api'
import { POLICY } from '../status'

const LABEL = {
  default_sample_rate: '默认采样率',
  sample_mode: '采样模式',
}

const settings = ref({})
const policy = ref('reject_all')
const err = ref('')
const ok = ref('')
const busy = ref(false)

onMounted(async () => {
  try {
    settings.value = await getJSON('/api/settings')
    policy.value = settings.value.partial_fail_policy || 'reject_all'
  } catch (e) { err.value = String(e.message || e) }
})

async function savePolicy() {
  err.value = ''
  ok.value = ''
  busy.value = true
  try {
    const r = await putJSON('/api/settings/partial-fail-policy', {
      partial_fail_policy: policy.value,
    })
    policy.value = r.partial_fail_policy
    settings.value = await getJSON('/api/settings')
    ok.value = '部分失败命运策略已保存（两策略互斥单选）'
  } catch (e) {
    err.value = String(e.message || e)
  } finally {
    busy.value = false
  }
}
</script>
<template>
  <div class="page">
    <h1>设置</h1>
    <p class="muted">清单存在未通过项时的部分失败命运：两策略互斥单选，不存在并存开关。</p>
    <p v-if="err" class="tag danger">{{ err }}</p>
    <p v-if="ok" class="tag ok">{{ ok }}</p>

    <div class="panel">
      <h3>部分失败命运策略（互斥）</h3>
      <div class="row" style="align-items:flex-start;gap:1.5rem;flex-wrap:wrap">
        <label class="field" style="min-width:20rem;max-width:26rem">
          <input type="radio" value="reject_all" v-model="policy" style="width:auto;margin-right:0.5rem" />
          <strong>{{ POLICY.reject_all }}</strong>
          <p class="muted" style="margin:0.25rem 0 0 1.4rem">
            未通过项全部保留，包进入不可放行/须驳回态；禁止放行，只能驳回留因或修复后复检。
          </p>
        </label>
        <label class="field" style="min-width:20rem;max-width:26rem">
          <input type="radio" value="suspend_items" v-model="policy" style="width:auto;margin-right:0.5rem" />
          <strong>{{ POLICY.suspend_items }}</strong>
          <p class="muted" style="margin:0.25rem 0 0 1.4rem">
            未通过项标挂起、已通过项保留；禁止放行，可逐项复检或豁免（豁免须留原因）；挂起未清不得放行。
          </p>
        </label>
      </div>
      <div style="margin-top:1rem">
        <button :disabled="busy" @click="savePolicy">保存策略</button>
      </div>
    </div>

    <div class="panel">
      <h3>其他底座配置（只读）</h3>
      <table>
        <thead>
          <tr><th>配置项</th><th>取值</th></tr>
        </thead>
        <tbody>
          <tr v-for="(v, k) in settings" :key="k" v-show="k !== 'partial_fail_policy'">
            <td>{{ LABEL[k] || k }}</td>
            <td><span class="tag">{{ v }}</span></td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>
