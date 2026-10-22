"""Tasks, who gets them, and the answers people submit.

Owns:
    form.task
    form.assignment
    form.task_recipient
    form.task_recurrence
    form.response
    form.response_correction_log
    form.diary_entry

All SQL for these tables lives in this file and nowhere else (CONVENTIONS.md §2).
Other code calls the functions here. Empty until the migration moves queries in.
"""
