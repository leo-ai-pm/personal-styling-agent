import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from m2.config import load_config
from m2.core import Fault


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.config=Path(self.tmp.name)/'model.json'
        self.env=patch.dict(os.environ,{'YIXU_MODEL_CONFIG':str(self.config)},clear=True)
        self.env.start();self.addCleanup(self.env.stop)

    def test_no_configuration_does_not_find_unrelated_secrets(self):
        self.assertFalse(load_config()['key'])

    def test_explicit_provider_never_reuses_another_provider_key(self):
        self.config.write_text(json.dumps({'provider':'qwen','base_url':'https://local-config.invalid'}))
        os.environ.update(YIXU_MODEL_PROVIDER='deepseek',DASHSCOPE_API_KEY='qwen-test-only')
        c=load_config();self.assertFalse(c['key']);self.assertEqual(c['base'],'https://api.deepseek.com')
        os.environ['DEEPSEEK_API_KEY']='deepseek-test-only'
        self.assertEqual(load_config()['key'],'deepseek-test-only')

    def test_key_file_is_read_without_execution_or_copy(self):
        source=Path(self.tmp.name)/'source.env'
        source.write_text('IGNORED=hello\nexport DASHSCOPE_API_KEY="test-only-$(never-execute)"\n')
        self.config.write_text(json.dumps({'provider':'qwen','api_key_env_file':str(source)}))
        c=load_config();self.assertEqual(c['key'],'test-only-$(never-execute)');self.assertEqual(c['name'],'qwen3.7-plus')
        self.assertNotIn(c['key'],self.config.read_text())

    def test_environment_selection_overrides_local_provider(self):
        self.config.write_text(json.dumps({'provider':'qwen'}))
        os.environ.update(OPENAI_API_KEY='generic-test',OPENAI_BASE_URL='https://example.invalid/v1',OPENAI_MODEL='generic-model')
        c=load_config();self.assertEqual(c['provider'],'compatible');self.assertEqual(c['name'],'generic-model')

    def test_configuration_cannot_contain_secret_value(self):
        self.config.write_text(json.dumps({'provider':'qwen','api_key':'secret-test-only'}))
        with self.assertRaises(Fault) as e:load_config()
        self.assertNotIn('secret-test-only',str(e.exception))
