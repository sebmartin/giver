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
- **Behavior lives on the model.** A node owns its `run()`; `Workflow` owns `run()` and `from_file()`. Don't split data and behavior into a separate dispatcher/runner module.
- **No `match`/`case` or `if isinstance` dispatch on types.** Use polymorphism — a `Protocol` plus a method each type implements. Adding a node type must not require editing a dispatcher.
- **`Protocol` over ABC/inheritance** for type contracts. Don't over-engineer separation of concerns — a separate runner class per node type is the wrong abstraction; the method belongs on the model.
- Each node type owns its model and runner in the same file — no monolithic `models.py`
- New node types are additive: extend the discriminated union on `type`, don't modify existing nodes
- Logging is scoped to an execution — `Logger` sets up handlers per node name, is used as a context manager, and tears down on exit; never accumulate global handlers
- The kernel is everything that runs in Docker — workflow loading, DAG scheduling, nodes, logging all live under `kernel/`. The host-only CLI is the sole layer outside it; nothing in `kernel` imports from the CLI
- No stubs or empty files — add files when the code exists

## Testing

- **Real dependencies by default.** Speed is the forcing function: ~50ms is a good test, ~1s is too slow. When a real dependency blows the budget, replace it — not before.
- **Mocks over fakes.** Fakes don't stay simple — the moment you want argument assertions they accrete into a worse, bespoke mock, and a hand-rolled fake is a custom dialect every reader must decode while `unittest.mock` is lingua franca. With a mock, asserting the *arguments it was called with* matters as much as the return value.
- **Compare whole structures in one assertion**, never field-by-field. `assert result == {"id": 1, "name": "Seb"}`, not a series of `assert result["id"] == 1`. It catches unexpected keys and gives a real diff. Multiple asserts are fine when they verify one concept.
- **Fixtures are for signal.** Keep test bodies short so the difference between two tests is obvious; heavy inline setup buries what's under test. Make fixtures self-documenting — configurable from the test so behavior is visible at the call site, not buried in the fixture. Promote to `conftest.py` only when reused across tests, and never at the cost of those two.
- **`@pytest.mark.parametrize` for data-only variation** (identical body, only the data changes). Branching on a parameter inside the body is a smell to critique. Add `ids=` only when the auto-generated id is unclear.
- **Test at the public boundary** of each unit; drive private methods through public ones. If that makes tests awkward, treat it as a design signal — prefer improving testability (composition) over reaching into internals.
- **Test by risk, not coverage %.** What bug could this produce if it changed? Skip low-risk, awkward-to-test edge cases. 90% coverage is ideal, ~80% is the floor — below that we're being too scrappy. No 100% mandate.
- **Test structure mirrors source structure.** Test each unit in isolation — don't re-verify shared behavior through every consumer (test logging once where it lives, not through every node type).
- **Don't reimplement production behavior in fixtures.** Use framework tools (e.g. `caplog`) instead of hand-rolling a stand-in for the component under test.
- Tests in `tests/` mirroring `src/`; fixture workflows in `tests/workflows/`
