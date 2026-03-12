"""Tests for Spotifone menu/home UI helpers."""

import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from menu_ui import BORDER, PURPLE, SURFACE, TEXT, TEXT_DIM, Host, MenuUI, build_host_state, probe_live_hosts


class TestBuildHostState(unittest.TestCase):

    def test_connected_hosts_are_always_live(self):
        hosts = [Host(mac="AA:AA:AA:AA:AA:AA", name="MacBook")]
        result = build_host_state(hosts, {"AA:AA:AA:AA:AA:AA"}, set())
        self.assertEqual(len(result), 1)
        self.assertTrue(result[0].connected)
        self.assertTrue(result[0].live)

    def test_live_hosts_sort_ahead_of_offline_hosts(self):
        hosts = [
            Host(mac="BB:BB:BB:BB:BB:BB", name="Office PC"),
            Host(mac="AA:AA:AA:AA:AA:AA", name="MacBook"),
        ]
        result = build_host_state(hosts, set(), {"AA:AA:AA:AA:AA:AA"})
        self.assertEqual([host.name for host in result], ["MacBook", "Office PC"])
        self.assertTrue(result[0].live)
        self.assertFalse(result[1].live)


class TestProbeLiveHosts(unittest.TestCase):

    @patch("menu_ui.run_cmd")
    def test_probe_live_hosts_filters_to_remembered_hosts(self, mock_run):
        mock_run.return_value = (
            0,
            "[NEW] Device AA:AA:AA:AA:AA:AA MacBook\n"
            "[NEW] Device CC:CC:CC:CC:CC:CC Stranger\n",
        )
        result = probe_live_hosts({"AA:AA:AA:AA:AA:AA", "BB:BB:BB:BB:BB:BB"}, timeout_s=3)
        self.assertEqual(result, {"AA:AA:AA:AA:AA:AA"})


class TestMenuUIHomeTap(unittest.TestCase):

    def test_home_surface_tap_routes_to_host_switch(self):
        ui = MenuUI()
        ui.visible = False
        ui.hosts = [Host(mac="AA:AA:AA:AA:AA:AA", name="MacBook", live=True)]

        with patch.object(ui, "_attempt_connect_device") as mock_connect:
            ui.on_tap(40, 180)

        mock_connect.assert_called_once_with("AA:AA:AA:AA:AA:AA")

    def test_home_layout_uses_two_columns(self):
        ui = MenuUI()
        ui.hosts = [
            Host(mac="AA:AA:AA:AA:AA:AA", name="One", live=True),
            Host(mac="BB:BB:BB:BB:BB:BB", name="Two", live=True),
        ]

        layout = ui._home_layout()

        self.assertEqual(len(layout), 2)
        self.assertEqual(layout[0][2], layout[1][2])
        self.assertNotEqual(layout[0][1], layout[1][1])
        self.assertLess(layout[1][1] + layout[1][3], ui._home_legend_layout()[0][1])

    def test_home_surface_right_tile_tap_routes_to_second_host(self):
        ui = MenuUI()
        ui.visible = False
        ui.hosts = [
            Host(mac="AA:AA:AA:AA:AA:AA", name="Left", live=True),
            Host(mac="BB:BB:BB:BB:BB:BB", name="Right", live=True),
        ]
        right_tile = ui._home_layout()[1]

        with patch.object(ui, "_attempt_connect_device") as mock_connect:
            ui.on_tap(right_tile[1] + 10, right_tile[2] + 10)

        mock_connect.assert_called_once_with("BB:BB:BB:BB:BB:BB")


class TestMenuUIHomeStyle(unittest.TestCase):

    def test_connected_host_uses_purple_border(self):
        ui = MenuUI()
        border, fill, name = ui._home_tile_style(Host(mac="AA", name="MacBook", connected=True, live=True))
        self.assertEqual(border, PURPLE)
        self.assertEqual(fill, SURFACE)
        self.assertEqual(name, TEXT)

    def test_offline_host_uses_default_border(self):
        ui = MenuUI()
        border, fill, name = ui._home_tile_style(Host(mac="AA", name="MacBook", connected=False, live=False))
        self.assertEqual(border, BORDER)
        self.assertEqual(fill, BORDER)
        self.assertEqual(name, TEXT_DIM)

    def test_home_legend_contains_requested_items(self):
        ui = MenuUI()
        labels = [item[0] for item in ui._home_legend_layout()]
        self.assertEqual(labels, ["Menu", "Left", "Enter", "Right", "Del"])

    def test_home_legend_uses_vertical_rail(self):
        ui = MenuUI()
        legend = {label: (x, y, w, h) for label, x, y, w, h in ui._home_legend_layout()}
        self.assertLess(legend["Menu"][1], legend["Left"][1])
        self.assertLess(legend["Left"][1], legend["Enter"][1])
        self.assertLess(legend["Enter"][1], legend["Right"][1])
        self.assertLess(legend["Right"][1], legend["Del"][1])
        self.assertLessEqual(abs(legend["Left"][0] - legend["Right"][0]), 4)
        self.assertLessEqual(abs(legend["Menu"][0] - legend["Left"][0]), 4)
        self.assertLessEqual(abs(legend["Right"][0] - legend["Del"][0]), 4)
        self.assertLessEqual(legend["Left"][2], 58)
        self.assertLessEqual(legend["Right"][2], 58)
        self.assertLessEqual(legend["Del"][2], 58)
        self.assertLessEqual(legend["Enter"][2], 60)
        self.assertLessEqual(legend["Menu"][2], 58)
        self.assertGreaterEqual(legend["Left"][0], 416)
        self.assertLessEqual(legend["Menu"][1], 132)
        self.assertGreaterEqual(legend["Del"][1], 380)


if __name__ == "__main__":
    unittest.main()
