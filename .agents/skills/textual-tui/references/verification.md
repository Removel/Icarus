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

## Snapshot workflow

Snapshots live in `apps/tui/test/__snapshots__/test_app_snapshots/` (SVG). A snapshot diff means the rendered terminal changed.

1. Run the snapshot file and read the failure:

   ```bash
   apps/tui/.venv/bin/python -m pytest apps/tui/test/test_app_snapshots.py -q
   ```

2. Inspect the SVG visually. Do not infer appearance from a pass/fail message.

   ```bash
   # If Playwright is available
   npx playwright screenshot "file:///ABSOLUTE/PATH/to/snapshot.svg" /tmp/snapshot.png
   ```

   Open the PNG and compare it with the intended layout. Check width, truncation, focus indicator, scrollbars, and color contrast.

3. If the change is intentional and the rendering is correct, update:

   ```bash
   apps/tui/.venv/bin/python -m pytest apps/tui/test/test_app_snapshots.py \
     --snapshot-update --snapshot-report /tmp/snapshot_report.html -q
   ```

4. Re-run without `--snapshot-update` to confirm the new baseline is stable.

5. Review the SVG diff before staging. A snapshot update is a visual contract change, not cleanup.

Required visual states after a layout change: initial welcome, running with pending queue, streaming markdown, narrow layout, short layout, session picker, and tool failure.

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
- `make test-tui` passes, or the remaining failures are pre-existing and unrelated (state them explicitly).
- Visual changes were checked in the real replay shell and in at least one snapshot.
- Narrow and short layouts were checked when layout or chrome changed.
- No new Textual private-API dependency was introduced without justification.
- The feature's `docs/spec` document reflects any architectural change.
