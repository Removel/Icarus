# Python SDK tests

This retained pytest suite covers the local `mem0/` package and self-hosted server.
Use the app's private environment; do not install optional providers into the root.

From the Icarus root:

```bash
icarus install mem0 --dev
make test-mem0        # offline memory contracts and script regressions
make test-mem0-full   # complete retained suite; reports actual failures
```

Optional-provider/server prerequisites are described in [README](../README.md).
Missing imports are an incomplete environment, not a passing suite. The migration
regression uses `fixtures/oss-to-platform-migrate.sh` with temporary test data only.

- Name files `test_<module>.py` and mirror provider source categories.
- Use pytest-mock and pytest-asyncio where needed and native assertions.
- Match the SDK's Ruff line length 120 and [local conventions](../mem0/AGENTS.md).
- Mock external provider SDKs, not the behavior under test.
- Bug fixes require a regression test observed failing before the fix.
- Keep existing full-suite assertions; compare failures with the same baseline/environment.
