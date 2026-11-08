import { getLineIdToken } from "./liffClient";
import type { PendingTask } from "./types";

const API_BASE = import.meta.env.VITE_API_BASE_URL;

function apiBase(): string {
  if (!API_BASE) {
    throw new Error("VITE_API_BASE_URL is not set -- see .env.sample");
  }
  return API_BASE;
}

// Our own access token, from swapping the LINE ID token once
// (POST /api/v1/auth/line/login) -- after that every call is a plain Bearer
// request the backend checks without asking LINE. Memory only: the LIFF page
// is short-lived, and a token kept in localStorage could be read by any
// script on the page.
let accessToken: string | null = null;

async function signIn(): Promise<string> {
  const response = await fetch(`${apiBase()}/api/v1/auth/line/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ id_token: getLineIdToken() }),
  });
  if (response.status === 403) {
    throw new Error("บัญชี LINE นี้ยังไม่ได้เชื่อมกับบัญชีในระบบ");
  }
  if (!response.ok) {
    throw new Error(`POST /api/v1/auth/line/login failed: ${response.status}`);
  }
  const pair = (await response.json()) as { access_token: string };
  accessToken = pair.access_token;
  return accessToken;
}

// Bearer request; an expired or rejected token is swapped once more and the
// call retried, so a page left open past the token's life keeps working.
async function authedFetch(path: string, init: RequestInit = {}): Promise<Response> {
  const send = (token: string) =>
    fetch(`${apiBase()}${path}`, {
      ...init,
      headers: { ...init.headers, Authorization: `Bearer ${token}` },
    });
  const response = await send(accessToken ?? (await signIn()));
  if (response.status !== 401) {
    return response;
  }
  accessToken = null;
  return send(await signIn());
}

export async function fetchPendingTasks(): Promise<PendingTask[]> {
  const response = await authedFetch("/line/liff/tasks");
  if (!response.ok) {
    throw new Error(`GET /line/liff/tasks failed: ${response.status}`);
  }
  return (await response.json()) as PendingTask[];
}

// Fire-and-forget from the caller's point of view: the actual conversation
// content arrives as a LINE push message, not in this response (see
// src/line/liff_tasks.py's start_task) -- the caller's job after this
// resolves is just closeLiffWindow(), not rendering anything from it.
export async function startTask(task: PendingTask): Promise<void> {
  const response = await authedFetch(`/line/liff/tasks/${task.task_id}/start`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      task_form_id: task.task_form_id,
      handler: task.handler,
    }),
  });
  if (!response.ok) {
    throw new Error(`POST /line/liff/tasks/${task.task_id}/start failed: ${response.status}`);
  }
}
