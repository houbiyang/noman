<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import type { GameView, Side } from '../shared/types'

type Marker = { key: string; side: Side; x: number; y: number; campCount: number; unitTypes: string[] }

const props = defineProps<{ view: Pick<GameView, 'units' | 'camps' | 'context' | 'map'> }>()
const selectedKey = ref<string | null>(null)
const battlefieldRef = ref<HTMLElement | null>(null)
const worldWidth = ref(0)
const worldHeight = ref(0)
const panX = ref(0)
const panY = ref(0)
const isDragging = ref(false)
let dragOrigin: { x: number; y: number; panX: number; panY: number; pointerId: number } | null = null
let suppressClick = false
let resizeObserver: ResizeObserver | null = null

function readPosition(value: unknown): { x: number; y: number } | null {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return null
  const position = value as Record<string, unknown>
  if (position.coordinate_system_id !== 'fixture-grid-v1') return null
  if (typeof position.x !== 'string' && typeof position.x !== 'number' || typeof position.y !== 'string' && typeof position.y !== 'number') return null
  const x = Number(position.x)
  const y = Number(position.y)
  return Number.isFinite(x) && Number.isFinite(y) && x >= 0 && x <= 100 && y >= 0 && y <= 100 ? { x, y } : null
}

function unitLabel(value: unknown): string {
  return value === 'recon' ? '侦察无人机' : value === 'ground' ? '陆兵' : value === 'support' ? '保障单位' : typeof value === 'string' ? value : '未标明类型单位'
}

const markers = computed(() => {
  const grouped = new Map<string, Marker>()
  const addMarker = (side: unknown, position: unknown, kind: 'camp' | 'unit', unitType?: unknown) => {
    if (side !== 'red' && side !== 'blue') return
    const coordinate = readPosition(position)
    if (!coordinate) return
    const key = `${side}:${coordinate.x}:${coordinate.y}`
    const marker = grouped.get(key) || { key, side, x: coordinate.x, y: coordinate.y, campCount: 0, unitTypes: [] }
    if (kind === 'camp') marker.campCount++
    else marker.unitTypes.push(unitLabel(unitType))
    grouped.set(key, marker)
  }
  props.view.camps.forEach(camp => addMarker(camp.side, camp.position, 'camp'))
  props.view.units.forEach(unit => addMarker(unit.side, unit.position, 'unit', unit.unit_type))
  return [...grouped.values()]
})

const selectedMarker = computed(() => markers.value.find(marker => marker.key === selectedKey.value) || null)
const totalObjects = computed(() => props.view.camps.length + props.view.units.length)
const mappedObjects = computed(() => markers.value.reduce((total, marker) => total + marker.campCount + marker.unitTypes.length, 0))
const mapSupported = computed(() => {
  const map = props.view.map
  if (map.coordinate_system_id !== 'fixture-grid-v1' || !map.bounds || typeof map.bounds !== 'object' || Array.isArray(map.bounds)) return false
  const bounds = map.bounds as Record<string, unknown>
  if ([bounds.min_x, bounds.min_y, bounds.max_x, bounds.max_y].some(value => typeof value !== 'string' && typeof value !== 'number')) return false
  return Number(bounds.min_x) === 0 && Number(bounds.min_y) === 0 && Number(bounds.max_x) === 100 && Number(bounds.max_y) === 100
})

function limitPan(value: number, mapSize: number, viewportSize: number): number {
  const limit = Math.max(0, (mapSize - viewportSize) / 2)
  return Math.max(-limit, Math.min(limit, value))
}

function updateWorldSize() {
  if (!battlefieldRef.value) return
  const viewportWidth = battlefieldRef.value.clientWidth
  const viewportHeight = battlefieldRef.value.clientHeight
  worldWidth.value = viewportWidth * 1.1
  worldHeight.value = viewportHeight * 1.1
  panX.value = limitPan(panX.value, worldWidth.value, viewportWidth)
  panY.value = limitPan(panY.value, worldHeight.value, viewportHeight)
}

function startMapDrag(event: PointerEvent) {
  if (event.button !== 0) return
  dragOrigin = { x: event.clientX, y: event.clientY, panX: panX.value, panY: panY.value, pointerId: event.pointerId }
  suppressClick = false
}

function moveMapDrag(event: PointerEvent) {
  if (!dragOrigin || dragOrigin.pointerId !== event.pointerId || !battlefieldRef.value) return
  const deltaX = event.clientX - dragOrigin.x
  const deltaY = event.clientY - dragOrigin.y
  if (!isDragging.value && Math.hypot(deltaX, deltaY) < 5) return
  if (!isDragging.value) {
    isDragging.value = true
    battlefieldRef.value.setPointerCapture(event.pointerId)
  }
  event.preventDefault()
  panX.value = limitPan(dragOrigin.panX + deltaX, worldWidth.value, battlefieldRef.value.clientWidth)
  panY.value = limitPan(dragOrigin.panY + deltaY, worldHeight.value, battlefieldRef.value.clientHeight)
}

function stopMapDrag(event: PointerEvent) {
  if (!dragOrigin || dragOrigin.pointerId !== event.pointerId) return
  suppressClick = isDragging.value
  if (battlefieldRef.value?.hasPointerCapture(event.pointerId)) battlefieldRef.value.releasePointerCapture(event.pointerId)
  isDragging.value = false
  dragOrigin = null
}

function guardMapClick(event: MouseEvent) {
  if (!suppressClick) return
  event.preventDefault()
  event.stopPropagation()
  suppressClick = false
}

watch(() => props.view.context.snapshot_id, () => { selectedKey.value = null })
onMounted(() => {
  updateWorldSize()
  if (battlefieldRef.value) {
    resizeObserver = new ResizeObserver(updateWorldSize)
    resizeObserver.observe(battlefieldRef.value)
  }
})
onUnmounted(() => resizeObserver?.disconnect())
</script>

<template>
  <section ref="battlefieldRef" :class="['service-battlefield', { dragging: isDragging }]" aria-label="服务端席位可见的地图位置" @pointerdown="startMapDrag" @pointermove="moveMapDrag" @pointerup="stopMapDrag" @pointercancel="stopMapDrag" @click.capture="guardMapClick">
    <div class="service-world" :style="{ width: `${worldWidth}px`, height: `${worldHeight}px`, transform: `translate(calc(-50% + ${panX}px), calc(-50% + ${panY}px))` }">
      <svg class="service-map-overlay" viewBox="0 0 1000 570" preserveAspectRatio="none" aria-hidden="true">
        <defs>
          <linearGradient id="service-red-territory" x1="0" x2="1"><stop offset="0" stop-color="#a1303d" stop-opacity=".3"/><stop offset="1" stop-color="#a1303d" stop-opacity=".03"/></linearGradient>
          <linearGradient id="service-blue-territory" x1="1" x2="0"><stop offset="0" stop-color="#1e5e9a" stop-opacity=".3"/><stop offset="1" stop-color="#1e5e9a" stop-opacity=".03"/></linearGradient>
          <filter id="service-map-marker-shadow" x="-80%" y="-80%" width="260%" height="260%"><feDropShadow dx="0" dy="2" stdDeviation="2" flood-color="#06131a" flood-opacity=".75"/></filter>
        </defs>
        <path class="service-territory red" d="M0 98 Q235 78 445 143 L474 250 Q444 395 393 512 L0 570 Z" fill="url(#service-red-territory)"/>
        <path class="service-territory blue" d="M1000 98 Q765 78 555 143 L526 250 Q556 395 607 512 L1000 570 Z" fill="url(#service-blue-territory)"/>
        <path class="service-frontier" d="M500 108 Q470 210 503 285 Q535 379 500 496"/>
        <g class="service-map-info mountain" transform="translate(632 96)"><circle r="3"/><text x="9" y="4">北部高山</text></g>
        <g class="service-map-info settlement" transform="translate(126 167)"><circle r="3"/><text x="9" y="4">西侧村镇</text></g>
        <g class="service-map-info road" transform="translate(545 231)"><circle r="3"/><text x="9" y="4">东西主干道</text></g>
        <g class="service-map-info river" transform="translate(537 334)"><circle r="3"/><text x="9" y="4">中央河流</text></g>
        <g class="service-map-info facility" transform="translate(866 285)"><circle r="3"/><text x="9" y="4">东部设施区</text></g>
        <g class="service-objective-node" transform="translate(346 274) scale(.58)"><circle r="11"/><text text-anchor="middle" y="4">A</text><text class="service-objective-label" x="17" y="4">巡查区</text></g>
        <g class="service-objective-node center" transform="translate(500 275) scale(.58)"><circle r="13"/><text text-anchor="middle" y="4">◎</text><text class="service-objective-label" x="19" y="4">中央设施</text></g>
        <g class="service-objective-node blue" transform="translate(654 274) scale(.58)"><circle r="11"/><text text-anchor="middle" y="4">C</text><text class="service-objective-label" x="17" y="4">巡查区</text></g>
        <g class="service-checkpoint" transform="translate(500 390) scale(.58)"><circle r="10"/><text text-anchor="middle" y="4">B</text><text class="service-objective-label" x="16" y="4">检查点</text></g>
      </svg>
      <div class="coordinate-grid" aria-hidden="true"></div>
      <template v-if="mapSupported">
        <button v-for="marker in markers" :key="marker.key" type="button" :class="['service-marker', marker.side, { selected: selectedKey === marker.key }]" :style="{ left: `${10 + marker.x * .8}%`, bottom: `${10 + marker.y * .8}%` }" :aria-label="`${marker.side === 'red' ? '红方' : '蓝方'}网格坐标 ${marker.x}，${marker.y}，${marker.campCount} 个兵营，${marker.unitTypes.length} 个单位`" @click.stop="selectedKey = marker.key">
          <span class="marker-core">{{ marker.campCount ? '◆' : marker.unitTypes.length }}</span>
          <span class="marker-name">{{ marker.side === 'red' ? '红方' : '蓝方' }} · {{ marker.campCount ? '兵营' : '单位' }}{{ marker.unitTypes.length ? ` + ${marker.unitTypes.length} 单位` : '' }}</span>
        </button>
      </template>
    </div>
    <div class="battlefield-vignette" aria-hidden="true"></div>
    <aside v-if="selectedMarker" class="map-detail" aria-label="地图标记详情"><button type="button" aria-label="关闭标记详情" @click="selectedKey = null">×</button><strong>{{ selectedMarker.side === 'red' ? '红方' : '蓝方' }} · {{ selectedMarker.x }}，{{ selectedMarker.y }}</strong><p>兵营 {{ selectedMarker.campCount }} 个 · 单位 {{ selectedMarker.unitTypes.length }} 个</p><p v-for="(unitType, index) in selectedMarker.unitTypes" :key="`${unitType}-${index}`">{{ unitType }}</p></aside>
    <div v-if="!mapSupported" class="map-warning" role="status">服务端地图坐标系与当前视图不一致，未绘制单位位置。</div>
    <div class="map-caption">示意底图 · 服务端网格坐标 {{ mappedObjects }} / {{ totalObjects }} 个对象 · 按住地图拖动</div>
  </section>
</template>

<style scoped>
.service-battlefield{position:absolute;inset:0;overflow:hidden;background:#071923;cursor:grab;touch-action:none;user-select:none}
.service-battlefield.dragging{cursor:grabbing}
.service-world{position:absolute;left:50%;top:50%;background:#0a1b23 url('/expanded-terrain.png') center/100% 100% no-repeat;will-change:transform}
.service-map-overlay{position:absolute;inset:0;width:100%;height:100%;display:block;pointer-events:none}
.coordinate-grid{position:absolute;inset:0;background-image:linear-gradient(#d8edf00b 1px,transparent 1px),linear-gradient(90deg,#d8edf00b 1px,transparent 1px);background-size:10% 10%;pointer-events:none}
.service-territory{stroke-width:1.6;stroke-dasharray:8 9;pointer-events:none}.service-territory.red{stroke:#f19a9e}.service-territory.blue{stroke:#8ac9fa}.service-frontier{fill:none;stroke:#f5e6b188;stroke-width:1.2;stroke-dasharray:7 10;pointer-events:none}.service-map-info{pointer-events:none}.service-map-info circle{fill:#cfdbd2;stroke:#092331;stroke-width:1.5}.service-map-info text{fill:#eef5ec;font-size:9px;font-weight:700;paint-order:stroke;stroke:#0b2230;stroke-width:3}.service-map-info.road circle{fill:#e4c58b}.service-map-info.river circle{fill:#83cce6}.service-map-info.facility circle{fill:#e4cc92}.service-objective-node,.service-checkpoint{filter:url(#service-map-marker-shadow);pointer-events:none}.service-objective-node circle{fill:#173a43de;stroke:#f0e5b0;stroke-width:1.5}.service-objective-node.blue circle{stroke:#aad7f5}.service-objective-node.center circle{fill:#b98a3dde;stroke:#ffefc7}.service-objective-node>text:first-of-type,.service-checkpoint>text:first-of-type{fill:#fff;font-size:11px;font-weight:900}.service-objective-label{fill:#f6f5e8;font-size:9px;font-weight:800;paint-order:stroke;stroke:#0c202b;stroke-width:3}.service-checkpoint circle{fill:#234e56dc;stroke:#d6e9ca;stroke-width:1.5}
.battlefield-vignette{position:absolute;inset:0;pointer-events:none;background:linear-gradient(180deg,#07151dbb 0%,transparent 24%,transparent 70%,#07151dd9 100%),linear-gradient(90deg,#07151d66,transparent 18%,transparent 82%,#07151d66)}
.service-marker{position:absolute;z-index:1;display:flex;align-items:center;gap:9px;transform:translate(-17px,50%);padding:0;border:0;background:transparent;color:#fff;cursor:pointer;filter:drop-shadow(0 3px 8px #061721)}
.marker-core{width:34px;height:34px;display:grid;place-items:center;border-radius:50%;border:3px solid #ffe5dc;background:#c7525d;box-shadow:0 0 0 5px #b8313a66;font-size:15px;line-height:1}
.service-marker.blue .marker-core{border-color:#e2f2ff;background:#458dc5;box-shadow:0 0 0 5px #28639c66}
.service-marker.selected .marker-core,.service-marker:focus-visible .marker-core{outline:3px solid #ffe09b;outline-offset:5px}
.marker-name{padding:5px 8px;border:1px solid #9db9c37a;border-radius:5px;background:#0b2535d9;font-size:11px;font-weight:800;white-space:nowrap}
.map-detail{position:absolute;z-index:2;right:22px;bottom:65px;width:230px;padding:16px;border:1px solid #9bb9c27d;border-radius:7px;background:#0b2635ed;color:#eef5f2;box-shadow:0 9px 24px #03131b88;font-size:12px}
.map-detail strong{display:block;margin-bottom:10px;font-size:15px}.map-detail p{margin:7px 0;color:#c7dce1}.map-detail button{float:right;border:0;background:transparent;color:#eaf3f3;font-size:20px;cursor:pointer}
.map-warning{position:absolute;left:50%;top:50%;transform:translate(-50%,-50%);padding:15px 20px;border:1px solid #d3ad7c;background:#0b2635e8;color:#f1e0c5;font-size:12px}
.map-caption{position:absolute;left:50%;bottom:47px;transform:translateX(-50%);padding:6px 10px;border:1px solid #b4c9ce6b;border-radius:5px;background:#0b2635dd;color:#e5eeed;font-size:10px;white-space:nowrap;pointer-events:none}
</style>
