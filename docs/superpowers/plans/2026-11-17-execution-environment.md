# Execution environment consistency

Approved scope: one choice for OKX execution mode and environment; remove the
independent sandbox switch, reject contradictory writes, and block strategy
startup for contradictory legacy settings. Do not restart services or strategies.

The existing local backtest and paper modes retain their behavior. Add the missing
OKX demo option and distinguish it from local paper execution. Derive sandbox=true
for demo and sandbox=false for live. Read legacy files without silently repairing
them so settings remain accessible. Require an explicit mode selection to repair
a legacy conflict in the UI. Keep the legacy demo field in serialized settings for
compatibility, validate it when explicitly provided, and derive it when omitted.

Alternatives: validating two independent UI controls would retain redundant
choices; silently normalizing legacy live settings could enable real trading.
The selected approach removes the duplicate choice and requires explicit repair.

Implementation (inline; no commits or service operations):

- [x] Add regression tests for conflicting API writes, mode-only writes, legacy
  reads/repair, startup rejection, and UI environment derivation/repair.
- [x] Centralize environment consistency validation, apply at persistence and
  strategy startup/adapter creation, and derive omitted file/API sandbox values.
- [x] Remove the settings toggle, add the demo option and environment text, and
  prevent saving or authentication of unresolved legacy conflicts.
- [x] Run isolated backend regressions, frontend settings tests/type checking,
  and review the scoped diff without modifying runtime data.

Validation: frontend 645 tests passed; type checking and production build passed.
Backend consistency/configuration tests and affected settings/startup/allocation
regressions passed. Added manual submit/cancel/amend conflict coverage. All backend
tests use isolated databases/settings and deny network access. The served Vite
module at localhost:3000 includes the updated UI. Backend PID 62408 has no reload
flag; the backend changes require the next service restart. No service was
restarted and no runtime configuration or strategy state was changed here.

Legacy tests used contradictory environments to enter the ownership-required
startup path. Validated their environments and explicitly mocked the automatic
live-ownership eligibility in the four tests that exercise the older gate;
production ownership behavior was left unchanged.
