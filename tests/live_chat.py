"""実際のサブスク経由で会話と Blender 内部ツールを確認（利用枠を消費）。"""
import importlib
import time
import tempfile
from pathlib import Path
import addon_utils
import bpy

MODULE = 'bl_ext.user_default.suketto'
addon_utils.enable(MODULE, default_set=True)
addon = importlib.import_module(MODULE)
r = addon.runtime


def wait_for(condition, timeout=150):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        r.poll()
        if condition():
            return
        time.sleep(0.05)
    raise TimeoutError('Codex の応答待ちが時間切れです: ' + r.status)


with tempfile.TemporaryDirectory(prefix='suketto-live-') as directory:
    try:
        r.connect('', directory, Path(directory) / 'history')
        wait_for(lambda: r.ready and r.authenticated and r.models)
        print('LOGIN_OK', r.account, flush=True)
        r.send('動作確認です。blender_scene ツールで現在の Blender バージョンを確認し、日本語で一文だけ答えてください。ファイルやシーンは変更しないでください。', 'gpt-5.6-terra', 'medium')
        wait_for(lambda: not r.busy)
        print('TERRA_RESPONSE', r.messages, flush=True)
        assert any(x['role'] == 'assistant' for x in r.messages), r.status
        assert any(x['role'] == 'tool' and 'シーン' in x['text'] for x in r.messages), '内部ツールが呼ばれませんでした'
        r.send('次は制作の動作確認です。blender_python を使い、位置 (0,0,0)、寸法 (1,2,3)、名前「助人_接続確認」の立方体を一つ追加してください。他のオブジェクトは変更せず、ファイル保存やレンダーは不要です。結果を日本語で短く答えてください。', 'gpt-6-astra', 'medium')
        wait_for(lambda: not r.busy, timeout=180)
        obj = bpy.data.objects.get('助人_接続確認')
        assert obj is not None, r.messages[-5:]
        assert all(abs(a-b) < 0.001 for a,b in zip(obj.dimensions, (1,2,3))), obj.dimensions
        print('ASTRA_MODEL_CREATION_OK', obj.name, tuple(obj.dimensions), flush=True)
        print('LATEST_RESPONSE', [x['text'] for x in r.messages if x['role']=='assistant'][-1], flush=True)
        sid = r.session_id
        r.new_chat()
        r.load(sid)
        assert r.resume_needed
        r.send('先ほど作ったオブジェクトの名前だけ答えてください。ツール実行は不要です。', 'gpt-5.6-terra', 'medium')
        wait_for(lambda: not r.busy)
        assert '助人_接続確認' in [x['text'] for x in r.messages if x['role']=='assistant'][-1]
        print('RESUME_AND_MODEL_SWITCH_OK', flush=True)
    finally:
        r.disconnect()
print('SUKETTO_LIVE_OK', flush=True)
