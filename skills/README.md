# Production baseline skills

This directory is reserved for reviewed baseline skills used by the Icarus Agent,
with one bundle per `skills/<name>/`:

```text
<name>/
├── SKILL.md
├── LICENSE            # when derived from third-party content
├── references/        # optional
└── scripts/           # optional
```

`SKILL.md` contains frontmatter `name` and `description` plus the actual instructions.
There are no baseline bundles in this directory yet. This change does not add an
installer, synchronize into `$ICARUS_DATA_DIR/skills` or `<workspace>/skills`, or
modify Agent skill-discovery behavior. Add an explicit consumer/installation design
before making that behavior automatic.

Development references belong in [.agents/skills/](../.agents/README.md). OpenKB's
three deck-related skills are internal application assets, not shared production
bundles. Keep their existing app-owned packaging and discovery paths.
