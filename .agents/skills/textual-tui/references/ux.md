# Terminal UX checklist for Icarus

A terminal UI is operated under constraints a browser does not have: one focus, a fixed cell grid, no hover on every device, inconsistent mouse reporting, and users who switch between keyboard and pointer. Design for the keyboard and the narrowest terminal first.

## Interaction model

- **Keyboard-first.** Every action available by mouse must have a key or command equivalent.
- **One focus owner.** The focused widget should be visually obvious, and `Tab` / `Shift+Tab` should traverse interactive regions in reading order.
- **Do not steal text input.** Global bindings must not fire while the composer is editing printable input. Check `event.key`, `event.is_printable`, and the focused widget before handling.
- **Predictable keys.** `Enter` submits, `Shift+Enter` or `Ctrl+J` inserts a newline, `Esc` cancels the current modal, `Ctrl+C` follows one documented rule. Do not overload the same key with unrelated meanings in the same context.
- **Mouse is an enhancement.** Wheel, click, and hover should be additive. A user who never touches the mouse must still reach every action.
- **Modal only for real decisions.** Use `ModalScreen` for session picking, destructive confirmation, or a blocking choice. Do not interrupt streaming output with token-level dialogs.

## Async feedback

Every asynchronous action needs an observable state within one frame:

- queued
- submitting / accepting
- streaming
- cancelling
- reconnecting
- accepted / finished
- failed, with a next action

Show state in the status bar or on the affected card. A silent wait reads as a hang. Avoid transient notifications for state that persists; use a stable status line.

When an operation can be retried, retain the user's input and the same submission identity so the retry is idempotent.

## Streaming and scrolling

- Auto-follow only when the viewport is already at the bottom.
- If the user scrolls up, detach from the live tail and do not move their reading position.
- Show a clear "new messages" affordance when detached; restore following on `Ctrl+End` or clicking it.
- Streaming text updates should not reset draft text, cursor position, or focus.
- Preserve expanded/collapsed state of thinking and tool cards across window scrolling.
- Pagination must not race with incoming deltas; finish or flush the current stream before swapping the mounted window.

## Layout and density

- Design for roughly 60–70 columns first, then widen.
- At narrow widths, drop secondary chrome (logo, rail, previews) before shrinking the composer or conversation.
- At short heights, cap the composer and queue panel; never let the status bar or composer be pushed off-screen.
- Use plain text and truncation instead of wrapping control labels into multiple rows.
- Keep the composer at the bottom and the status bar visible at all times.
- Reserve stable IDs/classes for regions so snapshots and Pilot tests can find them.

## Content states

Every panel that can be empty needs a designed empty state:

- welcome / no session
- empty session
- no queue
- no search results
- disconnected / initializing

Loading states should name what is loading ("Restoring Session", "Initializing") rather than showing a spinner with no context.

Errors should appear near the failed action, identify what was preserved, and say what the user can do next.

## Readability

- Use color for emphasis, not as the only carrier of meaning. Pair red/green with a word or symbol.
- Keep secondary metadata muted; do not let timestamps, usage, or tool labels compete with the content.
- Avoid more than two levels of indentation in a card.
- Long tool output belongs behind an explicit expand action, not in the default transcript.

## Clipboard and images

- Text paste should preserve multiline content exactly.
- Image paste needs a visible marker in the draft and a way to remove it by editing the marker.
- If the platform cannot read clipboard images, say so once; do not fail silently or block text paste.
- Temporary files created for pasted images must be removed after acceptance or cancellation.

## Exit and terminal restoration

- Leaving the app must restore the terminal screen, cursor, and input mode.
- Cleanup must close subscriptions, stop workers, and remove temporary files before exit.
- A second interrupt should not bypass cleanup.
- Document the exit keys in the README and keep them consistent with the status bar hints.

## Review checklist for a UX change

- Can I complete the flow with the keyboard only?
- Is the focused element visible at every step?
- What happens on a 70×24 terminal? On 60×20? On 100×12?
- What does the user see during a 5-second wait?
- What happens if the connection drops mid-operation?
- Does the change move the user's reading position or clear their draft?
- Is the error actionable, or only descriptive?
- Does the change preserve the existing 13 visual snapshots, or is the visual delta intentional and reviewed?
