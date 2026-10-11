import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import AutoImport from 'unplugin-auto-import/vite'
import Components from 'unplugin-vue-components/vite'
import { ElementPlusResolver } from 'unplugin-vue-components/resolvers'

export default defineConfig({
  plugins: [
    vue(),
    AutoImport({
      resolvers: [ElementPlusResolver()],
    }),
    Components({
      resolvers: [ElementPlusResolver()],
    }),
  ],
  build: {
    chunkSizeWarningLimit: 450,
    rollupOptions: {
      output: {
        manualChunks: {
          'vue-core': ['vue', 'vue-router'],
          'http-utils': ['axios'],
        },
      },
    },
  },
  server: {
    host: '127.0.0.1',
    port: 5173,
    strictPort: true,
    // 开发时把 /api 转给本地后端。
    //
    // 有这条代理，前端代码里就可以一律用**相对路径** /api：
    //   开发 → 5173 自己代理到后端
    //   打包 → 后端同源托管前端，同一个相对路径直接命中
    // 于是再也不需要写死端口。之前默认写的是 http://127.0.0.1:8000/api，
    // 而打包后启动器会挑任意空闲端口（8000 被占时）→ 界面就找不到后端了。
    //
    // 后端不在 8000 时：FILEPILOT_BACKEND_PORT=8011 npm run dev
    proxy: {
      '/api': {
        target: `http://127.0.0.1:${process.env.FILEPILOT_BACKEND_PORT || 8000}`,
        changeOrigin: true,
      },
    },
  },
})
