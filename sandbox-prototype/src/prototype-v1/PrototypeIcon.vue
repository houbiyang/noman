<script setup lang="ts">
withDefaults(defineProps<{ name: string; size?: number }>(), { size: 18 })
const paths: Record<string, string> = {
  map: 'M3 5l6-2 6 2 6-2v16l-6 2-6-2-6 2V5zm6-2v16m6-14v16',
  grid: 'M3 3h7v7H3zm11 0h7v7h-7zM3 14h7v7H3zm11 0h7v7h-7z',
  clock: 'M12 8v5l3 2M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18',
  flag: 'M5 21V3m0 1c5-4 9 4 15 0v10c-6 4-10-4-15 0',
  history: 'M3 11a9 9 0 1 1 2 7M3 4v7h7m2-4v6l4 2',
  arrow: 'M5 12h14m-5-5 5 5-5 5',
  chevron: 'm9 5 7 7-7 7',
  back: 'm14 5-7 7 7 7M7 12h14',
  close: 'm6 6 12 12M6 18 18 6',
  play: 'm8 4 12 8-12 8V4',
  pause: 'M8 5v14m8-14v14',
  target: 'M12 2v4m0 12v4M2 12h4m12 0h4M12 5a7 7 0 1 0 0 14 7 7 0 0 0 0-14m0 4a3 3 0 1 0 0 6 3 3 0 0 0 0-6',
  layers: 'm3 7 9-5 9 5-9 5-9-5zm0 5 9 5 9-5M3 17l9 5 9-5',
  check: 'm5 12 4 4L19 6',
  save: 'M5 3h12l4 4v14H3V3h2zm2 0v6h10V3M7 21v-8h10v8',
  bolt: 'm13 2-9 12h7l-1 8 10-13h-7l0-7',
  document: 'M5 3h10l5 5v13H5V3zm9 0v6h6M8 13h9m-9 4h7',
  search: 'm16 16 5 5M10 3a7 7 0 1 0 0 14 7 7 0 0 0 0-14',
  plus: 'M12 5v14M5 12h14',
  drone: 'M7 7l10 10M7 17 17 7M9 12h6M5 3a2 2 0 1 0 0 4 2 2 0 0 0 0-4m14 0a2 2 0 1 0 0 4 2 2 0 0 0 0-4M5 17a2 2 0 1 0 0 4 2 2 0 0 0 0-4m14 0a2 2 0 1 0 0 4 2 2 0 0 0 0-4',
  shield: 'm12 2 8 4v6c0 5-8 10-8 10S4 17 4 12V6l8-4',
  box: 'm3 6 9-4 9 4v12l-9 4-9-4V6zm0 0 9 5 9-5m-9 5v11',
  external: 'M14 3h7v7m0-7L10 14M10 3H3v18h18v-7',
  eye: 'M2 12s4-7 10-7 10 7 10 7-4 7-10 7S2 12 2 12zm10-3a3 3 0 1 0 0 6 3 3 0 0 0 0-6',
}
</script>
<template><svg :width="size" :height="size" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path :d="paths[name] || paths.grid"/></svg></template>
