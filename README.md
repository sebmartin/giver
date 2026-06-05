# give'r

A gated execution environment that lets LLMs just give'r.

LLM agents are powerful but anxious — they ask for permission, pause for confirmation, and stall when things get uncertain. give'r flips that: workflows run to completion without human intervention, inside a Docker container where agents have exactly the access they've been given and nothing more. The container is the gate. Within it, agents can move fast; outside it, nothing changes without your say-so.

give'r takes a YAML workflow file and executes it — scheduling nodes in parallel, routing logs, checking idempotency, and driving the DAG to completion. It has no opinion on what the workflow means or what the agents do.

```yaml
name: my-workflow
nodes:
  - name: load-context
    type: agent
    model: claude-haiku-4
    steps:
      - prompt: "/threads resume my-feature"

  - name: plan
    type: agent
    model: claude-haiku-4
    depends_on: [load-context]
    steps:
      - prompt: "Draft an implementation plan based on the thread context"
      - prompt: "Save the plan as a markdown artifact in the thread"
        model: claude-opus-4

  - name: validate
    type: bash
    command: "echo 'plan saved'"
    depends_on: [plan]
```

```bash
giver run workflow.yaml
```

## Architecture

- **Kernel** — parallel DAG runner; every node compiles to a process. Runs inside Docker.
- **Agent nodes** — multi-step Pi RPC sessions; each step is a prompt, context carries across steps, model can switch between steps via `set_model`.
- **Bash nodes** — deterministic steps: file prep, validation, shell commands.
- **CLI** — host-side only; invokes `docker run`, streams logs, manages container lifecycle.
- **Plugins** — workflows and skills are mounted into the container; give'r ships none.

## Usage

```bash
# Run a workflow (builds image on first use)
giver run workflow.yaml

# Run and detach — prints container name, exits immediately
giver run --detach workflow.yaml

# Stop a running workflow
giver cancel giver-my-workflow-1234567890
```

## Workflow DSL

### Agent node

```yaml
- name: plan
  type: agent
  model: claude-haiku-4        # default model for all steps
  output: plan.md              # skip node if this file already exists
  depends_on: [load-context]
  steps:
    - prompt: "Resume the thread and read the task spec"
    - prompt: "Write a detailed implementation plan to plan.md"
      model: claude-opus-4     # override for this step
```

Each step runs in the same Pi RPC session — the agent retains full context across steps. When `model:` changes between steps, give'r sends `set_model` to Pi automatically.

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
- `output` — idempotency gate; node is skipped on re-run if the file exists
- Failed nodes cause dependents to be skipped (transitive)

## Status

**Phase 2 complete.** Kernel runs bash + agent nodes with full DAG scheduling. Docker + CLI operational. Agent nodes use Pi RPC for multi-step, multi-model sessions.

- [x] Phase 1: Kernel — bash nodes, `depends_on`, parallel DAG, agent nodes (Pi RPC, multi-step), idempotency
- [x] Phase 2: Docker + CLI — `giver run`, `giver cancel`, auto-build, log streaming
- [ ] Phase 3: Plugin-mount — skills + workflow definitions mountable into the container
