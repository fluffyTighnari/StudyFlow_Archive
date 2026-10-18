import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { fileURLToPath } from "node:url";

/**
 * 前端开发服务器。
 *
 * 三件事值得写在这儿：
 *
 * 1. **主题套件用别名接进来**（`@mouxue/theme-kit`），而不是在 app 里复制一份。
 *    套件是唯一的值来源 —— 它一旦被复制，两份就会开始漂。
 * 2. **`/api` 走代理**到后端。这样开发期只有一个源（前端），没有跨域，
 *    也不用在后端里开 CORS（开了 CORS 就等于给所有源开门）。
 * 3. **`strictPort: true`**：端口被占时直接失败，而不是悄悄换一个。
 *    悄悄换端口会让"我打开的是哪个服务"变成一件要靠猜的事。
 */
const backend = process.env.MOUXUE_API ?? "http://127.0.0.1:8123";
const kit = fileURLToPath(new URL("../theme-kit/", import.meta.url));

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: [
      { find: /^@mouxue\/theme-kit\/base\.css$/, replacement: `${kit}src/base.css` },
      { find: /^@mouxue\/theme-kit\/packs$/, replacement: `${kit}src/packs.mjs` },
      { find: /^@mouxue\/theme-kit$/, replacement: `${kit}src/index.mjs` },
    ],
  },
  server: {
    // 显式绑 IPv4：默认的 `localhost` 在 Windows 上可能只监听 `::1`，
    // 于是 `127.0.0.1:5173` 连不上、`localhost:5173` 能连上 ——
    // 同一个服务两个地址表现不同，会让人以为是配置坏了。
    host: "127.0.0.1",
    port: 5173,
    strictPort: true,
    proxy: {
      "/api": { target: backend, changeOrigin: false },
    },
  },
  build: {
    // 构建产物给 FastAPI 直接静态托管（一个端口就能跑完整应用）。
    outDir: "dist",
    emptyOutDir: true,
  },
});
