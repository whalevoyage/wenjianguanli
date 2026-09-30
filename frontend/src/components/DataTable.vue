<script setup>
defineProps({
  columns: { type: Array, required: true },
  data: { type: Array, required: true },
  height: { type: [String, Number], default: 'auto' },
  emptyText: { type: String, default: '暂无数据' },
  showIndex: { type: Boolean, default: false },
})
</script>

<template>
  <div class="table-shell">
    <el-table :data="data" :height="height" :empty-text="emptyText" class="data-table" table-layout="fixed">
      <el-table-column v-if="showIndex" type="index" label="#" width="60" align="center" />
      <el-table-column
        v-for="column in columns"
        :key="column.prop"
        :prop="column.prop"
        :label="column.label"
        :width="column.width"
        :min-width="column.minWidth"
        :align="column.align || 'left'"
        :show-overflow-tooltip="column.ellipsis !== false"
      >
        <template #default="scope">
          <slot :name="column.prop" :row="scope.row" :value="scope.row[column.prop]">
            {{ scope.row[column.prop] }}
          </slot>
        </template>
      </el-table-column>
    </el-table>
  </div>
</template>

<style scoped>
.table-shell {
  overflow: hidden;
  border: 1px solid rgba(28, 41, 54, 0.08);
  border-radius: 22px;
}

.data-table {
  width: 100%;
  --el-table-bg-color: transparent;
  --el-table-tr-bg-color: transparent;
  --el-table-header-bg-color: rgba(15, 85, 108, 0.06);
  --el-table-row-hover-bg-color: rgba(15, 85, 108, 0.06);
}

:deep(.el-table th.el-table__cell) {
  height: 52px;
  color: var(--text-secondary);
  font-size: 12px;
  font-weight: 700;
  background: transparent;
}

:deep(.el-table td.el-table__cell) {
  height: 58px;
  color: var(--text-primary);
  background: transparent;
}

:deep(.el-table__body tr:last-child td.el-table__cell) {
  border-bottom: 0;
}

:deep(.el-table__inner-wrapper::before) {
  display: none;
}
</style>
