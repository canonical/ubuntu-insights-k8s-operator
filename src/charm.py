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
)
from charms.grafana_k8s.v0.grafana_dashboard import GrafanaDashboardProvider
from charms.loki_k8s.v1.loki_push_api import LogForwarder
from charms.nginx_ingress_integrator.v0.nginx_route import require_nginx_route
from charms.prometheus_k8s.v0.prometheus_scrape import MetricsEndpointProvider
from charms.rolling_ops.v0.rollingops import RollingOpsManager

from database import DatabaseHandler

logger = logging.getLogger(__name__)


class ServiceType(Enum):
    """Enum for service types."""

    WEB = "web-service"
    INGEST = "ingest-service"


APP_NAME = "ubuntu-insights"
CONTAINER_NAME = "ubuntu-insights-server"
REPORTS_CACHE_NAME = "reports-cache"

WEB_ALLOWLIST_PATH = "/etc/ubuntu-insights-service/web-allowlist.json"
INGEST_ALLOWLIST_PATH = "/etc/ubuntu-insights-service/ingest-allowlist.json"
INGEST_DATABASE_NAME = "insights"

WEB_PROMETHEUS_PORT = 2112
INGEST_PROMETHEUS_PORT = 2113

DATABASE_RELATION_NAME = "database"

MIGRATIONS_PATH = "/usr/share/insights/migrations"

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
    "26.04",
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
        self.framework.observe(self.on.upgrade_charm, self._on_upgrade_charm)
        self.framework.observe(self.on.leader_elected, self._on_leader_elected)
        self.framework.observe(self.on.ubuntu_insights_server_pebble_ready, self._on_pebble_ready)
        self.framework.observe(self.on.config_changed, self._on_config_changed)
        self.framework.observe(self.on.collect_unit_status, self._on_collect_status)

        self._init_relations()
        self._init_events()
        self._init_cos()

        # Rolling restarts
        self.restart_manager = RollingOpsManager(
            charm=self, relation="restart", callback=self._on_restart
        )

    def _init_relations(self):
        # The 'relation_name' comes from the 'charmcraft.yaml file'.
        self._database = DatabaseHandler(self, DATABASE_RELATION_NAME)

        self._require_nginx_route()

    def _init_events(self):
        self.framework.observe(
            self._database.database.on.database_created, self._on_database_created
        )
        self.framework.observe(
            self._database.database.on.endpoints_changed, self._on_database_endpoints_changed
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

    def _init_cos(self):
        self._logging = LogForwarder(self, relation_name="logging")
        self._metrics_endpoint = MetricsEndpointProvider(
            self,
            jobs=[
                {
                    "static_configs": [
                        {
                            "targets": [
                                f"*:{WEB_PROMETHEUS_PORT}",
                                f"*:{INGEST_PROMETHEUS_PORT}",
                            ]
                        }
                    ]
                }
            ],
        )
        self._grafana_dashboards = GrafanaDashboardProvider(
            self, relation_name="grafana-dashboard"
        )

    def _on_collect_status(self, event: ops.CollectStatusEvent) -> None:
        if not self.model.get_relation(DATABASE_RELATION_NAME):
            # We need the user to do 'juju integrate'.
            event.add_status(ops.BlockedStatus("Waiting for database relation"))
        elif not self._database.is_relation_ready():
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

        event.add_status(ops.ActiveStatus())

    def _on_pebble_ready(self, event: ops.PebbleReadyEvent) -> None:
        self._execute_migrations()
        self._on_config_changed(event)

    def _on_upgrade_charm(self, _: ops.EventBase) -> None:
        """Handle charm upgrade events."""
        assert type(self.restart_manager.name) is str
        self.on[self.restart_manager.name].acquire_lock.emit()

    def _on_leader_elected(self, _: ops.LeaderElectedEvent) -> None:
        """Retry database migrations when this unit becomes leader."""
        self._execute_migrations()

    def _on_config_changed(self, _: ops.EventBase) -> None:
        """Handle configuration changes."""
        self.web_apps = [item.strip() for item in str(self.config["web-apps"]).split(",")]
        self.ingest_apps = [item.strip() for item in str(self.config["ingest-apps"]).split(",")]

        # Write allowlist config files for web and ingest services.
        self._render_allowlist(ServiceType.WEB)
        self._render_allowlist(ServiceType.INGEST)

        # Expose web service port
        self.unit.set_ports(typing.cast(int, self.config["web-port"]))

        # Update nginx route config
        self._require_nginx_route()

        # Restart the services to apply the new configurations.
        self._update_layer_and_replan()

    @property
    def ingest_environment(self) -> dict[str, str]:
        """Environment variables for the ingest service."""
        key_prefix = "UBUNTU_INSIGHTS_INGEST_SERVICE_"
        if not self._database.is_relation_ready():
            return {}

        db_data = self._database.get_relation_data()
        if not db_data:
            return {}

        return {
            f"{key_prefix}DBCONFIG_HOST": db_data.host,
            f"{key_prefix}DBCONFIG_PORT": db_data.port,
            f"{key_prefix}DBCONFIG_USER": db_data.user,
            f"{key_prefix}DBCONFIG_PASSWORD": db_data.password,
            f"{key_prefix}DBCONFIG_DBNAME": db_data.db_name,
        }

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
        self._execute_migrations()
        self._update_layer_and_replan()

    def _on_database_endpoints_changed(self, _: DatabaseEndpointsChangedEvent) -> None:
        self._execute_migrations()
        self._update_layer_and_replan()

    def _on_database_relation_broken(self, _: ops.RelationBrokenEvent) -> None:
        """Handle the database relation being broken."""
        self._stop_service(ServiceType.INGEST)

    def _on_restart(self, event: ops.EventBase) -> None:
        """Handle rolling restart requests."""
        self._on_config_changed(event)

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

    def _require_nginx_route(self) -> None:
        require_nginx_route(
            charm=self,
            service_hostname=str(self.config["external-hostname"]) or self.app.name,
            service_name=self.app.name,
            service_port=int(self.config["web-port"]),
            max_body_size=1,
        )

    def _render_allowlist(self, service_type: ServiceType) -> None:
        """Write the allowlist configuration file for the specified service.

        Args:
            service_type: ServiceType enum specifying which service config to write.
        """
        if service_type == ServiceType.WEB:
            config_path = WEB_ALLOWLIST_PATH
            legacy = self.config["web-legacy"]
            allowlist = self.web_apps
        elif service_type == ServiceType.INGEST:
            config_path = INGEST_ALLOWLIST_PATH
            allowlist = self.ingest_apps
            legacy = self.config["ingest-legacy"]

        if legacy:
            allowlist = allowlist.copy()  # Make sure the original list is not modified
            allowlist.extend(
                f"ubuntu-report/ubuntu/desktop/{version}" for version in LEGACY_VERSIONS
            )

        allowlist_data = {"allowList": allowlist}

        try:
            allowlist_json = json.dumps(allowlist_data, indent=2)
            self.container.push(config_path, allowlist_json, make_dirs=True)
            logger.info(
                "Written allowlist config for %s service to %s", service_type.value, config_path
            )
        except (ops.pebble.APIError, ops.pebble.ConnectionError) as e:
            logger.error("Failed to write config file %s: %s", config_path, e)

    def _is_allowlist_rendered(self, service_type: ServiceType) -> bool:
        """Check if the allowlist configuration is ready for the specified service.

        Args:
            service_type: ServiceType enum specifying which service to check.

        Returns:
            True if the allowlist configuration is ready, False otherwise.
        """
        match service_type:
            case ServiceType.WEB:
                config_path = WEB_ALLOWLIST_PATH
            case ServiceType.INGEST:
                config_path = INGEST_ALLOWLIST_PATH

        return self.container.can_connect() and self.container.exists(config_path)

    def _on_storage_state_changed(self, event: ops.StorageEvent) -> None:
        if self.report_cache_path:
            self._on_config_changed(event)
            return

        # Storage is not available, stop the services.
        logger.critical("Reports cache storage is not available, stopping workloads.")
        self._stop_service(ServiceType.WEB)
        self._stop_service(ServiceType.INGEST)

    @property
    def _pebble_layer(self) -> ops.pebble.Layer:
        """Pebble layer for the web service."""
        debug = "-vv" if self.config["debug"] else "-v"

        web_command = " ".join(
            [
                "/bin/ubuntu-insights-web-service",
                WEB_ALLOWLIST_PATH,
                f"--listen-port={self.config['web-port']}",
                f"--reports-dir={self.report_cache_path}",
                f"--metrics-port={WEB_PROMETHEUS_PORT}",
                "--json-logs",
                debug,
            ]
        ).strip()

        ingest_command = " ".join(
            [
                "/bin/ubuntu-insights-ingest-service",
                INGEST_ALLOWLIST_PATH,
                f"--reports-dir={self.report_cache_path}",
                f"--metrics-port={INGEST_PROMETHEUS_PORT}",
                "--json-logs",
                debug,
            ]
        ).strip()

        # If the reports cache path is unavailable, disable the web and ingest services.
        # If the database relation is not ready, disable the ingest service.
        web_startup = ingest_startup = "enabled"

        if not self._is_allowlist_rendered(ServiceType.WEB) or not self.report_cache_path:
            web_startup = "disabled"
            logger.warning("Web service allowlist has not been rendered, web service is disabled.")

        if not self._is_allowlist_rendered(ServiceType.INGEST) or not self.report_cache_path:
            ingest_startup = "disabled"
            logger.warning(
                "Ingest service allowlist has not been rendered, ingest service is disabled."
            )

        if not self._database.is_relation_ready():
            ingest_startup = "disabled"
            logger.warning("Database relation is not ready, ingest service is disabled.")

        web_startup = "enabled" if self.report_cache_path else "disabled"
        ingest_startup = (
            "enabled"
            if self.report_cache_path and self._database.is_relation_ready()
            else "disabled"
        )

        pebble_layer: ops.pebble.LayerDict = {
            "summary": f"{APP_NAME} layer",
            "description": "pebble config layer for Ubuntu Insights server services",
            "services": {
                ServiceType.WEB.value: {
                    "override": "replace",
                    "summary": "web service",
                    "command": web_command,
                    "startup": web_startup,
                },
                ServiceType.INGEST.value: {
                    "override": "replace",
                    "summary": "ingest service",
                    "command": ingest_command,
                    "startup": ingest_startup,
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
        """Return the current workload version via the web-service version endpoint."""
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
        if not self.unit.is_leader():
            logger.debug("Skipping non-leader database migration: %s", self.unit.name)
            return

        if not self._database.is_relation_ready() or not self.container.can_connect():
            logger.info("Not ready to execute migrations.")
            return

        self.unit.status = ops.MaintenanceStatus("Running database migrations")
        try:
            process = self.container.exec(
                ["/bin/ubuntu-insights-ingest-service", "migrate", MIGRATIONS_PATH],
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


if __name__ == "__main__":
    ops.main(UbuntuInsightsCharm)
