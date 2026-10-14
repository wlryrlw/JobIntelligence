<script setup lang="ts">
import type { QueryResult } from "../types"

const props = defineProps<{
  clarification: QueryResult["clarification"]
}>()

const emit = defineEmits<{ choose: [optionId: string] }>()
</script>

<template>
  <section v-if="props.clarification" class="clarification-panel" aria-label="需要补充的信息">
    <p>{{ props.clarification.question }}</p>
    <small>{{ props.clarification.reason }}</small>
    <div class="clarification-options">
      <button v-for="option in props.clarification.options" :key="option.id" type="button" @click="emit('choose', option.id)">
        {{ option.label }}<span v-if="option.recommended">推荐</span>
      </button>
    </div>
  </section>
</template>

<style scoped>
.clarification-panel { display: grid; gap: 8px; padding: 13px 15px; border: 1px solid #eadfbe; border-radius: 12px; background: #fffaf0; color: #6d5b35; }
.clarification-panel p { margin: 0; color: #4e4026; font-size: 12px; line-height: 1.7; }
.clarification-panel small { color: #9a875e; font-size: 10px; }
.clarification-options { display: flex; flex-wrap: wrap; gap: 7px; }
.clarification-options button { padding: 7px 10px; border: 1px solid #dfcf9d; border-radius: 8px; color: #705a2a; background: #fff; cursor: pointer; font-size: 10px; }
.clarification-options button:hover { border-color: #b89a4d; background: #fff6dd; }
.clarification-options span { margin-left: 5px; color: #9a7623; font-size: 8px; }
</style>
