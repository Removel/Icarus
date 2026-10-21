# Mem0 for Icarus

This is a maintained subset of [Mem0](https://github.com/mem0ai/mem0), imported at
`c7ee362aff94a369af70f13f2b4f853f6793ff4c` under Apache-2.0. The import source is frozen;
Icarus patches and security maintenance continue. See [LICENSE](LICENSE),
[MODIFICATIONS.md](MODIFICATIONS.md), and [repository notices](../../THIRD_PARTY_NOTICES.md).

## Role and runtime

`mem0/` is the Python SDK, including providers and runtime resources. `server/` provides
the self-hosted REST API, auth/API keys, PostgreSQL/pgvector, migrations, and dashboard.
Icarus Agent consumes the service over HTTP rather than importing this App's SDK.

From the Icarus repository root:

```bash
icarus install mem0
icarus start mem0
icarus status mem0
icarus stop mem0
```

Configuration comes from the root `.env`; the only template is root `.example.env`.
Durable data is bind-mounted under `$ICARUS_DATA_DIR/services/mem0`. Stop does not delete
that data. This first cleanup retains API :8888, PostgreSQL, and dashboard :3000.
See [server operations](server/README.md).

## Development and tests

```bash
icarus install mem0 --dev
make test-mem0
make test-mem0-full
```

The private `apps/mem0/.venv` is for development and tests only. The installer checks
that it is an app-owned virtual environment, rejects redirected environment paths,
and isolates pip's destination/configuration overrides to prevent shared-environment
installation. Default checks are
an offline core-memory contract gate; full checks retain the upstream SDK/server
suite. Optional provider/server imports may require extra dependencies; an incomplete
collection is not a passing suite. For full environment preparation from this directory:

```bash
.venv/bin/python -m pip install -e '.[test,dev,vector-stores,llms,extras,nlp]' -r server/requirements.txt
```

Existing dependency/platform failures must be compared against an unchanged baseline,
not fixed or skipped merely to make cleanup green. Local development follows
[CONTRIBUTING](../../CONTRIBUTING.md), with application design/plans in [docs/spec](docs/spec/).
The Dockerfile installs server requirements and the local editable SDK via pip;
it retains `poetry.lock` but does not consume it as a locked resolution.

## Removed perimeter

Standalone CLI, TypeScript SDK, external integration packages, examples, upstream docs
site, plugin marketplaces, and upstream community/release automation are not shipped
in this Icarus subset. The five retained development skills live in
[.agents/skills](../../.agents/README.md), with their own licenses. None is automatically
installed or executed. Runtime `mem0/memory/oss_notices_config.json` remains SDK data,
not a plugin marketplace to remove.

The OSS-to-Platform migration script is preserved only as a
[fixture](tests/fixtures/oss-to-platform-migrate.sh) for its retained regression tests.
It is not an Icarus migration command. Do not run it on actual memories without a
separate migration decision and approval.
