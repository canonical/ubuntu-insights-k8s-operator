#!/usr/bin/env python3
# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

"""Go Charm entrypoint."""

import json
import logging
import typing
from enum import Enum

import ops
import requests
from charms.data_platform_libs.v0.data_interfaces import (
    DatabaseCreatedEvent,
    DatabaseEndpointsChangedEvent,
    DatabaseRequires,
)

logger = logging.getLogger(__name__)


class ServiceType(Enum):
    """Enum for service types."""

    WEB = "web-service"
    INGEST = "ingest-service"


APP_NAME = "ubuntu-insights"
CONTAINER_NAME = "ubuntu-insights-server"
REPORTS_CACHE_NAME = "reports-cache"

WEB_DYNAMIC_PATH = "/etc/ubuntu-insights-service/web-live-config.json"
INGEST_DYNAMIC_PATH = "/etc/ubuntu-insights-service/ingest-live-config.json"
INGEST_DATABASE_NAME = "insights"

DATABASE_RELATION_NAME = "database"

MIGRATIONS_PATH = "/usr/share/insights/migrations"
MIGRATIONS_DONE_FLAG = "/run/ubuntu-insights-k8s-operator/migrations_done"

LEGACY_VERSIONS = {
    "18.04",
    "18.10",
    "20.04",
    "20.10",
    "21.04",
    "21.10",
    "22.04",
    "22.10",
    "23.04",
    "23.10",
    "24.04",
    "24.10",
    "25.04",
    "25.10",
}


class UbuntuInsightsCharm(ops.CharmBase):
    """Go Charm service."""

    web_apps: list[str] = []
    ingest_apps: list[str] = []

    def __init__(self, *args: typing.Any) -> None:
        """Initialize the instance.

        Args:
            args: passthrough to CharmBase.
        """
        super().__init__(*args)

        self.container = self.unit.get_container(CONTAINER_NAME)

        self.framework.observe(self.on.start, self._on_pebble_ready)
        self.framework.observe(self.on.ubuntu_insights_server_pebble_ready, self._on_pebble_ready)
        self.framework.observe(self.on.config_changed, self._on_config_changed)
        self.framework.observe(self.on.collect_unit_status, self._on_collect_status)

        # The 'relation_name' comes from the 'charmcraft.yaml file'.
        # The 'database_name' is the name of the database that our application requires.
        self.database = DatabaseRequires(
            self, relation_name=DATABASE_RELATION_NAME, database_name=INGEST_DATABASE_NAME
        )

        # See https://charmhub.io/data-platform-libs/libraries/data_interfaces
        self.framework.observe(self.database.on.database_created, self._on_database_created)
        self.framework.observe(
            self.database.on.endpoints_changed, self._on_database_endpoints_changed
        )
        self.framework.observe(
            self.on[DATABASE_RELATION_NAME].relation_broken,
            self._on_database_relation_broken,
        )

        self.framework.observe(
            self.on.reports_cache_storage_attached, self._on_storage_state_changed
        )
        self.framework.observe(
            self.on.reports_cache_storage_detaching, self._on_storage_state_changed
        )

    def _on_collect_status(self, event: ops.CollectStatusEvent) -> None:
        if not self.model.get_relation(DATABASE_RELATION_NAME):
            # We need the user to do 'juju integrate'.
            event.add_status(ops.BlockedStatus("Waiting for database relation"))
        elif not self.fetch_postgres_relation_data():
            # We need the charms to finish integrating.
            event.add_status(ops.WaitingStatus("Waiting for database relation"))

        container_meta = self.framework.meta.containers.get(CONTAINER_NAME, None)
        if container_meta is None:
            event.add_status(ops.BlockedStatus("Container metadata not found"))
        elif (
            REPORTS_CACHE_NAME not in container_meta.mounts
            or not container_meta.mounts[REPORTS_CACHE_NAME].location
        ):
            event.add_status(ops.BlockedStatus("Waiting for reports cache storage mount"))

        try:
            web_status = self.container.get_service(ServiceType.WEB.value)
            ingest_status = self.container.get_service(ServiceType.INGEST.value)
        except (ops.pebble.APIError, ops.pebble.ConnectionError, ops.ModelError):
            event.add_status(ops.MaintenanceStatus("Waiting for Pebble in workload container"))
        else:
            if not web_status.is_running():
                event.add_status(ops.MaintenanceStatus("Waiting for the web service to start up"))

            if not ingest_status.is_running():
                event.add_status(
                    ops.MaintenanceStatus("Waiting for the ingest service to start up")
                )

        if self.config["migrate"] and not self._is_migrations_done():
            event.add_status(ops.MaintenanceStatus("Waiting for database migrations to complete"))

        event.add_status(ops.ActiveStatus())

    def _on_config_changed(self, _: ops.EventBase) -> None:
        """Handle configuration changes."""
        self.web_apps = [item.strip() for item in str(self.config["web-apps"]).split(",")]
        self.ingest_apps = [item.strip() for item in str(self.config["ingest-apps"]).split(",")]

        # Write dynamic config files for web and ingest services.
        self._render_dynamic_config(ServiceType.WEB)
        self._render_dynamic_config(ServiceType.INGEST)

        # Migrate the database if the database relation is created.
        if (
            self.model.get_relation(DATABASE_RELATION_NAME)
            and self.config["migrate"]
            and not self._is_migrations_done()
        ):
            self._execute_migrations()

        # Restart the services to apply the new configurations.
        self._update_layer_and_replan()

    def _on_pebble_ready(self, event: ops.PebbleReadyEvent) -> None:
        self._on_config_changed(event)

    @property
    def ingest_environment(self) -> dict[str, str]:
        """Environment variables for the ingest service."""
        key_prefix = "UBUNTU_INSIGHTS_INGEST_SERVICE_"

        db_data = self.fetch_postgres_relation_data()
        if not db_data:
            return {}
        env = {
            key: value
            for key, value in {
                f"{key_prefix}DBCONFIG_HOST": db_data.get("db_host", None),
                f"{key_prefix}DBCONFIG_PORT": db_data.get("db_port", None),
                f"{key_prefix}DBCONFIG_USER": db_data.get("db_username", None),
                f"{key_prefix}DBCONFIG_PASSWORD": db_data.get("db_password", None),
                f"{key_prefix}DBCONFIG_DBNAME": INGEST_DATABASE_NAME,
            }.items()
            if value is not None
        }
        return env

    @property
    def report_cache_path(self) -> str:
        """Path to the reports cache directory.

        If the reports cache mount or container metadata is not found,
        an error is logged and an empty string is returned.
        """
        container_meta = self.framework.meta.containers.get(CONTAINER_NAME, None)
        if container_meta is None:
            logger.error("Failed to get container metadata for %s", CONTAINER_NAME)
            return ""

        if REPORTS_CACHE_NAME not in container_meta.mounts:
            logger.error("Mount '%s' not found in container metadata", REPORTS_CACHE_NAME)
            return ""

        return container_meta.mounts[REPORTS_CACHE_NAME].location

    def _on_database_created(self, _: DatabaseCreatedEvent) -> None:
        self._mark_migrations(done=False)
        self._execute_migrations()
        self._update_layer_and_replan()

    def _on_database_endpoints_changed(self, _: DatabaseEndpointsChangedEvent) -> None:
        self._mark_migrations(done=False)
        self._execute_migrations()
        self._update_layer_and_replan()

    def _on_database_relation_broken(self, _: ops.RelationBrokenEvent) -> None:
        """Handle the database relation being broken."""
        self._stop_service(ServiceType.INGEST)

    def _update_layer_and_replan(self) -> None:
        ops.MaintenanceStatus("Assembling Pebble layers")
        try:
            self.container.add_layer(self.container.name, self._pebble_layer, combine=True)
            logger.info(f"Added updated layer '{self.container.name}' to Pebble plan.")

            self.container.pebble.replan_services()
            logger.info(f"Replanned Pebble container '{self.container.name}'.")
        except (ops.pebble.APIError, ops.pebble.ConnectionError) as e:
            logger.info("Unable to connect to Pebble: %s", e)
            return

        self.unit.set_workload_version(self.version)

    def fetch_postgres_relation_data(self) -> dict[str, str]:
        """Fetch postgres relation data.

        This function retrieves relation data from a postgres database using
        the `fetch_relation_data` method of the `database` object. The retrieved data is
        then logged for debugging purposes, and any non-empty data is processed to extract
        endpoint information, username, and password. This processed data is then returned as
        a dictionary. If no data or incomplete data is found, an empty dictionary is returned.
        """
        relations = self.database.fetch_relation_data()
        logger.debug("Got following database data: %s", relations)
        for data in relations.values():
            if not data:
                continue
            if not data.get("endpoints"):
                # Database relation data is incomplete.
                continue

            endpoints = data["endpoints"].split(",")
            if len(endpoints) < 1:
                return {}

            primary_endpoint = endpoints[0].split(":")
            logger.info("New PSQL database endpoint is %s", primary_endpoint)

            host, port = primary_endpoint
            db_data = {
                "db_host": host,
                "db_port": port,
                "db_username": data.get("username"),
                "db_password": data.get("password"),
                "db_name": data.get("database"),
            }

            if None in (
                db_data["db_username"],
                db_data["db_password"],
                db_data["db_name"],
            ):
                return {}

            return db_data
        return {}

    def _render_dynamic_config(self, service_type: ServiceType) -> None:
        """Write dynamic configuration file for the specified service.

        Args:
            service_type: ServiceType enum specifying which service config to write.
        """
        if service_type == ServiceType.WEB:
            config_path = WEB_DYNAMIC_PATH
            legacy = self.config["web-legacy"]
            allowlist = self.web_apps
        elif service_type == ServiceType.INGEST:
            config_path = INGEST_DYNAMIC_PATH
            allowlist = self.ingest_apps
            legacy = self.config["ingest-legacy"]

        if legacy:
            allowlist.extend(
                f"ubuntu-report/ubuntu/desktop/{version}" for version in LEGACY_VERSIONS
            )

        config_data = {"allowList": allowlist}

        try:
            config_json = json.dumps(config_data, indent=2)
            self.container.push(config_path, config_json, make_dirs=True)
            logger.info(
                "Written dynamic config for %s service to %s", service_type.value, config_path
            )
        except (ops.pebble.APIError, ops.pebble.ConnectionError) as e:
            logger.error("Failed to write config file %s: %s", config_path, e)

    def _on_storage_state_changed(self, event: ops.EventBase) -> None:
        if self.report_cache_path:
            self._on_config_changed(event)
            return

    @property
    def _pebble_layer(self) -> ops.pebble.Layer:
        """Pebble layer for the web service."""
        web_command = " ".join(
            [
                "ubuntu-insights-web-service",
                f"--listen-port={self.config['web-port']}",
                f"--daemon-config={WEB_DYNAMIC_PATH}",
                f"--reports-dir={self.report_cache_path}",
            ]
        )

        ingest_command = " ".join(
            [
                "ubuntu-insights-ingest-service",
                f"--daemon-config={INGEST_DYNAMIC_PATH}",
                f"--reports-dir={self.report_cache_path}",
            ]
        )

        pebble_layer: ops.pebble.LayerDict = {
            "summary": "{APP_NAME} layer",
            "description": "pebble config layer for Ubuntu Insights server services",
            "services": {
                ServiceType.WEB.value: {
                    "override": "replace",
                    "summary": "web service",
                    "command": web_command,
                    "startup": "enabled",
                },
                ServiceType.INGEST.value: {
                    "override": "replace",
                    "summary": "ingest service",
                    "command": ingest_command,
                    "startup": "enabled" if self.fetch_postgres_relation_data() else "disabled",
                    "environment": self.ingest_environment,
                },
            },
            "checks": {
                "web-service-ready": {
                    "override": "replace",
                    "level": "ready",
                    "http": {"url": f"http://localhost:{self.config['web-port']}/version"},
                },
            },
        }
        return ops.pebble.Layer(pebble_layer)

    @property
    def version(self) -> str:
        """Returns the current workload version via the web-service version endpoint."""
        try:
            if self.container.get_services(ServiceType.WEB.value):
                return self._request_version()
        except Exception as e:
            logger.warning("Unable to get version from web service API: %s", str(e), exc_info=True)
        return ""

    def _request_version(self) -> str:
        """Fetch the version from the running workload using the API."""
        resp = requests.get(f"http://localhost:{self.config['web-port']}/version", timeout=10)
        return resp.json()["version"]

    def _execute_migrations(self) -> None:
        """Run database migrations."""
        if not self.fetch_postgres_relation_data() or not self.container.can_connect():
            logger.info("Not ready to execute migrations.")
            return

        self.unit.status = ops.MaintenanceStatus("Running database migrations")
        try:
            process = self.container.exec(
                ["ubuntu-insights-ingest-service", "migrate", MIGRATIONS_PATH],
                environment=self.ingest_environment,
                timeout=120,
            )
            stdout, _ = process.wait_output()
            logger.info(stdout)
        except ops.pebble.ExecError as e:
            logger.exception(
                "Failed to run database migrations, exited with code. %d. Stderr:", e.exit_code
            )
            if not e.stderr:
                return

            for line in e.stderr.splitlines():
                logger.exception("    %s", line)
            return

        self._mark_migrations(done=True)

    def _stop_service(self, service: ServiceType) -> None:
        """Stop a service in the container."""
        if (
            self.container.can_connect()
            and service.value in self.container.get_plan().services
            and self.container.get_service(service.value).is_running()
        ):
            logger.info("Stopping %s service", service.value)

            try:
                self.container.stop(service.value)
            except ops.pebble.APIError as e:
                logger.error("Failed to stop %s service: %s", service.value, e)

    def _is_migrations_done(self) -> bool:
        """Check if migrations have been done."""
        return self.container.can_connect() and self.container.exists(MIGRATIONS_DONE_FLAG)

    def _mark_migrations(self, done: bool) -> None:
        """Mark migrations as done."""
        if not self.container.can_connect():
            return

        try:
            if done:
                self.container.push(MIGRATIONS_DONE_FLAG, "", make_dirs=True)
                logger.info("Marked migrations as done")
            else:
                self.container.remove_path(MIGRATIONS_DONE_FLAG, recursive=True)
                logger.info("Removed migrations done marker")
        except ops.pebble.APIError as e:
            logger.error("Failed to mark migrations: %s", e)


if __name__ == "__main__":
    ops.main(UbuntuInsightsCharm)
