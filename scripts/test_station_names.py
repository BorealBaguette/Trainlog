"""Dependency-free checks for request-only station-name overrides.

Run: python3 /home/sim/Trainlog/scripts/test_station_names.py
The two helpers are loaded from their AST to avoid booting the database-backed app.
"""

import ast
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch


class StationNamesTest(unittest.TestCase):
    def setUp(self):
        source = Path(__file__).resolve().parents[1] / "src" / "quai.py"
        tree = ast.parse(source.read_text())
        helpers = ast.Module(body=[node for node in tree.body
                                   if isinstance(node, ast.FunctionDef)
                                   and node.name in ("user_lang", "forced_script")], type_ignores=[])
        self.request = types.SimpleNamespace(headers={}, cookies={
            "quai_lang": "ru", "quai_force_script": "0"})
        self.flask = types.ModuleType("flask")
        self.flask.has_request_context = lambda: True
        self.flask.request = self.request
        self.flask.session = {"userinfo": {"lang": "en"}}
        users = types.ModuleType("src.users")
        users.station_name_settings = lambda: ("ja", True)
        users.valid_station_names = lambda value: value in ("", "local", "int", "ja", "ru")
        self.modules = patch.dict(sys.modules, {"flask": self.flask, "src.users": users})
        self.modules.start()
        self.addCleanup(self.modules.stop)
        self.scope = {"SCRIPT_OF_LANG": {"ja": "Jpan", "ru": "Cyrl"}}
        exec(compile(helpers, str(source), "exec"), self.scope)

    def test_saved_settings_ignore_old_cookies(self):
        self.assertEqual(self.scope["user_lang"](), "ja")
        self.assertEqual(self.scope["forced_script"](), "Jpan")

    def test_request_overrides_and_reset(self):
        self.request.headers = {"X-Trainlog-Station-Lang": "ru",
                                "X-Trainlog-Station-Script": "0"}
        self.assertEqual(self.scope["user_lang"](), "ru")
        self.assertIsNone(self.scope["forced_script"]())
        self.request.headers["X-Trainlog-Station-Script"] = "1"
        self.assertEqual(self.scope["forced_script"](), "Cyrl")
        self.request.headers.clear()
        self.assertEqual(self.scope["user_lang"](), "ja")
        self.assertEqual(self.scope["forced_script"](), "Jpan")

    def test_invalid_overrides_fall_back(self):
        self.request.headers = {"X-Trainlog-Station-Lang": "invalid",
                                "X-Trainlog-Station-Script": "invalid"}
        self.assertEqual(self.scope["user_lang"](), "ja")
        self.assertEqual(self.scope["forced_script"](), "Jpan")

    def test_local_and_no_request(self):
        self.request.headers = {"X-Trainlog-Station-Lang": "local"}
        self.assertIsNone(self.scope["forced_script"]())
        self.flask.has_request_context = lambda: False
        self.assertEqual(self.scope["user_lang"](), "en")
        self.assertIsNone(self.scope["forced_script"]())


if __name__ == "__main__":
    unittest.main()