"""Unit tests for Docker-sandboxed code execution."""
import subprocess
import unittest
from unittest.mock import MagicMock, patch

from meshflow.tools.code_interpreter import CodeInterpreter


class TestDockerCodeInterpreter(unittest.TestCase):

    def test_constructor_defaults(self):
        interpreter = CodeInterpreter(docker=True)
        self.assertTrue(interpreter.docker)
        self.assertEqual(interpreter.docker_image, "python:3.11-slim")

    def test_constructor_custom(self):
        interpreter = CodeInterpreter(docker=True, docker_image="my-custom-python:latest")
        self.assertTrue(interpreter.docker)
        self.assertEqual(interpreter.docker_image, "my-custom-python:latest")

    @patch("subprocess.run")
    def test_run_docker_compiles_correct_command(self, mock_run):
        # Configure mock return value
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = "Hello from Docker"
        mock_proc.stderr = ""
        mock_run.return_value = mock_proc

        interpreter = CodeInterpreter(docker=True, docker_image="python:3.11-slim", env_vars={"KEY1": "VAL1"})
        result = interpreter.run("print('Hello')", env={"KEY2": "VAL2"})

        self.assertTrue(result.success)
        self.assertEqual(result.stdout, "Hello from Docker")

        # Verify subprocess.run was called with docker run cmd
        mock_run.assert_called_once()
        cmd_args = mock_run.call_args[0][0]
        
        self.assertEqual(cmd_args[0], "docker")
        self.assertEqual(cmd_args[1], "run")
        self.assertIn("--name", cmd_args)
        self.assertIn("--rm", cmd_args)
        self.assertIn("-m", cmd_args)
        self.assertIn("256m", cmd_args)
        self.assertIn("--cpus", cmd_args)
        self.assertIn("1.0", cmd_args)
        self.assertIn("-v", cmd_args)
        self.assertIn("-w", cmd_args)
        self.assertIn("/app", cmd_args)
        
        # Verify env vars
        self.assertIn("-e", cmd_args)
        self.assertIn("KEY1=VAL1", cmd_args)
        self.assertIn("KEY2=VAL2", cmd_args)
        
        # Verify image and script execution
        self.assertIn("python:3.11-slim", cmd_args)
        self.assertIn("python", cmd_args)
        self.assertIn("run.py", cmd_args)

    @patch("subprocess.run")
    def test_run_docker_timeout_kills_container(self, mock_run):
        # Trigger TimeoutExpired on first call, success on second call (docker kill)
        mock_run.side_effect = [
            subprocess.TimeoutExpired(cmd=["docker"], timeout=2.0),
            MagicMock(returncode=0)
        ]

        interpreter = CodeInterpreter(docker=True, timeout_s=2.0)
        result = interpreter.run("while True: pass")

        self.assertFalse(result.success)
        self.assertTrue(result.timed_out)
        self.assertIn("timed out", result.error)

        # Verify docker kill was executed
        self.assertEqual(mock_run.call_count, 2)
        kill_cmd = mock_run.call_args_list[1][0][0]
        self.assertEqual(kill_cmd[0], "docker")
        self.assertEqual(kill_cmd[1], "kill")
        self.assertTrue(kill_cmd[2].startswith("meshflow-exec-"))


if __name__ == "__main__":
    unittest.main()
