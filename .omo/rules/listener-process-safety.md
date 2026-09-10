# Listener and process safety review

Apply this rule to listener discovery, port-owner decisions, PID files, process signals,
test process guards, and child teardown.

## Authority and scope

External owner documentation is canonical for tool behavior. On every change, record the
object, consumer, operating systems, installed version, review date, exact official URL,
and the claim it supports. Compare upstream and the installed runtime separately. Search
snippets, local notes, old receipts, and source comments are leads only. Preserve stricter
local safety. If current official pages conflict, record `DOC_CONFLICT`, use only their
shared narrow contract, and do not generalize a local experiment into vendor behavior.

Baseline reviewed 2026-09-09:

- lsof field and exit contracts: https://lsof.readthedocs.io/en/latest/manpage/ and
  https://lsof.readthedocs.io/en/latest/options/
- Python process and signal contracts: https://docs.python.org/3/library/subprocess.html and
  https://docs.python.org/3/library/os.html#os.kill
- psutil process identity and signalling: https://psutil.readthedocs.io/stable/index.html
- pytest teardown: https://docs.pytest.org/en/stable/how-to/fixtures.html#safe-teardowns
- upstream lsof releases: https://github.com/lsof-org/lsof/releases

Do not adopt a new flag or output mode until the minimum supported installed version is
verified. In particular, do not require lsof JSON or `-Q` while supported hosts still carry
lsof 4.91. Keep the portable listener query constrained to TCP `LISTEN`, numeric host/port,
and machine-readable PID output.

## Ten mandatory passes

Run all ten passes and record `PASS | FAIL | UNKNOWN`, evidence, and action for each.

1. Top-down: bound object, scope, consumer, OS matrix, date, and stop condition.
2. Top-down: verify current owner docs, release, installed versions, and feature gap.
3. Top-down: map command, timeout, return code, stdout, stderr, parser, and normalized model.
4. Top-down: preserve every process/file observation needed by the consumer; deduplicate
   only in a PID-set projection, never in the evidence record.
5. Top-down: trace normalized state through every caller to the final action or message.
6. Bottom-up: start from concrete stdout/stderr fixtures for empty, repeated, malformed,
   multiple-PID, timeout, missing-tool, permission, and nonzero cases.
7. Bottom-up: prove `ABSENT`, `PRESENT`, `READBACK_UNAVAILABLE`, and `AMBIGUOUS_OWNER` remain
   distinct until the bounded consumer decision.
8. Bottom-up: trace every signal target back through exact executable, full argv role,
   profile/session, PID, process creation time, listener endpoint, and owner entrypoint.
9. Bottom-up: verify `terminate -> bounded wait -> kill -> final wait`; test-owned children
   and private process groups must be recorded before any destructive action.
10. Bottom-up: rerun callers, platform-marked tests, canonical runner, static checks,
    `py_compile` with explicit temporary output, diff review, and artifact cleanup.

## Fail-closed contracts

- A PID, port, display name, executable family such as `node`, or argv substring is never
  process ownership by itself.
- Before a signal, require one identity-bound `psutil.Process` object plus PID creation time
  and every identity field applicable to that consumer. Port-owner cleanup requires the exact
  executable, full role argv, expected profile/session, and expected listener endpoint. PID-file
  cleanup requires the exact executable and role argv plus a write/read-back runtime record.
  Signal through that same object so PID reuse is rechecked.
- Multiple listeners are a set of observations, not one row; never select the first PID. A
  single-instance consumer such as Photon treats more than one candidate as `AMBIGUOUS_OWNER`.
  A multi-member cleanup such as WhatsApp may proceed only if every candidate independently
  matches the same bounded bridge/session/port contract before the first signal; otherwise it
  is `AMBIGUOUS_OWNER` or `OWNER_UNCONFIRMED` and sends no signal.
- A normal empty query is `ABSENT`. Timeout, command absence, permission error, malformed
  output, or error-bearing nonzero exit is `HOST_LISTENER_READBACK_UNAVAILABLE`, never absent.
  A destructive consumer may collapse both to “send no signal” only after retaining the
  reason code for logs and callers.
- Destructive PID or PGID `0`, negative broadcast targets, the test runner's own process
  group, and unrecorded groups are blocked. Signal `0` remains a liveness probe.
- Verify all candidate owners before signalling any of them. Partial cleanup is not allowed
  when another listener remains unverified.
- Do not restart, kill, attach to, or mutate a live service while proving source/test logic.
  Use only bounded test-owned children with guaranteed final waits.

## Test and completion receipt

Establish a safe failing test before changing behavior. Use `scripts/run_tests.sh`; do not
replace platform coverage by faking the host OS. Report skipped platform lanes as unverified
locally. The change is complete only when all ten passes are recorded, the intended red test
is green, relevant callers pass, static and compile checks pass, meaningful diffs are reviewed,
temporary children and files are gone, live systems were not mutated, and every remaining
uncertainty has an owner plus the smallest verification step.
