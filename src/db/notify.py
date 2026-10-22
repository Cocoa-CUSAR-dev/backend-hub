"""Reminder schedules, recipients and send log.

Owns:
    notify.reminder_schedule
    notify.reminder_recipient
    notify.reminder_log

All SQL for these tables lives in this file and nowhere else (CONVENTIONS.md §2).
Other code calls the functions here. Empty until the migration moves queries in.
"""
