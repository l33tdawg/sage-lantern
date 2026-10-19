"""Tests for the read-only hardware inventory collector."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import scripts.lantern.hardware_inventory as inventory


def write(root, path, text):
    target = Path(root) / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")


class InventoryTests(unittest.TestCase):
    def test_collects_board_storage_usb_and_display(self):
        with tempfile.TemporaryDirectory() as tmp:
            write(tmp, "proc/device-tree/model", "Orange Pi 5 Plus\x00")
            write(tmp, "proc/device-tree/compatible", "rockchip,rk3588\x00")
            write(tmp, "proc/device-tree/serial-number", "SN123\x00")
            write(tmp, "proc/sys/kernel/hostname", "lantern-c\n")
            write(tmp, "proc/cpuinfo", "processor\t: 0\nprocessor\t: 1\n")
            write(tmp, "proc/meminfo", "MemTotal:       16384000 kB\n")
            write(tmp, "sys/block/nvme0n1/size", "500118192\n")
            write(tmp, "sys/block/nvme0n1/device/model", "Netac NVMe SSD 256GB\n")
            (Path(tmp) / "sys/block/nvme0n1/nvme0n1p1").mkdir(parents=True)
            write(tmp, "sys/block/nvme0n1/nvme0n1p1/partition", "1\n")
            write(tmp, "sys/bus/usb/devices/1-1/idVendor", "046d\n")
            write(tmp, "sys/bus/usb/devices/1-1/idProduct", "0825\n")
            write(tmp, "sys/bus/usb/devices/1-1/product", "C270\n")
            write(tmp, "sys/class/drm/card0-HDMI-A-1/status", "connected\n")
            write(tmp, "sys/class/drm/card0-HDMI-A-1/enabled", "enabled\n")
            (Path(tmp) / "dev").mkdir()
            (Path(tmp) / "dev/ttyS6").touch()
            (Path(tmp) / "dev/i2c-6").touch()

            data = inventory.collect(tmp)
            self.assertEqual(data["schema"], inventory.SCHEMA)
            self.assertEqual(data["host"]["model"], "Orange Pi 5 Plus")
            self.assertEqual(data["host"]["compatible"], ["rockchip,rk3588"])
            self.assertIsNone(data["host"]["serial"])
            self.assertEqual(data["cpu"]["processors"], 2)
            self.assertEqual(data["memory"]["mem_total_kb"], 16384000)
            self.assertEqual(data["storage"][0]["model"], "Netac NVMe SSD 256GB")
            self.assertEqual(data["storage"][0]["partitions"], ["nvme0n1p1"])
            self.assertEqual(data["usb"][0]["name"], "C270")
            self.assertEqual(data["serial_ports"], ["ttyS6"])
            self.assertEqual(data["i2c_buses"], ["i2c-6"])
            self.assertEqual(data["displays"][0]["status"], "connected")

    def test_serials_are_opt_in(self):
        with tempfile.TemporaryDirectory() as tmp:
            write(tmp, "proc/device-tree/serial-number", "SN123\x00")
            data = inventory.collect(tmp, include_serials=True)
            self.assertEqual(data["host"]["serial"], "SN123")

    def test_missing_root_is_empty_not_fatal(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = inventory.collect(tmp)
            self.assertEqual(data["storage"], [])
            self.assertEqual(data["usb"], [])
            self.assertIsNone(data["host"]["model"])


if __name__ == "__main__":
    unittest.main()
