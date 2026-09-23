"""独立した Blender ウィンドウで UI と Undo・画像ツールを検証。設定は保存しない。"""
import importlib
from pathlib import Path
import tempfile
import traceback

import addon_utils
import bpy

MODULE = 'bl_ext.user_default.suketto'
addon_utils.enable(MODULE, default_set=True)
addon = importlib.import_module(MODULE)
tools = importlib.import_module(MODULE + '.blender_tools')
r = addon.runtime
out = Path(tempfile.gettempdir()) / 'suketto-visual'
out.mkdir(exist_ok=True)
bpy.context.preferences.view.language = 'ja_JP'
bpy.context.preferences.view.use_translate_interface = True
bpy.context.preferences.view.show_splash = False
r.ready = r.authenticated = True
r.status, r.account = '接続済み', 'ChatGPT · ログイン済み'
r.models = [
    {'model': model, 'displayName': label, 'defaultReasoningEffort': 'medium',
     'supportedReasoningEfforts': [{'reasoningEffort': e} for e in ['low', 'medium', 'high', 'xhigh', 'max', 'ultra']]}
    for model,label in [('gpt-6-luna', 'GPT-6 Luna'), ('gpt-6-sol', 'GPT-6 Sol'), ('gpt-6-astra', 'GPT-6 Astra')]]
# この表示用サンプルをユーザーの履歴へ保存しない。
r.storage = None
r.add('user', 'この立方体を幅1、高さ3、奥行き2にしてください。')
r.add('assistant', '立方体の寸法を確認して変更します。')
r.add('tool', '選択した立方体の寸法を変更')
r.add('assistant', '指定の寸法に変更しました。続けて形や材質を調整できます。')
addon.sync_ui()
bpy.ops.suketto.action(action='normal')
bpy.context.window_manager.suketto.prompt = '角を少し丸めてください'
area = next(a for a in bpy.context.screen.areas if a.type == 'VIEW_3D')
area.spaces.active.show_region_ui = True


def finish():
    try:
        region = next(r for r in area.regions if r.type == 'UI')
        region.active_panel_category = '助人'
        area.tag_redraw()
        bpy.app.timers.register(capture, first_interval=2)
    except Exception:
        (out / 'error.txt').write_text(traceback.format_exc(), encoding='utf-8')
        bpy.ops.wm.quit_blender()


def capture():
    try:
        with bpy.context.temp_override(**tools.view_context()):
            bpy.ops.screen.screenshot(filepath=str(out / 'panel.png'))
        image = tools.execute('blender_screenshot', {})
        assert image['success'] and image['contentItems'][0]['imageUrl'].startswith('data:image/png;base64,')
        with bpy.context.temp_override(**tools.view_context()):
            bpy.ops.ed.undo_push(message='検証前')
        result = tools.execute('blender_python', {'code': "bpy.ops.mesh.primitive_cube_add(); bpy.context.object.name='助人_Undo検証'"})
        assert result['success']
        assert bpy.data.objects.get('助人_Undo検証')
        with bpy.context.temp_override(**tools.view_context()):
            bpy.ops.ed.undo()
        assert bpy.data.objects.get('助人_Undo検証') is None
        (out / 'success.txt').write_text('画面描画・画像ツール・Undo を確認', encoding='utf-8')
    except Exception:
        (out / 'error.txt').write_text(traceback.format_exc(), encoding='utf-8')
    finally:
        bpy.ops.wm.quit_blender()


bpy.app.timers.register(finish, first_interval=3)
