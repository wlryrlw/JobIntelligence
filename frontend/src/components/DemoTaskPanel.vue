<script setup lang="ts">
import { onBeforeUnmount, ref } from "vue"
import { api } from "../api"
import type { TaskEvent, TaskSnapshot } from "../types"

const running = ref(false)
const snapshot = ref<TaskSnapshot | null>(null)
const events = ref<TaskEvent[]>([])
let controller: AbortController | null = null

async function start() {
  if (running.value) return
  running.value = true
  events.value = []
  controller?.abort()
  controller = new AbortController()
  try {
    snapshot.value = await api.createTask("岗位分析离线演示任务", "offeragent-demo")
    snapshot.value = await api.streamTaskEvents(snapshot.value.task_id, (event) => {
      events.value = [...events.value, event]
    }, controller!.signal)
  } catch (error) {
    events.value = [...events.value, { event_id: `error-${Date.now()}`, task_id: snapshot.value?.task_id || "", sequence: events.value.length + 1, event_type: "task.failed", timestamp: new Date().toISOString(), payload: { message: error instanceof Error ? error.message : "SSE 连接失败" } }]
  } finally {
    running.value = false
  }
}

onBeforeUnmount(() => controller?.abort())
</script>

<template>
  <section class="demo-task-panel" aria-label="OfferAgent 离线任务演示">
    <header><div><small>OFFERAGENT DEMO</small><strong>求职分析执行轨迹</strong></div><button :disabled="running" type="button" @click="start">{{ running ? "执行中…" : "运行演示" }}</button></header>
    <p v-if="!events.length" class="empty">通过 SSE 查看可恢复的任务状态、心跳和完成事件。</p>
    <div v-else class="demo-events">
      <div v-for="event in events" :key="event.event_id" class="demo-event"><i></i><span>{{ event.payload.message || event.event_type }}</span><small>#{{ event.sequence }}</small></div>
    </div>
    <footer v-if="snapshot">状态：{{ snapshot.status }} · {{ snapshot.sequence }} 个事件</footer>
  </section>
</template>

<style scoped>
.demo-task-panel { margin-top: 16px; padding: 12px; border: 1px solid #dfdfdf; border-radius: 13px; background: linear-gradient(145deg, #fff, #f1f1f1); }
.demo-task-panel header { display: flex; align-items: center; justify-content: space-between; gap: 8px; }.demo-task-panel header div { display: grid; gap: 3px; }.demo-task-panel header small { color: #6f6f6f; font-size: 8px; letter-spacing: .08em; }.demo-task-panel header strong { color: #292929; font-size: 11px; }.demo-task-panel button { padding: 6px 8px; border: 0; border-radius: 7px; color: #fff; background: #282828; cursor: pointer; font-size: 9px; }.demo-task-panel button:disabled { opacity: .55; }.empty { margin: 10px 0 0; color: #878787; font-size: 9px; line-height: 1.5; }.demo-events { display: grid; gap: 5px; max-height: 135px; margin-top: 10px; overflow: auto; }.demo-event { display: grid; grid-template-columns: 7px 1fr auto; align-items: center; gap: 7px; color: #525252; font-size: 9px; }.demo-event i { width: 6px; height: 6px; border-radius: 50%; background: #383838; }.demo-event small, .demo-task-panel footer { color: #919191; font-size: 8px; }.demo-task-panel footer { margin-top: 9px; }
</style>
