# give'r

A gated execution environment that lets LLMs just give'r.

LLM agents are powerful but anxious — they ask for permission, pause for confirmation, and stall when things get uncertain. give'r flips that: workflows run to completion without human intervention, inside a Docker container where agents have exactly the access they've been given and nothing more. The container is the gate. Within it, agents can move fast; outside it, nothing changes without your say-so.

give'r takes a YAML workflow file and executes it — scheduling nodes in parallel, routing logs, checking idempotency, and driving the DAG to completion. It has no opinion on what the workflow means or what the agents do.

```yaml
name: my-workflow
nodes:
  - name: plan
    type: agent
    prompt: "Analyze the codebase and produce a plan"

  - name: review
    type: agent
    prompt: "Review the plan"
    depends_on: [plan]
```

```bash
giver run workflow.yaml
```

## Architecture

- **Kernel** — parallel DAG command runner; every node compiles to a shell command
- **Agent nodes** — DSL sugar over Pi (`pi --mode json`); multi-provider by design
- **Bash nodes** — deterministic steps: file prep, diff filtering, injection stripping
- **Plugins** — workflows and skills are mounted into the container; give'r ships none

Runs inside Docker. The CLI runs on the host and invokes `docker run`.

## Status

Early development — the kernel runs YAML workflows of bash nodes with `depends_on`, executing the DAG in parallel and skipping nodes whose dependencies fail. Agent nodes, Docker, and the CLI are not built yet.
