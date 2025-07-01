#!/usr/bin/env python3
# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

import logging

import jubilant
import requests

from tests.integration.helpers import ExampleReport

logger = logging.getLogger(__name__)


def test_active(app: str, juju: jubilant.Juju):
    """Check that the charm is active.

    Assume that the charm has been deployed and is running.
    """
    status = juju.status()
    assert status.apps[app].units[app + "/0"].is_active


def test_web_service_running(insights_address: str, requests_timeout: float):
    """Check that the web service is running.

    Ensure that it is responding to HTTP requests in an expected manner.
    """
    response = requests.get(f"{insights_address}/version", timeout=requests_timeout)
    assert response.status_code == 200

    response = requests.post(
        f"{insights_address}/upload/linux",
        json=ExampleReport.BASIC.value,
        timeout=requests_timeout,
    )
    assert response.status_code == 202

    response = requests.post(
        f"{insights_address}/upload/linux",
        json=ExampleReport.OPT_OUT.value,
        timeout=requests_timeout,
    )
    assert response.status_code == 202

    response = requests.post(
        f"{insights_address}/upload/ubuntu_desktop_provision",
        json=ExampleReport.WITH_SOURCE_METRICS.value,
        timeout=requests_timeout,
    )
    assert response.status_code == 202

    response = requests.post(
        f"{insights_address}/ubuntu/desktop/20.04",
        json=ExampleReport.UBUNTU_REPORT.value,
        timeout=requests_timeout,
    )
    assert response.status_code == 200

    # Bad application name
    response = requests.post(
        f"{insights_address}/upload/bad-app",
        json=ExampleReport.BASIC.value,
        timeout=requests_timeout,
    )
    assert response.status_code == 403

    # Bad payload
    response = requests.post(
        f"{insights_address}/upload/linux",
        data='{"bad":}',
        timeout=requests_timeout,
    )
    assert response.status_code == 400

    # Payload with extra root fields
    payload = ExampleReport.BASIC.value.copy()
    payload["extra_field"] = "extra_value"
    response = requests.post(
        f"{insights_address}/upload/linux",
        json=payload,
        timeout=requests_timeout,
    )
    assert response.status_code == 202


def test_db_state(juju: jubilant.Juju):
    """Attempt to connect to the database and check what is in it."""
    juju.wait(jubilant.all_active)

    task = juju.run("postgresql-k8s/0", "get-password", {"username": "operator"})
    db_pass = task.results["password"]

    def run_query(query: str) -> str:
        return juju.cli(
            "ssh",
            "--container",
            "postgresql",
            "postgresql-k8s/0",
            f'psql -h localhost -U operator \
              --password -d insights \
              -t -c "{query}"',
            stdin=db_pass + "\n",
        )

    def count_query(table: str, optout: bool) -> str:
        fields = [
            "insights_version",
            "collection_time",
            "hardware",
            "software",
            "platform",
            "source_metrics",
        ]

        if optout:
            # Create conditions checking that all fields are NULL
            null_conditions = " AND ".join([f"{field} IS NULL" for field in fields])
            return f"SELECT COUNT(*) FROM {table} WHERE optout = true AND ({null_conditions})"
        else:
            # For non-optout records, check that at least one field is NOT NULL
            not_null_conditions = " OR ".join([f"{field} IS NOT NULL" for field in fields])
            return f"SELECT COUNT(*) FROM {table} WHERE optout = false AND ({not_null_conditions})"

    # Check contents of the linux table
    linux_count = run_query("SELECT COUNT(*) FROM linux;")
    linux_optout_count = run_query(count_query("linux", True))
    linux_non_optout_count = run_query(count_query("linux", False))

    assert linux_count.strip() == "3"
    assert linux_optout_count.strip() == "1"
    assert linux_non_optout_count.strip() == "2"

    # Check contents of the ubuntu_desktop_provision table
    ubuntu_count = run_query("SELECT COUNT(*) FROM ubuntu_desktop_provision;")
    ubuntu_optout_count = run_query(count_query("ubuntu_desktop_provision", True))
    ubuntu_non_optout_count = run_query(count_query("ubuntu_desktop_provision", False))

    assert ubuntu_count.strip() == "1"
    assert ubuntu_optout_count.strip() == "0"
    assert ubuntu_non_optout_count.strip() == "1"

    # Check contents of ubuntu_report table
    ubuntu_report_count = run_query("SELECT COUNT(*) FROM ubuntu_report;")
    ubuntu_report_filtered_count = run_query(
        "SELECT COUNT(*) FROM ubuntu_report "
        "WHERE optout = false "
        "AND distribution = 'ubuntu' "
        "AND version = '20.04';"
    )

    assert ubuntu_report_count.strip() == "1"
    assert ubuntu_report_filtered_count.strip() == "1"

    # Check the contents of invalid_reports
    invalid_count = run_query("SELECT COUNT(*) FROM invalid_reports;")
    assert invalid_count.strip() == "1"


def test_database_relations(
    app: str,
    juju: jubilant.Juju,
    insights_address: str,
    requests_timeout: float,
):
    def ping_web_service():
        response = requests.get(f"{insights_address}/version", timeout=requests_timeout)
        return response.status_code == 200

    juju.wait(jubilant.all_active)
    assert ping_web_service()

    # Remove database relation
    juju.remove_relation(app, "postgresql-k8s:database")
    juju.wait(jubilant.any_blocked)
    assert ping_web_service()

    # Re-add database relation
    juju.integrate(app, "postgresql-k8s:database")
    juju.wait(jubilant.all_active)
    assert ping_web_service()


def test_config_changed(
    app: str,
    juju: jubilant.Juju,
    insights_address: str,
    requests_timeout: float,
):
    """Check that the charm reacts to config changes."""
    juju.wait(jubilant.all_active)

    # Change the config, ensure that the changes are applied
    juju.config(app, {"web-apps": "linux"})

    juju.wait(
        lambda status: requests.post(
            f"{insights_address}/upload/windows",
            json=ExampleReport.OPT_OUT.value,
            timeout=requests_timeout,
        ).status_code
        == 403
        and jubilant.all_active(status, app)
    )
