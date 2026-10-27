"""Scenario data stays off the network in tests (issue #7).

The scenarios' captures and pinned skills live in the scenario data
repository, fetched by digest on first use. Tests must be hermetic
(CLAUDE.md), so fetching is off here, and a test that reads a scenario whose
data nobody fetched skips, saying how to fetch it. Run `uv run k8srca
scenario fetch` once and every such test runs.
"""

import os

import pytest

from k8srca.scenario.model import MissingData
from k8srca.scenario.sources import FETCH_ENV

os.environ[FETCH_ENV] = "0"


def _skip_missing(outcome) -> None:
    excinfo = outcome.excinfo
    if excinfo is not None and issubclass(excinfo[0], MissingData):
        outcome.force_exception(pytest.skip.Exception(str(excinfo[1]), _use_item_location=True))


@pytest.hookimpl(wrapper=False, hookwrapper=True)
def pytest_runtest_setup(item):
    _skip_missing((yield))


@pytest.hookimpl(wrapper=False, hookwrapper=True)
def pytest_runtest_call(item):
    _skip_missing((yield))
