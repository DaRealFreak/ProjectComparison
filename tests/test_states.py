#!/usr/bin/env python3
"""Tests for the interaction state file loader."""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from states import StateFileError, file_sha256, load_states, states_to_json  # noqa: E402

VALID = {
    "version": 1,
    "states": [
        {
            "id": "modal-open",
            "when": '[data-toggle="modal"], [data-bs-toggle="modal"]',
            "steps": [
                {"action": "click", "selector": '[data-toggle="modal"]', "index": 0},
                {"action": "waitFor", "selector": ".modal.in, .modal.show"},
            ],
            "sizes": ["375x667", "1920x1080"],
        }
    ],
}


class StatesTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)

    def write(self, data):
        path = os.path.join(self.dir.name, "states.json")
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(data, handle)
        return path

    def assert_error(self, data, text):
        with self.assertRaises(StateFileError) as ctx:
            load_states(self.write(data))
        self.assertIn(text, str(ctx.exception))

    def test_loads_valid_file(self):
        states = load_states(self.write(VALID))
        self.assertEqual(states[0].steps[1].action, "waitFor")
        self.assertEqual(states[0].sizes, ("375x667", "1920x1080"))

    def test_rejects_duplicate_id(self):
        step = [{"action": "wait", "ms": 10}]
        data = {"version": 1, "states": [{"id": "a", "steps": step}, {"id": "a", "steps": step}]}
        self.assert_error(data, "duplicate state id: a")

    def test_rejects_unknown_action(self):
        data = {"version": 1, "states": [{"id": "a", "steps": [{"action": "drag"}]}]}
        self.assert_error(data, "unknown action: drag")

    def test_rejects_missing_selector(self):
        data = {"version": 1, "states": [{"id": "a", "steps": [{"action": "click"}]}]}
        self.assert_error(data, "needs selector")

    def test_rejects_wrong_version(self):
        self.assert_error({"version": 2, "states": []}, "version must be 1")

    def test_rejects_fullscreen_size(self):
        data = {"version": 1, "states": [
            {"id": "a", "steps": [{"action": "wait", "ms": 10}], "sizes": ["fullscreen"]}]}
        self.assert_error(data, "invalid size: fullscreen")

    def test_rejects_bad_ms_and_empty_steps_and_bad_id(self):
        self.assert_error({"version": 1, "states": [
            {"id": "a", "steps": [{"action": "wait", "ms": 20000}]}]}, "ms")
        self.assert_error({"version": 1, "states": [{"id": "a", "steps": []}]}, "steps")
        self.assert_error({"version": 1, "states": [
            {"id": "Bad_Id", "steps": [{"action": "wait", "ms": 5}]}]}, "invalid id")

    def test_states_to_json_omits_none_step_keys(self):
        out = states_to_json(load_states(self.write(VALID)))
        self.assertEqual(out[0]["steps"][1], {"action": "waitFor", "selector": ".modal.in, .modal.show", "index": 0})
        self.assertEqual(out[0]["sizes"], ["375x667", "1920x1080"])
        self.assertEqual(out[0]["id"], "modal-open")

    def test_states_to_json_sizes_null_when_absent(self):
        data = {"version": 1, "states": [{"id": "a", "steps": [{"action": "wait", "ms": 10}]}]}
        out = states_to_json(load_states(self.write(data)))
        self.assertIsNone(out[0]["sizes"])
        self.assertIsNone(out[0]["when"])

    def test_file_sha256(self):
        path = os.path.join(self.dir.name, "f")
        with open(path, "wb") as handle:
            handle.write(b"abc")
        self.assertEqual(file_sha256(path),
                         "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad")


if __name__ == "__main__":
    unittest.main()
