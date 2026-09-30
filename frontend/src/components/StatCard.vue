<script setup>
defineProps({
  title: { type: String, required: true },
  value: { type: String, required: true },
  note: { type: String, default: '' },
  icon: { type: [Object, Function], required: true },
  tone: { type: String, default: 'blue' },
  trend: { type: String, default: '' },
})
</script>

<template>
  <article class="stat-card app-card">
    <div class="stat-card__head">
      <span class="stat-card__title">{{ title }}</span>
      <div class="stat-card__icon" :class="`is-${tone}`">
        <el-icon :size="18"><component :is="icon" /></el-icon>
      </div>
    </div>

    <div class="stat-card__value">{{ value }}</div>
    <div class="stat-card__foot">
      <span class="stat-card__note">{{ note }}</span>
      <el-tag v-if="trend" size="small" effect="plain">{{ trend }}</el-tag>
    </div>
  </article>
</template>

<style scoped>
.stat-card {
  min-height: 144px;
  display: grid;
  gap: 14px;
  padding: 18px;
}

.stat-card__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
}

.stat-card__title {
  color: var(--text-secondary);
  font-size: 13px;
  font-weight: 600;
}

.stat-card__icon {
  width: 38px;
  height: 38px;
  display: grid;
  place-items: center;
  border-radius: 12px;
}

.is-blue {
  color: #1768ac;
  background: rgba(23, 104, 172, 0.1);
}

.is-green {
  color: #177a5f;
  background: rgba(23, 122, 95, 0.1);
}

.is-orange {
  color: #b87926;
  background: rgba(184, 121, 38, 0.1);
}

.is-purple,
.is-cyan {
  color: #4a5f7a;
  background: rgba(74, 95, 122, 0.1);
}

.is-slate {
  color: #5d6874;
  background: rgba(93, 104, 116, 0.1);
}

.stat-card__value {
  color: var(--text-primary);
  font-size: clamp(26px, 2vw, 32px);
  line-height: 1.08;
  font-weight: 700;
  letter-spacing: -0.05em;
  word-break: break-word;
}

.stat-card__foot {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: 10px;
  margin-top: auto;
}

.stat-card__note {
  color: var(--text-muted);
  font-size: 12px;
  line-height: 1.65;
}
</style>
