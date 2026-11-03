# 文件说明：提供跨平台的配置初始化、环境检查和服务启动入口。
# 使用当前虚拟环境启动子进程，读取各服务自己的 .env，不依赖 WSL 名称或个人磁盘目录。

from __future__ import annotations

import argparse
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import ProxyHandler, Request, build_opener


ROOT = Path(__file__).resolve().parents[1]
MODEL_ID = "Qwen/Qwen3-Embedding-0.6B"


def initialize(root: Path = ROOT) -> list[str]:
    """只创建缺失的配置文件；重复运行不覆盖已有凭据或自定义设置。"""
    created = []
    for relative in (Path('.env'), Path('rag_service/.env')):
        destination = root / relative
        if not destination.exists():
            shutil.copyfile(destination.with_name('.env.example'), destination)
            created.append(relative.as_posix())
    return created


def environment(component: str, root: Path = ROOT) -> dict[str, str]:
    """读取对应服务配置；操作系统环境变量优先，支持带空格路径和 UTF-8 BOM。"""
    try:
        from dotenv import dotenv_values
    except ImportError as exc:
        raise ValueError('Install requirements in the selected virtual environment first.') from exc
    folder = root if component in {'agent', 'demo'} else root / 'rag_service'
    values = {key: value for key, value in dotenv_values(folder / '.env', encoding='utf-8-sig').items()
              if value is not None}
    return {**values, **os.environ, 'PYTHONDONTWRITEBYTECODE': '1'}


def port_number(value: str | int) -> int:
    """检查端口范围，防止配置错误直到启动服务时才被发现。"""
    port = int(value)
    if not 1 <= port <= 65535:
        raise ValueError('Port must be between 1 and 65535.')
    return port


def model_reference(value: str, root: Path) -> str:
    """本地相对模型目录以 RAG 根目录为基准；Hugging Face 模型 ID 原样保留。"""
    path = Path(value).expanduser()
    if path.is_absolute():
        return str(path)
    if value.startswith('.') or (root / path).exists():
        return str((root / path).resolve())
    return value


def server_command(component: str, config: dict[str, str], *, port: int | None = None,
                   host: str | None = None, root: Path = ROOT) -> tuple[list[str], Path]:
    """把服务名转换为参数列表；不经过 shell，保证含空格的目录也可启动。"""
    python = sys.executable
    if component == 'embedding':
        selected_port = port_number(port or config.get('EMBEDDING_PORT', '8003'))
        model = model_reference(config.get('EMBEDDING_MODEL_PATH') or config.get('EMBEDDING_MODEL', MODEL_ID), root / 'rag_service')
        return [python, '-m', 'vllm.entrypoints.cli.main', 'serve', model,
                '--task', 'embed', '--served-model-name', config.get('EMBEDDING_MODEL', MODEL_ID),
                '--host', host or config.get('EMBEDDING_HOST', '127.0.0.1'), '--port', str(selected_port),
                '--gpu-memory-utilization', config.get('EMBEDDING_GPU_MEMORY_UTILIZATION', '0.30'),
                '--max-model-len', config.get('EMBEDDING_MAX_MODEL_LEN', '4096'),
                '--dtype', config.get('EMBEDDING_DTYPE', 'half'), '--disable-log-requests'], root / 'rag_service'
    modules = {'agent': 'app.main:app', 'rag': 'app.main:app',
               'demo': 'tests.workspace_server:app', 'reranker': 'reranker_service.main:app'}
    prefixes = {'agent': ('AGENT', 18091), 'rag': ('RAG', 18081),
                'demo': ('DEMO', 18192), 'reranker': ('RERANKER', 8004)}
    prefix, default_port = prefixes[component]
    selected_port = port_number(port or config.get(prefix + '_PORT', str(default_port)))
    selected_host = host or config.get(prefix + '_HOST', '127.0.0.1')
    folder = root if component in {'agent', 'demo'} else root / 'rag_service'
    return [python, '-B', '-m', 'uvicorn', modules[component], '--host', selected_host,
            '--port', str(selected_port), '--workers', '1'], folder


def configured(value: str | None) -> bool:
    """区分已经填写的配置与模板占位值，不输出密钥内容。"""
    return bool(value and not value.lower().startswith(('replace-', 'your-', 'change-me')))


def probe(url: str, headers: dict[str, str] | None = None) -> tuple[bool, str]:
    """探测 HTTP 服务；本机地址绕过环境代理，错误只返回类型或状态码。"""
    try:
        local = urlsplit(url).hostname in {'localhost', '127.0.0.1', '::1'}
        opener = build_opener(ProxyHandler({})) if local else build_opener()
        with opener.open(Request(url, headers=headers or {}), timeout=5) as response:
            return 200 <= response.status < 300, 'HTTP ' + str(response.status)
    except HTTPError as exc:
        return False, 'HTTP ' + str(exc.code)
    except (URLError, OSError, ValueError) as exc:
        return False, type(exc).__name__


def doctor(*, services: bool = False, root: Path = ROOT) -> list[dict]:
    """检查配置与基础依赖；可选探测外部服务，不调用收费的生成接口。"""
    agent, rag = environment('agent', root), environment('rag', root)
    checks = [{'name': 'python', 'ok': sys.version_info >= (3, 12), 'detail': sys.version.split()[0]}]
    for relative in ('.env', 'rag_service/.env'):
        checks.append({'name': relative, 'ok': (root / relative).is_file(), 'detail': 'configuration file'})
    for name in ('fastapi', 'uvicorn', 'httpx', 'langchain', 'playwright'):
        checks.append({'name': name, 'ok': importlib.util.find_spec(name) is not None, 'detail': 'Agent dependency'})
    for label, config in [('agent', agent), ('rag', rag)]:
        checks.append({'name': label + '.DEEPSEEK_API_KEY', 'ok': configured(config.get('DEEPSEEK_API_KEY')),
                       'detail': 'set or missing; value hidden'})
    for label, config, name in [('agent', agent, 'DEEPSEEK_MODEL'), ('rag', rag, 'HYDE_MODEL'),
                                 ('rag', rag, 'GENERATION_MODEL')]:
        checks.append({'name': label + '.' + name, 'ok': configured(config.get(name)), 'detail': 'available provider model name'})
    qdrant_url = rag.get('QDRANT_URL', '')
    local_qdrant = urlsplit(qdrant_url).hostname in {'localhost', '127.0.0.1', '::1'}
    checks.append({'name': 'QDRANT_URL', 'ok': urlsplit(qdrant_url).scheme in {'http', 'https'}, 'detail': 'HTTP endpoint'})
    checks.append({'name': 'QDRANT_API_KEY', 'ok': local_qdrant or configured(rag.get('QDRANT_API_KEY')),
                   'detail': 'optional for loopback; value hidden'})
    docker = shutil.which(agent.get('DOCKER_BIN', 'docker'))
    try:
        docker_ready = bool(docker and subprocess.run([docker, 'info'], capture_output=True, timeout=10).returncode == 0)
    except (OSError, subprocess.TimeoutExpired):
        docker_ready = False
    checks.append({'name': 'docker', 'ok': docker_ready, 'detail': 'needed by Python sandbox'})
    if services:
        urls = {'grobid': rag.get('GROBID_URL', 'http://127.0.0.1:8070').rstrip('/') + '/api/isalive',
                'embedding': rag.get('EMBEDDING_URL', 'http://127.0.0.1:8003').rstrip('/') + '/health',
                'reranker': rag.get('RERANKER_URL', 'http://127.0.0.1:8004').rstrip('/') + '/health',
                'qdrant': qdrant_url.rstrip('/') + '/collections'}
        for name, url in urls.items():
            headers = {'api-key': rag['QDRANT_API_KEY']} if name == 'qdrant' and configured(rag.get('QDRANT_API_KEY')) else {}
            ok, detail = probe(url, headers)
            checks.append({'name': name + '.http', 'ok': ok, 'detail': detail})
    return checks


def main(argv: list[str] | None = None) -> int:
    """解析启动参数，输出可读检查结果，并转交服务子进程。"""
    parser = argparse.ArgumentParser(description='PaperLens portable service manager')
    parser.add_argument('command', choices=['init', 'doctor', 'agent', 'rag', 'demo', 'embedding', 'reranker'])
    parser.add_argument('--port', type=port_number)
    parser.add_argument('--host')
    parser.add_argument('--services', action='store_true', help='probe external service health (doctor only)')
    parser.add_argument('--json', action='store_true', help='print doctor result as JSON')
    args = parser.parse_args(argv)
    if sys.version_info < (3, 12):
        parser.error('Python 3.12 or newer is required.')
    if args.command == 'init':
        created = initialize()
        print('Created: ' + ', '.join(created) if created else 'Configuration already exists; nothing overwritten.')
        return 0
    try:
        if args.command == 'doctor':
            checks = doctor(services=args.services)
            if args.json:
                print(json.dumps({'ok': all(check['ok'] for check in checks), 'checks': checks}, indent=2))
            else:
                for check in checks:
                    print(('PASS ' if check['ok'] else 'FAIL ') + check['name'] + ': ' + check['detail'])
            return 0 if all(check['ok'] for check in checks) else 1
        config = environment(args.command)
        if args.command in {'embedding', 'reranker'}:
            config.setdefault('VLLM_USE_V1', '0')
            config.setdefault('TOKENIZERS_PARALLELISM', 'false')
        if args.command == 'reranker':
            config['RERANKER_MODEL'] = model_reference(config.get('RERANKER_MODEL', 'Qwen/Qwen3-Reranker-0.6B'), ROOT / 'rag_service')
        module = 'vllm' if args.command in {'embedding', 'reranker'} else 'uvicorn'
        if importlib.util.find_spec(module) is None:
            raise ValueError('Missing ' + module + ' in the selected environment; see docs/setup.md.')
        command, folder = server_command(args.command, config, port=args.port, host=args.host)
        print('Starting ' + args.command + ' with current Python environment. Ctrl+C to stop.', flush=True)
        if args.command == 'demo':
            print('DEMO: simulated paper and model responses; no external model calls.', flush=True)
        process = subprocess.Popen(command, cwd=folder, env=config)
        try:
            return process.wait()
        except KeyboardInterrupt:
            try:
                return process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.terminate()
                process.wait(timeout=10)
                return 130
    except (ValueError, OSError) as exc:
        print('Startup error: ' + str(exc), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
