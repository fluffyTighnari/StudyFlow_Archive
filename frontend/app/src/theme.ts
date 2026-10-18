/**
 * 主题：把套件的两个主题包接进来，并记住用户的选择。
 *
 * 这个文件**只有三件事**：拿包、挑包、装上。判断逻辑全部在套件里
 * （`chooseTheme` / `applyTheme`），这里不复述 —— 一旦复述，就会有两套
 * 挑选规则，而两套规则迟早会对同一个 `localStorage` 值给出不同答案。
 */

import { applyTheme, chooseTheme, storeThemeId, THEME_STORAGE_KEY } from "@mouxue/theme-kit";
import { BASE_THEME_ID, THEME_PACKS } from "@mouxue/theme-kit/packs";

export interface ThemeOption {
  id: string;
  name: string;
  appearance: string | undefined;
}

export function themeOptions(): ThemeOption[] {
  return THEME_PACKS.map((pack) => ({
    id: pack.id,
    name: pack.name,
    appearance: pack.appearance,
  }));
}

/** 系统是否偏好暗色（没有 `matchMedia` 的环境按浅色处理）。 */
function prefersDark(): boolean {
  if (typeof window === "undefined" || typeof window.matchMedia !== "function") return false;
  return window.matchMedia("(prefers-color-scheme: dark)").matches;
}

/** 启动时装一次：用户在浏览器里选过的 > 系统偏好对应的主题 > 基础主题。 */
export function bootstrapTheme(): string {
  const pack = chooseTheme({
    packs: THEME_PACKS,
    baseId: BASE_THEME_ID,
    storedId: readStored(),
    prefersDark: prefersDark(),
  });
  return applyTheme(pack, { base: baseTheme() });
}

/** 用户点了某个主题：装上并记住。 */
export function selectTheme(themeId: string): string {
  const pack = THEME_PACKS.find((candidate) => candidate.id === themeId);
  if (pack === undefined) {
    // 不静默回退：选了一个不存在的主题，是一个应该被看见的问题。
    throw new Error(`没有这个主题：${themeId}`);
  }
  const applied = applyTheme(pack, { base: baseTheme() });
  storeThemeId(applied);
  return applied;
}

function baseTheme() {
  const base = THEME_PACKS.find((pack) => pack.id === BASE_THEME_ID);
  if (base === undefined) throw new Error(`基础主题 ${BASE_THEME_ID} 不在主题列表里`);
  return base;
}

function readStored(): string | null {
  try {
    return window.localStorage.getItem(THEME_STORAGE_KEY);
  } catch {
    // 隐私模式/被禁用时读不到 —— 这不影响用哪套配色，只是不记得上次选的。
    return null;
  }
}
