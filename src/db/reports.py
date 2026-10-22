"""Read-only queries that join many schemas (analytics, Excel export).

Owns: no tables. Never writes. If a query only touches one schema, it belongs
in that schema's file instead.
"""
