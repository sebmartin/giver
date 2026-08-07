# give'r — coding guidelines

> If you have the `ai-workspace:threads` skill available, run `/ai-workspace:threads resume dev-workflow` to load the full design context, decision logs, and build plan for this project before doing any work.

give'r is a **parallel workflow engine for LLM agents**. It takes a workflow file and executes it. That is the entire contract. It has no knowledge of what workflows mean, what tasks are, or what agents do.

See `README.md` for the workflow DSL, the harness model, and how credentials work.

## Commands

```bash
uv run pytest        # run tests
uv run pytest -v     # verbose
```

## Architecture rules

- **The kernel runs inside Docker.** The CLI runs on the host, invokes `docker run`, and streams output back. Do not add interactive or host-signaling logic to the kernel.
- **Agent-CLI knowledge lives in `giver/harness/`.** One class per harness holds everything about that program — how to invoke it headless, how to parse its event stream, what infrastructure it needs, how it gets into an image. `AgentNode` delegates to it and knows nothing else; the execution core (scheduler, logging) and every other node stay harness-ignorant. The harness package depends on neither `cli` nor `kernel`, and both read it.
- **A harness states what it needs, never where it lands.** `~/.pi/agent`, not `/home/giver/.pi/agent`. Deriving Docker words — volume names, container paths, `-v`/`-e`/`-p`, the Dockerfile itself — is the CLI's job, because a local no-container mode is planned.
- **The image is generated, never hand-written.** It carries exactly the harnesses a workflow names and what those need to run, built from each harness's `install` and `pre_install`. Nothing enumerates harnesses in a Dockerfile.
- **The container is an ordinary Unix box.** A real account, a writable `$HOME`, a working directory the user owns. The kernel must not be able to tell it is in a sandbox, and no uid is baked into an image — `giver.entrypoint` creates the account for whatever uid it is handed, then drops to it.
- **`run` is a contract, not a spawn.** "Execute these steps, stream to the log, return status." Both shipped harnesses shell out, but nothing in the interface may assume a subprocess — an in-process harness has to stay buildable.
- **Bash nodes carry their own command.** The execution core runs what a node gives it and never branches on node type.
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
- The CLI is a host-side wrapper that runs the kernel inside a Docker container; the kernel is agnostic of that infrastructure and could run on the host directly. The CLI and kernel may share code, but the kernel must not depend on `cli`.
- No stubs or empty files — add files when the code exists

## Documentation

- **`README.md` ships with the code.** A PR that changes user-visible behaviour updates it in the same PR. It is the only description of give'r anyone outside this repo reads, and a README describing the previous design is worse than none.
- **User-visible means**: a command or flag added, removed or changed; a changed default; how images are built, tagged or labelled; what the container looks like from inside; a field on the harness Protocol; a `Status` box that just became true.
- **Never document what isn't built.** A decided-but-unimplemented design belongs in `Status` as an unchecked box, never in prose that reads as current. Claiming "CI builds the image once and runs inside it" while `giver run` still shells out to `docker` is a lie a reader only discovers by trying it.
- **Verify each claim against the code, not against memory.** Documentation written from what you intended rather than what shipped is how the README came to say a pi image contains no node, when pi is an npm package and every pi image carries one.
- **Explain the load-bearing ideas, don't just assert them.** "Bring your own harness" is why the harness layer is shaped the way it is, so say what it lets a reader do and how. Explaining is showing the interface and what each field buys; it is not writing a pitch.
- **Write documentation, not copy.** Say what a thing does, when you would use it, and what it costs. Ordinary connective sentences are fine and most sentences should be ordinary. Specific banned habits, all of which showed up in this README:
  - "X, not Y" antithesis for emphasis — *"the three it ships are conveniences, not the product"*
  - paradox or reversal as a punchline — *"give'r is built to be wrong about which harness you want"*
  - escalating stakes to make a design choice sound momentous — *"inherits every decision its vendor makes"*
  - an em-dash pivot into a summarising flourish at the end of a paragraph
  - making every sentence quotable, so there is no plain prose between the claims
- **Two tests before a sentence stays.** Could it appear in a man page? Can the reader do something differently having read it? A sentence that is memorable but actionless is decoration standing where a fact belongs.

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
