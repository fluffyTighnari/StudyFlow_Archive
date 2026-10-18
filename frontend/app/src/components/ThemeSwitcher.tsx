import { useMemo, useState } from "react";

import { themeOptions, selectTheme } from "../theme";

/**
 * 主题切换器。
 *
 * 三点讲究：
 *
 * 1. **选项从主题包来**，不写死。加一个主题包，这里自动多一个按钮。
 * 2. **不显示"生效中"以外的状态**：换主题是同步的（写一个 `<style>` 的文本），
 *    没有加载中、没有失败 —— 显示一个假的 loading 只会让人以为它在做什么。
 * 3. **每个选项带一小块真颜色**（用色板表达自己），让人不必先点一遍才知道
 *    那是什么主题。色块的颜色来自令牌，不是写死的。
 */
export function ThemeSwitcher({ current }: { current: string }) {
  const options = useMemo(() => themeOptions(), []);
  const [error, setError] = useState<string | null>(null);

  return (
    <div className="theme-switcher">
      <span className="theme-switcher__label">主题</span>
      <div className="theme-switcher__options" role="group" aria-label="主题">
        {options.map((option) => (
          <button
            key={option.id}
            type="button"
            className="theme-swatch tk-focusable"
            aria-pressed={option.id === current}
            title={`${option.name}（${option.appearance ?? "未标注"}）`}
            onClick={() => {
              try {
                selectTheme(option.id);
                setError(null);
              } catch (cause) {
                setError(cause instanceof Error ? cause.message : String(cause));
              }
            }}
          >
            {option.name}
          </button>
        ))}
      </div>
      {error !== null && <p className="error-note">{error}</p>}
    </div>
  );
}
