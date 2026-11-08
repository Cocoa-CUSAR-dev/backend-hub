// Mirrors src/tasks/schemas.py's TaskOut -- what GET /line/liff/tasks answers
// with, the same list the mobile app gets from GET /api/v1/tasks
// (docs-and-plan#176). Tasks without a form are left out, so the ids and the
// handler are always there.
export type TaskStatus = "NOT_STARTED" | "IN_PROGRESS" | "OVERDUE" | "COMPLETED";

export interface PendingTask {
  task_id: string;
  task_form_id: string;
  title: string;
  description: string | null;
  open_at: string | null;
  close_at: string | null;
  handler: string;
  is_multiple_submit: boolean;
  status: TaskStatus;
}
