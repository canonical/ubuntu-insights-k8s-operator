from enum import Enum


class ExampleReport(Enum):
    """Example report payloads."""

    OPT_OUT = {"optOut": True}
    BASIC = {
        "insightsVersion": "Dev",
        "collectionTime": 1748013676,
        "systemInfo": {
            "hardware": {
                "product": {
                    "family": "My Product Family",
                    "name": "My Product Name",
                    "vendor": "My Product Vendor",
                },
                "cpu": {
                    "name": "9 1200SX",
                    "vendor": "Authentic",
                    "architecture": "x86_64",
                    "cpus": 16,
                    "sockets": 1,
                    "coresPerSocket": 8,
                    "threadsPerCore": 2,
                },
                "gpus": [
                    {"device": "0x0294", "vendor": "0x10df", "driver": "gpu"},
                    {"device": "0x03ec", "vendor": "0x1003", "driver": "gpu"},
                ],
                "memory": {"size": 23247},
                "disks": [
                    {
                        "size": 1887436,
                        "type": "disk",
                        "children": [
                            {"size": 750, "type": "part"},
                            {"size": 260, "type": "part"},
                            {"size": 16, "type": "part"},
                            {"size": 1887436, "type": "part"},
                            {"size": 869, "type": "part"},
                            {"size": 54988, "type": "part"},
                        ],
                    }
                ],
                "screens": [
                    {
                        "physicalResolution": "2560x1440",
                        "size": "600mm x 340mm",
                        "refreshRate": "143.85",
                    },
                    {
                        "physicalResolution": "2560x1600",
                        "size": "300mm x 190mm",
                        "refreshRate": "120.00",
                    },
                ],
            },
            "software": {
                "os": {"family": "linux", "distribution": "Ubuntu", "version": "25.04"},
                "timezone": "EDT",
                "language": "en_US",
                "bios": {"vendor": "Bios Vendor", "version": "Bios Version"},
            },
            "platform": {
                "desktop": {
                    "desktopEnvironment": "ubuntu:GNOME",
                    "sessionName": "ubuntu",
                    "sessionType": "wayland",
                },
                "proAttached": True,
            },
        },
    }
    WITH_SOURCE_METRICS = {
        **BASIC,
        "sourceMetrics": {"some field": "some value"},
    }
    UBUNTU_REPORT = {
        "Version": "18.04",
        "OEM": {"Vendor": "Vendor Name", "Product": "4287CTO"},
        "BIOS": {"Vendor": "Vendor Name", "Version": "8DET52WW (1.27)"},
        "CPU": {
            "OpMode": "32-bit, 64-bit",
            "CPUs": "8",
            "Threads": "2",
            "Cores": "4",
            "Sockets": "1",
            "Vendor": "Genuine",
            "Family": "6",
            "Model": "158",
            "Stepping": "10",
            "Name": "Intius i5-8300H CPU @ 2.30GHz",
            "Virtualization": "VT-x",
        },
        "Arch": "amd64",
        "GPU": [{"Vendor": "8086", "Model": "0126"}],
        "RAM": 8,
        "Disks": [240.1, 500.1],
        "Partitions": [229.2, 479.7],
        "Screens": [
            {"Size": "277mmx156mm", "Resolution": "1366x768", "Frequency": "60.02"},
            {"Resolution": "1920x1080", "Frequency": "60.00"},
        ],
        "Autologin": False,
        "LivePatch": True,
        "Session": {"DE": "ubuntu:GNOME", "Name": "ubuntu", "Type": "x11"},
        "Language": "fr_FR",
        "Timezone": "Europe/Paris",
        "Install": {
            "Media": 'Ubuntu 18.04 LTS "Bionic Beaver" - Alpha amd64 (20180305)',
            "Type": "GTK",
            "PartitionMethod": "use_device",
            "DownloadUpdates": True,
            "Language": "fr",
            "Minimal": False,
            "RestrictedAddons": False,
            "Stages": {
                "0": "language",
                "3": "language",
                "10": "console_setup",
                "15": "prepare",
                "25": "partman",
                "27": "start_install",
                "37": "timezone",
                "49": "usersetup",
                "57": "user_done",
                "829": "done",
            },
        },
    }
