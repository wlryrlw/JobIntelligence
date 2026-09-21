<script setup lang="ts">
import { computed } from "vue"
import type { QueryResult } from "../types"

const props = defineProps<{
  steps: string[]
  attempts?: QueryResult["attempts"]
  status: QueryResult["status"]
}>()

const normalized = computed(() => props.steps.map((step, index) => ({ label: step, index, done: props.status !== "failed" || index < props.steps.length - 1 })))
</script>

<template>
  <details v-if="normalized.length || props.attempts?.length" class="execution-timeline">
    <summary>查看执行轨迹 · {{ normalized.length }} 步<span v-if="props.attempts?.length"> · SQL {{ props.attempts.length }} 次</span></summary>
    <div class="timeline-body">
      <div v-for="item in normalized" :key="`${item.index}:${item.label}`" class="timeline-row">
        <i :class="{ done: item.done }"></i><span>{{ item.label }}</span>
      </div>
      <div v-for="attempt in props.attempts || []" :key="`sql:${attempt.attempt}`" class="attempt-row">
        <strong>SQL 尝试 {{ attempt.attempt }}</strong><span>{{ attempt.validation }} / {{ attempt.execution }}</span><em v-if="attempt.error_kind">{{ attempt.error_kind }}</em>
      </div>
    </div>
  </details>
</template>

<style scoped>
.execution-timeline { margin: 10px 0; border: 1px solid #e3ebe5; border-radius: 10px; background: #fbfdfb; color: #66746b; font-size: 10px; }
.execution-timeline summary { padding: 9px 11px; cursor: pointer; list-style: none; }.execution-timeline summary::-webkit-details-marker { display: none; }
.timeline-body { display: grid; gap: 7px; padding: 0 11px 11px; border-top: 1px solid #edf2ee; }.timeline-row, .attempt-row { display: flex; align-items: center; gap: 7px; padding-top: 7px; }.timeline-row i { width: 7px; height: 7px; border-radius: 50%; background: #c9d4cd; }.timeline-row i.done { background: #2c8058; }.attempt-row { gap: 8px; color: #718178; }.attempt-row strong { color: #405048; font-size: 9px; }.attempt-row em { color: #a45a49; font-style: normal; }
</style>
