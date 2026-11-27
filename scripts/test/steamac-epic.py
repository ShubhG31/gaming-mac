#!/usr/bin/env python3
"""Unit tests for guest/layer/usr/lib/steamac/steamac-epic (no Flatpak/VM needed)."""

import importlib.machinery
import importlib.util
import json
import pathlib
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "guest/layer/usr/lib/steamac/steamac-epic"
LOADER = importlib.machinery.SourceFileLoader("steamac_epic", str(SCRIPT))
SPEC = importlib.util.spec_from_loader(LOADER.name, LOADER)
MOD = importlib.util.module_from_spec(SPEC)
LOADER.exec_module(MOD)


class SteamacEpicTests(unittest.TestCase):
    def test_parse_gpu_budget_validation(self):
        with tempfile.TemporaryDirectory() as td:
            p = pathlib.Path(td) / "cmdline"
            p.write_text("console=hvc0 steamac.gpu_mib=8192 rootwait\n", encoding="utf-8")
            self.assertEqual(MOD.parse_gpu_budget_mib(p), 8192)
            p.write_text("steamac.gpu_mib=128\n", encoding="utf-8")
            self.assertIsNone(MOD.parse_gpu_budget_mib(p))
            p.write_text("steamac.gpu_mib=999999\n", encoding="utf-8")
            self.assertIsNone(MOD.parse_gpu_budget_mib(p))
            p.write_text("steamac.gpu_mib=$(id)\n", encoding="utf-8")
            self.assertIsNone(MOD.parse_gpu_budget_mib(p))

    def test_dxmt_config_preserves_user_overrides(self):
        self.assertEqual(
            MOD.build_dxmt_config("", gpu_mib=10240),
            "d3d11.cpuCachedDynamicResources=True;dxgi.customVideoMemory=10240",
        )
        self.assertEqual(
            MOD.build_dxmt_config("dxgi.customVideoMemory=4096", gpu_mib=10240),
            "d3d11.cpuCachedDynamicResources=True;dxgi.customVideoMemory=10240;dxgi.customVideoMemory=4096",
        )

    def test_build_game_env_precedence_and_sanitization(self):
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            rt = root / "runtime"
            cfg = root / "config"
            (rt / "steam/env").mkdir(parents=True)
            (cfg / "steam/env").mkdir(parents=True)
            (rt / "steam/env/gfx.txt").write_text(
                "# comment\n"
                "VK_VRAM_REPORT_LIMIT_DEVICE_ID=106b:1234\n"
                "VK_VRAM_REPORT_LIMIT_HEAP_SIZE=8192\n"
                "bad_lower=ignored\n",
                encoding="utf-8",
            )
            (cfg / "steam/env/gfx.txt").write_text(
                "VK_VRAM_REPORT_LIMIT_HEAP_SIZE=6144\n",
                encoding="utf-8",
            )
            cmdline = root / "cmdline"
            cmdline.write_text("steamac.gpu_mib=8192\n", encoding="utf-8")

            env = MOD.build_game_env(
                base_env={"DXMT_CONFIG": "d3d11.metalSpatialUpscaleFactor=2"},
                runtime_dir=rt,
                config_home=cfg,
                cmdline_path=cmdline,
            )
            self.assertEqual(env["VK_VRAM_REPORT_LIMIT_DEVICE_ID"], "106b:1234")
            self.assertEqual(env["VK_VRAM_REPORT_LIMIT_HEAP_SIZE"], "6144")
            self.assertNotIn("bad_lower", env)
            self.assertEqual(env["FEX_X87REDUCEDPRECISION"], "1")
            self.assertEqual(env["PROTON_USE_NTSYNC"], "1")
            self.assertTrue(env["DXMT_CONFIG"].endswith("d3d11.metalSpatialUpscaleFactor=2"))
            self.assertIn("dxgi.customVideoMemory=8192", env["DXMT_CONFIG"])

    def test_discover_and_select_proton(self):
        with tempfile.TemporaryDirectory() as td:
            steam = pathlib.Path(td)
            for rel in [
                "steamapps/common/Proton 10.0/proton",
                "steamapps/common/Proton - Experimental/proton",
                "steamapps/common/Proton 11.0/proton",
                "compatibilitytools.d/steamac-proton-dxmt-arm64ec/proton",
            ]:
                p = steam / rel
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text("#!/bin/sh\n", encoding="utf-8")

            vulkan_pick = MOD.select_best_proton(steam, prefer_dxmt=False)
            self.assertEqual(vulkan_pick["name"], "Proton 11.0")
            dxmt_pick = MOD.select_best_proton(steam, prefer_dxmt=True)
            self.assertEqual(dxmt_pick["name"], "steamac-proton-dxmt-arm64ec")

    def test_seed_heroic_config_is_non_destructive(self):
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            steam = root / "Steam"
            p = steam / "steamapps/common/Proton 11.0/proton"
            p.parent.mkdir(parents=True)
            p.write_text("#!/bin/sh\n", encoding="utf-8")
            cfg = root / "heroic/config.json"

            self.assertTrue(MOD.seed_heroic_config(cfg, steam_root=steam))
            data = json.loads(cfg.read_text(encoding="utf-8"))
            self.assertTrue(data["defaultSettings"]["addToSteam"])
            self.assertEqual(data["defaultSettings"]["wineVersion"]["bin"], str(p))
            self.assertEqual(
                data["defaultSettings"]["wrapperOptions"][0]["exe"],
                "/usr/lib/steamac/steamac-epic",
            )

            # Second call must not overwrite user customizations.
            cfg.write_text('{"custom": true}\n', encoding="utf-8")
            self.assertFalse(MOD.seed_heroic_config(cfg, steam_root=steam))
            self.assertEqual(json.loads(cfg.read_text(encoding="utf-8")), {"custom": True})

    def test_flatpak_override_args(self):
        args = MOD.flatpak_override_args("/home/steamos/.local/share/Steam", runtime_dir="/run/user/1000", gpu_mib=8192)
        self.assertIn("--filesystem=/home/steamos/.local/share/Steam:rw", args)
        self.assertIn("--filesystem=/run/user/1000/steam/env:ro", args)
        self.assertIn("--env=FEX_X87REDUCEDPRECISION=1", args)
        self.assertIn("--env=PROTON_USE_NTSYNC=1", args)
        self.assertTrue(any("dxgi.customVideoMemory=8192" in a for a in args))

    def test_run_game_execs_with_env(self):
        out = subprocess.check_output(
            [
                sys.executable,
                str(SCRIPT),
                "run-game",
                "--",
                sys.executable,
                "-c",
                "import os; print(os.environ['FEX_X87REDUCEDPRECISION'], os.environ['PROTON_USE_NTSYNC'])",
            ],
            text=True,
        ).strip()
        self.assertEqual(out, "1 1")


if __name__ == "__main__":
    unittest.main()
