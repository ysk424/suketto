"""Codex から呼ぶ Blender 内部ツール。必ずメインスレッドで実行する。"""
import base64
import contextlib
import io
import json
import math
from pathlib import Path
import tempfile
import threading
import traceback

import bpy
import mathutils


def tool(name, description, properties=None, required=None):
    return {"type": "function", "name": name, "description": description,
            "inputSchema": {"type": "object", "properties": properties or {},
                            "required": required or [], "additionalProperties": False}}


TOOLS = [
    tool("blender_scene", "現在開いている Blender のシーン、選択、オブジェクトを確認する。"),
    tool("blender_python", "開いている Blender のメインスレッドで Python を実行する。bpy, mathutils, math が利用可能。print と result 変数が返る。短い処理に分割し、長いレンダーや待機を実行しない。変更は Undo 対象。",
         {"code": {"type": "string"}, "description": {"type": "string", "description": "実行内容の短い日本語説明"}}, ["code", "description"]),
    tool("blender_screenshot", "現在の Blender ウィンドウの画像を確認する。ヘッドレスでは利用不可。"),
]

INSTRUCTIONS = """あなたは Blender 拡張機能「助人」の中で動く Codex です。すべて日本語で会話してください。
通常の Codex と同様に、相談には説明し、制作依頼には実際に作業してください。
Blender の状態は blender_scene で確認してください。ライブシーンの操作には blender_python を使います。
外部の Python プロセスの bpy はこの Blender のシーンに接続しません。外部 MCP は不要です。
モデル生成はこの Blender 内で実行し、blender_screenshot で見た目も確認してください。
blender_python は UI のメインスレッドを使うため、短い処理に分割してください。
重い計算・ファイル調査は通常のターミナルツールを使用できます。Windows のバックグラウンド処理はウィンドウを表示しないでください。
現在のシーンや既存オブジェクトを一括削除せず、依頼対象を確認して編集してください。
ユーザーが求めていない上書き保存、終了、環境設定の変更を勝手に行わないでください。
会話を続けてもシーンが手動変更される可能性があります。過去の状態を決めつけないでください。
"""


def text_result(value, success=True):
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    return {"success": success, "contentItems": [{"type": "inputText", "text": text[:50000]}]}


def scene_info():
    scene = bpy.context.scene
    objects = []
    for obj in list(scene.objects)[:250]:
        row = {"名前": obj.name, "種類": obj.type, "位置": list(obj.location),
               "寸法": list(obj.dimensions), "選択": obj.select_get(),
               "モディファイア": [m.type for m in obj.modifiers]}
        if obj.type == "MESH":
            row["頂点数"] = len(obj.data.vertices)
        objects.append(row)
    active = bpy.context.view_layer.objects.active
    return {"Blender": bpy.app.version_string, "ファイル": bpy.data.filepath,
            "シーン": scene.name, "モード": bpy.context.mode,
            "アクティブ": active.name if active else None, "フレーム": scene.frame_current,
            "オブジェクト数": len(scene.objects), "オブジェクト": objects,
            "一覧省略": len(scene.objects) > 250}


_execution = None
_execution_result = None


class SUKETTO_OT_execute_tool(bpy.types.Operator):
    bl_idname = "suketto.execute_tool"
    bl_label = "助人の操作"
    bl_options = {"REGISTER", "UNDO", "INTERNAL"}

    def execute(self, context):
        global _execution_result
        output = io.StringIO()
        scope = {"bpy": bpy, "mathutils": mathutils, "math": math, "__name__": "__suketto__"}
        try:
            with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
                exec(compile(_execution, "<助人>", "exec"), scope, scope)
            result = {"出力": output.getvalue()[-24000:], "結果": scope.get("result")}
            _execution_result = text_result(result)
        except Exception:
            _execution_result = text_result(output.getvalue()[-12000:] + "\n" + traceback.format_exc(), False)
        # 部分的な変更でも Undo を残す。
        return {"FINISHED"}


def view_context():
    for window in bpy.context.window_manager.windows:
        for area in window.screen.areas:
            if area.type == "VIEW_3D":
                region = next((r for r in area.regions if r.type == "WINDOW"), None)
                if region:
                    return {"window": window, "area": area, "region": region}
    return {}


def execute(name, arguments, allow_changes=True):
    global _execution, _execution_result
    if threading.current_thread() is not threading.main_thread():
        raise RuntimeError("Blender ツールはメインスレッドで実行してください。")
    try:
        if isinstance(arguments, str):
            arguments = json.loads(arguments)
        if name == "blender_scene":
            return text_result(scene_info())
        if name == "blender_python":
            if not allow_changes:
                return text_result("PC・シーン操作は設定で無効です。", False)
            code = arguments.get("code", "")
            if not isinstance(code, str) or len(code) > 200000:
                return text_result("コードの形式または長さが不正です。", False)
            _execution, _execution_result = code, None
            try:
                with bpy.context.temp_override(**view_context()):
                    bpy.ops.suketto.execute_tool()
                return _execution_result or text_result("実行結果がありません。", False)
            finally:
                _execution = None
        if name == "blender_screenshot":
            if bpy.app.background or not bpy.context.window_manager.windows:
                return text_result("画面のない実行では画像を取得できません。", False)
            with tempfile.TemporaryDirectory(prefix="suketto-view-") as directory:
                path = Path(directory) / "blender.png"
                with bpy.context.temp_override(**view_context()):
                    bpy.ops.screen.screenshot(filepath=str(path))
                encoded = base64.b64encode(path.read_bytes()).decode("ascii")
                return {"success": True, "contentItems": [
                    {"type": "inputImage", "imageUrl": "data:image/png;base64," + encoded}]}
        return text_result("未対応の Blender ツールです: " + name, False)
    except Exception:
        return text_result(traceback.format_exc(), False)
