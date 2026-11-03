# 文件说明：验证 Docker Python 沙箱的输出路径、超时、取消清理和 UTF-8 解码。
# 通过可控的进程替身检查容器调用与清理，不依赖实际执行模型生成代码。

import asyncio
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from app.sandbox import DockerPythonSandbox


class SandboxTests(unittest.IsolatedAsyncioTestCase):
    def test_custom_docker_executable_is_used_for_execution_and_cleanup(self):
        with tempfile.TemporaryDirectory() as directory:
            sandbox = DockerPythonSandbox(Path(directory), docker_bin='C:/Tools With Spaces/docker.exe')
            process = MagicMock()
            process.communicate.return_value = (b'done', b'')
            process.returncode = 0
            with patch('app.sandbox.subprocess.Popen', return_value=process) as launch, patch('app.sandbox.subprocess.run') as cleanup:
                result = sandbox._execute_sync('print(1)')
            self.assertTrue(result['ok'])
            self.assertEqual(launch.call_args.args[0][0], sandbox.docker_bin)
            self.assertTrue(all(call.args[0][0] == sandbox.docker_bin for call in cleanup.call_args_list))

    def test_docker_utf8_output_is_decoded_without_windows_locale_dependency(self):
        with tempfile.TemporaryDirectory() as directory:
            sandbox = DockerPythonSandbox(Path(directory))
            process = MagicMock()
            process.communicate.return_value = ("计时页数: 12\n合计分钟: 10.0\n".encode("utf-8"), None)
            process.returncode = 0
            with patch("app.sandbox.subprocess.Popen", return_value=process) as launch, patch("app.sandbox.subprocess.run"):
                result = sandbox._execute_sync("print('合计分钟: 10.0')")
            self.assertFalse(launch.call_args.kwargs["text"])
            self.assertEqual(result["stderr"], "")
            self.assertIn("合计分钟: 10.0", result["stdout"])

    async def test_cancellation_waits_for_worker_cleanup(self):
        with tempfile.TemporaryDirectory() as directory:
            sandbox = DockerPythonSandbox(Path(directory))
            entered, cleaned = threading.Event(), threading.Event()

            def worker(code, cancelled):
                entered.set()
                cancelled.wait(2)
                if cancelled.is_set():
                    cleaned.set()
                return {"ok": False}

            with patch.object(sandbox, "_execute_sync", side_effect=worker):
                task = asyncio.create_task(sandbox.execute("print(1)"))
                while not entered.is_set():
                    await asyncio.sleep(0.01)
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task
            self.assertTrue(cleaned.is_set())

    def test_timeout_removes_named_container_and_terminates_client(self):
        with tempfile.TemporaryDirectory() as directory:
            sandbox = DockerPythonSandbox(Path(directory), timeout_seconds=0)
            process = MagicMock()
            process.communicate.side_effect = [subprocess.TimeoutExpired("docker", 0.2), ("", "")]
            process.returncode = -1
            with patch("app.sandbox.subprocess.Popen", return_value=process) as launch, patch("app.sandbox.subprocess.run") as cleanup:
                result = sandbox._execute_sync("print(1)")
            command = launch.call_args.args[0]
            name = command[command.index("--name") + 1]
            self.assertFalse(result["ok"])
            self.assertIn("超时", result["stderr"])
            process.kill.assert_called_once()
            self.assertTrue(all(call.args[0] == ["docker", "rm", "--force", name] for call in cleanup.call_args_list))
            self.assertFalse(list(Path(directory).rglob("main.py")))

    def test_generated_files_have_run_relative_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            sandbox = DockerPythonSandbox(Path(directory))

            def launch(command, **kwargs):
                mounted = command[command.index("-v") + 1].removesuffix(":/workspace:rw")
                (Path(mounted) / "values.csv").write_text("value\n1\n", encoding="utf-8")
                process = MagicMock()
                process.communicate.return_value = ("done", "")
                process.returncode = 0
                return process

            with patch("app.sandbox.subprocess.Popen", side_effect=launch), patch("app.sandbox.subprocess.run"):
                result = sandbox._execute_sync("print(1)")
            self.assertTrue(result["ok"])
            self.assertEqual(len(result["artifacts"]), 1)
            self.assertTrue(result["artifacts"][0].startswith("job_"))
            self.assertTrue((Path(directory) / result["artifacts"][0]).is_file())
