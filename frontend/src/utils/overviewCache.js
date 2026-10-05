/* overview 的模块级缓存。
   Home.vue 每次进入都会重新挂载，<script setup> 里的顶层变量会随实例重建，
   所以缓存必须放在模块作用域（这里），才能在切走再切回时复用上一次的数据，
   避免「切到首页先空白一下」。 */

let cache = null

export function readOverviewCache() {
  return cache
}

export function writeOverviewCache(value) {
  cache = value
}
