# Remediation — Production Stabilization 10.15.15

## Закрываемые findings

- HS-AUDIT-008: dangerous dual frontend workflow ownership — materially remediated by removal of stateful imperative compatibility layer; only presentation-only bridge remains.
- HS-AUDIT-019: New Project keeps old selected project — remediated with explicit draft context reset.
- HS-AUDIT-020: duplicate jobs polling / hidden global jobs state — production uses one global jobs poll and React-owned hydration/rendering.
- Additional project-scoped async stale-response risks — guarded with project generation and AbortController where appropriate.

## Regression invariants

1. Analysis `done` + stale client snapshot must hydrate dashboard and unlock Review/Montage.
2. Navigation remains manual after hydration.
3. New Project must remove old project-scoped candidates, segments, preview and selected-project marker.
4. Add Candidate must use `/segments/add` with expected revision.
5. Late project A responses must not mutate project B.
6. Production must not ship `ux-workflow-101513.js`.
7. Presentation helper may not contain API requests, polling, reload or click interception.
8. AI Reliability 10.15.14 behavior must remain unchanged.

## Remaining environment limitation

A clean Vite rebuild cannot be proven in the current isolated container. This is explicitly not reported as passed.
