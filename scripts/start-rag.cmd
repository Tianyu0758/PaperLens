@echo off
@rem 文件说明：Windows 下启动独立 RAG 副本的简便入口。
@rem 把命令行参数交给同目录 PowerShell 脚本，再由后者调用 WSL 服务管理脚本。

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0start-rag.ps1" %*
