# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

import logging
from pathlib import Path
from typing import Any, Dict

import jubilant
import pytest
import yaml

logger = logging.getLogger(__name__)


@pytest.fixture(scope="session")
def metadata():
    """Pytest fixture to load charm metadata."""
    yield yaml.safe_load(Path("./charmcraft.yaml").read_text())


@pytest.fixture(scope="module")
def juju(request: pytest.FixtureRequest):
    def show_debug_log(juju: jubilant.Juju):
        if request.session.testsfailed:
            log = juju.debug_log(limit=1000)
            print(log, end="")

    model = request.config.getoption("--model")
    if model:
        juju = jubilant.Juju(model=model, wait_timeout=10 * 60)
        yield juju
        show_debug_log(juju)
        return

    keep_models = bool(request.config.getoption("--keep-models"))
    with jubilant.temp_model(keep=keep_models) as juju:
        juju.wait_timeout = 10 * 60

        yield juju  # run the test
        show_debug_log(juju)
        return


@pytest.fixture(scope="module")
def app(
    juju: jubilant.Juju,
    metadata: Dict[str, Any],
    charm_path: str,
    resource_images: Dict[str, str],
):
    app_name = metadata["name"]

    # Deploy postgres
    juju.deploy(
        charm="postgresql-k8s",
        channel="14/stable",
        trust=True,
        config={"profile": "testing"},
    )

    juju.deploy(
        charm=charm_path,
        app=app_name,
        resources=resource_images,
    )

    # Wait for PostgreSQL to be ready
    juju.wait(
        lambda status: jubilant.all_active(status, "postgresql-k8s"),
        timeout=20 * 60,
    )

    juju.wait(
        lambda status: (
            jubilant.all_blocked(status, app_name) and jubilant.all_agents_idle(status, app_name)
        )
    )

    juju.integrate(app_name, "postgresql-k8s:database")
    juju.wait(jubilant.all_active)

    yield app_name


@pytest.fixture(scope="module")
def insights_address(app: str, juju: jubilant.Juju):
    """Fixture to get the address of the Ubuntu Insights web service."""
    port = juju.config(app)["web-port"]
    assert type(port) is int

    status = juju.status()
    app_ip = status.apps[app].address
    return f"http://{app_ip}:{port}"


@pytest.fixture(scope="session")
def requests_timeout():
    """Fixture to provide a global timeout for HTTP requests."""
    yield 15
