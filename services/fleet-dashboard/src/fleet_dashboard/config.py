"""Small fixed constants shared across the dashboard.

`POLL_INTERVAL_SECONDS` is what the brief calls "a real interval, a few
seconds" (Sec 16.5: "cards update within a few seconds of the Registry
row changing, not on manual refresh"). It drives the static client's
`setInterval` poll loop and is exposed back to the client (and in tests)
so the UI's own staleness disclosure can name the actual interval it
uses rather than a vague "periodically."
"""

from __future__ import annotations

POLL_INTERVAL_SECONDS = 3

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8808

# How many Run rows to pull per RegistryService.list_runs() page while
# fully draining a tenant's in-flight runs. Kept small on purpose -- this
# is a fleet dashboard for a handful of concurrent runs, not a bulk
# export tool.
LIST_RUNS_PAGE_SIZE = 200
