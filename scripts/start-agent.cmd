@echo off
@rem 文件说明：Windows 下启动新 Agent 的简便入口，将参数转交给同目录 PowerShell 脚本。
@rem 实际虚拟环境检查、目录选择和 Uvicorn 启动由 start-agent.ps1 完成。

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0start-agent.ps1" %*
