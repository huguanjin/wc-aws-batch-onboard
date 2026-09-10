#!/usr/bin/env python3
"""New API AWS (type 33) 批量上号本地代理。"""

from __future__ import annotations

import json
import os
import ssl
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent
STATIC = ROOT / "static"
DEFAULT_PORT = 8787
ALLOWED_API_PREFIXES = ("/api/",)
REQUEST_TIMEOUT = 60

CTX = ssl.create_default_context()


def json_bytes(data: dict, status: int = 200) -> tuple[int, bytes, str]:
    return status, json.dumps(data, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8"


def read_json(handler: BaseHTTPRequestHandler) -> dict:
    length = int(handler.headers.get("Content-Length") or 0)
    raw = handler.rfile.read(length) if length else b"{}"
    if not raw:
        return {}
    try:
        data = json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"JSON 解析失败: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError("请求体必须是 JSON 对象")
    return data


def normalize_base_url(url: str) -> str:
    url = (url or "").strip().rstrip("/")
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError("New API 地址必须是 http(s) 开头的完整 URL")
    return url


def normalize_path(path: str) -> str:
    path = (path or "").strip() or "/"
    if not path.startswith("/"):
        path = "/" + path
    if not path.startswith(ALLOWED_API_PREFIXES):
        raise ValueError("只允许代理 /api/ 接口")
    return path


def newapi_request(base_url: str, path: str, method: str, token: str, user_id: str, body=None, timeout: int = REQUEST_TIMEOUT):
    url = normalize_base_url(base_url) + normalize_path(path)
    payload = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "Authorization": token if token.lower().startswith("bearer ") else f"Bearer {token}",
        "New-Api-User": str(user_id).strip(),
    }
    req = urllib.request.Request(url, data=payload, headers=headers, method=method.upper())
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=CTX) as resp:
            raw = resp.read()
            text = raw.decode("utf-8", errors="replace")
            try:
                data = json.loads(text) if text else {}
            except json.JSONDecodeError:
                data = {"success": False, "message": text or "上游返回了非 JSON 响应"}
            return resp.status, data
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            data = json.loads(raw) if raw else {"success": False, "message": exc.reason}
        except json.JSONDecodeError:
            data = {"success": False, "message": raw or str(exc)}
        return exc.code, data
    except urllib.error.URLError as exc:
        return 502, {"success": False, "message": f"无法连接 New API: {exc.reason}"}
    except TimeoutError:
        return 504, {"success": False, "message": "请求 New API 超时"}


def content_type_for(path: Path) -> str:
    return {
        ".html": "text/html; charset=utf-8",
        ".js": "application/javascript; charset=utf-8",
        ".css": "text/css; charset=utf-8",
        ".svg": "image/svg+xml",
        ".json": "application/json; charset=utf-8",
        ".ico": "image/x-icon",
    }.get(path.suffix.lower(), "application/octet-stream")


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args) -> None:
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    def send_bytes(self, status: int, body: bytes, content_type: str, extra: dict | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def send_json(self, data: dict, status: int = 200) -> None:
        status, body, content_type = json_bytes(data, status)
        self.send_bytes(status, body, content_type)

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/api/health":
            self.send_json({"ok": True, "service": "aws-batch-onboard"})
            return
        if path in ("/", "/index.html"):
            target = STATIC / "index.html"
        else:
            target = (STATIC / path.lstrip("/")).resolve()
            if not str(target).startswith(str(STATIC)):
                self.send_json({"success": False, "message": "非法路径"}, 403)
                return
        if not target.is_file():
            self.send_json({"success": False, "message": "文件不存在"}, 404)
            return
        self.send_bytes(200, target.read_bytes(), content_type_for(target))

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        try:
            payload = read_json(self)
        except ValueError as exc:
            self.send_json({"success": False, "message": str(exc)}, 400)
            return
        if path == "/api/proxy":
            self.handle_proxy(payload)
            return
        if path == "/api/onboard":
            self.handle_onboard(payload)
            return
        self.send_json({"success": False, "message": "未知接口"}, 404)

    def handle_proxy(self, payload: dict) -> None:
        try:
            status, data = newapi_request(
                payload.get("base_url", ""),
                payload.get("path", ""),
                payload.get("method", "GET"),
                payload.get("token", ""),
                payload.get("user_id", ""),
                payload.get("body"),
                timeout=int(payload.get("timeout") or REQUEST_TIMEOUT),
            )
        except ValueError as exc:
            self.send_json({"success": False, "message": str(exc)}, 400)
            return
        self.send_json(data, 200 if status < 500 else status)

    def handle_onboard(self, payload: dict) -> None:
        try:
            base_url = payload.get("base_url", "")
            token = payload.get("token", "")
            user_id = payload.get("user_id", "")
            mode = payload.get("mode") or "sequential"
            accounts = payload.get("accounts") or []
            channel = payload.get("channel") or {}
            test_after = bool(payload.get("test_after"))
            delay_ms = max(0, int(payload.get("delay_ms") or 200))
            if not isinstance(accounts, list) or not accounts:
                raise ValueError("accounts 不能为空")
            if not isinstance(channel, dict):
                raise ValueError("channel 配置无效")
        except (TypeError, ValueError) as exc:
            self.send_json({"success": False, "message": str(exc)}, 400)
            return

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.end_headers()

        def emit(event: str, data: dict) -> None:
            chunk = f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n".encode("utf-8")
            self.wfile.write(chunk)
            self.wfile.flush()

        try:
            if mode == "native_batch":
                self.run_native_batch(emit, base_url, token, user_id, accounts, channel, test_after)
            else:
                self.run_sequential(emit, base_url, token, user_id, accounts, channel, test_after, delay_ms)
        except BrokenPipeError:
            return
        except Exception as exc:
            emit("error", {"message": str(exc)})
        emit("done", {"ok": True})

    def run_native_batch(self, emit, base_url, token, user_id, accounts, channel, test_after) -> None:
        emit("start", {"total": len(accounts), "mode": "native_batch"})
        groups = {}
        for index, account in enumerate(accounts):
            tag = account.get("tag") or channel.get("tag") or ""
            groups.setdefault(tag, []).append((index, account))

        success = 0
        failed = 0
        created_names = []
        for tag, items in groups.items():
            keys = "\n".join(account["key"] for _, account in items)
            names = [account.get("name") or channel.get("name") or "AWS" for _, account in items]
            body = {
                "mode": "batch",
                "batch_add_set_key_prefix_2_name": True,
                "channel": {
                    **channel,
                    "key": keys,
                    "type": 33,
                    "tag": tag or None,
                },
            }
            status, data = newapi_request(base_url, "/api/channel/", "POST", token, user_id, body)
            ok = bool(data.get("success"))
            message = data.get("message") or ("批量创建成功" if ok else "批量创建失败")
            if ok:
                success += len(items)
                created_names.extend(names)
            else:
                failed += len(items)
            for index, account in items:
                emit("item", {
                    "index": index,
                    "name": account.get("name"),
                    "region": account.get("region"),
                    "tag": tag,
                    "masked": account.get("masked"),
                    "success": ok,
                    "message": message,
                })
        emit("summary", {"success": success, "failed": failed})
        if created_names and test_after:
            self.test_recent(emit, base_url, token, user_id, created_names, channel.get("test_model") or "")

    def run_sequential(self, emit, base_url, token, user_id, accounts, channel, test_after, delay_ms) -> None:
        emit("start", {"total": len(accounts), "mode": "sequential"})
        success = 0
        failed = 0
        created_names = []
        for index, account in enumerate(accounts):
            name = account.get("name") or f"{channel.get('name') or 'AWS'}-{index + 1:03d}"
            tag = account.get("tag") or channel.get("tag") or None
            body = {
                "mode": "single",
                "channel": {
                    **channel,
                    "type": 33,
                    "name": name,
                    "key": account["key"],
                    "tag": tag or None,
                },
            }
            status, data = newapi_request(base_url, "/api/channel/", "POST", token, user_id, body)
            ok = bool(data.get("success"))
            if ok:
                success += 1
                created_names.append(name)
            else:
                failed += 1
            emit("item", {
                "index": index,
                "name": name,
                "region": account.get("region"),
                "tag": tag,
                "masked": account.get("masked"),
                "success": ok,
                "message": data.get("message") or ("创建成功" if ok else "创建失败"),
            })
            if delay_ms and index < len(accounts) - 1:
                time.sleep(delay_ms / 1000)
        emit("summary", {"success": success, "failed": failed})
        if test_after and created_names:
            self.test_recent(emit, base_url, token, user_id, created_names, channel.get("test_model") or "")

    def test_recent(self, emit, base_url, token, user_id, names, test_model) -> None:
        emit("test_start", {"total": len(names)})
        status, data = newapi_request(
            base_url,
            "/api/channel/?p=1&page_size=100&id_sort=true&type=33",
            "GET",
            token,
            user_id,
        )
        items = ((data.get("data") or {}).get("items") if isinstance(data.get("data"), dict) else data.get("data")) or []
        name_set = set(names)
        matched = [item for item in items if item.get("name") in name_set]
        for item in matched:
            query = f"/api/channel/test/{item['id']}"
            if test_model:
                query += f"?model={urllib.parse.quote(str(test_model))}"
            _, result = newapi_request(base_url, query, "GET", token, user_id, timeout=90)
            emit("test_item", {
                "id": item.get("id"),
                "name": item.get("name"),
                "success": bool(result.get("success")),
                "time": result.get("time"),
                "message": result.get("message") or ("测试通过" if result.get("success") else "测试失败"),
            })


def main() -> None:
    port = int(os.environ.get("PORT") or DEFAULT_PORT)
    if not (STATIC / "index.html").is_file():
        print("缺少 static/index.html", file=sys.stderr)
        sys.exit(1)
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"AWS 批量上号面板: http://127.0.0.1:{port}")
    print("仅监听本机。请使用你自己的 New API 管理员访问令牌。")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")
        server.server_close()


if __name__ == "__main__":
    threading.current_thread().name = "main"
    main()
