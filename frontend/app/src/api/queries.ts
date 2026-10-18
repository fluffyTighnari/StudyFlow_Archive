/**
 * 服务端状态的查询钩子（TanStack Query）。
 *
 * **一个端点取全**：`GET /api/sessions/{id}/state` 就是界面需要的整份快照
 * （会话、定义、计划、暂存、对话、版本号）。这不是偷懒 ——
 *
 * 1. 分开取会拿到互相不一致的几份快照（计划里已经有第 4 步、对话里还没有
 *    对应的那条消息），而界面上无法表达"这是两个不同时刻";
 * 2. 那些路径上本来也没有 GET：`/input` 只有 PATCH、`/chat` 只有 POST。
 *    照"REST 应该长这样"去猜，得到的是 405 —— 而 405 在界面上会显示成
 *    "加载失败"，看起来像后端坏了。
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "./client";
import type {
  DiscussionContext,
  FolderRead,
  InputUpdate,
  SessionRead,
  SessionStateRead,
} from "./types";

export const queryKeys = {
  folders: ["folders"] as const,
  sessions: (folderId: string) => ["sessions", folderId] as const,
  /** 整份快照。所有派生数据都从它来。 */
  overview: (sessionId: string) => ["overview", sessionId] as const,
  context: (sessionId: string, stepId: string) => ["context", sessionId, stepId] as const,
};

export function useFolders() {
  return useQuery({
    queryKey: queryKeys.folders,
    queryFn: () => api.get<FolderRead[]>("/api/folders"),
  });
}

export function useSessions(folderId: string | null) {
  return useQuery({
    queryKey: queryKeys.sessions(folderId ?? ""),
    queryFn: () => api.get<SessionRead[]>(`/api/sessions?folder_id=${folderId}`),
    enabled: folderId !== null,
  });
}

export function useOverview(sessionId: string | null) {
  return useQuery({
    queryKey: queryKeys.overview(sessionId ?? ""),
    queryFn: () => api.get<SessionStateRead>(`/api/sessions/${sessionId}/state`),
    enabled: sessionId !== null,
  });
}

export function useContextPack(sessionId: string | null, stepId: string | null) {
  return useQuery({
    queryKey: queryKeys.context(sessionId ?? "", stepId ?? ""),
    queryFn: () =>
      api.get<DiscussionContext>(`/api/sessions/${sessionId}/plan/steps/${stepId}/context`),
    enabled: sessionId !== null && stepId !== null,
  });
}

// ---------------------------------------------------------------------------
// 写入
// ---------------------------------------------------------------------------

export function useUpdateInput(sessionId: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (body: InputUpdate) =>
      api.patch<SessionStateRead["input"]>(`/api/sessions/${sessionId}/input`, body),
    onSuccess: () => {
      // 改完定义，整份快照都该重读：`versions.input` 变了，
      // 而版本号是要显示给人看的。
      void client.invalidateQueries({ queryKey: queryKeys.overview(sessionId) });
    },
  });
}

export function useTransition() {
  const client = useQueryClient();
  return useMutation({
    /** `expected_version` 必填 —— 状态转移是带乐观锁的。 */
    mutationFn: (body: { sessionId: string; target: string; expectedVersion: number }) =>
      api.post<SessionRead>(`/api/sessions/${body.sessionId}/transitions`, {
        target: body.target,
        expected_version: body.expectedVersion,
      }),
    onSuccess: (data) => {
      void client.invalidateQueries({ queryKey: queryKeys.overview(data.session_id) });
      void client.invalidateQueries({ queryKey: queryKeys.sessions(data.folder_id) });
    },
  });
}

export function useCreateSession() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({ folderId, title }: { folderId: string; title: string }) =>
      api.post<SessionRead>("/api/sessions", { folder_id: folderId, title }),
    onSuccess: (data) => {
      void client.invalidateQueries({ queryKey: queryKeys.sessions(data.folder_id) });
    },
  });
}

export function useCreateFolder() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (name: string) => api.post<FolderRead>("/api/folders", { name }),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: queryKeys.folders });
    },
  });
}
