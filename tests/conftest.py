"""
conftest.py — shared pytest configuration for selfconnect tests.

Windows restricts access to the system temp directory (C:\AppData\Local\Temp\pytest-of-<user>)
when running as certain user contexts. This conftest overrides tmp_path_factory to use a
local directory (tests/_tmp/) so all tmp_path fixtures work without --basetemp.
"""

import pathlib
import pytest


@pytest.fixture(scope="session")
def tmp_path_factory(tmp_path_factory, request):
    # Let pytest's own tmp_path_factory work, but override its base dir
    # to a path inside the repo that we know is writable.
    local_base = pathlib.Path(__file__).parent / "_tmp"
    local_base.mkdir(parents=True, exist_ok=True)
    tmp_path_factory._basetemp = local_base
    return tmp_path_factory
