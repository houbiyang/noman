<script setup lang="ts">
import { onMounted, onUnmounted, ref } from 'vue'
import type { TaskPair } from './taskFormat'

type Side = 'red' | 'blue'
type Role = 'host' | Side
type Unit = { id: string; side: Side; name: string; state: string; x: number; y: number; shape: string }

const props = defineProps<{
  role: Role
  preparationPreview: boolean
  selectedTroopType: string | null
  selectedUnitId: string | null
  tasks: TaskPair
  units: Unit[]
}>()

const emit = defineEmits<{
  selectUnit: [unitId: string]
  placePreview: [position: { x: number; y: number }]
}>()

const MAP_SCALE = 1.4
const MAP_ASPECT_RATIO = 1000 / 570
const battlefieldRef = ref<HTMLElement | null>(null)
const worldWidth = ref(0)
const worldHeight = ref(0)
const panX = ref(0)
const panY = ref(0)
const isDragging = ref(false)
let dragOrigin: { x: number; y: number; panX: number; panY: number; pointerId: number } | null = null
let suppressClick = false
let resizeObserver: ResizeObserver | null = null

function limitPan(value: number, mapSize: number, viewportSize: number) {
  const limit = Math.max(0, (mapSize - viewportSize) / 2)
  return Math.max(-limit, Math.min(limit, value))
}

function updateWorldSize() {
  if (!battlefieldRef.value) return
  const viewportWidth = battlefieldRef.value.clientWidth
  const viewportHeight = battlefieldRef.value.clientHeight
  worldWidth.value = Math.max(viewportWidth, viewportHeight * MAP_ASPECT_RATIO) * MAP_SCALE
  worldHeight.value = worldWidth.value / MAP_ASPECT_RATIO
  panX.value = limitPan(panX.value, worldWidth.value, viewportWidth)
  panY.value = limitPan(panY.value, worldHeight.value, viewportHeight)
}

onMounted(() => {
  updateWorldSize()
  if (battlefieldRef.value) {
    resizeObserver = new ResizeObserver(updateWorldSize)
    resizeObserver.observe(battlefieldRef.value)
  }
})

onUnmounted(() => resizeObserver?.disconnect())

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

function selectUnit(unitId: string) {
  emit('selectUnit', unitId)
}

function placePreview(event: MouseEvent) {
  if (!props.preparationPreview || props.role === 'host' || !props.selectedTroopType) return
  const map = event.currentTarget as SVGSVGElement
  const screenMatrix = map.getScreenCTM()
  if (!screenMatrix) return
  const position = new DOMPoint(event.clientX, event.clientY).matrixTransform(screenMatrix.inverse())
  emit('placePreview', { x: position.x, y: position.y })
}
</script>

<template>
  <section ref="battlefieldRef" :class="['battlefield', { dragging: isDragging }]" aria-label="可按住拖动的虚构地图沙盘" @pointerdown="startMapDrag" @pointermove="moveMapDrag" @pointerup="stopMapDrag" @pointercancel="stopMapDrag" @click.capture="guardMapClick">
    <div class="battlefield-world" :style="{ width: `${worldWidth}px`, height: `${worldHeight}px`, transform: `translate(calc(-50% + ${panX}px), calc(-50% + ${panY}px))` }">
    <svg class="battlefield-svg" viewBox="0 0 1000 570" preserveAspectRatio="none" role="img" aria-label="红蓝阵营、道路、河流、高山和可见单位" @click="placePreview">
      <defs>
        <linearGradient id="red-territory" x1="0" x2="1"><stop offset="0" stop-color="#a1303d" stop-opacity=".3"/><stop offset="1" stop-color="#a1303d" stop-opacity=".03"/></linearGradient>
        <linearGradient id="blue-territory" x1="1" x2="0"><stop offset="0" stop-color="#1e5e9a" stop-opacity=".3"/><stop offset="1" stop-color="#1e5e9a" stop-opacity=".03"/></linearGradient>
        <filter id="map-marker-shadow" x="-80%" y="-80%" width="260%" height="260%"><feDropShadow dx="0" dy="2" stdDeviation="2" flood-color="#06131a" flood-opacity=".75"/></filter>
      </defs>

      <path class="territory red" d="M0 98 Q235 78 445 143 L474 250 Q444 395 393 512 L0 570 Z" fill="url(#red-territory)"/>
      <path class="territory blue" d="M1000 98 Q765 78 555 143 L526 250 Q556 395 607 512 L1000 570 Z" fill="url(#blue-territory)"/>
      <path class="frontier" d="M500 108 Q470 210 503 285 Q535 379 500 496"/>

      <g class="map-info mountain" transform="translate(632 96)"><circle r="3"/><text x="9" y="4">北部高山</text></g>
      <g class="map-info settlement" transform="translate(126 167)"><circle r="3"/><text x="9" y="4">西侧村镇</text></g>
      <g class="map-info road" transform="translate(545 231)"><circle r="3"/><text x="9" y="4">东西主干道</text></g>
      <g class="map-info river" transform="translate(537 334)"><circle r="3"/><text x="9" y="4">中央河流</text></g>
      <g class="map-info facility" transform="translate(866 285)"><circle r="3"/><text x="9" y="4">东部设施区</text></g>

      <g class="camp red-camp" transform="translate(153 219) scale(.58)"><circle class="camp-ring" r="20"/><circle class="camp-core" r="14"/><text class="camp-glyph" text-anchor="middle" y="5">◆</text><text class="camp-title" text-anchor="middle" y="38">红方阵营</text></g>
      <g class="camp blue-camp" transform="translate(848 219) scale(.58)"><circle class="camp-ring" r="20"/><circle class="camp-core" r="14"/><text class="camp-glyph" text-anchor="middle" y="5">◆</text><text class="camp-title" text-anchor="middle" y="38">蓝方阵营</text></g>

      <g class="objective-node" transform="translate(346 274) scale(.58)"><circle r="11"/><text text-anchor="middle" y="4">A</text><text class="objective-label" x="17" y="4">巡查区</text></g>
      <g class="objective-node center" transform="translate(500 275) scale(.58)"><circle r="13"/><text text-anchor="middle" y="4">◎</text><text class="objective-label" x="19" y="4">中央设施</text></g>
      <g class="objective-node blue" transform="translate(654 274) scale(.58)"><circle r="11"/><text text-anchor="middle" y="4">C</text><text class="objective-label" x="17" y="4">巡查区</text></g>
      <g class="checkpoint" transform="translate(500 390) scale(.58)"><circle r="10"/><text text-anchor="middle" y="4">B</text><text class="objective-label" x="16" y="4">检查点</text></g>
      <g class="exit-point" transform="translate(115 460) scale(.58)"><path d="M0 -8 L8 0 L0 8 L-8 0 Z"/><text x="14" y="4">撤离点</text></g>
      <g class="exit-point blue" transform="translate(885 460) scale(.58)"><path d="M0 -8 L8 0 L0 8 L-8 0 Z"/><text x="-14" y="4" text-anchor="end">撤离点</text></g>

      <g v-if="preparationPreview">
        <rect :class="['deployment-area', { own: role === 'red' }]" x="45" y="150" width="375" height="345" rx="24"/>
        <rect :class="['deployment-area', 'blue', { own: role === 'blue' }]" x="580" y="150" width="375" height="345" rx="24"/>
        <g class="deployment-label" transform="translate(68 155)"><rect width="96" height="23" rx="5"/><text x="9" y="16">红方部署区</text></g>
        <g class="deployment-label blue" transform="translate(836 155)"><rect width="96" height="23" rx="5"/><text x="9" y="16">蓝方部署区</text></g>
      </g>

      <g v-for="unit in units" :key="unit.id" :class="['battle-unit', unit.side, { selected: selectedUnitId === unit.id }]" :transform="`translate(${unit.x} ${unit.y}) scale(.58)`" role="button" tabindex="0" :aria-label="`${unit.id} ${unit.name} ${unit.state}`" @click.stop="selectUnit(unit.id)" @keydown.enter="selectUnit(unit.id)">
        <circle class="unit-hitarea" r="20"/><circle class="unit-pulse" r="15"/><circle class="unit-body" r="10"/><text class="unit-glyph" text-anchor="middle" y="4">{{ unit.shape }}</text>
        <g class="unit-nameplate" transform="translate(15 -13)"><rect width="100" height="22" rx="5"/><text x="7" y="15">{{ unit.id }} · {{ unit.name }}</text></g>
      </g>
    </svg>
    </div>
    <div class="battlefield-vignette" aria-hidden="true"></div>
    <div v-if="!preparationPreview" class="map-drag-hint" aria-hidden="true">按住地图拖动查看</div>
  </section>
</template>

<style scoped>
.battlefield{position:absolute;inset:0;overflow:hidden;background:#0a1b23;cursor:grab;touch-action:none;user-select:none}
.battlefield.dragging{cursor:grabbing}
.battlefield-world{position:absolute;left:50%;top:50%;background:#0a1b23 url('/expanded-terrain.png') center/100% 100% no-repeat;will-change:transform}
.battlefield-svg{width:100%;height:100%;display:block}
.battlefield-vignette{position:absolute;inset:0;pointer-events:none;background:linear-gradient(180deg,#07151dbb 0%,transparent 22%,transparent 72%,#07151dbb 100%),linear-gradient(90deg,#07151d66,transparent 17%,transparent 83%,#07151d66)}
.map-drag-hint{position:absolute;left:50%;bottom:46px;transform:translateX(-50%);pointer-events:none;padding:6px 11px;border:1px solid #b0c8cf99;border-radius:5px;background:#0b2534d9;color:#e6f3f4;font-size:11px;font-weight:700;box-shadow:0 2px 8px #03111ba0}
.territory{stroke-width:1.6;stroke-dasharray:8 9;pointer-events:none}
.territory.red{stroke:#f19a9e}.territory.blue{stroke:#8ac9fa}
.frontier{fill:none;stroke:#f5e6b188;stroke-width:1.2;stroke-dasharray:7 10;pointer-events:none}
.map-info{pointer-events:none}
.map-info circle{fill:#cfdbd2;stroke:#092331;stroke-width:1.5}
.map-info text{fill:#eef5ec;font-size:9px;font-weight:700;paint-order:stroke;stroke:#0b2230;stroke-width:3}
.map-info.road circle{fill:#e4c58b}.map-info.river circle{fill:#83cce6}.map-info.facility circle{fill:#e4cc92}
.camp,.objective-node,.checkpoint,.exit-point{filter:url(#map-marker-shadow);pointer-events:none}
.camp-ring{fill:#762b31a0;stroke:#ffc0bb;stroke-width:2;stroke-dasharray:5 5}
.camp-core{fill:#8f3640ee;stroke:#ffe0d8;stroke-width:1.5}
.camp-glyph{fill:#fff0e8;font-size:14px}
.camp-title{fill:#fff;font-size:11px;font-weight:800;paint-order:stroke;stroke:#0b1b27;stroke-width:3}
.blue-camp .camp-ring{fill:#204b7ea0;stroke:#b5deff}
.blue-camp .camp-core{fill:#286ca6ee;stroke:#d9eeff}
.objective-node circle{fill:#173a43de;stroke:#f0e5b0;stroke-width:1.5}
.objective-node.blue circle{stroke:#aad7f5}
.objective-node.center circle{fill:#b98a3dde;stroke:#ffefc7}
.objective-node>text:first-of-type,.checkpoint>text:first-of-type{fill:#fff;font-size:11px;font-weight:900}
.objective-label,.exit-point text{fill:#f6f5e8;font-size:9px;font-weight:800;paint-order:stroke;stroke:#0c202b;stroke-width:3}
.checkpoint circle{fill:#234e56dc;stroke:#d6e9ca;stroke-width:1.5}
.exit-point path{fill:#215347e8;stroke:#bce7d0;stroke-width:1.5}
.exit-point.blue path{fill:#245276e8;stroke:#b8dafa}
.deployment-area{fill:#b4474c12;stroke:#ffb6aa;stroke-width:2;stroke-dasharray:7 7}
.deployment-area.blue{fill:#3a85c012;stroke:#a6d8ff}
.deployment-area.own{fill:#f4c6751c;stroke:#ffe39a;stroke-width:2.5;filter:drop-shadow(0 0 4px #eabd6f)}
.deployment-label rect{fill:#622b35eb;stroke:#ffc7bb}.deployment-label.blue rect{fill:#214f7beb;stroke:#c0e1ff}
.deployment-label text{fill:#fff;font-size:10px;font-weight:800}
.battle-unit{cursor:pointer;filter:url(#map-marker-shadow)}
.unit-hitarea{fill:transparent}
.unit-pulse{fill:#d0535a30;stroke:#ffc0bb;stroke-width:1.5}
.battle-unit.blue .unit-pulse{fill:#4b93d133;stroke:#b5dcff}
.battle-unit.selected .unit-pulse{stroke:#ffe392;stroke-width:3;filter:drop-shadow(0 0 4px #ffde80)}
.unit-body{fill:#d4535e;stroke:#fff0ee;stroke-width:2}
.battle-unit.blue .unit-body{fill:#488dc8;stroke:#eef8ff}
.unit-glyph{fill:#fff;font-size:9px;font-weight:800;pointer-events:none}
.unit-nameplate{opacity:0;pointer-events:none;transition:opacity .16s}
.battle-unit:hover .unit-nameplate,.battle-unit:focus-visible .unit-nameplate,.battle-unit.selected .unit-nameplate{opacity:1}
.unit-nameplate rect{fill:#0b2434f2;stroke:#84a5b777;stroke-width:1.5}
.unit-nameplate text{fill:#fff;font-size:10px;font-weight:800}
</style>
