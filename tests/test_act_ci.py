"""scripts/act_ci.py (``make ci-local``) runs what ci.yml defines, with the image .actrc names."""

from __future__ import annotations

import shlex

import pytest
import yaml

from scripts import act_ci


def test_every_job_and_every_matrix_cell_is_run_once():
    jobs = yaml.safe_load(act_ci.WORKFLOW.read_text())["jobs"]
    expected = 0
    for spec in jobs.values():
        matrix = (spec.get("strategy") or {}).get("matrix") or {}
        size = 1
        for values in matrix.values():
            size *= len(values)
        expected += size
    runs = act_ci.cells()
    assert len(runs) == expected
    assert len({cell.name for cell in runs}) == expected
    assert {cell.job for cell in runs} == set(jobs)


def test_a_cell_is_handed_to_act_as_its_job_and_its_matrix_values():
    cell = next(cell for cell in act_ci.cells(jobs=["test"]))
    assert cell.args() == ["-j", "test", *(arg for key, value in cell.matrix for arg in ("--matrix", f"{key}:{value}"))]
    assert cell.name.startswith("test[") and "python-version=" in cell.name


def test_a_job_that_does_not_exist_is_refused():
    with pytest.raises(SystemExit, match="no-such-job"):
        act_ci.cells(jobs=["no-such-job"])


def test_actrc_runs_ci_yml_on_the_image_the_script_builds():
    options = [shlex.split(line) for line in (act_ci.ROOT / ".actrc").read_text().splitlines() if line.strip()]
    assert ["-W", ".github/workflows/ci.yml"] in options
    assert ["-P", f"ubuntu-24.04={act_ci.IMAGE}"] in options
    assert ["--container-options=--init"] in options
    assert act_ci.DOCKERFILE.is_file()
