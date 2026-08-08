# give'r

A gated execution environment that lets LLMs just give'r.

Agent CLIs stop and ask for confirmation, which makes them awkward to run unattended. give'r runs them in a Docker container with permission prompts turned off, so a workflow finishes without you watching it. The container is what makes that safe: an agent can do what it likes inside, and reaches nothing outside except the directories you mount.

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

A **harness** is a coding-agent CLI that give'r drives — `pi`, `claude-code` or `codex`. give'r orchestrates; the harness does the agent work.

give'r ships three harnesses, but it is not tied to them. No code in give'r branches on which harness is running — there is no `if harness == "claude"` anywhere — so the three that ship use the same interface as one you write.

The hooks in that interface exist because a shipped harness needed one. `ports` is there because pi's login runs a local OAuth callback. `prepare()` is there because claude-code keeps its config outside the directory give'r mounts, so something has to reconcile that before it runs. A harness with a similar problem uses the same hook.

So you can run an agent CLI give'r doesn't ship, and give'r doesn't have to release when one of them changes.

### Writing a harness

One class, most of it declaration:

```python
class MyHarness:
    name = "mine"
    state_path = "~/.mine"                    # credentials and sessions
    env = {}                                  # what it needs to function at all
    ports = ()                                # only its interactive login
    repl_cmd = ("mine",)
    install = "npm install -g my-agent"       # becomes a RUN line
    pre_install = (NODE,)                     # run before it, so node is there
    forks_on_resume = True                    # can it branch a session?

    def serves(self, vendor): return vendor == "acme"
    def prepare(self): ...                    # arrange anything else, in place
    async def run(self, steps, log): ...      # do the work, return an exit status
```

Register it in `giver/harness/registry.py`. `install` and `pre_install` get it into generated images without editing a Dockerfile, and `state_path` gets it a persisted volume without naming a container path. `run` is the only part with substantial work in it: parsing the CLI's event stream to find the session id and whether the step succeeded.

`run` takes a list of steps, streams output to the log, and returns an exit status. The three shipped harnesses do that by running a subprocess, but the interface does not require one — a harness calling a library in-process works the same way.

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

Branching leaves the parent session untouched, so re-running a step can't corrupt the one it came from. codex's headless mode has no branching variant; it resumes in place. Each harness declares `forks_on_resume` so replay logic can read it. Nothing reads it yet: give'r currently replays whole nodes, skipping any whose output artifact exists. It will matter once give'r can replay individual steps.

## Models

Write `vendor/model`. The vendor prefix is optional when the name is unambiguous — `claude-*` is Anthropic, `gpt-*`/`o3-*` are OpenAI — and give'r qualifies it at load time:

```yaml
model: claude-opus-4-5              # → anthropic/claude-opus-4-5
model: anthropic/claude-opus-4-5    # explicit
model: ollama/qwen-2.5-coder        # vendor required — qwen is served by several
```

A bare name give'r can't place is a load-time error rather than a guess, because `qwen`, `llama` and `deepseek` are served by different vendors with different credentials and costs. Qualified names pass through unvalidated — the harness itself will reject a bad model id with a real error.

The workflow decides which harness runs a step; the model does not. `harness: pi` with an Anthropic model runs Claude through pi on an API key, which is a supported combination.

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
giver run workflow.yaml              # builds an image for it if there isn't a current one
giver run --detach workflow.yaml     # prints the container name, exits
giver cancel giver-my-workflow-1234567890

giver shell [harness]                # bash in the container; omit for a bare shell
giver chat <harness>                 # the harness's own REPL

giver dockerfile workflow.yaml --dev # print the Dockerfile; build it yourself
```

## Images

An image carries exactly the harnesses its workflows name, and what those need to
run — a pi workflow gets pi and the node it runs on; a workflow of nothing but
bash nodes gets neither. give'r generates the Dockerfile from what each harness
declares, so adding a harness is still one class and no file lists them.

Each harness set gets its own image: `giver:dev-pi`, `giver:dev-claude-code_pi`.
give'r does not grow one image to cover everything you have run, because its
contents would then depend on your run history — you would end up running a
combination of harnesses that nothing was tested against, and a colleague
running the same workflow would get a different image. Duplication is cheap
here: the base image and everything installed before the harnesses are shared
layers, and node is installed before any harness, so every npm-based image
shares it.

`giver run` builds the image a workflow needs if it isn't already there. Two
labels record what an image is. `giver.harnesses` lists the harnesses in it, and
`giver.source` is a digest of the give'r inside it — a version number does not
change while you edit the source, so without the digest a run can silently use
an image built from older code:

```bash
docker images --filter label=giver.harnesses
```

`giver dockerfile` prints the file for anyone who wants to build their own image
— CI building one per pipeline, or a runtime you publish. give'r never builds for
anyone but itself. It needs `--dev [path]` while `giver` is an unregistered name
on PyPI, since a generated file that installed it would run whoever claims the
name inside the container holding every credential.

The image bakes in no user. `giver run` passes the uid it was invoked as, and
the container creates an account for it before dropping to it — so an image is
portable, run output on a bind mount belongs to the person who will read it, and
harnesses that refuse to run as root work.

Running a generated image yourself means passing that uid yourself, since
nothing else in the image will:

```bash
docker run -e GIVER_UID=$(id -u) -e GIVER_GID=$(id -g) \
  -v "$PWD/runs:/runs" giver:dev-pi python -m giver.kernel workflow.yaml
```

Without it the container has no account and no home, and the entrypoint says so
and exits rather than running the workflow as root.

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
- **Entrypoint** (`giver/entrypoint.py`) — runs first in every container give'r starts, sets it up for the invoking user, then execs the command. Execs unchanged when `GIVER_UID` is unset and the container already has a writable home, so one someone else started is left alone.

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
