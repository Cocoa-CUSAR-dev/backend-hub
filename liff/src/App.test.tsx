// docs-and-plan#177 (US4-2): the to-do list must always show exactly what is
// still outstanding for the farmer. These drive the whole screen -- App,
// api.ts and liffClient.ts together -- against a fake chatbot backend, with
// only the LINE SDK itself stubbed out. The backend half (COMPLETED filtered
// out, multi-submit tasks kept) is tests/line/test_liff_tasks.py.

import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { PendingTask } from "./types";

const liff = vi.hoisted(() => ({
  init: vi.fn(() => Promise.resolve()),
  isLoggedIn: vi.fn(() => true),
  login: vi.fn(),
  getIDToken: vi.fn(() => "liff-id-token"),
  isInClient: vi.fn(() => true),
  closeWindow: vi.fn(),
}));
vi.mock("@line/liff", () => ({ default: liff }));

const API = "https://chatbot.test";

function task(overrides: Partial<PendingTask>): PendingTask {
  return {
    task_id: "00000000-0000-0000-0000-000000000001",
    task_form_id: "10000000-0000-0000-0000-000000000001",
    title: "บันทึกการเก็บเกี่ยว",
    description: null,
    open_at: null,
    close_at: null,
    handler: "harvest",
    is_multiple_submit: false,
    status: "NOT_STARTED",
    ...overrides,
  };
}

// What the chatbot backend currently says is outstanding. Tests change it
// between loads to stand in for the farmer finishing (or being given) work.
let outstanding: PendingTask[] = [];
let failNextList = false;
let failNextStart = false;
const requests: { method: string; url: string; headers: Headers; body?: unknown }[] = [];

function fakeBackend(input: RequestInfo | URL, init: RequestInit = {}): Promise<Response> {
  const url = String(input);
  const method = init.method ?? "GET";
  requests.push({
    method,
    url,
    headers: new Headers(init.headers),
    body: init.body ? JSON.parse(String(init.body)) : undefined,
  });
  if (method === "GET" && url === `${API}/line/liff/tasks`) {
    if (failNextList) {
      failNextList = false;
      return Promise.resolve(new Response("upstream down", { status: 502 }));
    }
    return Promise.resolve(Response.json(outstanding));
  }
  const start = url.match(/\/line\/liff\/tasks\/([^/]+)\/start$/);
  if (method === "POST" && start) {
    if (failNextStart) {
      failNextStart = false;
      return Promise.resolve(new Response("nope", { status: 500 }));
    }
    return Promise.resolve(Response.json({ pushed: true }));
  }
  return Promise.resolve(new Response("not found", { status: 404 }));
}

// liffClient.ts caches liff.init() at module level, so each test gets a
// fresh copy of the app modules.
async function renderApp() {
  vi.resetModules();
  const { default: App } = await import("./App");
  render(<App />);
}

const cards = () => document.querySelectorAll<HTMLElement>(".card");
const titles = () => [...cards()].map((card) => card.querySelector(".card-title")?.textContent);

beforeEach(() => {
  outstanding = [];
  failNextList = false;
  failNextStart = false;
  requests.length = 0;
  vi.stubGlobal("fetch", vi.fn(fakeBackend));
  liff.isLoggedIn.mockReturnValue(true);
  liff.isInClient.mockReturnValue(true);
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  vi.unstubAllGlobals();
});

describe("to-do list", () => {
  it("lists exactly the tasks still outstanding, with their status and due date", async () => {
    outstanding = [
      task({ task_id: "t-1", title: "บันทึกการเก็บเกี่ยว", status: "NOT_STARTED", close_at: "2026-10-25T10:00:00Z" }),
      // A multi-submit form with one row in already: still on the list.
      task({ task_id: "t-2", title: "คัดเกรดผลโกโก้", status: "IN_PROGRESS", is_multiple_submit: true }),
      task({ task_id: "t-3", title: "ใส่ปุ๋ย", status: "OVERDUE", close_at: "2026-10-01T10:00:00Z" }),
    ];
    await renderApp();

    await waitFor(() => expect(cards()).toHaveLength(3));
    expect(titles()).toEqual(["บันทึกการเก็บเกี่ยว", "คัดเกรดผลโกโก้", "ใส่ปุ๋ย"]);
    expect(screen.getByText("3 รายการ")).toBeTruthy();

    const [first, second, third] = cards();
    expect(within(first).getByText("ยังไม่เริ่ม")).toBeTruthy();
    expect(within(first).getByText(/กำหนดส่ง/)).toBeTruthy();
    expect(within(second).getByText("กำลังทำ")).toBeTruthy();
    // No close_at: no due line rather than a made-up date.
    expect(within(second).queryByText(/กำหนดส่ง/)).toBeNull();
    expect(within(third).getByText("เลยกำหนดแล้ว")).toBeTruthy();
    expect(third.querySelector(".due-row--overdue")).not.toBeNull();
    expect(first.querySelector(".due-row--overdue")).toBeNull();
  });

  it("asks the backend as the logged-in farmer, with the LIFF ID token", async () => {
    outstanding = [task({})];
    await renderApp();

    await waitFor(() => expect(cards()).toHaveLength(1));
    expect(liff.init).toHaveBeenCalledWith({ liffId: "1234567890-abcdefgh" });
    const list = requests.find((r) => r.method === "GET");
    expect(list?.url).toBe(`${API}/line/liff/tasks`);
    expect(list?.headers.get("Authorization")).toBe("Bearer liff-id-token");
  });

  it("says there is nothing to do when nothing is outstanding", async () => {
    await renderApp();

    await waitFor(() => expect(screen.getByText(/ไม่มีงานที่ต้องทำในตอนนี้/)).toBeTruthy());
    expect(screen.getByText("0 รายการ")).toBeTruthy();
    expect(cards()).toHaveLength(0);
  });

  it("shows the current list again after a failed load, not a stale one", async () => {
    failNextList = true;
    outstanding = [task({ task_id: "t-1", title: "บันทึกการเก็บเกี่ยว" })];
    await renderApp();

    await waitFor(() => expect(screen.getByText(/GET \/line\/liff\/tasks failed: 502/)).toBeTruthy());
    expect(cards()).toHaveLength(0);

    // Meanwhile the farmer was given another task.
    outstanding = [...outstanding, task({ task_id: "t-2", title: "ใส่ปุ๋ย" })];
    await userEvent.click(screen.getByRole("button", { name: "ลองใหม่" }));

    await waitFor(() => expect(titles()).toEqual(["บันทึกการเก็บเกี่ยว", "ใส่ปุ๋ย"]));
  });

  it("drops a task the farmer has since finished the next time the list opens", async () => {
    outstanding = [
      task({ task_id: "t-1", title: "บันทึกการเก็บเกี่ยว" }),
      task({ task_id: "t-2", title: "ใส่ปุ๋ย" }),
    ];
    await renderApp();
    await waitFor(() => expect(cards()).toHaveLength(2));
    cleanup();

    // Submitted through the chat; the backend no longer lists it.
    outstanding = outstanding.filter((t) => t.task_id !== "t-1");
    await renderApp();

    await waitFor(() => expect(titles()).toEqual(["ใส่ปุ๋ย"]));
    expect(screen.getByText("1 รายการ")).toBeTruthy();
  });
});

describe("opening a task", () => {
  it("starts that task, then closes LIFF so the farmer sees the pushed question", async () => {
    outstanding = [
      task({ task_id: "t-1", title: "บันทึกการเก็บเกี่ยว" }),
      task({ task_id: "t-2", task_form_id: "f-2", handler: "fertilizer", title: "ใส่ปุ๋ย" }),
    ];
    await renderApp();
    await waitFor(() => expect(cards()).toHaveLength(2));

    await userEvent.click(within(cards()[1]).getByRole("button", { name: /เปิด/ }));

    await waitFor(() => expect(liff.closeWindow).toHaveBeenCalledTimes(1));
    const start = requests.find((r) => r.method === "POST");
    expect(start?.url).toBe(`${API}/line/liff/tasks/t-2/start`);
    expect(start?.body).toEqual({ task_form_id: "f-2", handler: "fertilizer" });
    expect(start?.headers.get("Authorization")).toBe("Bearer liff-id-token");
  });

  it("shows the error and lets the farmer reload when starting fails", async () => {
    outstanding = [task({ task_id: "t-1" })];
    failNextStart = true;
    await renderApp();
    await waitFor(() => expect(cards()).toHaveLength(1));

    await userEvent.click(within(cards()[0]).getByRole("button", { name: /เปิด/ }));

    await waitFor(() => expect(screen.getByText(/start failed: 500/)).toBeTruthy());
    expect(liff.closeWindow).not.toHaveBeenCalled();
    await userEvent.click(screen.getByRole("button", { name: "ลองใหม่" }));
    await waitFor(() => expect(cards()).toHaveLength(1));
  });

  it("does not try to close a window outside the LINE app", async () => {
    liff.isInClient.mockReturnValue(false);
    outstanding = [task({ task_id: "t-1" })];
    await renderApp();
    await waitFor(() => expect(cards()).toHaveLength(1));

    await userEvent.click(within(cards()[0]).getByRole("button", { name: /เปิด/ }));

    await waitFor(() => expect(requests.some((r) => r.method === "POST")).toBe(true));
    expect(liff.closeWindow).not.toHaveBeenCalled();
  });
});

describe("signing in", () => {
  it("sends a farmer who is not logged in to LINE login", async () => {
    liff.isLoggedIn.mockReturnValue(false);
    await renderApp();

    await waitFor(() => expect(liff.login).toHaveBeenCalledTimes(1));
  });
});
