# Agent Notes

## Cache-First Data Access

- Always check the available cache layer before calling an external API.
- For Sofascore data, prefer PocketBase cache first, then fall back to the wrapper/API only on cache miss, expired cache, or incomplete cached data.
- Frontend flows should also reuse in-memory/session cache before sending HTTP requests for data that was already loaded in the current session.
- When adding a new route or UI data fetch, include the cache lookup in the first implementation instead of adding it later.
- If a cache hit returns enough data for the current UI/API contract, do not call the external API just to refresh it synchronously; refresh in the background only when needed.
