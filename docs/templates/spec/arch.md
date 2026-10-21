# <Feature> architecture

## Position and evidence

Link the common requirement, if any, and the inspected source/tests.
Describe current behavior separately from proposed changes.

## Components and boundaries

State ownership, dependency direction, public interfaces, and concrete consumers.

## Data flow and errors

Describe input, state transitions, outputs, cleanup, persistence, and error behavior.

## Validation and compatibility

Name observable contracts and their tests, data migrations, and rollback constraints.

## Non-goals

Do not add abstractions without a caller. This document describes code, not a rule
preventing code evolution. Create a counterpart plan only when there is real work to plan.
