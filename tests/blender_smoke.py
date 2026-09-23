"""Blender 5.2 の実プロセス内でプリセット・直接操作・会話状態を確認。"""
import importlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

import addon_utils
import bpy

MODULE = 'bl_ext.user_default.suketto'
addon_utils.enable(MODULE, default_set=True)
addon = importlib.import_module(MODULE)
tools = importlib.import_module(MODULE + '.blender_tools')
runtime_module = importlib.import_module(MODULE + '.runtime')


class FakeTransport:
    def __init__(self):
        self.sent = []
        self.counter = 0

    def reply(self, rid, result):
        self.sent.append({'id': rid, 'result': result})

    def request(self, method, params):
        self.counter += 1
        self.sent.append({'id': self.counter, 'method': method, 'params': params})
        return self.counter


class SmokeTests(unittest.TestCase):
    def test_presets(self):
        addon.runtime.models = [
            {'model': m, 'displayName': m, 'defaultReasoningEffort': 'low',
             'supportedReasoningEfforts': [{'reasoningEffort': e} for e in ['low', 'medium', 'high', 'xhigh']]}
            for m in ['gpt-6-astra', 'gpt-6-sol', 'gpt-6-luna']]
        addon.sync_ui()
        state = bpy.context.window_manager.suketto
        self.assertEqual((state.model, state.effort), ("gpt-6-luna", "medium"))
        for action, (model, effort, label) in addon.PRESETS.items():
            self.assertEqual(bpy.ops.suketto.action(action=action), {'FINISHED'})
            self.assertEqual((state.model, state.effort), (model, effort))

    def test_blender_tool(self):
        result = tools.execute('blender_python', {'code': "bpy.ops.mesh.primitive_cube_add(); bpy.context.object.name='助人_テスト'; result=list(bpy.context.object.dimensions)"})
        self.assertTrue(result['success'], result)
        self.assertIsNotNone(bpy.data.objects.get('助人_テスト'))
        self.assertIn('助人_テスト', tools.execute('blender_scene', {})['contentItems'][0]['text'])
        result = tools.execute('blender_python', {'code': "raise ValueError('検証用エラー')"})
        self.assertFalse(result['success'])
        result = tools.execute('blender_python', {'code': 'result=1'}, allow_changes=False)
        self.assertFalse(result['success'])
        bpy.data.objects.remove(bpy.data.objects['助人_テスト'], do_unlink=True)

    def test_stream_and_history(self):
        r = runtime_module.Runtime()
        r.thread_id = 'test-thread'
        r.busy = True
        for text in ['日本語で', '回答します。']:
            r.handle({'method': 'item/agentMessage/delta', 'params': {'threadId': r.thread_id, 'itemId': 'a', 'delta': text}})
        self.assertEqual(r.messages[0]['text'], '日本語で回答します。')
        with tempfile.TemporaryDirectory() as directory:
            r.storage = Path(directory)
            r.add('user', '再開のテスト')
            r.handle({'method': 'turn/completed', 'params': {'threadId': r.thread_id, 'turn': {'status': 'completed'}}})
            sid = r.session_id
            r.new_chat()
            r.load(sid)
            self.assertEqual(r.thread_id, 'test-thread')
            self.assertTrue(r.resume_needed)
            self.assertEqual(r.messages[0]['text'], '日本語で回答します。')

    def test_cancel_rejects_late_scene_edits(self):
        r = runtime_module.Runtime()
        r.transport = FakeTransport()
        r.thread_id, r.turn_id, r.busy = 'thread', 'turn', True
        r.interrupt()
        r.handle({'id': 99, 'method': 'item/tool/call', 'params': {'threadId': 'thread', 'tool': 'blender_python', 'arguments': {'code': "bpy.ops.mesh.primitive_cube_add(); bpy.context.object.name='中止後の操作'"}}})
        self.assertFalse(r.transport.sent[-1]['result']['success'])
        self.assertIsNone(bpy.data.objects.get('中止後の操作'))

    def test_model_and_effort_sent_to_codex(self):
        r = runtime_module.Runtime()
        r.transport = FakeTransport()
        r.ready = r.authenticated = True
        r.thread_id = 'thread'
        r.models = [{'model': 'gpt-6-astra', 'supportedReasoningEfforts': [{'reasoningEffort': 'xhigh'}]}]
        r.send('テスト', 'gpt-6-astra', 'xhigh')
        self.assertEqual(r.transport.sent[-1]['params']['effort'], 'xhigh')
        self.assertEqual(r.transport.sent[-1]['params']['model'], 'gpt-6-astra')

    def test_followup_targets_current_turn(self):
        r = runtime_module.Runtime()
        r.transport = FakeTransport()
        r.thread_id, r.turn_id, r.busy = 'thread', 'turn', True
        r.steer('色は青にしてください')
        self.assertEqual(r.transport.sent[-1]['method'], 'turn/steer')
        self.assertEqual(r.transport.sent[-1]['params']['expectedTurnId'], 'turn')
        r.interrupted = True
        with self.assertRaises(RuntimeError):
            r.steer('中止後の指示')


result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(SmokeTests))
assert result.wasSuccessful(), '助人の検証に失敗しました'
print('SUKETTO_SMOKE_OK')
