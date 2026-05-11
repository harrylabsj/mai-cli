import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MAI = ROOT / "scripts" / "mai.py"


class AgentDaemonLifecycleTest(unittest.TestCase):
    def run_mai(self, *args, state_dir, check=True):
        env = os.environ.copy()
        env["MAI_CLI_STATE_DIR"] = str(state_dir)
        proc = subprocess.run(
            [sys.executable, str(MAI), *args],
            capture_output=True,
            text=True,
            env=env,
            timeout=10,
        )
        if check and proc.returncode != 0:
            self.fail(f"mai.py {' '.join(args)} failed\nstdout={proc.stdout}\nstderr={proc.stderr}")
        return proc

    def seed_longjing_conversation(self, db_file, state_dir):
        self.run_mai(
            "--db",
            str(db_file),
            "merchant",
            "create",
            "--id",
            "seller-a",
            "--name",
            "West Lake Tea",
            "--city",
            "Hangzhou",
            "--service-area",
            "West Lake",
            "--delivery-eta-minutes",
            "45",
            state_dir=state_dir,
        )
        self.run_mai(
            "--db",
            str(db_file),
            "product",
            "add",
            "--merchant",
            "seller-a",
            "--sku",
            "tea-a",
            "--title",
            "Longjing Gift Box",
            "--price",
            "88",
            "--stock",
            "5",
            "--tags",
            "longjing,gift",
            state_dir=state_dir,
        )
        self.run_mai(
            "--db",
            str(db_file),
            "buyer",
            "ask",
            "--buyer",
            "alice",
            "--text",
            "longjing gift delivery today",
            "--city",
            "Hangzhou",
            state_dir=state_dir,
        )

    def wait_for_status(self, db_file, state_dir, predicate, timeout=5):
        deadline = time.time() + timeout
        last_status = None
        while time.time() < deadline:
            proc = self.run_mai(
                "agent",
                "status",
                "--merchant",
                "seller-a",
                "--db",
                str(db_file),
                "--format",
                "json",
                state_dir=state_dir,
            )
            last_status = json.loads(proc.stdout)
            if predicate(last_status):
                return last_status
            time.sleep(0.1)
        self.fail(f"status did not satisfy predicate; last={last_status}")

    def test_agent_daemon_start_status_logs_stop_and_duplicate_guard(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            db_file = tmp_path / "mai-cli.sqlite"
            state_dir = tmp_path / "state"
            self.seed_longjing_conversation(db_file, state_dir)

            start = json.loads(
                self.run_mai(
                    "agent",
                    "start",
                    "--merchant",
                    "seller-a",
                    "--db",
                    str(db_file),
                    "--interval",
                    "0.1",
                    "--format",
                    "json",
                    state_dir=state_dir,
                ).stdout
            )
            self.assertTrue(start["running"])
            self.assertTrue(Path(start["pid_file"]).exists())
            self.assertTrue(Path(start["log_file"]).exists())

            try:
                status = self.wait_for_status(
                    db_file,
                    state_dir,
                    lambda value: value["running"] and value["counters"]["replied"] >= 1,
                )
                self.assertEqual(status["merchant_id"], "seller-a")
                self.assertEqual(status["heartbeat"]["status"], "online")
                self.assertGreaterEqual(status["counters"]["checked"], 1)

                summary = json.loads(
                    self.run_mai(
                        "--db",
                        str(db_file),
                        "buyer",
                        "summarize",
                        "--conversation",
                        "CONV-0001",
                        "--format",
                        "json",
                        state_dir=state_dir,
                    ).stdout
                )
                self.assertEqual(summary["conversation"]["status"], "waiting_buyer")

                logs = json.loads(
                    self.run_mai(
                        "agent",
                        "logs",
                        "--merchant",
                        "seller-a",
                        "--tail",
                        "20",
                        "--format",
                        "json",
                        state_dir=state_dir,
                    ).stdout
                )
                self.assertTrue(
                    any(entry.get("event") == "process_once" and entry.get("replied_count", 0) >= 1 for entry in logs["entries"])
                )

                duplicate = self.run_mai(
                    "agent",
                    "start",
                    "--merchant",
                    "seller-a",
                    "--db",
                    str(db_file),
                    "--interval",
                    "0.1",
                    state_dir=state_dir,
                    check=False,
                )
                self.assertNotEqual(duplicate.returncode, 0)
                self.assertIn("already running", duplicate.stderr.lower())
            finally:
                self.run_mai(
                    "agent",
                    "stop",
                    "--merchant",
                    "seller-a",
                    "--db",
                    str(db_file),
                    "--format",
                    "json",
                    state_dir=state_dir,
                    check=False,
                )

            stopped = self.wait_for_status(db_file, state_dir, lambda value: not value["running"])
            self.assertEqual(stopped["heartbeat"]["status"], "away")


if __name__ == "__main__":
    unittest.main()
