# give'r

A gated execution environment that lets LLMs just give'r.

LLM agents are powerful but anxious — they ask for permission, pause for confirmation, and stall when things get uncertain. give'r flips that: workflows run to completion without human intervention, inside a Docker container where agents have exactly the access they've been given and nothing more. The container is the gate. Within it, agents can move fast; outside it, nothing changes without your say-so.

give'r takes a YAML workflow file and executes it — scheduling nodes in parallel, routing logs, checking idempotency, and driving the DAG to completion. It has no opinion on what the workflow means or what the agents do.

```yaml
name: my-workflow
defaults:
  model: anthropic/claude-haiku-4-5

nodes:
  - name: load-context
    type: agent
    steps:
      - prompt: "Read the task spec and summarise it"

  - name: plan
    type: agent
    depends_on: [load-context]
    steps:
      - prompt: "Draft an implementation plan"
      - prompt: "Save the plan to plan.md"
        model: anthropic/claude-opus-4-5

  - name: validate
    type: bash
    command: "test -s plan.md"
    depends_on: [plan]
```

```bash
giver run workflow.yaml
```

## Harnesses

A **harness** is a coding-agent CLI that give'r drives — `pi`, `claude-code` or `codex`. give'r orchestrates; the harness does the agent work. One class describes each one: where it keeps credentials and sessions, what environment and ports it needs, how it gets installed and what that install needs first, its REPL command, which vendors it can serve, and how to run a node's steps.

Nothing in give'r branches on which harness is running. Anything harness-specific is expressed through a mechanism every harness has, so adding one is a single class.

**`pi` is the default and the batteries-included path** — it serves any vendor by API key and needs no configuration to work. Name a different harness when you want one:

```yaml
defaults:
  harness: claude-code              # or per-node
  model: anthropic/claude-opus-4-5
```

Harnesses are driven as one-shot invocations per step, with continuity threaded through the harness's own session. That continuity is not uniform, so each harness declares what it can do rather than give'r assuming:

| Harness | Headless | Continuity | Branches instead of continuing? |
|---|---|---|---|
| `pi` | `pi -p` | `--fork <id>` | yes |
| `claude-code` | `claude -p` | `--resume <id> --fork-session` | yes, opt-in |
| `codex` | `codex exec` | `codex exec resume <id>` | **no** |

Branching leaves the parent session untouched, so re-running a step can't corrupt the one it came from. codex's headless mode has no branching variant — it resumes in place. Every harness therefore declares `forks_on_resume` for replay logic to read; give'r replays at node granularity today (a node whose output artifact exists is skipped whole), so nothing consults the flag yet — step-level replay is where it becomes load-bearing.

## Models

Write `vendor/model`. The vendor prefix is optional when the name is unambiguous — `claude-*` is Anthropic, `gpt-*`/`o3-*` are OpenAI — and give'r qualifies it at load time:

```yaml
model: claude-opus-4-5              # → anthropic/claude-opus-4-5
model: anthropic/claude-opus-4-5    # explicit
model: ollama/qwen-2.5-coder        # vendor required — qwen is served by several
```

A bare name give'r can't place is a load-time error rather than a guess, because `qwen`, `llama` and `deepseek` are served by different vendors with different credentials and costs. Qualified names pass through unvalidated — the harness itself will reject a bad model id with a real error.

Which harness runs a step is decided by the workflow, not by the model: `harness: pi` with an Anthropic model is Claude on an API key, and that's a legitimate thing to ask for.

## Credentials

give'r stores no credentials and defines no credential format. Each harness reads and writes its own, in its own location, on a Docker volume that persists between runs. A run mounts the volumes for the harnesses that workflow actually names, and nothing else.

Your host's credentials are never read: give'r's logins live only inside give'r's own environment.

Log in once per harness:

```bash
giver shell pi        # drops you into bash with pi's volume mounted; run its login
giver chat claude-code     # or straight into the harness's own REPL
```

Missing credentials surface as that harness's own "not logged in" error on first use, with its own remedy.

## Usage

```bash
giver run workflow.yaml              # builds an image for it, if there isn't one
giver run --detach workflow.yaml     # prints the container name, exits
giver cancel giver-my-workflow-1234567890

giver shell [harness]                # bash in the container; omit for a bare shell
giver chat <harness>                 # the harness's own REPL

giver dockerfile workflow.yaml --dev # print the Dockerfile; build it yourself
```

## Images

An image carries exactly the harnesses its workflows name — a pi workflow gets a
runtime with pi in it and nothing else, not even node. give'r generates the
Dockerfile from what each harness declares, so adding a harness is still one
class and no file lists them.

Each harness set is its own image — `giver:dev-pi`, `giver:dev-claude-code_pi` —
rather than one that grows to cover everything you've ever run. An image whose
contents depend on your run history is a combination nobody chose and nobody
tested, and it would differ between two people running the same workflow. The
expensive layers are shared, and node is installed before any harness, so every
npm-based image shares that one too.

`giver run` builds the image for what a workflow names, if it isn't already
there. `LABEL giver.harnesses` records the contents and `giver.source` records
which give'r is inside — a version doesn't move while you're editing, so the
fingerprint is what stops a run using yesterday's kernel:

```bash
docker images --filter label=giver.harnesses
```

`giver dockerfile` prints the file for anyone who wants to build their own: CI
builds it once and runs inside it. It needs `--dev [path]` while `giver` is an
unregistered name on PyPI, since a generated file that installed it would run
whoever claims the name inside the container holding every credential.

The image bakes in no user. `giver run` passes the uid it was invoked as, and
the container creates an account for it before dropping to it — so an image is
portable, run output on a bind mount belongs to the person who will read it, and
harnesses that refuse to run as root work.

## Workflow DSL

### Defaults

```yaml
defaults:
  model: anthropic/claude-haiku-4-5
  harness: claude-code
```

`model` cascades to every step; `harness` cascades to every node. Nearest declaration wins, so a node overrides the defaults and a step overrides its node.

`harness` stops at the node deliberately: a node's steps share one session, and session ids belong to the harness that issued them — so a harness can't change partway through. A `harness:` key on a step is a load-time error.

### Agent node

```yaml
- name: plan
  type: agent
  harness: claude-code              # optional; omitted means pi
  model: anthropic/claude-haiku-4-5
  output: plan.md              # skip this node if the file already exists
  depends_on: [load-context]
  steps:
    - prompt: "Read the task spec"
    - prompt: "Write the plan to plan.md"
      model: anthropic/claude-opus-4-5
```

Steps run sequentially in one session — context carries across them, and the model can change between them within a single vendor. All of a node's steps must resolve to one vendor; cross-vendor work happens between nodes, through files.

### Bash node

```yaml
- name: validate
  type: bash
  command: "jq . plan.md > /dev/null"
  output: validated.txt
  depends_on: [plan]
```

### DAG features

- `depends_on` — explicit ordering; nodes without dependencies run in parallel
- `output` — idempotency gate; a node is skipped on re-run if the file exists
- A failed node causes its dependents to be skipped, transitively

Everything resolvable is resolved when the workflow loads, so a typo'd model, an unknown harness, or a cycle fails before any container starts.

## Architecture

- **Kernel** — parallel DAG runner; schedules nodes, routes logs, checks idempotency. Runs inside Docker and knows nothing about it.
- **Harnesses** (`giver/harness/`) — one class per agent CLI, holding everything about that program. Depends on neither the CLI nor the kernel; both read it.
- **Agent nodes** — multi-step sessions delegated to a harness.
- **Bash nodes** — deterministic steps: file prep, validation, shell commands.
- **CLI** — host-side only; owns every Docker word. A harness declares `~/.pi/agent`; turning that into a volume, a container path and a line in a Dockerfile is the CLI's job, because a local no-container mode is planned.
- **Entrypoint** (`giver/entrypoint.py`) — runs first in every container give'r starts, makes it an ordinary environment for the invoking user, and execs. Skipping it is a no-op, so a runtime somebody else started stays their business.

## Status

Kernel runs bash and agent nodes with full DAG scheduling; Docker and the CLI are operational; harnesses are unified behind one description read by both layers.

- [x] Kernel — bash nodes, `depends_on`, parallel DAG, multi-step agent nodes, idempotency
- [x] Docker + CLI — `giver run`, `giver cancel`, `giver shell`, `giver chat`, auto-build, log streaming
- [x] Harness layer — one class per agent CLI, workflow-declared routing, credential isolation
- [x] Generated images — a runtime containing exactly the harnesses a workflow names, run as the invoking user
- [ ] Checkpoint and resume — re-run a workflow without redoing completed work
- [ ] Plugin-mount — skills and workflow definitions mounted into the container

## Development

```bash
uv run pytest
```
