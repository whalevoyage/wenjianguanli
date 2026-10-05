<script setup>
import EmptyState from './EmptyState.vue'

defineProps({
  columns: { type: Array, required: true },
  data: { type: Array, required: true },
  height: { type: [String, Number], default: 'auto' },
  emptyText: { type: String, default: '暂无数据' },
  emptyDescription: { type: String, default: '' },
  emptyIcon: { type: [Object, Function], default: null },
  showIndex: { type: Boolean, default: false },
  rowClassName: { type: [String, Function], default: '' },
})
</script>

<template>
  <div class="table-shell">
    <el-table :data="data" :height="height" class="data-table" table-layout="fixed" :row-class-name="rowClassName">
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

      <!-- 空态走自绘组件，不用 el-table 的裸文本 -->
      <template #empty>
        <EmptyState :icon="emptyIcon" :title="emptyText" :description="emptyDescription" />
      </template>
    </el-table>
  </div>
</template>

<style scoped>
/* 保留既有行高；局部变量便于组件内统一调整。 */
.table-shell {
  --dt-head-h: 46px;
  --dt-row-h: 54px;
}
/* 表格是实色层，不参与毛玻璃——长列表 + blur 会掉帧，且小字对比度不可控 */
.table-shell {
  overflow-x: auto;
  overflow-y: hidden;
  border: 1px solid var(--hairline);
  border-radius: var(--radius);
  transition: border-color var(--dur) var(--ease);
}

.data-table {
  width: 100%;
  --el-table-bg-color: transparent;
  --el-table-tr-bg-color: transparent;
  --el-table-header-bg-color: transparent;
  --el-table-row-hover-bg-color: var(--surface-2);
  --el-table-border-color: var(--hairline);
}

:deep(.el-table th.el-table__cell) {
  height: var(--dt-head-h);
  color: var(--ink-3);
  font-size: var(--fs-micro);
  font-weight: 600;
  letter-spacing: 0.02em;
  background: transparent;
}

:deep(.el-table td.el-table__cell) {
  height: var(--dt-row-h);
  color: var(--ink);
  background: transparent;
  font-size: var(--fs-body);
}

:deep(.el-table .cell) {
  font-variant-numeric: tabular-nums;
}

:deep(.el-table__body tr) {
  transition: background-color var(--dur-fast) var(--ease);
}

:deep(.el-table__body tr:last-child td.el-table__cell) {
  border-bottom: 0;
}

:deep(.el-table__inner-wrapper::before) {
  display: none;
}

:deep(.el-table__border-left-patch) {
  background: transparent;
}

/* 空态单元格：去掉表格默认的居中内边距挤压 */
:deep(.el-table__empty-block) {
  background: transparent;
}

:deep(.el-table__empty-text) {
  width: 100%;
  line-height: normal;
}
</style>
