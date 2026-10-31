<script setup lang="ts">
import { computed } from 'vue'

export interface MapUnit {
  id: string
  name: string
  code: string
  type: 'recon' | 'ground' | 'support'
  side: 'red' | 'blue'
  x: number
  y: number
  state: string
}

const props = defineProps<{
  units: MapUnit[]
  selected: string
  follow: boolean
  zoom: number
  deploying: boolean
  side: 'red' | 'blue' | 'host'
  showRoutes: boolean
  showGrid: boolean
  redCamp: { x: number; y: number } | null
  blueCamp: { x: number; y: number } | null
}>()
const emit = defineEmits<{
  select: [id: string]
  deploy: [point: { x: number; y: number }]
}>()
const selectedUnit = computed(() => props.units.find(unit => unit.id === props.selected))
const mapTransform = computed(() => {
  const scale = props.follow ? 1.55 : props.zoom
  const unit = selectedUnit.value
  const x = props.follow && unit ? 570 - unit.x * scale : 600 * (1 - scale)
  const y = props.follow && unit ? 330 - unit.y * scale : 360 * (1 - scale)
  return `translate(${x} ${y}) scale(${scale})`
})
const ridges = Array.from({ length: 17 }, (_, index) => {
  const inset = index * 11
  return `M ${-90 + inset} ${260 - inset * .32} C ${80 + inset} ${90 - inset * .6}, ${220 + inset * .9} ${260 - inset}, ${330 + inset} ${130 - inset * .25} S ${610 - inset} ${-30 + inset * .3}, ${820 - inset * .7} ${130 - inset * .6}`
})
const easternRidges = Array.from({ length: 14 }, (_, index) => {
  const inset = index * 10
  return `M ${810 + inset * .4} 740 C ${600 + inset} ${560 - inset * .5}, ${1150 - inset} ${610 - inset}, ${970 + inset * .2} ${360 - inset} S ${1250 - inset} ${190 + inset}, 1300 ${60 + inset}`
})
const trees = Array.from({ length: 110 }, (_, index) => {
  const x = (index * 137 + 39) % 1200
  const y = (index * 83 + 79) % 720
  return { x, y, radius: 9 + index % 7 }
}).filter(tree => !(tree.x > 350 && tree.x < 680))
const buildings = Array.from({ length: 23 }, (_, index) => ({ x: 670 + (index * 43) % 190, y: 270 + (index * 37) % 180, width: 9 + index % 3 * 4, height: 8 + index % 4 * 3 }))

function selectMap(event: MouseEvent) {
  if (!props.deploying) return
  const target = event.currentTarget as SVGSVGElement
  const matrix = target.getScreenCTM()
  if (!matrix) return
  const point = new DOMPoint(event.clientX, event.clientY).matrixTransform(matrix.inverse())
  const scale = props.zoom
  emit('deploy', { x: Math.round((point.x - 600 * (1 - scale)) / scale), y: Math.round((point.y - 360 * (1 - scale)) / scale) })
}
</script>

<template>
  <svg class="terrain-map" viewBox="0 0 1200 720" role="img" aria-label="青岚河谷虚构战术地图，可选择单位，部署模式可点选兵营" @click="selectMap">
    <defs>
      <radialGradient id="terrain-light"><stop stop-color="#344b41"/><stop offset="1" stop-color="#1d302c"/></radialGradient>
      <linearGradient id="river-fill" x2="1" y2="1"><stop stop-color="#4e777d"/><stop offset="1" stop-color="#254d57"/></linearGradient>
      <pattern id="terrain-grid" width="100" height="90" patternUnits="userSpaceOnUse"><path d="M100 0H0V90" fill="none" stroke="#c1d5be" stroke-opacity=".1" stroke-width="1"/></pattern>
      <pattern id="terrain-grain" width="9" height="9" patternUnits="userSpaceOnUse"><circle cx="2" cy="4" r=".7" fill="#c7cfab" opacity=".1"/><circle cx="7" cy="1" r=".6" fill="#010b0b" opacity=".2"/></pattern>
      <filter id="map-shadow"><feDropShadow dx="0" dy="3" stdDeviation="4" flood-color="#061318" flood-opacity=".8"/></filter>
    </defs>
    <rect width="1200" height="720" fill="#20342e"/>
    <g :transform="mapTransform" class="map-moving-world">
      <rect x="-800" y="-600" width="2800" height="2000" fill="url(#terrain-light)"/>
      <path d="M0 0H1200V720H0Z" fill="url(#terrain-grain)"/>
      <path d="M0 0H790L675 112 499 135 370 255 154 287 0 224Z" fill="#456048" opacity=".27"/>
      <path d="M790 720L728 572 898 443 891 293 1092 131 1200 0V720Z" fill="#455949" opacity=".35"/>
      <path v-for="(ridge, index) in ridges" :key="'north-'+index" :d="ridge" fill="none" stroke="#9baf86" :stroke-opacity="index % 3 === 0 ? .29 : .13" :stroke-width="index % 3 === 0 ? 1.3 : .8"/>
      <path v-for="(ridge, index) in easternRidges" :key="'east-'+index" :d="ridge" fill="none" stroke="#a1b08d" :stroke-opacity="index % 3 === 0 ? .25 : .11"/>
      <g opacity=".45"><circle v-for="(tree,index) in trees" :key="index" :cx="tree.x" :cy="tree.y" :r="tree.radius" fill="#122c24"/><circle v-for="(tree,index) in trees" :key="'c'+index" :cx="tree.x-2" :cy="tree.y-3" :r="tree.radius*.6" fill="#4a6246"/></g>
      <path d="M561 -30 C462 73 662 148 552 238 S411 319 497 400 S638 488 535 577 S469 673 587 770" fill="none" stroke="#86a1a0" stroke-width="68" opacity=".17"/>
      <path d="M561 -30 C462 73 662 148 552 238 S411 319 497 400 S638 488 535 577 S469 673 587 770" fill="none" stroke="url(#river-fill)" stroke-width="51"/>
      <path d="M559 -30 C460 73 660 148 550 238 S409 319 495 400 S636 488 533 577 S467 673 585 770" fill="none" stroke="#a2b7ad" stroke-width="1.2" stroke-dasharray="14 11" opacity=".25"/>
      <path d="M-30 538L191 471 333 436 459 352 670 352 786 266 1046 233 1230 175 M333 436L376 594 571 644 842 568 1062 583 1230 663 M670 352L725 456 842 568 M191 471L158 286 272 214 441 192 670 352" class="map-road-border"/>
      <path d="M-30 538L191 471 333 436 459 352 670 352 786 266 1046 233 1230 175 M333 436L376 594 571 644 842 568 1062 583 1230 663 M670 352L725 456 842 568 M191 471L158 286 272 214 441 192 670 352" class="map-road"/>
      <path d="M449 339L503 339V365H449Z" fill="#9b9b82" stroke="#192b26" stroke-width="3"/><path d="M450 346H503M450 358H503" stroke="#2d3a30" stroke-width="3"/>
      <g transform="rotate(-9 750 350)" opacity=".7"><rect v-for="(building,index) in buildings" :key="index" :x="building.x" :y="building.y" :width="building.width" :height="building.height" fill="#9ca28a" stroke="#c6c7a6" stroke-width=".5"/></g>
      <rect v-if="showGrid" width="1200" height="720" fill="url(#terrain-grid)"/>
      <g class="region-labels"><text x="213" y="116">北 岭 山 地</text><text x="680" y="500">青 岚 镇</text><text x="1000" y="626">东 部 林 区</text><text x="403" y="306" transform="rotate(-54 403 306)">青 岚 河</text></g>
      <path v-if="deploying" d="M60 390L251 344 325 546 129 618Z" fill="#e475631c" stroke="#ed8a76" stroke-dasharray="8 7" stroke-width="2"/>
      <path v-if="deploying && (side === 'host' || side === 'blue')" d="M922 199L1110 161 1157 349 949 382Z" fill="#6daedc18" stroke="#6daedc" stroke-dasharray="8 7" stroke-width="2"/>
      <g v-if="showRoutes && !deploying"><path d="M238 472L333 436 459 352 670 352 786 266" fill="none" stroke="#8ed1b0" stroke-width="3" stroke-dasharray="7 6" opacity=".85"/><path d="M316 328Q480 188 685 246T934 288" fill="none" stroke="#a4cdbc" stroke-width="1.5" stroke-dasharray="4 9" opacity=".65"/></g>
      <g v-for="point in [{x:333,y:436,code:'A',name:'西岸集结点'},{x:670,y:352,code:'B',name:'桥东检查点'},{x:786,y:266,code:'C',name:'北侧观察点'}]" :key="point.code" class="map-target" :transform="`translate(${point.x} ${point.y})`"><circle r="20"/><circle r="5"/><text y="-30" class="target-code">{{ point.code }}</text><text x="27" y="5">{{ point.name }}</text></g>
      <g v-if="side !== 'blue'" :transform="`translate(${redCamp?.x || 191} ${redCamp?.y || 471})`" class="camp-marker"><path d="M-18 9V-9L0-21 18-9V9Z"/><path d="M-7 9V-3H7V9"/><text x="-28" y="38">红方兵营</text></g>
      <g v-if="side === 'host' || side === 'blue'" :transform="`translate(${blueCamp?.x || 1046} ${blueCamp?.y || 233})`" class="camp-marker blue-camp"><path d="M-18 9V-9L0-21 18-9V9Z"/><path d="M-7 9V-3H7V9"/><text x="-28" y="38">蓝方兵营</text></g>
      <g v-for="unit in units" :key="unit.id" class="map-unit" :class="[unit.side, {selected: selected === unit.id}]" :transform="`translate(${unit.x} ${unit.y})`" tabindex="0" role="button" :aria-label="'选择'+unit.name" @click.stop="emit('select',unit.id)" @keydown.enter="emit('select',unit.id)">
        <circle v-if="selected === unit.id" r="35" class="selection-ring"/><circle r="22" class="unit-disc"/>
        <path v-if="unit.type === 'recon'" d="M-12 0H12M0-10V10M-8-7L8 7M-8 7L8-7" class="unit-glyph"/>
        <path v-else-if="unit.type === 'ground'" d="M-10-7H10V7H-10ZM-10-7L10 7M10-7L-10 7" class="unit-glyph"/>
        <path v-else d="M-10-7H10V7H-10ZM0-5V5M-5 0H5" class="unit-glyph"/>
        <rect x="-27" y="29" width="54" height="23" rx="3" class="unit-label-bg"/><text y="45" text-anchor="middle" class="unit-label">{{ unit.code }}</text>
      </g>
      <g v-if="side !== 'host' && !deploying" transform="translate(972 300)" class="last-contact"><path d="M0-17L17 0 0 17-17 0Z"/><text x="25" y="4">历史发现 · T+12</text><text y="5" text-anchor="middle">?</text></g>
    </g>
  </svg>
</template>
