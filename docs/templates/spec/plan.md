# <Feature> implementation plan

## Goal and design

Link the owning design and common requirement. State the change's concrete scope.

## Baseline and constraints

Record the source baseline, environment, known failure identities, and protected data.

## Task: <independently testable outcome>

### Files and interfaces

List exact created/modified/tested paths, consumed interfaces, and produced contracts.

### Steps

- [ ] Write the failing behavioral test and name the missing behavior.
- [ ] Run the test; record expected and actual failure, not collection errors.
- [ ] Implement the smallest change satisfying the contract.
- [ ] Run focused tests, the affected suite, compile, and diff checks.
- [ ] Record results, deviations, and any pending verification.

Repeat tasks only at independently reviewable boundaries. Commit only when explicitly
authorized, with implementation and tests together.

## Acceptance and rollback

List exact commands and observable outcomes. Preserve original exit codes and distinguish
pre-existing failures, missing dependencies, and unexecuted checks. Define rollback
without destroying user data or others' changes.
