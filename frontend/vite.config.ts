import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import { resolve } from 'path';

// https://vitejs.dev/config/
export default defineConfig({
  // 项目根目录
  root: '.',

  // 公共基础路径 - 匹配输出目录
  base: '/static/js/dist/',

  // React 插件
  plugins: [react()],

  // 路径别名
  resolve: {
    alias: {
      '@': resolve(__dirname, 'src'),
      '@api': resolve(__dirname, 'src/api'),
      '@components': resolve(__dirname, 'src/components'),
      '@hooks': resolve(__dirname, 'src/hooks'),
      '@store': resolve(__dirname, 'src/store'),
      '@utils': resolve(__dirname, 'src/utils'),
      '@i18n': resolve(__dirname, 'src/i18n'),
      '@types': resolve(__dirname, 'src/types'),
    },
  },

  // 构建配置
  build: {
    // 输出目录 - 构建到父目录的 static/js/dist 目录
    outDir: '../static/js/dist',
    emptyOutDir: true,

    // 生成 source map 用于调试
    sourcemap: true,

    // 代码分割策略
    rollupOptions: {
      output: {
        // 入口文件命名
        entryFileNames: '[name].[hash].js',
        // 代码块文件命名
        chunkFileNames: '[name].[hash].js',
        // 资源文件命名
        assetFileNames: '[name].[hash].[ext]',

        // 手动代码分割 - Phase 4 React 优化
        manualChunks: (id) => {
          // React 核心库
          if (id.includes('node_modules/react/') || id.includes('node_modules/react-dom/')) {
            return 'react-vendor';
          }
          // React Router
          if (id.includes('node_modules/react-router-dom/')) {
            return 'router';
          }
          // TanStack Query
          if (id.includes('node_modules/@tanstack/')) {
            return 'query';
          }
          // Zustand 状态管理
          if (id.includes('node_modules/zustand/')) {
            return 'zustand';
          }
          // Chart.js
          if (id.includes('node_modules/chart.js/') || id.includes('node_modules/react-chartjs-2/')) {
            return 'charts';
          }
          // date-fns
          if (id.includes('node_modules/date-fns/')) {
            return 'date-fns';
          }
          // API 模块
          if (id.includes('src/api/')) {
            return 'api';
          }
          // 页面组件 - 懒加载，不打包到 components chunk
          // 这些组件会自动生成单独的 chunk
          if (id.includes('src/components/features/')) {
            // 让懒加载的页面组件保持独立
            return;
          }
          // 公共组件模块（不包括 features）
          if (id.includes('src/components/common/') || id.includes('src/components/layout/')) {
            return 'components';
          }
          // Hooks
          if (id.includes('src/hooks/')) {
            return 'hooks';
          }
          // Store
          if (id.includes('src/store/')) {
            return 'store';
          }
          // Utils
          if (id.includes('src/utils/')) {
            return 'utils';
          }
          // i18n
          if (id.includes('src/i18n/')) {
            return 'i18n';
          }
        },
      },
    },

    // 块大小警告限制
    chunkSizeWarningLimit: 500,

    // 启用 CSS 代码分割
    cssCodeSplit: true,

    // 启用最小化
    minify: 'esbuild',

    // 目标浏览器
    target: 'es2020',
  },

  // 开发服务器配置
  server: {
    port: 3000,
    open: false,
    cors: true,
    // 代理配置 - 代理 API 请求到后端
    proxy: {
      '/api': {
        target: 'http://localhost:5001',
        changeOrigin: true,
      },
      '/auth': {
        target: 'http://localhost:5001',
        changeOrigin: true,
      },
    },
  },

  // CSS 配置
  css: {
    devSourcemap: true,
  },

  // 优化依赖预构建
  optimizeDeps: {
    include: ['react', 'react-dom', 'react-router-dom', '@tanstack/react-query', 'zustand'],
  },

  // PWA 配置 - 复制 public 目录资源
  publicDir: 'public',
});