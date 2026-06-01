# give'r — coding guidelines

> If you have the `ai-workspace:threads` skill available, run `/ai-workspace:threads resume dev-workflow` to load the full design context, decision logs, and build plan for this project before doing any work.

give'r is a **parallel workflow engine for LLM agents**. It takes a workflow file and executes it. That is the entire contract. It has no knowledge of what workflows mean, what tasks are, or what agents do.

For full architecture and design decisions see `decisions/`.

## Commands

```bash
uv run pytest        # run tests
uv run pytest -v     # verbose
```

## Architecture rules

- **The kernel runs inside Docker.** The CLI runs on the host, invokes `docker run`, and streams output back. Do not add interactive or host-signaling logic to the kernel.
- **The kernel knows nothing about LLMs, Pi, prompts, or skills.** It only sees processes: command in, exit code out. LLM-specific behavior belongs in the agent wrapper command.
- **Every node compiles to a bash command.** The DSL has node types (bash, agent) but the compiler distills them to shell commands before the kernel runs them. The kernel has no concept of node types at runtime.
- **No hardcoded workflows.** Workflows are user-provided YAML. give'r ships none.
- **No `human_gate` node type.** Workflows run to completion unassisted. Human checkpoints happen between `giver run` invocations on the host, not inside give'r.
- **Idempotency by artifact existence.** A node is skipped on re-run if its output artifact already exists. Do not hash agent outputs.

## Code style

- Python 3.13, asyncio
- Each node type owns its model and runner in the same file — no monolithic `models.py`
- New node types are additive: extend the discriminated union on `type`, don't modify existing nodes
- Logging is scoped to an execution — `Logger` sets up handlers per node name, is used as a context manager, and tears down on exit; never accumulate global handlers
- Imports flow one direction: `runner → workflow → kernel.nodes.*`
- No stubs or empty files — add files when the code exists
- Tests in `tests/`; fixture workflows in `tests/workflows/`
