# Verification loop for the Icarus TUI

The TUI has three layers of regression protection: pure unit tests, Pilot interaction tests, and visual snapshots. Use the cheapest layer that can observe the change, then finish with the full suite.

## Commands

```bash
# Install the TUI environment (once, or after dependency changes)
./apps/tui/scripts/install.sh --dev

# Narrowest affected test file
apps/tui/.venv/bin/python -m pytest apps/tui/test/widgets/test_conversation.py -q

# Full TUI suite
make test-tui

# Compile check (test.sh already includes this)
apps/tui/.venv/bin/python -m compileall -q apps/tui/src apps/tui/test packages

# Whitespace / conflict markers
git diff --check
```

Do not fix unrelated failures in a focused change. Compare the failing set against the baseline rather than matching counts alone; a flaky history-restore test and snapshot drift have existed independently of feature work.

## Real visual verification (no maintained SVG baselines)

The user removed the old 13 SVG snapshots and their test module. Do not run that missing file or blindly recreate baselines. Pilot tests verify behavior, not appearance.

```bash
apps/tui/.venv/bin/python apps/tui/scripts/capture_preview.py --out /tmp/icarus-preview --width 120 --height 40
./apps/tui/scripts/start.sh --preview
```

Capture uses the actual App and an explicit offline fixture, submits text, waits for completion, opens details/menu and shuts down. Inspect welcome/chat/menu at 120×40, 100×32, 70×24, 58×24, 100×12. SVG-to-PNG browser conversion should use installed CJK monospace fallback and avoid font-network timeouts. Also drive the native app through a real terminal where available.

The repository `icarus tui` launcher still requires Gateway readiness; offline preview uses the app start script above. Formal conversation uses `icarus start gateway` followed by `icarus tui`.

## Deterministic replay

Replay exercises the real Textual shell without calling a model or writing a Session:

```bash
apps/tui/.venv/bin/python apps/tui/scripts/replay_events.py \
  --tui-real --speed 8 \
  apps/tui/test/fixtures/synthetic_tui_events.jsonl
```

Use transcript-only replay for ordering/timeline logic:

```bash
apps/tui/.venv/bin/python apps/tui/scripts/replay_events.py \
  apps/tui/test/fixtures/synthetic_tui_events.jsonl
```

Add or extend a fixture when a bug depends on event ordering, reconnect, early updates, or queue interleaving. Do not reproduce those with live model calls.

## What to test at which layer

| Change | Cheapest test |
| --- | --- |
| Pure state machine, projection, command parsing | Plain pytest, no Textual app |
| Widget rendering, local keys, focus | Widget `run_test()` / Pilot |
| Screen composition and routing | `App.run_test()` with a fake runtime service |
| Full shell behavior | `test_app.py` + snapshot |
| Streaming/markdown layout | Replay + snapshot |
| Protocol or transport | Gateway client tests with a fake connector |

Fake the runtime at the `RuntimeClient` boundary. Tests should not need a Gateway process, a model key, or a real Session.

## Definition of done

- The changed behavior is covered by a focused test, or an existing test fails without the change.
- `make test-tui` passes, or remaining failures are identified with actual baseline evidence (state names explicitly).
- Visual changes were checked in the real replay shell and in at least one snapshot.
- Narrow and short layouts were checked when layout or chrome changed.
- No new Textual private-API dependency was introduced without justification.
- The feature's `docs/spec` document reflects any architectural change.
