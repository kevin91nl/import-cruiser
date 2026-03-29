# AGENTS

## Token usage
- Keep outputs short and consistent.
- Use a fixed, short output structure: `Issues` / `Actions` / `Result`.
- In automations, use an early exit with `OK` or `No issues`.
- Use only UI-supported RRULEs.

## Release workflow
- For every new release: bump `version` in `pyproject.toml`.
- Create a git tag on `main` that exactly matches that bumped version with a `v` prefix (example: `version = "0.2.27"` => tag `v0.2.27`).
- Validate on git before publishing: confirm the tag format exists historically and verify the new tag points to the `main` release commit.
