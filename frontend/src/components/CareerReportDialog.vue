<script setup lang="ts">
import type { CareerReport, SkillCoverageItem } from "../types"

defineProps<{
  open: boolean
  report: CareerReport | null
  loading?: boolean
  error?: string
}>()

defineEmits<{
  close: []
  download: []
  retry: []
}>()

function statusLabel(status: SkillCoverageItem["status"]) {
  return { covered: "已覆盖", partial: "部分覆盖", missing: "待补足" }[status]
}

function statusClass(status: SkillCoverageItem["status"]) {
  return `status-${status}`
}

function percentage(value: number | null | undefined) {
  return value == null ? "—" : `${(value * 100).toFixed(0)}%`
}

function money(value: number | null | undefined) {
  return value == null ? "—" : `${value.toFixed(1)}K/月`
}
</script>

<template>
  <Teleport to="body">
    <div v-if="open" class="career-dialog-backdrop" @click.self="$emit('close')">
      <section class="career-dialog" role="dialog" aria-modal="true" aria-label="简历与岗位证据分析">
        <header class="career-dialog-header">
          <div>
            <small>RESUME · JOB EVIDENCE</small>
            <h2>{{ report?.title || "简历与岗位证据分析" }}</h2>
            <p v-if="report">证据来自已上传简历与岗位数据样本，不代表录用概率。</p>
          </div>
          <button class="icon-button" aria-label="关闭分析报告" @click="$emit('close')">×</button>
        </header>

        <div v-if="loading" class="career-dialog-state">
          <span class="state-spinner"></span>
          <strong>正在构建岗位画像并比对简历证据…</strong>
          <small>系统会先统计岗位样本，再给出技能覆盖和准备建议。</small>
        </div>
        <div v-else-if="error" class="career-dialog-state error">
          <strong>分析未完成</strong>
          <p>{{ error }}</p>
          <button class="primary-button" @click="$emit('retry')">重新分析</button>
        </div>
        <div v-else-if="report" class="career-dialog-body">
          <div class="career-summary">
            <strong>分析结论</strong>
            <p>{{ report.summary }}</p>
          </div>

          <div class="career-stat-grid">
            <div><small>岗位样本</small><strong>{{ report.job_profile.sample_size }} 份</strong></div>
            <div><small>有效 JD</small><strong>{{ report.job_profile.valid_jd_count }} 份</strong></div>
            <div><small>技能覆盖</small><strong>{{ report.skill_coverage.filter(item => item.status === 'covered').length }} / {{ report.skill_coverage.length }}</strong></div>
            <div><small>薪资中位数</small><strong>{{ money(report.job_profile.salary_p50_k) }}</strong></div>
          </div>

          <p v-if="report.job_profile.small_sample_warning" class="career-warning">{{ report.job_profile.small_sample_warning }}</p>

          <section class="career-section">
            <h3>技能覆盖</h3>
            <div v-if="!report.skill_coverage.length" class="career-empty">岗位样本中暂未提取到结构化技能。</div>
            <div v-else class="career-skill-table">
              <div class="career-skill-row career-skill-head"><span>技能</span><span>岗位出现率</span><span>状态</span><span>建议</span></div>
              <div v-for="item in report.skill_coverage" :key="`${item.skill}-${item.category}`" class="career-skill-row">
                <strong>{{ item.skill }}</strong>
                <span>{{ percentage(item.jd_occurrence_rate) }}</span>
                <span><em class="career-status" :class="statusClass(item.status)">{{ statusLabel(item.status) }}</em></span>
                <span class="skill-suggestion">{{ item.suggestion || "—" }}</span>
              </div>
            </div>
          </section>

          <section v-if="report.recommendations.length" class="career-section">
            <h3>优先建议</h3>
            <ul><li v-for="item in report.recommendations" :key="item">{{ item }}</li></ul>
          </section>

          <div class="career-two-columns">
            <section class="career-section"><h3>简历改写方向</h3><ul><li v-for="item in report.rewrite_directions" :key="item">{{ item }}</li><li v-if="!report.rewrite_directions.length">暂无额外改写方向</li></ul></section>
            <section class="career-section"><h3>面试准备清单</h3><ul><li v-for="item in report.interview_checklist" :key="item">{{ item }}</li><li v-if="!report.interview_checklist.length">暂无额外清单</li></ul></section>
          </div>

          <section v-if="report.facts.length" class="career-section career-facts"><h3>口径与事实</h3><ul><li v-for="item in report.facts" :key="item">{{ item }}</li></ul></section>
        </div>
        <div v-else class="career-dialog-state"><strong>请先上传简历，再开始分析。</strong></div>

        <footer v-if="report && !loading" class="career-dialog-footer">
          <small>报告 ID：{{ report.report_id }} · 生成于 {{ new Date(report.generated_at).toLocaleString("zh-CN") }}</small>
          <div><button class="secondary-button" @click="$emit('close')">关闭</button><button class="primary-button" @click="$emit('download')">下载 Markdown</button></div>
        </footer>
      </section>
    </div>
  </Teleport>
</template>

<style scoped>
.career-dialog-backdrop { position: fixed; inset: 0; z-index: 100; display: grid; place-items: center; padding: 24px; background: rgba(0, 0, 0, .48); backdrop-filter: blur(4px); }
.career-dialog { display: flex; width: min(900px, 100%); max-height: min(88vh, 920px); flex-direction: column; overflow: hidden; border: 1px solid #d6d6d6; border-radius: 20px; background: #fff; box-shadow: 0 28px 90px rgba(0, 0, 0, .2); }
.career-dialog-header { display: flex; align-items: flex-start; justify-content: space-between; gap: 18px; padding: 24px 28px 18px; border-bottom: 1px solid #e7e7e7; background: linear-gradient(135deg, #f5f5f5, #fff); }
.career-dialog-header small { color: #555; font-size: 11px; font-weight: 700; letter-spacing: 1.6px; }.career-dialog-header h2 { margin: 8px 0 4px; color: #202020; font-family: Georgia, "Noto Serif SC", serif; font-size: 27px; font-weight: 500; }.career-dialog-header p { margin: 0; color: #7c7c7c; font-size: 13px; }
.icon-button { width: 34px; height: 34px; border: 0; border-radius: 9px; color: #6e7c73; background: #eef5f0; font-size: 24px; line-height: 1; cursor: pointer; }
.career-dialog-body { padding: 22px 28px 30px; overflow: auto; color: #404040; font-size: 14px; line-height: 1.7; }.career-summary { padding: 16px 18px; border: 1px solid #d8d8d8; border-radius: 13px; background: #f4f4f4; }.career-summary strong { color: #222; font-size: 15px; }.career-summary p { margin: 6px 0 0; }
.career-stat-grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 10px; margin: 16px 0; }.career-stat-grid > div { padding: 13px 14px; border: 1px solid #e4e4e4; border-radius: 11px; background: #fbfbfb; }.career-stat-grid small, .career-dialog-footer small { display: block; color: #858585; font-size: 12px; }.career-stat-grid strong { display: block; margin-top: 3px; color: #2c2c2c; font-size: 17px; }.career-warning { margin: 10px 0; padding: 10px 12px; border-radius: 9px; color: #805d2a; background: #fff8e7; font-size: 13px; }
.career-section { margin-top: 22px; }.career-section h3 { margin: 0 0 9px; color: #2d2d2d; font-size: 17px; }.career-section ul { margin: 0; padding-left: 22px; }.career-section li + li { margin-top: 6px; }.career-skill-table { overflow: hidden; border: 1px solid #e0e0e0; border-radius: 11px; }.career-skill-row { display: grid; grid-template-columns: 1.1fr .7fr .75fr 2fr; gap: 12px; align-items: center; padding: 10px 12px; border-top: 1px solid #ededed; }.career-skill-row:first-child { border-top: 0; }.career-skill-head { color: #777; background: #f7f7f7; font-size: 12px; font-weight: 700; }.career-skill-row strong { color: #313131; }.skill-suggestion { color: #666; font-size: 13px; }.career-status { display: inline-block; padding: 3px 7px; border-radius: 999px; font-size: 12px; font-style: normal; font-weight: 700; }.status-covered { color: #333; background: #e4e4e4; }.status-partial { color: #866127; background: #fff3d4; }.status-missing { color: #a74c42; background: #fdebea; }.career-two-columns { display: grid; grid-template-columns: 1fr 1fr; gap: 24px; }.career-facts { padding-top: 17px; border-top: 1px solid #e8e8e8; }.career-empty, .career-dialog-state { padding: 28px; color: #848484; text-align: center; }.career-dialog-state { display: grid; min-height: 230px; place-items: center; align-content: center; gap: 10px; }.career-dialog-state.error { color: #a2473d; }.career-dialog-state p { max-width: 500px; margin: 0; }
.career-dialog-footer { display: flex; align-items: center; justify-content: space-between; gap: 16px; padding: 14px 28px; border-top: 1px solid #e7e7e7; background: #fbfbfb; }.career-dialog-footer > div { display: flex; gap: 8px; }.primary-button, .secondary-button { min-height: 36px; padding: 0 14px; border-radius: 8px; font-size: 13px; font-weight: 700; cursor: pointer; }.primary-button { border: 1px solid #222; color: #fff; background: #222; }.secondary-button { border: 1px solid #d5d5d5; color: #4d4d4d; background: #fff; }
@media (max-width: 700px) { .career-dialog-backdrop { padding: 10px; }.career-dialog-header, .career-dialog-body, .career-dialog-footer { padding-left: 18px; padding-right: 18px; }.career-stat-grid { grid-template-columns: repeat(2, 1fr); }.career-skill-row { grid-template-columns: 1fr 1fr; }.career-skill-head { display: none; }.career-skill-row .skill-suggestion { grid-column: 1 / -1; }.career-two-columns { grid-template-columns: 1fr; gap: 0; }.career-dialog-footer { align-items: flex-start; flex-direction: column; } }
</style>
