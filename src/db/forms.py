"""Form definitions and the handler registry.

Owns:
    form.task_form
    form.section
    form.question
    form.question_visibility
    form.field_validation_rule
    form.handler_registry
    form.field_dependency

All SQL for these tables lives in this file and nowhere else (CONVENTIONS.md §2).
Other code calls the functions here. Empty until the migration moves queries in.
"""
