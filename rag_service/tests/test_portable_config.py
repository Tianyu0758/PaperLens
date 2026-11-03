# 文件说明：验证本机与远程 Qdrant 的凭据要求，以及本地 tokenizer 路径解析。
# 使用临时环境变量，不读取或连接真实云服务。
from dataclasses import replace
import os
from pathlib import Path
from unittest.mock import patch

import pytest
from app.config import PROJECT_ROOT, _model_setting, settings


def test_loopback_qdrant_can_run_without_api_key():
    replace(settings, qdrant_url='http://127.0.0.1:6333', qdrant_api_key=None).validate_qdrant()


def test_remote_qdrant_requires_api_key():
    with pytest.raises(RuntimeError, match='QDRANT_API_KEY'):
        replace(settings, qdrant_url='https://example.cloud.qdrant.io', qdrant_api_key=None).validate_qdrant()


def test_relative_tokenizer_path_uses_rag_root():
    with patch.dict(os.environ, {'EMBEDDING_TOKENIZER': './models/tokenizer with spaces'}):
        assert _model_setting('EMBEDDING_TOKENIZER', '') == str((PROJECT_ROOT / 'models/tokenizer with spaces').resolve())


def test_model_id_does_not_turn_into_a_machine_path():
    with patch.dict(os.environ, {'EMBEDDING_TOKENIZER': 'Qwen/Qwen3-Embedding-0.6B'}):
        assert _model_setting('EMBEDDING_TOKENIZER', '') == 'Qwen/Qwen3-Embedding-0.6B'
