# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.
#
# Learn more about testing at: https://juju.is/docs/sdk/testing


import json
from enum import Enum

import ops
from ops import testing

from charm import UbuntuInsightsCharm


class ServiceType(Enum):
    """Enum for service types."""

    WEB = "web-service"
    INGEST = "ingest-service"


CONTAINER_NAME = "ubuntu-insights-server"
WEB_DYNAMIC_PATH = "/etc/ubuntu-insights-service/web-live-config.json"
INGEST_DYNAMIC_PATH = "/etc/ubuntu-insights-service/ingest-live-config.json"

REPORTS_CACHE_MOUNT_LOCATION = "/var/lib/ubuntu-insights/"


def test_pebble_layer():
    ctx = testing.Context(UbuntuInsightsCharm)
    container = testing.Container(name=CONTAINER_NAME, can_connect=True)
    state_in = testing.State(
        containers={container},
        leader=True,
    )
    state_out = ctx.run(ctx.on.pebble_ready(container), state_in)

    expected_plan = {
        "services": {
            ServiceType.WEB.value: {
                "override": "replace",
                "summary": "web service",
                "command": (
                    f"ubuntu-insights-web-service "
                    f"--listen-port=8080 "
                    f"--daemon-config={WEB_DYNAMIC_PATH} "
                    f"--reports-dir={REPORTS_CACHE_MOUNT_LOCATION}"
                ),
                "startup": "enabled",
            },
            ServiceType.INGEST.value: {
                "override": "replace",
                "summary": "ingest service",
                "command": (
                    f"ubuntu-insights-ingest-service "
                    f"--daemon-config={INGEST_DYNAMIC_PATH} "
                    f"--reports-dir={REPORTS_CACHE_MOUNT_LOCATION}"
                ),
                "startup": "disabled",
            },
        },
        "checks": {
            "web-service-ready": {
                "override": "replace",
                "level": "ready",
                "http": {"url": "http://localhost:8080/version"},
            },
        },
    }

    assert state_out.get_container(container.name).plan == expected_plan

    assert (
        state_out.get_container(container.name).service_statuses[ServiceType.WEB.value]
        == ops.pebble.ServiceStatus.ACTIVE
    )

    # Ingest should be disabled as there isn't a database relation yet
    assert ServiceType.INGEST.value not in state_out.get_container(container.name).service_statuses


def test_config_changed():
    ctx = testing.Context(UbuntuInsightsCharm)
    container = testing.Container(name=CONTAINER_NAME, can_connect=True)
    state_in = testing.State(
        containers={container},
        config={
            "web-port": 8081,
            "web-apps": "linux",
            "ingest-apps": "linux, windows",
            "ingest-legacy": False,
        },
        leader=True,
    )
    state_out = ctx.run(ctx.on.config_changed(), state_in)
    assert (
        "--listen-port=8081"
        in state_out.get_container(container.name)
        .layers[container.name]
        .services[ServiceType.WEB.value]
        .command
    )

    container_fs = state_out.get_container(container.name).get_filesystem(ctx)
    ingest_daemon_cfg_file = container_fs / INGEST_DYNAMIC_PATH[1:]
    web_daemon_cfg_file = container_fs / WEB_DYNAMIC_PATH[1:]
    ingest_daemon_config = json.loads(ingest_daemon_cfg_file.read_text())
    web_daemon_config = json.loads(web_daemon_cfg_file.read_text())

    assert "linux" in ingest_daemon_config["allowList"]
    assert "windows" in ingest_daemon_config["allowList"]
    assert "ubuntu-report/ubuntu/desktop/24.04" not in ingest_daemon_config["allowList"]

    assert "linux" in web_daemon_config["allowList"]
    assert "windows" not in web_daemon_config["allowList"]
    assert "ubuntu-report/ubuntu/desktop/24.04" in web_daemon_config["allowList"]


def test_relation_data():
    ctx = testing.Context(UbuntuInsightsCharm)
    relation = testing.Relation(
        endpoint="database",
        interface="postgresql_client",
        remote_app_name="postgresql-k8s",
        remote_app_data={
            "endpoints": "example.com:5432",
            "username": "foo",
            "password": "bar",
            "database": "insights",
        },
    )
    container = testing.Container(name=CONTAINER_NAME, can_connect=True)
    state_in = testing.State(
        containers={container},
        relations={relation},
        leader=True,
    )

    state_out = ctx.run(ctx.on.relation_changed(relation), state_in)

    assert state_out.get_container(container.name).layers[container.name].services[
        ServiceType.INGEST.value
    ].environment == {
        "UBUNTU_INSIGHTS_INGEST_SERVICE_DBCONFIG_HOST": "example.com",
        "UBUNTU_INSIGHTS_INGEST_SERVICE_DBCONFIG_PORT": "5432",
        "UBUNTU_INSIGHTS_INGEST_SERVICE_DBCONFIG_USER": "foo",
        "UBUNTU_INSIGHTS_INGEST_SERVICE_DBCONFIG_PASSWORD": "bar",
        "UBUNTU_INSIGHTS_INGEST_SERVICE_DBCONFIG_DBNAME": "insights",
    }


def test_database_relation_broken():
    ctx = testing.Context(UbuntuInsightsCharm)
    relation = testing.Relation(
        endpoint="database",
        interface="postgresql_client",
        remote_app_name="postgresql-k8s",
    )
    container = testing.Container(name=CONTAINER_NAME, can_connect=True)
    state_in = testing.State(
        containers={container},
        relations={relation},
        leader=True,
    )

    state_out = ctx.run(ctx.on.relation_broken(relation), state_in)

    assert state_out.unit_status == testing.BlockedStatus("Waiting for database relation")


def test_no_database_blocked():
    ctx = testing.Context(UbuntuInsightsCharm)
    container = testing.Container(name=CONTAINER_NAME, can_connect=True)
    state_in = testing.State(
        containers={container},
        leader=True,
    )

    state_out = ctx.run(ctx.on.collect_unit_status(), state_in)

    assert state_out.unit_status == testing.BlockedStatus("Waiting for database relation")


def test_storage_attached():
    ctx = testing.Context(UbuntuInsightsCharm)
    storage = testing.Storage("reports-cache")
    container = testing.Container(name=CONTAINER_NAME, can_connect=True)

    state_in = testing.State(
        containers={container},
        storages={storage},
        leader=True,
    )

    state_out = ctx.run(ctx.on.storage_attached(storage), state_in)

    assert state_out.get_container(container.name).layers[container.name].services[
        ServiceType.WEB.value
    ].command == (
        f"ubuntu-insights-web-service --listen-port=8080 "
        f"--daemon-config={WEB_DYNAMIC_PATH} --reports-dir={REPORTS_CACHE_MOUNT_LOCATION}"
    )
