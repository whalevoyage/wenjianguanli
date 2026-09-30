<script setup>
defineProps({
  title: { type: String, required: true },
  description: { type: String, default: '' },
  eyebrow: { type: String, default: '' },
})
</script>

<template>
  <header class="page-header">
    <div class="page-header__copy">
      <div v-if="eyebrow" class="page-header__eyebrow">{{ eyebrow }}</div>
      <h1>{{ title }}</h1>
      <p v-if="description">{{ description }}</p>
      <div v-if="$slots.meta" class="page-header__meta"><slot name="meta" /></div>
    </div>

    <div v-if="$slots.actions" class="page-header__actions"><slot name="actions" /></div>
  </header>
</template>

<style scoped>
.page-header {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  gap: 20px;
  padding-bottom: 18px;
  border-bottom: 1px solid var(--border);
}

.page-header__copy {
  min-width: 0;
}

.page-header__eyebrow {
  margin-bottom: 8px;
  color: var(--text-muted);
  font-size: 11px;
  font-weight: 700;
  letter-spacing: 0.08em;
  text-transform: uppercase;
}

h1 {
  margin: 0;
  color: var(--text-primary);
  font-size: clamp(28px, 3vw, 38px);
  line-height: 1.12;
  letter-spacing: -0.05em;
}

p {
  max-width: 760px;
  margin: 8px 0 0;
  color: var(--text-secondary);
  font-size: 14px;
  line-height: 1.75;
}

.page-header__meta {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 14px;
}

.page-header__actions {
  display: flex;
  align-items: flex-start;
  justify-content: flex-end;
  gap: 10px;
}

@media (max-width: 900px) {
  .page-header {
    grid-template-columns: 1fr;
  }

  .page-header__actions {
    justify-content: flex-start;
  }
}
</style>
