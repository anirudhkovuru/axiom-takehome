import type {
  ChatDetail,
  ChatSummary,
  DatasetSummary,
  Experiment,
  Health,
  Message,
  Run,
} from "../types";

export const API_BASE =
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://127.0.0.1:8000";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: {
      "content-type": "application/json",
      ...init?.headers,
    },
  });
  if (!response.ok) {
    const body = await response.json().catch(() => ({ detail: response.statusText }));
    throw new Error(body.detail ?? "Request failed");
  }
  return response.json() as Promise<T>;
}

export const api = {
  health: () => request<Health>("/api/health"),
  dataset: () => request<DatasetSummary>("/api/dataset/summary"),
  chats: () => request<ChatSummary[]>("/api/chats"),
  chat: (chatId: string) => request<ChatDetail>(`/api/chats/${chatId}`),
  createChat: () =>
    request<ChatDetail>("/api/chats", {
      method: "POST",
      body: JSON.stringify({ title: "New analysis" }),
    }),
  renameChat: (chatId: string, title: string) =>
    request<ChatDetail>(`/api/chats/${chatId}`, {
      method: "PATCH",
      body: JSON.stringify({ title }),
    }),
  deleteChat: (chatId: string) =>
    request<{ id: string }>(`/api/chats/${chatId}`, {
      method: "DELETE",
    }),
  sendMessage: (chatId: string, content: string) =>
    request<{ message: Message; run: Run }>(`/api/chats/${chatId}/messages`, {
      method: "POST",
      body: JSON.stringify({ content }),
    }),
  experiments: () => request<Experiment[]>("/api/experiments"),
  saveExperiment: (chatId: string, name: string) =>
    request<Experiment>(`/api/chats/${chatId}/experiments`, {
      method: "POST",
      body: JSON.stringify({
        name,
        description: "Saved analysis with transcript, execution trace, code, figures, and result tables.",
      }),
    }),
};
