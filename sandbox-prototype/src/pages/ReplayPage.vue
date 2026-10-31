<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { apiClient, ApiClientError } from '../shared/api'
import { navigateTo } from '../shared/navigation'
import ServiceGridMap from '../components/ServiceGridMap.vue'
import type { GameView, ReplayFrame, Role } from '../shared/types'

const props = defineProps<{ gameId: string; branchId: string | null; snapshotId: string | null; step: number | null; role: Role }>()
const emit = defineEmits<{ navigate: [view: 'history' | 'result' | 'sandbox', gameId?: string] }>()
const frame = ref<ReplayFrame | null>(null)
const loading = ref(true)
const errorMessage = ref('')
const selectedStep = ref(props.step ?? 0)
const selectedSnapshotId = ref(props.snapshotId)
const branchId = ref(props.branchId || '')
const gameFinished = ref<boolean | null>(null)
const appliedRequest = ref(0)
let readController: AbortController | null = null
const frameTitle = computed(() => frame.value ? `第 ${frame.value.simulation_step} 仿真分钟` : '尚未读取复盘帧')
const mapView = computed<Pick<GameView, 'units' | 'camps' | 'context' | 'map'> | null>(() => frame.value ? {
  units: frame.value.units,
  camps: [],
  map: frame.value.map,
  context: { game_id: frame.value.game_id, branch_id: frame.value.branch_id, snapshot_id: frame.value.snapshot_id, context_token: '' },
} : null)

async function loadFrame() {
  readController?.abort()
  readController = new AbortController()
  const requestId = ++appliedRequest.value
  loading.value = true
  errorMessage.value = ''
  try {
    if (gameFinished.value === null || !branchId.value) {
      const view = await apiClient.getGameView(props.gameId, readController.signal)
      gameFinished.value = view.clock.phase === 'finished'
      if (!branchId.value) branchId.value = view.context.branch_id
      if (!branchId.value) throw new Error('结果未返回可复盘分支标识')
    }
    const nextFrame = await apiClient.getReplay(props.gameId, { branchId: branchId.value, step: selectedStep.value, snapshotId: selectedSnapshotId.value || undefined }, readController.signal)
    if (requestId === appliedRequest.value) {
      if (nextFrame.read_only !== true || nextFrame.game_id !== props.gameId || !Array.isArray(nextFrame.events) || !Array.isArray(nextFrame.units)) throw new Error('复盘帧不符合只读数据契约')
      frame.value = nextFrame
    }
  } catch (error) {
    if (requestId === appliedRequest.value) errorMessage.value = error instanceof ApiClientError ? `${error.message}（${error.details.code}）` : error instanceof Error ? error.message : '复盘读取失败。'
  } finally {
    if (requestId === appliedRequest.value) loading.value = false
  }
}

onMounted(() => { if (props.gameId) loadFrame() })
watch(selectedStep, step => {
  selectedSnapshotId.value = null
  navigateTo({ view: 'replay', gameId: props.gameId, branchId: branchId.value || null, step, snapshotId: null }, true)
  if (branchId.value) loadFrame()
})
watch(() => props.step, step => { if (step !== null && step !== selectedStep.value) selectedStep.value = step })
watch(() => props.snapshotId, snapshotId => { if (snapshotId && snapshotId !== selectedSnapshotId.value) { selectedSnapshotId.value = snapshotId; loadFrame() } })
watch(() => props.branchId, nextBranchId => { if (nextBranchId && nextBranchId !== branchId.value) { branchId.value = nextBranchId; loadFrame() } })
watch(() => props.gameId, () => { readController?.abort(); branchId.value = props.branchId || ''; gameFinished.value = null; frame.value = null; selectedStep.value = props.step ?? 0; loadFrame() })
onBeforeUnmount(() => { readController?.abort(); ++appliedRequest.value })
</script>

<template>
  <main class="replay-page">
    <header class="replay-header"><div><span class="eyebrow">REPLAY / READ ONLY</span><h1>只读复盘</h1><p>复盘只读取已保存的快照，不推进实时对局、不触发事件或再次生成建议。</p></div><div class="replay-actions"><button type="button" class="secondary-button" @click="emit('navigate', 'history')">返回历史对局</button><button v-if="gameFinished" type="button" class="secondary-button" @click="emit('navigate', 'result', props.gameId)">查看结果</button></div></header>
    <div v-if="loading" class="replay-state">正在读取第 {{ selectedStep }} 步复盘帧……</div>
    <div v-else-if="errorMessage" class="replay-state error-state"><strong>复盘帧暂不可用</strong><p>{{ errorMessage }}</p><button type="button" class="secondary-button" @click="loadFrame">重新读取</button><small>快速跳转只应用最后一次成功读取的帧。</small></div>
    <template v-else-if="frame">
      <section class="replay-frame"><div class="frame-head"><div><span class="eyebrow">FROZEN SNAPSHOT</span><h2>{{ frameTitle }}</h2></div><span class="readonly-tag">只读</span></div><div class="frame-summary"><span>快照 {{ frame.snapshot_id }}</span><span>{{ frame.units.length }} 个单位记录</span><span>{{ frame.events.length }} 条事件记录</span></div><ServiceGridMap v-if="mapView" :view="mapView" /></section>
      <section class="replay-controls"><label>仿真步 <input v-model.number="selectedStep" type="range" min="0" max="60"><output>{{ selectedStep }}</output></label><div class="replay-events"><article v-for="event in frame.events" :key="event.event_id || `${event.simulation_step}-${event.title}`"><small>第 {{ event.simulation_step }} 步</small><strong>{{ event.title }}</strong><span>{{ event.detail || '服务端未返回详细说明' }}</span></article><p v-if="!frame.events.length">当前帧没有可见事件。</p></div></section>
    </template>
  </main>
</template>

<style scoped>
.replay-page{min-height:100vh;background:#edf1f2;padding:36px clamp(22px,6vw,96px);color:#243b50}.replay-header{display:flex;justify-content:space-between;align-items:flex-start;gap:20px;margin-bottom:28px}.replay-header h1{font-size:30px;margin:8px 0}.replay-header p{color:#708396;font-size:13px}.replay-actions{display:flex;gap:8px}.replay-state,.replay-frame,.replay-controls{background:#fff;border:1px solid #ccd8de;border-radius:10px;padding:25px;max-width:1000px}.replay-state{color:#60788c;line-height:1.7}.error-state{border-left:3px solid #c98b62}.frame-head,.frame-summary{display:flex;align-items:center;justify-content:space-between;gap:14px}.frame-head h2{font-size:22px;margin-top:8px}.readonly-tag{color:#67836f;background:#edf7ef;border:1px solid #c7ddcb;border-radius:4px;padding:5px 8px;font-size:10px}.frame-summary{justify-content:flex-start;color:#8395a3;font-size:11px;border-top:1px solid #edf1f4;border-bottom:1px solid #edf1f4;padding:13px 0;margin:18px 0}.replay-empty{padding:34px;background:#f5f8fa;border-radius:7px;color:#7c8f9e;font-size:12px}.replay-controls{margin-top:14px}.replay-controls label{display:flex;align-items:center;gap:12px;font-size:12px;color:#5d768b}.replay-controls input{flex:1}.replay-controls output{font-weight:700;color:#315473}.replay-events{display:grid;gap:8px;margin-top:20px}.replay-events article{display:grid;grid-template-columns:90px 150px 1fr;gap:12px;padding:11px;border-bottom:1px solid #edf1f4;font-size:11px}.replay-events small{color:#a07b42}.replay-events span{color:#788d9d}.secondary-button{border:1px solid #c4d1d8;background:#fff;color:#45647d;border-radius:5px;padding:9px 14px;font-size:12px}@media(max-width:700px){.replay-header,.replay-actions,.frame-head,.frame-summary{flex-direction:column;align-items:flex-start}.replay-events article{grid-template-columns:1fr}}
</style>
