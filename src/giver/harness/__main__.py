"""Prepare harnesses, then become the program that needs them.

`giver shell` and `giver chat` hand a container straight to bash or to a
harness's own REPL, so without this nothing of give'r would run inside them —
and those are the paths a first-pass login goes through, the one place a
harness's state has to be arranged correctly or the login is written somewhere
that does not survive the container.

`exec` rather than spawn: the harness replaces this process instead of running
under it, so the REPL owns the terminal and signals and exit codes travel
between the user and the program they asked for, with nothing in between.
"""

import argparse
import os
import sys

from giver.harness.registry import harness_by_name


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="giver.harness",
        description="prepare harnesses in this environment, then exec a command",
    )
    parser.add_argument(
        "--harness",
        action="append",
        default=[],
        metavar="NAME",
        help="harness to prepare before exec; repeatable, may be omitted entirely",
    )
    parser.add_argument(
        "command",
        nargs=argparse.REMAINDER,
        help="argv to exec once every harness is prepared",
    )
    args = parser.parse_args(argv)

    command = args.command
    if command and command[0] == "--":
        command = command[1:]
    if not command:
        parser.error("no command to exec")

    for name in args.harness:
        try:
            harness_by_name(name).prepare()
        except ValueError as e:
            print(f"error: {e}", file=sys.stderr)
            raise SystemExit(1)

    os.execvp(command[0], command)


if __name__ == "__main__":
    main()
