# /// script
# requires-python = ">=3.11"
# dependencies = ["lark-oapi==1.7.5", "requests>=2.32,<3"]
# ///

import argparse
import fcntl
import json
import os
import re
import secrets
import sys
import tempfile
import time
from getpass import getpass
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlsplit
from uuid import UUID

import lark_oapi as lark
import requests
from lark_oapi.api.im.v1 import (
    CreateMessageRequest,
    CreateMessageRequestBody,
    ListMessageRequest,
)


CONFIG = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "send_to_mingming"
CREDENTIALS = CONFIG / "credentials.json"
REDIRECT_URI = "https://127.0.0.1"
SCOPES = {"im:message", "im:message.send_as_user", "im:message.p2p_msg:get_as_user"}


def exchange_token(app_id, app_secret, **grant):
    response = requests.post(
        f"{lark.FEISHU_DOMAIN}/open-apis/authen/v2/oauth/token",
        json={"client_id": app_id, "client_secret": app_secret, **grant},
        timeout=15,
        allow_redirects=False,
    )
    if response.status_code != 200 or not response.headers.get("Content-Type", "").startswith("application/json"):
        raise ValueError(f"飞书 OAuth 请求失败（HTTP {response.status_code}）")
    payload = response.json()
    if not payload.get("access_token") or not payload.get("refresh_token"):
        raise ValueError(f"未获得可续期的用户凭证（飞书错误码 {payload.get('code', 'unknown')}）")
    return {
        "access_token": payload["access_token"],
        "refresh_token": payload["refresh_token"],
        "expires_at": time.time() + int(payload["expires_in"]),
        "scopes": payload.get("scope", "").split(),
    }


def save_credentials(credentials):
    with tempfile.NamedTemporaryFile(mode="w", dir=CONFIG, delete=False) as file:
        temporary = Path(file.name)
        try:
            json.dump(credentials, file)
            file.close()
            temporary.replace(CREDENTIALS)
        finally:
            temporary.unlink(missing_ok=True)


def authorize(recipient):
    app_id = input("飞书自建应用 App ID：").strip()
    app_secret = getpass("App Secret：").strip()
    if not app_id or not app_secret:
        raise ValueError("需要 App ID 和 App Secret")

    state = secrets.token_urlsafe(32)
    query = urlencode({
        "client_id": app_id,
        "redirect_uri": REDIRECT_URI,
        "scope": " ".join(sorted(SCOPES | {"offline_access"})),
        "state": state,
    })
    print(f"请在浏览器用发信账号打开并授权：\nhttps://accounts.feishu.cn/open-apis/authen/v1/authorize?{query}", flush=True)
    callback = urlsplit(getpass("授权后粘贴浏览器地址栏的完整回调 URL：").strip())
    values = parse_qs(callback.query)
    if (callback.scheme, callback.netloc, callback.path.rstrip("/")) != ("https", "127.0.0.1", ""):
        raise ValueError("回调地址与配置不符")
    if not secrets.compare_digest(values.get("state", [""])[0], state) or not values.get("code"):
        raise ValueError("未收到本次授权的有效授权码")

    token = exchange_token(
        app_id, app_secret, grant_type="authorization_code",
        code=values["code"][0], redirect_uri=REDIRECT_URI,
    )
    missing = SCOPES - set(token["scopes"])
    if missing:
        raise ValueError(f"未授权：{', '.join(sorted(missing))}")
    save_credentials({"app_id": app_id, "app_secret": app_secret, "recipient": recipient, **token})


def get_credentials():
    if not CREDENTIALS.is_file():
        raise ValueError("尚未配置飞书用户授权，请按 references/feishu-setup.md 完成 auth")
    if CREDENTIALS.stat().st_mode & 0o077:
        raise ValueError(f"凭证文件权限过宽，请将 {CREDENTIALS} 设为 0600")
    credentials = json.loads(CREDENTIALS.read_text())
    if credentials["expires_at"] <= time.time() + 60:
        token = exchange_token(
            credentials["app_id"], credentials["app_secret"],
            grant_type="refresh_token", refresh_token=credentials["refresh_token"],
        )
        credentials.update(token)
        save_credentials(credentials)
    return credentials


def require_success(response):
    if not response.success():
        raise ValueError(f"飞书请求失败（{response.code}）：{response.msg}")
    return response.data


def send(message_api, option, recipient, message, request_id):
    body = (
        CreateMessageRequestBody.builder()
        .receive_id(recipient)
        .msg_type("text")
        .content(json.dumps({"text": message}, ensure_ascii=False))
        .uuid(str(request_id))
        .build()
    )
    request = CreateMessageRequest.builder().receive_id_type("open_id").request_body(body).build()
    try:
        response = message_api.create(request, option)
    except (requests.RequestException, ValueError) as error:
        raise ValueError("发送结果待核对，请先查看飞书会话") from error
    data = require_success(response)
    if not (data and data.message_id and data.chat_id and data.create_time):
        raise ValueError("飞书未返回完整发送回执，请先查看飞书会话")
    return {
        "message_id": data.message_id,
        "chat_id": data.chat_id,
        "sent_at_ms": int(data.create_time),
    }


def read_replies(message_api, option, recipient, chat_id, after_ms):
    replies = []
    page_token = None
    while True:
        builder = (
            ListMessageRequest.builder()
            .container_id_type("chat")
            .container_id(chat_id)
            .start_time(str(after_ms // 1000))
            .sort_type("ByCreateTimeAsc")
            .page_size(50)
        )
        if page_token:
            builder.page_token(page_token)
        data = require_success(message_api.list(builder.build(), option))
        for item in data.items or []:
            if item.deleted or not item.sender or item.sender.id != recipient or int(item.create_time) <= after_ms:
                continue
            replies.append({
                "message_id": item.message_id,
                "parent_id": item.parent_id,
                "root_id": item.root_id,
                "created_at_ms": int(item.create_time),
                "type": item.msg_type,
                "content": item.body.content if item.body else None,
            })
        if not data.has_more:
            return replies
        if not data.page_token or data.page_token == page_token:
            raise ValueError("飞书返回了无效的分页游标")
        page_token = data.page_token


def parse_args():
    parser = argparse.ArgumentParser(description="以已授权的飞书用户身份发送和读取时明明的私信")
    commands = parser.add_subparsers(dest="command", required=True)
    auth = commands.add_parser("auth", help="在本机配置飞书 OAuth 和收件人")
    auth.add_argument("--recipient-open-id", required=True)
    send_cmd = commands.add_parser("send", help="从标准输入读取并发送一条文本私信")
    send_cmd.add_argument("--uuid", type=UUID, required=True)
    replies = commands.add_parser("replies", help="读取发送回执之后由收件人发来的消息")
    replies.add_argument("--chat-id", required=True)
    replies.add_argument("--after-ms", type=int, required=True)
    return parser.parse_args()


def main():
    args = parse_args()
    if args.command == "auth" and not re.fullmatch(r"ou_[a-zA-Z0-9]+", args.recipient_open_id):
        raise ValueError("请提供已核对的飞书收件人 open_id（ou_ 开头）")
    if args.command == "replies" and (not re.fullmatch(r"oc_[a-zA-Z0-9]+", args.chat_id) or args.after_ms <= 0):
        raise ValueError("请使用发送回执里的 chat_id 和 sent_at_ms")

    CONFIG.mkdir(mode=0o700, parents=True, exist_ok=True)
    with (CONFIG / "credentials.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if args.command == "auth":
            if not sys.stdin.isatty():
                raise ValueError("请在本机交互式终端运行 auth")
            authorize(args.recipient_open_id)
            print("飞书用户授权已保存")
            return
        credentials = get_credentials()
    client = lark.Client.builder().enable_set_token(True).timeout(15).build()
    option = lark.RequestOption.builder().user_access_token(credentials["access_token"]).build()
    message_api = client.im.v1.message
    recipient = credentials["recipient"]

    if args.command == "send":
        message = sys.stdin.read()
        if not message.strip():
            raise ValueError("请通过标准输入提供私信正文")
        result = send(message_api, option, recipient, message, args.uuid)
    else:
        result = read_replies(message_api, option, recipient, args.chat_id, args.after_ms)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    try:
        main()
    except requests.RequestException as error:
        sys.exit(f"飞书连接失败（{type(error).__name__}）")
    except (OSError, ValueError, KeyError) as error:
        sys.exit(str(error))
