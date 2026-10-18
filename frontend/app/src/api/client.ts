/**
 * 一层很薄的 fetch 封装。
 *
 * 它只做三件事，每件都对应一个"不这么写就会踩"的坑：
 *
 * 1. **把 HTTP 错误变成一条带 `code` 的异常**。后端的错误体是
 *    `{"error": {"code": ..., "message": ...}}`（见 `app/api/errors.py`），
 *    直接 `res.json()` 拿不到"为什么失败"，界面只能显示"出错了"。
 * 2. **非 2xx 不当成空数据**。把失败读成空列表，是这一层最容易犯的错：
 *    界面会说"还没有会话"，而真相是请求失败了。
 * 3. **不吞异常**。调用方（TanStack Query）拿到的必须是异常，才能区分
 *    "加载失败"与"确实是空"。
 */

export interface ApiErrorBody {
  error?: { code?: string; message?: string; detail?: unknown };
}

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;

  constructor(status: number, code: string, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
  }
}

/** 从错误响应里尽可能取出一句人话（取不到就如实说取不到）。 */
async function describe(response: Response): Promise<{ code: string; message: string }> {
  const text = await response.text();
  if (text.trim() === "") {
    return {
      code: `HTTP_${response.status}`,
      message: `HTTP ${response.status}（响应体是空的）`,
    };
  }
  try {
    const body = JSON.parse(text) as ApiErrorBody;
    const code = body.error?.code ?? `HTTP_${response.status}`;
    const message = body.error?.message ?? text.slice(0, 300);
    return { code, message };
  } catch {
    return { code: `HTTP_${response.status}`, message: text.slice(0, 300) };
  }
}

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  const init: RequestInit = { method, headers: { accept: "application/json" } };
  if (body !== undefined) {
    init.headers = { ...init.headers, "content-type": "application/json" };
    init.body = JSON.stringify(body);
  }
  const response = await fetch(path, init);
  if (!response.ok) {
    const { code, message } = await describe(response);
    throw new ApiError(response.status, code, message);
  }
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

export const api = {
  get: <T>(path: string) => request<T>("GET", path),
  post: <T>(path: string, body?: unknown) => request<T>("POST", path, body),
  patch: <T>(path: string, body?: unknown) => request<T>("PATCH", path, body),
};

/** 把任意异常变成一句可以直接显示的话 —— **不猜原因**，只转述。 */
export function describeError(error: unknown): string {
  if (error instanceof ApiError) return `${error.code}：${error.message}`;
  if (error instanceof Error) return error.message;
  return String(error);
}
