"""Logins, roles, permissions and LINE links.

Owns:
    auth.user_account
    auth.user_role
    auth.role
    auth.role_permission
    auth.permission
    auth.line_identity
    auth.line_link_code
    auth.refresh_token
    auth.sso_used_token

All SQL for these tables lives in this file and nowhere else (CONVENTIONS.md §2).
Other code calls the functions here. Empty until the migration moves queries in.
"""
