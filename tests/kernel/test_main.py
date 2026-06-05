import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from giver.kernel.__main__ import _run, main


async def test_run_returns_true_on_empty_workflow(tmp_path):
    wf = tmp_path / "test.yaml"
    wf.write_text("name: test\nnodes: []")
    assert await _run(wf, tmp_path / "runs") is True


async def test_run_returns_false_on_missing_file(tmp_path):
    assert await _run(tmp_path / "missing.yaml", tmp_path / "runs") is False


async def test_run_returns_false_on_invalid_yaml(tmp_path):
    wf = tmp_path / "bad.yaml"
    wf.write_text("name: test\nnodes: [bad")
    assert await _run(wf, tmp_path / "runs") is False


def test_main_exits_nonzero_when_no_args():
    with patch.object(sys, "argv", ["giver.kernel"]):
        with pytest.raises(SystemExit) as exc:
            main()
    assert exc.value.code != 0
