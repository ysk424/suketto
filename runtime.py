"""会話・イベント・永続化。UI スレッドから poll() を呼ぶ。"""
import json
from pathlib import Path
import queue
import time
import uuid
import webbrowser

from .rpc import Transport


class Runtime:
    def __init__(self):
        self.transport = None
        self.pending = {}
        self.ready = False
        self.status = "未接続"
        self.account = "未確認"
        self.authenticated = False
        self.models = []
        self.messages = []
        self.thread_id = ""
        self.turn_id = ""
        self.busy = False
        self.requests = []
        self.revision = 0
        self.cwd = ""
        self.allow_changes = True
        self.storage = None
        self.session_id = uuid.uuid4().hex
        self.resume_needed = False
        self.login_id = None
        self.tokens = ""
        self.interrupted = False

    def changed(self):
        self.revision += 1

    def add(self, role, text, item_id=None):
        item = {"id": item_id or uuid.uuid4().hex, "role": role, "text": text}
        self.messages.append(item)
        self.changed()
        return item

    def error(self, error):
        self.status = "エラー"
        self.busy = False
        self.add("system", "エラー: " + str(error))
        self.save()

    def call(self, method, params, callback=None):
        request_id = self.transport.request(method, params)
        self.pending[request_id] = (callback, method, time.monotonic())
        return request_id

    def connect(self, executable, cwd, storage, allow_changes=True):
        self.disconnect()
        self.cwd = str(Path(cwd).expanduser().resolve())
        if not Path(self.cwd).is_dir():
            raise ValueError("作業フォルダーが存在しません。")
        self.allow_changes = allow_changes
        self.storage = Path(storage)
        self.storage.mkdir(parents=True, exist_ok=True)
        self.transport = Transport(executable)
        self.transport.start(self.cwd)
        self.status = "Codex に接続中"
        self.call("initialize", {
            "clientInfo": {"name": "suketto", "title": "助人", "version": "0.1.0"},
            "capabilities": {"experimentalApi": True},
        }, self._initialized)
        self.changed()

    def _initialized(self, result):
        self.transport.send({"method": "initialized"})
        self.ready = True
        self.status = "接続済み"
        self.refresh_account()
        self.refresh_models()

    def refresh_account(self):
        self.call("account/read", {"refreshToken": False}, self._account)

    def _account(self, result):
        account = result.get("account") or {}
        self.authenticated = account.get("type") in ("chatgpt", "chatgptAuthTokens")
        self.account = ("ChatGPT · " + str(account.get("planType", "ログイン済み"))) if self.authenticated else "ChatGPT へのログインが必要です"
        if account.get("type") == "apiKey":
            self.account = "API キーでログイン中。サブスクを使うには ChatGPT でログインしてください"
        self.changed()

    def refresh_models(self, cursor=None):
        if cursor is None:
            self.models = []
        self.call("model/list", {"limit": 100, "includeHidden": False, "cursor": cursor}, self._models)

    def _models(self, result):
        self.models.extend(result.get("data", []))
        if result.get("nextCursor"):
            self.refresh_models(result["nextCursor"])
        self.changed()

    def login(self):
        self.status = "ブラウザーでログインしてください"
        self.call("account/login/start", {"type": "chatgpt"}, self._login)

    def _login(self, result):
        self.login_id = result.get("loginId")
        url = result.get("authUrl", "")
        if url.startswith("https://"):
            webbrowser.open(url)
        else:
            self.error("ログイン URL を取得できませんでした。")

    def disconnect(self):
        self.save()
        if self.transport:
            self.transport.close()
        self.transport = None
        self.pending.clear()
        self.requests.clear()
        self.ready = self.busy = False
        self.turn_id = ""
        self.resume_needed = bool(self.thread_id)
        self.status = "未接続"
        self.changed()

    def new_chat(self):
        if self.busy:
            raise RuntimeError("応答を停止してから新しい会話を作成してください。")
        self.save()
        self.messages = []
        self.thread_id = self.turn_id = ""
        self.session_id = uuid.uuid4().hex
        self.resume_needed = False
        self.tokens = ""
        self.changed()

    def save(self):
        if not self.storage or not self.messages:
            return
        payload = {"id": self.session_id, "thread_id": self.thread_id, "cwd": self.cwd,
                   "messages": self.messages, "updated": time.time()}
        path = self.storage / (self.session_id + ".json")
        temporary = path.with_suffix(".tmp")
        try:
            temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            temporary.replace(path)
        except OSError as error:
            self.status = "履歴を保存できません: " + str(error)

    def histories(self):
        result = []
        if self.storage:
            for path in self.storage.glob("*.json"):
                try:
                    data = json.loads(path.read_text(encoding="utf-8"))
                    title = next((x["text"] for x in data["messages"] if x["role"] == "user"), "会話")
                    result.append((data["id"], title[:70], data.get("updated", 0)))
                except (OSError, ValueError, KeyError):
                    pass
        return sorted(result, key=lambda x: x[2], reverse=True)

    def load(self, session_id):
        if self.busy:
            raise RuntimeError("応答を停止してから履歴を開いてください。")
        if not session_id.isalnum():
            raise ValueError("履歴 ID が不正です。")
        self.save()
        data = json.loads((self.storage / (session_id + ".json")).read_text(encoding="utf-8"))
        self.session_id = session_id
        self.thread_id = data.get("thread_id", "")
        self.messages = data["messages"]
        self.cwd = data.get("cwd", self.cwd)
        self.resume_needed = bool(self.thread_id)
        self.changed()

    def send(self, prompt, model, effort):
        if not self.ready or not self.authenticated:
            raise RuntimeError("Codex に接続し、ChatGPT でログインしてください。")
        if self.busy:
            raise RuntimeError("応答中です。停止するか、完了を待ってください。")
        model_info = next((x for x in self.models if x["model"] == model), None)
        if not model_info:
            raise ValueError("利用できるモデルを選択してください。")
        efforts = [x["reasoningEffort"] for x in model_info.get("supportedReasoningEfforts", [])]
        if efforts and effort not in efforts:
            raise ValueError("このモデルに対応した推論量を選択してください。")
        self.busy = True
        self.interrupted = False
        self.status = "応答を待っています"
        self.add("user", prompt)
        self.save()

        def start_turn(_=None):
            self.resume_needed = False
            self.call("turn/start", {"threadId": self.thread_id,
                "input": [{"type": "text", "text": prompt}], "model": model,
                "effort": effort}, self._turn_started)

        if not self.thread_id or self.resume_needed:
            from .blender_tools import TOOLS, INSTRUCTIONS
            params = {"model": model, "cwd": self.cwd, "approvalPolicy": "never",
                      "sandbox": "danger-full-access" if self.allow_changes else "read-only",
                      "developerInstructions": INSTRUCTIONS,
                      "dynamicTools": TOOLS if self.allow_changes else [x for x in TOOLS if x["name"] != "blender_python"]}
            method = "thread/start"
            if self.thread_id:
                method = "thread/resume"
                params["threadId"] = self.thread_id

            def thread_started(result):
                self.thread_id = result["thread"]["id"]
                self.save()
                start_turn()

            self.call(method, params, thread_started)
        else:
            start_turn()

    def _turn_started(self, result):
        self.turn_id = result["turn"]["id"]
        self.changed()

    def interrupt(self):
        self.interrupted = True
        if self.turn_id:
            self.status = "停止中"
            self.call("turn/interrupt", {"threadId": self.thread_id, "turnId": self.turn_id})
        elif self.busy:
            self.disconnect()

    def steer(self, prompt):
        if not self.busy or not self.turn_id or self.interrupted:
            raise RuntimeError("追加指示を送れる応答がありません。")
        self.call("turn/steer", {"threadId": self.thread_id,
            "expectedTurnId": self.turn_id, "input": [{"type": "text", "text": prompt}]})
        self.add("user", prompt)
        self.save()

    def poll(self):
        if not self.transport:
            return
        for _ in range(150):
            try:
                event = self.transport.events.get_nowait()
            except queue.Empty:
                break
            try:
                self.handle(event)
            except Exception as error:
                self.error(error)
        now = time.monotonic()
        for request_id, (callback, method, started) in list(self.pending.items()):
            if now - started > 120:
                del self.pending[request_id]
                self.error(method + " の応答がありません。再接続してください。")

    def handle(self, event):
        if "id" in event and "method" not in event:
            callback, method, _ = self.pending.pop(event["id"], (None, "", 0))
            if event.get("error"):
                self.error(event["error"].get("message", str(event["error"])))
            elif callback:
                callback(event.get("result", {}))
            return
        method, params = event.get("method", ""), event.get("params", {})
        if "id" in event:
            if method == "item/tool/call":
                from .blender_tools import execute, text_result
                if params.get("threadId") != self.thread_id or not self.busy or self.interrupted:
                    self.transport.reply(event["id"], text_result("会話が停止または切り替わりました。", False))
                    return
                arguments = params.get("arguments", {})
                if isinstance(arguments, str):
                    arguments = json.loads(arguments)
                description = arguments.get("description") or {"blender_scene": "シーンを確認", "blender_screenshot": "画面を確認"}.get(params["tool"], "Blender を操作")
                self.add("tool", description)
                result = execute(params["tool"], arguments, self.allow_changes)
                self.transport.reply(event["id"], result)
                if not result["success"]:
                    self.add("tool", "操作エラー: " + str(result["contentItems"][0].get("text", "")))
            elif method in ("item/commandExecution/requestApproval", "item/fileChange/requestApproval", "item/tool/requestUserInput", "item/permissions/requestApproval", "mcpServer/elicitation/request"):
                self.requests.append(event)
                self.status = "確認への回答を待っています"
                self.changed()
            else:
                self.transport.send({"id": event["id"], "error": {"code": -32601, "message": "助人では未対応の要求です: " + method}})
            return
        if params.get("threadId") and params["threadId"] != self.thread_id:
            return
        if method == "item/agentMessage/delta":
            item_id = params["itemId"]
            item = next((x for x in self.messages if x["id"] == item_id), None)
            if item is None:
                item = self.add("assistant", "", item_id)
            item["text"] += params.get("delta", "")
            self.status = "回答中"
            self.changed()
        elif method in ("item/started", "item/completed"):
            item = params.get("item", {})
            kind = item.get("type")
            if kind == "agentMessage" and method == "item/completed":
                old = next((x for x in self.messages if x["id"] == item.get("id")), None)
                if old:
                    old["text"] = item.get("text", old["text"])
                else:
                    self.add("assistant", item.get("text", ""), item.get("id"))
                self.changed()
            elif kind == "commandExecution":
                if method == "item/started":
                    self.add("tool", "ターミナル: " + item.get("command", "実行中"), item.get("id"))
                    self.status = "ターミナルで作業中"
                else:
                    old = next((x for x in self.messages if x["id"] == item.get("id")), None)
                    if old:
                        old["text"] += "\n" + item.get("aggregatedOutput", "")[-12000:] + "\n終了コード: " + str(item.get("exitCode"))
                    self.changed()
            elif kind == "fileChange" and method == "item/completed":
                self.add("tool", "ファイル変更: " + ", ".join(x.get("path", "") for x in item.get("changes", [])))
            elif kind == "webSearch" and method == "item/started":
                self.add("tool", "ウェブ検索: " + str(item.get("query", "")))
        elif method == "turn/started":
            self._turn_started(params)
        elif method == "turn/completed":
            self.busy = False
            self.turn_id = ""
            self.requests.clear()
            turn = params.get("turn", {})
            self.status = "停止しました" if turn.get("status") == "interrupted" else "完了"
            if turn.get("error"):
                self.error(turn["error"].get("message", str(turn["error"])))
            self.changed()
            self.save()
        elif method == "account/login/completed":
            if params.get("success"):
                self.status = "ログインしました"
                self.refresh_account()
                self.refresh_models()
            else:
                self.error(params.get("error") or "ログインがキャンセルされました。")
        elif method == "account/updated":
            self.refresh_account()
        elif method == "serverRequest/resolved":
            self.requests = [r for r in self.requests if r["id"] != params.get("requestId")]
            self.changed()
        elif method == "error":
            error = params.get("error", {})
            self.add("system", str(error.get("message", error)))
        elif method == "thread/tokenUsage/updated":
            usage = params.get("tokenUsage", {}).get("total", {})
            self.tokens = "使用トークン: " + str(usage.get("totalTokens", "—"))
            self.changed()
        elif method == "suketto/disconnected":
            self.ready = self.busy = False
            self.resume_needed = bool(self.thread_id)
            self.requests.clear()
            self.pending.clear()
            self.status = "切断されました。再接続してください"
            self.changed()
            self.save()

    def answer(self, accept, answers=None):
        request = self.requests[0]
        method, params = request["method"], request["params"]
        if method == "item/tool/requestUserInput":
            result = {"answers": answers or {}}
        elif method == "item/permissions/requestApproval":
            result = {"permissions": params.get("permissions", {}) if accept else {}, "scope": "turn"}
        elif method == "mcpServer/elicitation/request":
            # 汎用フォームの暗黙同意をしない。初版では辞退して会話へ戻す。
            result = {"action": "decline", "content": None}
        else:
            result = {"decision": "accept" if accept else "decline"}
        self.transport.reply(request["id"], result)
        self.requests.pop(0)
        self.status = "処理を続けています"
        self.changed()


runtime = Runtime()
