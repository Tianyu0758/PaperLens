# 文件说明：把模型生成的 Python 代码放入受限 Docker 容器执行，并收集标准输出和生成文件。
# 限制网络、资源、权限和执行时间；超时或取消时清理容器，文件按任务相对路径返回。

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import threading
import time
import uuid
from pathlib import Path


class SandboxError(RuntimeError):
    pass


class DockerPythonSandbox:
    """Run agent-produced Python only inside a restricted Docker container."""

    def __init__(
        self,
        root: Path,
        *,
        image: str = "python:3.12-slim",
        timeout_seconds: int = 30,
        memory: str = "512m",
        cpus: str = "0.5",
        docker_bin: str = "docker",
        max_output_bytes: int = 32_000,
    ) -> None:
        self.root = root
        self.image = image
        self.timeout_seconds = timeout_seconds
        self.memory = memory
        self.cpus = cpus
        self.docker_bin = docker_bin
        self.max_output_bytes = max_output_bytes
        self.root.mkdir(parents=True, exist_ok=True)

    async def execute(self, code: str) -> dict:
        if not code.strip():
            raise SandboxError("代码不能为空")
        if len(code.encode("utf-8")) > 50_000:
            raise SandboxError("代码超过 50 KB 限制")
        cancelled = threading.Event()
        worker = asyncio.create_task(asyncio.to_thread(self._execute_sync, code, cancelled))
        try:
            return await asyncio.shield(worker)
        except asyncio.CancelledError:
            cancelled.set()
            # The worker owns Docker cleanup. Wait for it before ending the run.
            await asyncio.shield(worker)
            raise

    def _execute_sync(self, code: str, cancelled: threading.Event | None = None) -> dict:
        job_dir = self.root / f"job_{uuid.uuid4().hex[:12]}"
        job_dir.mkdir(parents=True)
        script = job_dir / "main.py"
        script.write_text(code, encoding="utf-8")
        container_name = f"academic-{job_dir.name}"
        # Linux 挂载目录按当前普通用户写入；Windows 采用固定非 root UID。
        # 避免宿主目录属于 UID 1000、容器 UID 65532 导致无法生成图表文件。
        user = f"{os.getuid()}:{os.getgid()}" if hasattr(os, 'getuid') and os.getuid() != 0 else '65532:65532'
        if hasattr(os, 'chown') and os.getuid() == 0:
            os.chown(job_dir, 65532, 65532)
        command = [
            self.docker_bin, "run", "--rm", "--name", container_name,
            "--network", "none",
            "--read-only",
            "--cpus", self.cpus,
            "--memory", self.memory,
            "--pids-limit", "64",
            "--security-opt", "no-new-privileges:true",
            "--cap-drop", "ALL",
            "--tmpfs", "/tmp:rw,noexec,nosuid,size=64m",
            "-v", f"{job_dir.resolve()}:/workspace:rw",
            "-w", "/workspace",
            "--user", user,
            self.image,
            "python", "-I", "/workspace/main.py",
        ]
        try:
            process = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                # Docker emits UTF-8. Windows locale decoding can fail in the pipe
                # reader thread and leave communicate() with None for stdout.
                text=False,
            )
            deadline = time.monotonic() + self.timeout_seconds
            timed_out = False
            while True:
                try:
                    stdout, stderr = process.communicate(timeout=0.2)
                    break
                except subprocess.TimeoutExpired:
                    if (cancelled and cancelled.is_set()) or time.monotonic() >= deadline:
                        timed_out = not (cancelled and cancelled.is_set())
                        self._remove_container(container_name)
                        process.kill()
                        stdout, stderr = process.communicate()
                        stderr = "执行超时，容器已终止" if timed_out else "任务已中断，容器已终止"
                        break
            artifacts = [
                str(path.relative_to(self.root)).replace("\\", "/")
                for path in job_dir.rglob("*")
                if path.is_file() and path.name != "main.py"
            ]
            return {
                "ok": process.returncode == 0 and not timed_out,
                "returncode": process.returncode,
                "stdout": self._output_text(stdout),
                "stderr": self._output_text(stderr),
                "artifacts": artifacts,
            }
        finally:
            self._remove_container(container_name)
            # Keep generated charts/data as run artifacts; remove only source
            # code so the generated program is not retained unnecessarily.
            script.unlink(missing_ok=True)

    def _output_text(self, value: bytes | str | None) -> str:
        if value is None:
            return ""
        raw = value if isinstance(value, bytes) else value.encode("utf-8")
        return raw[:self.max_output_bytes].decode("utf-8", errors="replace")

    def _remove_container(self, name: str) -> None:
        try:
            subprocess.run([self.docker_bin, "rm", "--force", name], capture_output=True, timeout=5, check=False)
        except (OSError, subprocess.TimeoutExpired):
            pass


def compact_result(result: dict) -> str:
    return json.dumps(result, ensure_ascii=False)
