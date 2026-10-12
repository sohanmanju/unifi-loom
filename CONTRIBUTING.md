# Contributing

Use Python 3.11+ and keep changes focused on UniFi Network.

```sh
uv sync --extra test
uv run pytest -q
uv run python tests/stdio_smoke.py
uv run python scripts/check_release.py
uv build
```

## Ownership boundaries

- `config.py`: environment and TLS/auth configuration.
- `client.py`: verified transport and explicit authentication modes.
- `schema.py` / `catalog.py`: the canonical API schema graph and request validation.
- `changes.py`: plans, preconditions, submission outcomes and audit behavior.
- `snapshot.py` / `topology.py` / `diagrams.py`: normalization, collection and pure Mermaid rendering.
- `radios.py`: AP configuration edits and capability constraints.
- `task_tools.py`: direct, typed tools for common read tasks.
- `server.py`: MCP registration, human approval and result formatting.

Keep vendor-specific data normalization outside renderers. Reuse the canonical schema contract. Add a task tool only when it saves a real discovery step; leave uncommon APIs in the generic catalog. Never add an automatically retried write or a model-supplied approval flag.

Every behavioral fix needs a meaningful regression test. Tests and examples use synthetic identifiers and no live credentials. Do not commit recorded console responses, screenshots, certificates or personal filesystem paths.

When changing tools, regenerate the published definitions:

```sh
uv run python scripts/export_tool_definitions.py
```

Update `docs/TOOLS.md`, the changelog and validation notes with supported behavior and its verification limits.
