"""Public sample assets and local-only configuration regression checks."""
import http.client
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from m2.config import load_config
from m2.core import execute, new_state, size
from m2.preview import preview_resources
from m2.server import make_server
from m2.service import Service
from m2.tests.browser_harness import ScriptedBrowserModel


class OpenSourceReleaseTests(unittest.TestCase):
    def test_sample_recommendation_is_not_described_as_a_model_photo(self):
        resources=preview_resources();at=resources.inventory['captured_at']
        state=new_state(resources.data['tenant_id'],False,at)
        result=execute(resources,state,[],{'updates':{'budget_max_fen':50000,'scene':'scene:work_commute','usual_sizes':{slot:size(slot,value) for slot,value in [('top','S'),('bottom','S'),('shoes','36')]}}},'turn','request',at)
        self.assertEqual(result['decision']['status'],'recommendation')
        self.assertIn('商品组合示意',result['response']['text'])
        self.assertNotIn('AI 搭配示意图',result['response']['text'])
        self.assertTrue(result['response']['image_url'].endswith('.svg'))
        self.assertLessEqual(result['history'][-1]['total_price_fen'],50000)

    def test_svg_http_content_type_and_private_file_routes(self):
        resources=preview_resources()
        service=Service(resources,ScriptedBrowserModel());self.addCleanup(service.close)
        server=make_server(service,0);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        def close():server.shutdown();server.server_close();thread.join()
        self.addCleanup(close)
        for path,status in [(next(iter(resources.looks.values()))['image_url'],200),('/.env',404),('/config/model.json',404),('/.git/config',404),('/data/m3_draft/provenance/image_jobs.json',404)]:
            connection=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=3)
            try:
                connection.request('GET',path);response=connection.getresponse();body=response.read()
                self.assertEqual(response.status,status,path)
                if status==200:
                    self.assertEqual(response.getheader('Content-Type'),'image/svg+xml')
                    self.assertTrue(body.startswith(b'<svg'))
                    self.assertNotIn(b'<script',body)
            finally:connection.close()

    def test_clean_checkout_never_reads_implicit_home_credentials(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict('os.environ',{},clear=True), patch('m2.config.ROOT',Path(folder)), patch('pathlib.Path.home',side_effect=AssertionError('Unexpected home lookup')):
            self.assertFalse(load_config()['key'])
            (Path(folder)/'.env').write_text('DEEPSEEK_API_KEY=local-test-only\n')
            self.assertEqual(load_config()['key'],'local-test-only')
            self.assertEqual(load_config()['provider'],'deepseek')
            config=Path(folder)/'explicit.json';config.write_text(json.dumps({'provider':'qwen'}))
            with patch.dict('os.environ',{'YIXU_MODEL_CONFIG':str(config)}):
                self.assertFalse(load_config()['key'])


if __name__=='__main__':unittest.main()
