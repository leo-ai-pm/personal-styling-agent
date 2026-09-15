"""Start or reuse the local preview, without replacing another application."""
import argparse
import json
from pathlib import Path
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser

ROOT = Path(__file__).resolve().parents[1]
PORT = 8765
URL = f'http://127.0.0.1:{PORT}/'


def preview_status():
    try:
        response = urllib.request.urlopen(URL + 'api/health', timeout=1)
    except urllib.error.HTTPError as error:
        response = error
    except (urllib.error.URLError, TimeoutError, OSError):
        return 'absent'
    try:
        with response:
            data = json.loads(response.read(20000))
        if not isinstance(data, dict) or not str(data.get('version', '')).startswith('m2-preview-'):
            return 'other'
        if 'catalog_query_v1' not in data.get('capabilities', []):
            return 'other'
        return 'ready' if data.get('ready') else 'unready'
    except (ValueError, TypeError, OSError):
        return 'other'


def ensure_server():
    status = preview_status()
    if status == 'ready':
        return 'reused'
    if status == 'unready':
        raise RuntimeError('个性化穿搭Agent服务已运行，但模型或商品数据尚未就绪，请查看页面提示。')
    with socket.socket() as probe:
        try:
            probe.bind(('127.0.0.1', PORT))
        except OSError:
            raise RuntimeError('8765端口已被占用，未关闭或替换该程序。') from None
    runtime = ROOT / 'reports/m2/runtime'
    runtime.mkdir(parents=True, exist_ok=True)
    with (runtime / 'local-launch.log').open('ab') as log:
        process = subprocess.Popen(
            [sys.executable, '-m', 'm2.server', '--preview-assets', '--port', str(PORT),
             '--db', 'reports/m2/runtime/local-preview.sqlite3'],
            cwd=ROOT, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    deadline = time.monotonic() + 12
    while time.monotonic() < deadline:
        status = preview_status()
        if status == 'ready':
            return 'started'
        if status == 'unready':
            raise RuntimeError('个性化穿搭Agent已启动，但模型或商品数据尚未就绪，请查看页面提示。')
        if process.poll() is not None:
            break
        time.sleep(.2)
    raise RuntimeError('个性化穿搭Agent未能完成启动，请检查 reports/m2/runtime/local-launch.log。')


def main():
    parser = argparse.ArgumentParser(description='启动个性化穿搭Agent本机预览')
    parser.add_argument('--no-open', action='store_true', help='只启动服务，不打开浏览器')
    args = parser.parse_args()
    try:
        result = ensure_server()
        print(('个性化穿搭Agent已启动：' if result == 'started' else '个性化穿搭Agent已在运行：') + URL)
        if not args.no_open:
            webbrowser.open(URL)
    except (RuntimeError, OSError, subprocess.CalledProcessError) as error:
        print(str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
