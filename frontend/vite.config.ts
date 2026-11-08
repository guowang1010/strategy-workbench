import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// 开发：vite 跑在 5173，/api 代理到后端 8765。
// 生产：build 产物 dist 交由后端 FastAPI 静态托管（base 用相对路径，便于任意子目录）。
export default defineConfig({
  plugins: [react()],
  base: './',
  server: {
    port: 5173,
    proxy: {
      '/api': { target: 'http://127.0.0.1:8765', changeOrigin: true },
    },
  },
  build: { outDir: 'dist', emptyOutDir: true },
})
