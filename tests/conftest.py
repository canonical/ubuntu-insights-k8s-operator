# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.
"""Module for pytest configuration."""

import pytest


def pytest_addoption(parser: pytest.Parser):
    parser.addoption(
        "--model",
        action="store",
        help="Juju model to use."
        "If not provided, a temporary model will be created for each test that requires one.",
    )
    parser.addoption(
        "--keep-models",
        action="store_true",
        default=False,
        help="Keep temporarily-created models",
    )
