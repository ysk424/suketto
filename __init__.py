"""助人 — Blender の N パネルで使う日本語 Codex クライアント。"""
import json
from pathlib import Path
import textwrap
import unicodedata

import bpy
from bpy.props import BoolProperty, CollectionProperty, EnumProperty, IntProperty, StringProperty

from .blender_tools import SUKETTO_OT_execute_tool
from .runtime import runtime

PACKAGE = __package__
_model_items = []
_effort_items = []
_history_items = []
_last_revision = -1
_last_width = 0

EFFORT_LABELS = {"none": "なし", "minimal": "最小", "low": "低", "medium": "中",
                 "high": "高", "xhigh": "非常に高い", "max": "最大", "ultra": "最高"}
ROLES = {"user": "あなた", "assistant": "助人", "tool": "作業", "system": "お知らせ"}
PRESETS = {
    "question": ("gpt-6-luna", "medium", "問い合わせ · Luna / 中"),
    "normal": ("gpt-6-sol", "medium", "通常操作 · Sol / 中"),
    "difficult": ("gpt-6-astra", "xhigh", "難しい仕事 · Astra / 非常に高い"),
}


def preferences():
    addon = bpy.context.preferences.addons.get(PACKAGE)
    return addon.preferences if addon else None


def storage_path():
    return Path(bpy.utils.user_resource("CONFIG")) / "suketto" / "conversations"


def model_items(self, context):
    return _model_items or [("NONE", "接続してください", "Codex がモデルの一覧を取得します")]


def effort_items(self, context):
    global _effort_items
    model = next((m for m in runtime.models if m["model"] == self.model), None)
    values = model.get("supportedReasoningEfforts", []) if model else []
    values = [x["reasoningEffort"] for x in values] or ["low", "medium", "high"]
    items = [(x, EFFORT_LABELS.get(x, x), "推論に使う量: " + EFFORT_LABELS.get(x, x)) for x in values]
    if items != _effort_items:
        _effort_items = items
    return _effort_items


def model_changed(self, context):
    model = next((m for m in runtime.models if m["model"] == self.model), None)
    if model:
        self.effort = model.get("defaultReasoningEffort", "medium")


def history_items(self, context):
    return _history_items or [("NONE", "保存された会話はありません", "")]


def wrap_japanese(text, width):
    """全角を二桁として折り返す。長いコード・日本語も切らずに表示。"""
    for paragraph in text.splitlines() or [""]:
        line, used = "", 0
        for char in paragraph:
            size = 2 if unicodedata.east_asian_width(char) in "WF" else 1
            if used + size > width and line:
                yield line
                line, used = "", 0
            line += char
            used += size
        yield line


class SUKETTO_Preferences(bpy.types.AddonPreferences):
    bl_idname = PACKAGE
    codex_path: StringProperty(name="Codex 実行ファイル", subtype="FILE_PATH", description="空欄ならインストール済みの Codex CLI を検索します")
    cwd: StringProperty(name="作業フォルダー", subtype="DIR_PATH", default=str(Path.home() / "git" / "suketto"))
    allow_changes: BoolProperty(name="PC・シーンの操作を許可", default=True,
        description="Codex に通常のターミナル操作、ファイル編集、Blender 内の Python 実行を許可します。変更後は再接続してください")
    show_tools: BoolProperty(name="作業ログを表示", default=True)

    def draw(self, context):
        layout = self.layout
        layout.label(text="助人 — あなたの Blender のための Codex")
        layout.prop(self, "codex_path")
        layout.prop(self, "cwd")
        layout.prop(self, "allow_changes")
        layout.prop(self, "show_tools")
        layout.label(text="ログインは Codex が管理します。API キーの入力は不要です。")
        layout.label(text="実行ファイル・作業フォルダー・操作許可の変更は再接続後に反映されます。")


class SUKETTO_Line(bpy.types.PropertyGroup):
    text: StringProperty()
    role: StringProperty()


class SUKETTO_Answer(bpy.types.PropertyGroup):
    question_id: StringProperty()
    title: StringProperty()
    value: StringProperty(name="回答")


class SUKETTO_State(bpy.types.PropertyGroup):
    prompt: StringProperty(name="入力", description="日本語で質問や制作の依頼を入力してください")
    prompt_line2: StringProperty(name="入力・2行目", description="改行して続ける内容を入力してください")
    prompt_line3: StringProperty(name="入力・3行目", description="3行まとめて一つのメッセージとして送信します")
    draft: StringProperty()
    model: EnumProperty(name="モデル", items=model_items, update=model_changed)
    effort: EnumProperty(name="推論量", items=effort_items)
    lines: CollectionProperty(type=SUKETTO_Line)
    line_index: IntProperty(default=0)
    follow: BoolProperty(name="最新の発言を追う", default=True)
    history: EnumProperty(name="会話", items=history_items)
    answers: CollectionProperty(type=SUKETTO_Answer)
    request_id: StringProperty()
    settings_open: BoolProperty(name="設定", default=False)


class SUKETTO_UL_chat(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        layout.label(text=item.text, translate=False)


def guarded(operator, function):
    try:
        function()
        return {"FINISHED"}
    except Exception as error:
        operator.report({"ERROR"}, str(error))
        runtime.error(error)
        return {"CANCELLED"}


def compose_text(state):
    return state.draft or "\n".join((state.prompt, state.prompt_line2, state.prompt_line3)).rstrip("\n")


def set_compose_text(state, text):
    state.prompt = state.prompt_line2 = state.prompt_line3 = state.draft = ""
    lines = text.split("\n")
    if len(lines) <= 3:
        for name, value in zip(("prompt", "prompt_line2", "prompt_line3"), lines):
            setattr(state, name, value)
    else:
        state.draft = text


class SUKETTO_OT_connect(bpy.types.Operator):
    bl_idname = "suketto.connect"
    bl_label = "Codex に接続"

    def execute(self, context):
        prefs = preferences()
        return guarded(self, lambda: runtime.connect(bpy.path.abspath(prefs.codex_path) if prefs.codex_path else "",
            bpy.path.abspath(prefs.cwd), storage_path(), prefs.allow_changes))


class SUKETTO_OT_action(bpy.types.Operator):
    bl_idname = "suketto.action"
    bl_label = "助人"
    action: StringProperty()

    def execute(self, context):
        state = context.window_manager.suketto
        def run():
            if self.action == "disconnect":
                runtime.disconnect()
            elif self.action == "login":
                runtime.login()
            elif self.action == "stop":
                runtime.interrupt()
            elif self.action == "new":
                runtime.new_chat()
            elif self.action == "refresh":
                runtime.refresh_models()
                runtime.refresh_account()
            elif self.action in ("send", "steer"):
                prompt = compose_text(state)
                if not prompt.strip():
                    raise ValueError("質問や依頼を入力してください。")
                if self.action == "steer":
                    runtime.steer(prompt)
                else:
                    runtime.send(prompt, state.model, state.effort)
                set_compose_text(state, "")
            elif self.action == "paste":
                set_compose_text(state, context.window_manager.clipboard)
            elif self.action == "clear_draft":
                set_compose_text(state, "")
            elif self.action == "copy":
                context.window_manager.clipboard = "\n\n".join(
                    ROLES.get(m["role"], m["role"]) + "\n" + m["text"] for m in runtime.messages)
                self.report({"INFO"}, "会話をコピーしました")
            elif self.action == "load":
                if state.history != "NONE":
                    runtime.load(state.history)
            elif self.action in PRESETS:
                model, wanted, _ = PRESETS[self.action]
                if not any(m["model"] == model for m in runtime.models):
                    raise ValueError("このアカウントのモデル一覧にありません。")
                state.model = model
                options = [x[0] for x in effort_items(state, context)]
                if wanted in options:
                    state.effort = wanted
                else:
                    raise ValueError("このモデルでは指定の推論量を利用できません。")
            runtime.changed()
        return guarded(self, run)


class SUKETTO_OT_answer(bpy.types.Operator):
    bl_idname = "suketto.answer"
    bl_label = "回答する"
    accept: BoolProperty(default=True)

    def execute(self, context):
        state = context.window_manager.suketto
        answers = {q.question_id: {"answers": [q.value]} for q in state.answers}
        return guarded(self, lambda: runtime.answer(self.accept, answers))


class SUKETTO_OT_question_option(bpy.types.Operator):
    bl_idname = "suketto.question_option"
    bl_label = "選択する"
    question_id: StringProperty()
    value: StringProperty()

    def execute(self, context):
        for answer in context.window_manager.suketto.answers:
            if answer.question_id == self.question_id:
                answer.value = self.value
        return {"FINISHED"}


class SUKETTO_OT_edit_draft(bpy.types.Operator):
    bl_idname = "suketto.edit_draft"
    bl_label = "長文を入力"
    # Blender 標準入力欄を使うことで日本語 IME と貼り付けを維持する。
    line1: StringProperty(name="1")
    line2: StringProperty(name="2")
    line3: StringProperty(name="3")
    line4: StringProperty(name="4")
    line5: StringProperty(name="5")
    line6: StringProperty(name="6")
    line7: StringProperty(name="7")
    line8: StringProperty(name="8")

    def invoke(self, context, event):
        state = context.window_manager.suketto
        lines = compose_text(state).split("\n")
        for i in range(8):
            setattr(self, "line" + str(i + 1), lines[i] if i < len(lines) else "")
        if len(lines) > 8:
            self.report({"WARNING"}, "8 行を超える下書きはクリップボードで編集してください")
            return {"CANCELLED"}
        return context.window_manager.invoke_props_dialog(self, width=650, confirm_text="下書きに反映")

    def draw(self, context):
        self.layout.label(text="各行に日本語で入力できます。長い文章は「貼り付け」も使えます。")
        for i in range(8):
            self.layout.prop(self, "line" + str(i + 1))

    def execute(self, context):
        set_compose_text(context.window_manager.suketto, "\n".join(getattr(self, "line" + str(i + 1)) for i in range(8)).rstrip())
        return {"FINISHED"}


def button(layout, text, action, icon="NONE"):
    op = layout.operator("suketto.action", text=text, icon=icon)
    op.action = action
    return op


class SUKETTO_PT_main(bpy.types.Panel):
    bl_label = "助人"
    bl_idname = "SUKETTO_PT_main"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "助人"

    def draw(self, context):
        layout = self.layout
        state = context.window_manager.suketto
        prefs = preferences()
        layout.label(text=runtime.status, icon="TIME" if runtime.busy else "COMMUNITY")
        if not runtime.ready:
            layout.operator("suketto.connect", icon="LINKED")
        else:
            layout.label(text=runtime.account, icon="USER")
            if not runtime.authenticated:
                button(layout, "ChatGPT でログイン", "login", "URL")
            presets = layout.column(align=True)
            for action, (model, effort, label) in PRESETS.items():
                icon = "RADIOBUT_ON" if state.model == model and state.effort == effort else "RADIOBUT_OFF"
                button(presets, label, action, icon)
            layout.prop(state, "model")
            layout.prop(state, "effort")

        row = layout.row(align=True)
        row.enabled = not runtime.busy
        button(row, "新しい会話", "new", "ADD")
        button(row, "コピー", "copy", "COPYDOWN")
        layout.template_list("SUKETTO_UL_chat", "", state, "lines", state, "line_index", rows=24, maxrows=30)
        layout.prop(state, "follow")
        if runtime.tokens:
            layout.label(text=runtime.tokens)

        if runtime.requests:
            event = runtime.requests[0]
            params = event["params"]
            box = layout.box()
            box.label(text="助人からの確認", icon="QUESTION")
            if event["method"] == "item/tool/requestUserInput":
                for question, answer in zip(params.get("questions", []), state.answers):
                    for line in wrap_japanese(question.get("question", ""), 42):
                        box.label(text=line)
                    for option in question.get("options") or []:
                        op = box.operator("suketto.question_option", text=option["label"])
                        op.question_id, op.value = question["id"], option["label"]
                    box.prop(answer, "value", text="回答")
                box.operator("suketto.answer", text="回答を送る").accept = True
            else:
                summary = params.get("reason") or params.get("command") or params.get("message") or "操作の許可が求められています"
                for line in wrap_japanese(str(summary)[:1500], 42):
                    box.label(text=line)
                row = box.row(align=True)
                if event["method"] != "mcpServer/elicitation/request":
                    row.operator("suketto.answer", text="許可").accept = True
                row.operator("suketto.answer", text="辞退").accept = False

        box = layout.box()
        if state.draft:
            for line in list(wrap_japanese(state.draft, 42))[:7]:
                box.label(text=line, translate=False)
            if len(list(wrap_japanese(state.draft, 42))) > 7:
                box.label(text="… 続きもまとめて送信します")
            button(box, "下書きを消す", "clear_draft", "X")
        else:
            inputs = box.column(align=True)
            for name in ("prompt", "prompt_line2", "prompt_line3"):
                inputs.prop(state, name, text="")
        row = box.row(align=True)
        row.operator("suketto.edit_draft", text="長文入力", icon="TEXT")
        button(row, "貼り付け", "paste", "PASTEDOWN")
        row = box.row()
        row.scale_y = 1.3
        if runtime.busy:
            followup = box.row()
            followup.enabled = bool(runtime.turn_id) and not runtime.interrupted
            button(followup, "追加指示を送る", "steer", "PLAY")
            button(row, "応答を停止", "stop", "CANCEL")
        else:
            row.enabled = runtime.ready and runtime.authenticated
            button(row, "送信", "send", "PLAY")

        box = layout.box()
        box.label(text="会話履歴")
        row = box.row(align=True)
        row.enabled = not runtime.busy
        row.prop(state, "history", text="")
        button(row, "開く", "load", "FILE_FOLDER")

        layout.prop(state, "settings_open", icon="TRIA_DOWN" if state.settings_open else "TRIA_RIGHT", emboss=False)
        if state.settings_open and prefs:
            box = layout.box()
            settings = box.column()
            settings.enabled = not runtime.transport
            settings.prop(prefs, "codex_path")
            settings.prop(prefs, "cwd")
            settings.prop(prefs, "allow_changes")
            box.prop(prefs, "show_tools")
            if runtime.ready:
                button(box, "モデル・ログインを再確認", "refresh", "FILE_REFRESH")
            if runtime.transport:
                button(box, "切断", "disconnect", "UNLINKED")
            box.label(text="履歴とログインはこの PC に保存されます")


def sync_ui():
    global _last_revision, _model_items, _history_items, _last_width
    if not hasattr(bpy.context.window_manager, "suketto"):
        return
    runtime.poll()
    state = bpy.context.window_manager.suketto
    prefs = preferences()
    widths = [r.width for w in bpy.context.window_manager.windows for a in w.screen.areas
              if a.type == "VIEW_3D" for r in a.regions if r.type == "UI" and r.width > 0]
    scale = bpy.context.preferences.system.ui_scale or 1.0
    width = max(20, int((max(widths, default=340) - 58) / (7 * scale)))
    revision = (runtime.revision, prefs.show_tools if prefs else True)
    if revision != _last_revision or width != _last_width:
        items = [(m["model"], m.get("displayName", m["model"]), "このアカウントで利用できるモデル") for m in runtime.models]
        if items != _model_items:
            previous_model = state.model if _model_items else None
            previous_effort = state.effort if _model_items else None
            _model_items = items
            if items:
                available = [m[0] for m in items]
                state.model = previous_model if previous_model in available else ("gpt-6-luna" if "gpt-6-luna" in available else available[0])
                choices = [x[0] for x in effort_items(state, bpy.context)]
                if previous_effort in choices:
                    state.effort = previous_effort
                elif "medium" in choices:
                    state.effort = "medium"
        old_index = state.line_index
        state.lines.clear()
        lines = []
        for message in runtime.messages:
            if message["role"] == "tool" and prefs and not prefs.show_tools:
                continue
            lines.append(("▸ " + ROLES.get(message["role"], "助人"), message["role"]))
            lines.extend((line, message["role"]) for line in wrap_japanese(message["text"], width))
            lines.append(("", message["role"]))
        for text, role in lines[-3000:]:
            item = state.lines.add()
            item.text, item.role = text, role
        state.line_index = max(0, len(state.lines) - 1) if state.follow else min(old_index, max(0, len(state.lines) - 1))
        _history_items = [(i, title, "保存した会話を続ける") for i, title, _ in runtime.histories()]
        request = runtime.requests[0] if runtime.requests else None
        request_id = str(request["id"]) if request else ""
        if request_id != state.request_id:
            state.answers.clear()
            state.request_id = request_id
            if request:
                for question in request["params"].get("questions", []):
                    answer = state.answers.add()
                    answer.question_id = question["id"]
                    answer.title = question.get("question", "")
        for window in bpy.context.window_manager.windows:
            for area in window.screen.areas:
                if area.type == "VIEW_3D":
                    area.tag_redraw()
        _last_revision, _last_width = revision, width
    return 0.1


CLASSES = (SUKETTO_Preferences, SUKETTO_Line, SUKETTO_Answer, SUKETTO_State,
           SUKETTO_UL_chat, SUKETTO_OT_connect, SUKETTO_OT_action, SUKETTO_OT_answer,
           SUKETTO_OT_question_option, SUKETTO_OT_edit_draft, SUKETTO_OT_execute_tool, SUKETTO_PT_main)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.WindowManager.suketto = bpy.props.PointerProperty(type=SUKETTO_State)
    runtime.storage = storage_path()
    bpy.app.timers.register(sync_ui, first_interval=0.2, persistent=True)


def unregister():
    if bpy.app.timers.is_registered(sync_ui):
        bpy.app.timers.unregister(sync_ui)
    runtime.disconnect()
    del bpy.types.WindowManager.suketto
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
