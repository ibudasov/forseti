"""ADK tool adapters over Forseti's deterministic services.

Every tool in this package is a thin, typed wrapper around an existing
`app.services` / `app.rag` function. Tools never contain business rules,
never perform money math, and never call I/O beyond delegating to the
collaborator they were built with (explicit dependency injection, no
global singletons).
"""
