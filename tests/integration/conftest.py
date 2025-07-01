import logging
import subprocess
from pathlib import Path
from typing import Any, Dict

import jubilant
import pytest
import yaml

logger = logging.getLogger(__name__)


def pytest_addoption(parser: pytest.Parser):
    parser.addoption(
        "--keep-models",
        action="store_true",
        default=False,
        help="keep temporarily-created models",
    )
    parser.addoption(
        "--charm-file",
        action="store",
    )
    parser.addoption(
        "--ubuntu-insights-server-image",
        action="store",
    )


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

    keep_models = bool(request.config.getoption("--keep-models"))
    with jubilant.temp_model(keep=keep_models) as juju:
        juju.wait_timeout = 10 * 60

        yield juju  # run the test
        show_debug_log(juju)
        return


@pytest.fixture(scope="session")
def charm_file(metadata: Dict[str, Any], pytestconfig: pytest.Config):
    """Pytest fixture to pack the charm and return the filename, or --charm-file if set."""
    charm_file = pytestconfig.getoption("--charm-file")
    if charm_file:
        yield charm_file
        return

    try:
        subprocess.run(["charmcraft", "pack"], check=True, capture_output=True, text=True)
    except FileNotFoundError:
        raise OSError("charmcraft command not found. Please install charmcraft.") from None
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"Failed to pack charm: {exec}; Stderr: \n{e.stderr}") from None

    app_name = metadata["name"]
    charm_path = Path(__file__).parent.parent.parent
    logger.debug(f"Looking for {app_name} .charm file in {charm_path}")

    charms = [p.absolute() for p in charm_path.glob(f"{app_name}*.charm")]
    assert charms, f"{app_name} .charm file not found in {charm_path}"
    assert len(charms) == 1, f"{app_name} .charm file not unique, unsure which to use"
    logger.debug(f"Found charm file: {charms[0]}")
    yield str(charms[0])


@pytest.fixture(scope="module")
def app(
    juju: jubilant.Juju, metadata: Dict[str, Any], charm_file: str, pytestconfig: pytest.Config
):
    app_name = metadata["name"]

    # Deploy postgres
    juju.deploy(
        charm="postgresql-k8s",
        channel="14/stable",
        trust=True,
        config={"profile": "testing"},
    )

    image = pytestconfig.getoption("--ubuntu-insights-server-image")
    if not image:
        image = metadata["resources"]["ubuntu-insights-server-image"]["upstream-source"]
    assert image, "Ubuntu Insights server image must be specified"

    resources = {
        "ubuntu-insights-server-image": image,
    }

    juju.deploy(
        charm=charm_file,
        app=app_name,
        resources=resources,
        base="ubuntu@24.04",
    )

    # Should be blocked waiting for database relation
    juju.wait(lambda status: jubilant.all_blocked(status, app_name))

    # Wait for PostgreSQL to be ready
    juju.wait(lambda status: jubilant.all_active(status, "postgresql-k8s"))

    status = juju.status()
    assert status.apps[app_name].units[app_name + "/0"].is_blocked
    juju.integrate(app_name, "postgresql-k8s:database")
    juju.wait(jubilant.all_active)

    yield app_name


@pytest.fixture(scope="module")
def insights_address(app: str, juju: jubilant.Juju):
    """Fixture to get the address of the Ubuntu Insights web service (unit 0)."""
    port = juju.config(app)["web-port"]
    assert type(port) is int

    status = juju.status()
    unit_ip = status.apps[app].units[app + "/0"].address
    return f"http://{unit_ip}:{port}"


@pytest.fixture(scope="session")
def requests_timeout():
    """Fixture to provide a global timeout for HTTP requests."""
    yield 15
