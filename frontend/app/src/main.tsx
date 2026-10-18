import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { App } from "./App";
import { bootstrapTheme } from "./theme";
import "./styles/app.css";

/**
 * 入口。
 *
 * Query 的重试策略是特意设成 0 的：默认会重试 3 次，而"同一个写请求
 * 重发三次"在某些场景下就是三次副作用。这个后端是幂等的读多写少，
 * 但**默认值不该由框架替我们决定** —— 要重试的地方显式写。
 */
const queryClient = new QueryClient({
  defaultOptions: {
    queries: { retry: 0, refetchOnWindowFocus: false },
    mutations: { retry: 0 },
  },
});

/**
 * **在 render 之前装主题**（而不是在 `useEffect` 里）。
 *
 * 放在 effect 里会有一个真实的后果：首帧渲染时 `<style>` 还不存在，
 * 所有 `var(--…)` 都解析不出值，于是页面以"没有背景、没有颜色"的样子
 * 闪一下 —— 而这段闪烁恰好发生在用户第一眼看到它的时候。
 * 在 render 之前调用，注入样式与首次渲染在同一帧里完成。
 */
const initialThemeId = bootstrapTheme();

const container = document.getElementById("root");
if (container === null) {
  throw new Error("页面里没有 #root —— index.html 与入口脚本对不上");
}

createRoot(container).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <App initialThemeId={initialThemeId} />
    </QueryClientProvider>
  </StrictMode>,
);
