---
name: textual-tui
description: Build, restyle, refactor, and verify the Icarus Textual terminal client in apps/tui. Use this skill whenever a task touches Textual App/Screen/Widget code, .tcss styling, terminal layout, focus/keyboard/mouse behavior, streaming transcript rendering, session picker, composer input, snapshot tests, or TUI replay — including requests phrased as "调整 TUI 布局", "改 tcss", "这个 Widget 怎么拆", "snapshot 挂了", "窄屏显示", or "TUI 交互". Prefer it over generic frontend guidance because terminal layout, focus, and rendering constraints differ from the browser.
---

# Icarus Textual TUI

Use this skill for work inside `apps/tui`. It encodes the project's Textual architecture, TCSS conventions, terminal UX rules, and the verification loop that keeps snapshot and replay tests trustworthy.

## Read the current state first

Before editing, inspect the smallest relevant set:

- `apps/tui/src/app.py` — App-owned lifecycle, Workers and serial result application; `apps/tui/src/screens/chat/screen.py` — retained chat page
- `apps/tui/src/widgets/` — visual components and local interaction
- `apps/tui/src/screens/` — screens and modal screens
- `apps/tui/src/styles/` — shared layout and theme tokens
- `apps/tui/docs/spec/` — the feature's current architecture and decisions
- `apps/tui/test/` — Pilot tests, widget tests, snapshots, fixtures

Architecture documents are derived from code. When code and docs disagree, trust current code and tests, then update the doc.

## Architecture defaults

Textual projects are organized by runtime unit, not by abstract clean-architecture layers:

```text
App          process lifecycle, screen routing, long-lived IO workers and serial results
Screen       retained page layout, focus, child-message routing and view rendering
Widget       local state, rendering, local interaction; emits Message upward
Message      explicit intent/result delivery, no reflective App proxies
Service      plain Python chat workflows and external IO; no Textual imports
Store        owns ChatState and widget-free conversation facts
ViewModel    pure read-only display derivation, no transcript copy or IO
Config       immutable startup settings
```

Dependency direction:

```text
app.py -> services/chat -> client/gateway
   |           |
   v           v
screens/ -> stores/ <- viewmodels/
   |
widgets/chat -> domain/ + event_pipeline/
configs/ and platform/ remain independent of UI
```

Rules that keep this boundary real:

- Keep `App` thin. It should answer "how does the app start, which global keys exist, which Screen is shown", not "how does a queued message get steered".
- Put page-specific orchestration in a `Screen`, and feature behavior in a composite widget or a plain service.
- Widgets own their local rendering state. They may import pure vocabulary such as `UiAction` or `RuntimePhase`; they must not import `client/gateway`, services, or `app`.
- Services provide framework-free workflows and captured I/O results. App owns long-lived Textual workers; Screens/Widgets own view-only work. Apply runtime results serially in App, never by editing Widgets from I/O workers.
- Use `Screen` when navigation changes the user's working context; use `ModalScreen` for short interruptions such as the Session picker.
- Prefer nested `Message` classes for widget-local events (`PersistentComposer.Submitted`). Keep a shared `messages.py` only for events that cross screens, and keep it small.
- One owner per piece of state. Local UI state uses `reactive` + `watch_*`; cross-page state lives in a service/model and is passed down.
- `query_one` belongs in the Screen/Widget that owns the DOM. Do not reach across the tree from a service or from deep App code by ID.

If `app.py` is doing session lifecycle, dispatch, update consumption, and input routing at the same time, that is the signal to extract a service or move page orchestration into a Screen — not a reason to change frameworks.

## TCSS

Read [references/tcss.md](references/tcss.md) before a non-trivial styling change. Core rules:

- Define theme tokens once in `styles/tokens.tcss`. App supplies the variables to every stylesheet; use tokens, not repeated hex values.
- Put layout in `styles/screens/` and widget appearance in `styles/widgets/`; tokens are centralized in `styles/tokens.tcss` and supplied by App.get_css_variables to every file. Put a widget's intrinsic defaults in its `DEFAULT_CSS` only when the widget must look right in isolation.
- Use IDs for stable test hooks and classes for variants/states. Avoid long descendant chains; style the component at its own boundary.
- Prefer `1fr` for the flexible region and fixed rows for chrome. Give shrinking containers `min-width: 0` so text can ellipsize instead of forcing overflow.
- Keep one scroll owner per region. The execution log has a fixed-height scroller per Run with default latest-follow. Latest Thinking is below its frame and older Thinking archives inside. Assistant text is always fully expanded in the outer transcript, never a capped local scroller. Consume wheel only where that region can move, then hand off at its edge.
- Style `:focus`, `:hover`, and `:focus-within` explicitly. Never rely on color alone to communicate state.
- Implement responsive behavior with classes toggled in `on_resize` (the current app uses `-narrow`, `-compact-logo`, `-short`), not with duplicated widget trees.
- Do not use inline `CSS` for feature work beyond a tiny default; it hides layout from the snapshot review and from the next maintainer.

## UX

Read [references/ux.md](references/ux.md) for the checklist. The load-bearing rules:

- Keyboard-first. Every mouse action needs a keyboard equivalent, and global keys must not steal text-editing input.
- Make async state visible: queued, submitting, streaming, cancelling, reconnecting, failed. A silent wait reads as a hang.
- Streaming output must not move the user's reading position. Auto-follow only at the bottom; preserve draft, cursor, and focus.
- Design narrow and short first. Check roughly 60–70 columns and 12 rows, then the wide layout.
- Provide empty, loading, and error states. Errors belong near the thing that failed, with a next action.
- Exit cleanly and restore the terminal screen; a TUI that leaves the shell in a broken state is a bug.
- Use the terminal's own affordances: plain text, truncated previews, explicit labels, and subdued color for secondary information.

## Verification

Read [references/verification.md](references/verification.md). Minimum loop:

1. Run the narrowest affected test file.
2. Run `make test-tui`.
3. For visual changes, replay deterministically and inspect the rendered snapshot; do not trust test pass/fail alone.
4. Run `git diff --check`.

The old 13 SVG baseline tests were removed at user request. Use Pilot/semantic tests and temporary real-App screenshots for visual inspection; do not require or recreate nonexistent baselines.

## Change checklist

Before calling TUI work done:

- The changed behavior has a focused test, or an existing test now covers it.
- Layout was checked at wide, narrow, and short sizes when relevant.
- Focus order and keyboard reachability were checked for new interactive widgets.
- No service or model became Textual-dependent; no widget started calling the Gateway directly.
- `app.py` did not grow a new unrelated responsibility.
- Snapshot updates were visually reviewed, not blindly accepted.
- The feature's `docs/spec` entry was updated when the architecture changed.

## Anti-patterns

- Fat `App` that mixes lifecycle, protocol handling, queueing, and rendering.
- Widgets importing `client/gateway` or reaching into global app state.
- Duplicating the same value in App, Screen, and Widget without one owner.
- Styling by global widget type (for example, every `Static`) instead of by component ID/class.
- Deep descendant selectors that break whenever the layout moves.
- Nested scroll containers, or a fixed height that silently clips content.
- Hiding errors in logs while the UI still looks busy.
- Updating snapshots without inspecting the rendered SVG.
- Using Textual private APIs (`textual.widgets._markdown`, `textual.content._FormattedLine`, `rich._wrap`) without a comment explaining why the public API is insufficient.
