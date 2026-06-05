import argparse
import asyncio
import sys
from pathlib import Path

from giver.kernel.workflow import Workflow


async def _run(workflow_path: Path, log_dir: Path) -> bool:
    try:
        workflow = Workflow.from_file(workflow_path)
    except Exception as e:
        print(f"error loading workflow: {e}", file=sys.stderr)
        return False
    return await workflow.run(log_dir)


def main() -> None:
    parser = argparse.ArgumentParser(prog="giver.kernel")
    parser.add_argument("workflow", type=Path, help="path to workflow YAML")
    args = parser.parse_args()
    log_dir = Path("/runs") / args.workflow.stem
    sys.exit(0 if asyncio.run(_run(args.workflow, log_dir)) else 1)


if __name__ == "__main__":
    main()
