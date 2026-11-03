# 文件说明：验证跨平台启动入口的配置初始化、优先级、含空格路径和服务参数。
# 不启动真实模型；检查配置重复初始化不会覆盖密钥，错误检查也不会泄露密钥。
import importlib.util
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('paperlens_manage', Path(__file__).resolve().parents[1] / 'scripts/manage.py')
manage = importlib.util.module_from_spec(spec)
spec.loader.exec_module(manage)


class PortableStartupTests(unittest.TestCase):
    def test_init_preserves_existing_credentials_and_copies_missing_config(self):
        with tempfile.TemporaryDirectory(prefix='paperlens with spaces ') as directory:
            root = Path(directory)
            (root / 'rag_service').mkdir()
            (root / '.env.example').write_text('DEEPSEEK_API_KEY=replace-me\n')
            (root / 'rag_service/.env.example').write_text('RAG_PORT=19081\n')
            (root / '.env').write_text('DEEPSEEK_API_KEY=private-test-value\n')
            self.assertEqual(manage.initialize(root), ['rag_service/.env'])
            self.assertEqual(manage.initialize(root), [])
            self.assertIn('private-test-value', (root / '.env').read_text())

    def test_bom_and_space_paths_survive_loading_and_environment_override(self):
        with tempfile.TemporaryDirectory(prefix='paperlens with spaces ') as directory:
            root = Path(directory)
            (root / '.env').write_text('AGENT_PORT=19091\nDOCKER_BIN="C:/Tools With Spaces/docker.exe"\n', encoding='utf-8-sig')
            with patch.dict(os.environ, {'AGENT_PORT': '29091'}, clear=True):
                config = manage.environment('agent', root)
            command, cwd = manage.server_command('agent', config, root=root)
            self.assertEqual(cwd, root)
            self.assertEqual(command[command.index('--port') + 1], '29091')
            self.assertEqual(config['DOCKER_BIN'], 'C:/Tools With Spaces/docker.exe')

    def test_rag_and_agent_select_different_working_directories(self):
        root = Path('/arbitrary checkout with spaces')
        rag_command, rag_folder = manage.server_command('rag', {'RAG_PORT': '19081'}, root=root)
        agent_command, agent_folder = manage.server_command('agent', {}, port=29091, root=root)
        self.assertEqual(rag_folder, root / 'rag_service')
        self.assertEqual(agent_folder, root)
        self.assertEqual(rag_command[rag_command.index('--port') + 1], '19081')
        self.assertEqual(agent_command[agent_command.index('--port') + 1], '29091')

    def test_embedding_separates_local_model_path_and_api_alias(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = {'EMBEDDING_MODEL_PATH': './models with spaces', 'EMBEDDING_MODEL': 'public-api-alias',
                      'EMBEDDING_PORT': '28003', 'EMBEDDING_GPU_MEMORY_UTILIZATION': '0.2'}
            command, _ = manage.server_command('embedding', config, root=root)
            self.assertEqual(command[command.index('serve') + 1], str((root / 'rag_service/models with spaces').resolve()))
            self.assertEqual(command[command.index('--served-model-name') + 1], 'public-api-alias')
            self.assertIn('28003', command)

    def test_template_values_and_invalid_ports_are_rejected(self):
        self.assertFalse(manage.configured('replace-with-your-model-name'))
        self.assertTrue(manage.configured('configured-provider-model'))
        for port in [0, -1, 65536]:
            with self.assertRaises(ValueError):
                manage.port_number(port)
