# /// script
# requires-python = ">=3.11"
# dependencies = ["lark-oapi==1.7.5", "requests>=2.32,<3"]
# ///

import argparse
import json
import os
import re
import sys
import tempfile
import time
from getpass import getpass
from pathlib import Path
from uuid import UUID

import lark_oapi as lark
import requests
from lark_oapi.api.im.v1 import (
    CreateMessageRequest,
    CreateMessageRequestBody,
    GetChatMembersRequest,
    ListMessageRequest,
)


CONFIG = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "send_to_mingming"
BOT_CONFIG = CONFIG / "bot.json"


def save_config(config):
    CONFIG.mkdir(mode=0o700, parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", dir=CONFIG, delete=False) as file:
        temporary = Path(file.name)
        try:
            json.dump(config, file, ensure_ascii=False)
            file.close()
            temporary.replace(BOT_CONFIG)
        finally:
            temporary.unlink(missing_ok=True)


def load_config():
    if not BOT_CONFIG.is_file():
        raise ValueError("尚未绑定飞书外部群，请按 references/feishu-setup.md 执行 configure")
    if BOT_CONFIG.stat().st_mode & 0o077:
        raise ValueError(f"机器人配置文件权限过宽，请将 {BOT_CONFIG} 设为 0600")
    return json.loads(BOT_CONFIG.read_text())


def build_client(app_id, app_secret):
    return (
        lark.Client.builder()
        .app_id(app_id)
        .app_secret(app_secret)
        .timeout(15)
        .build()
        .im.v1
    )


def require_success(response):
    if not response.success():
        raise ValueError(f"飞书请求失败（{response.code}）：{response.msg}")
    if response.data is None:
        raise ValueError("飞书未返回预期的响应数据")
    return response.data


def pages(fetch):
    page_token = None
    seen = set()
    while True:
        data = require_success(fetch(page_token))
        yield data
        if not data.has_more:
            return
        page_token = data.page_token
        if not page_token or page_token in seen:
            raise ValueError("飞书返回了无效的分页游标")
        seen.add(page_token)


def find_respondent(chat_members_api, chat_id, name):
    def fetch(page_token):
        builder = GetChatMembersRequest.builder().chat_id(chat_id).member_id_type("open_id").page_size(50)
        if page_token:
            builder.page_token(page_token)
        return chat_members_api.get(builder.build())

    matches = {
        member.member_id for page in pages(fetch) for member in (page.items or [])
        if member.name == name and member.member_id and member.member_id.startswith("ou_")
    }
    if len(matches) != 1:
        raise ValueError(f"未能在群成员列表中唯一识别「{name}」，请核对目标群和群成员显示名")
    return matches.pop()


def message_pages(message_api, chat_id, after_ms):
    def fetch(page_token):
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
        return message_api.list(builder.build())

    return pages(fetch)


def configure(chat_id, respondent_name):
    if not sys.stdin.isatty():
        raise ValueError("请在本机交互式终端运行 configure")
    app_id = input("飞书自建应用 App ID：").strip()
    app_secret = getpass("App Secret：").strip()
    if not app_id or not app_secret:
        raise ValueError("需要 App ID 和 App Secret")

    client = build_client(app_id, app_secret)
    recipient = find_respondent(client.chat_members, chat_id, respondent_name)
    # Probe history access for this group without reporting its messages.
    next(message_pages(client.message, chat_id, time.time_ns() // 1_000_000))
    save_config({
        "app_id": app_id,
        "app_secret": app_secret,
        "chat_id": chat_id,
        "respondent_open_id": recipient,
    })


def send(message_api, chat_id, message, request_id):
    body = (
        CreateMessageRequestBody.builder()
        .receive_id(chat_id)
        .msg_type("text")
        .content(json.dumps({"text": message}, ensure_ascii=False))
        .uuid(str(request_id))
        .build()
    )
    request = CreateMessageRequest.builder().receive_id_type("chat_id").request_body(body).build()
    try:
        response = message_api.create(request)
    except (requests.RequestException, ValueError) as error:
        raise ValueError("发送结果待核对，请先查看目标群") from error
    data = require_success(response)
    if not (data.message_id and data.chat_id == chat_id and data.create_time):
        raise ValueError("飞书未返回完整发送回执，请先查看目标群")
    return {
        "message_id": data.message_id,
        "chat_id": data.chat_id,
        "sent_at_ms": int(data.create_time),
    }


def read_replies(message_api, recipient, chat_id, after_ms):
    replies = []
    for page in message_pages(message_api, chat_id, after_ms):
        for item in page.items or []:
            if item.deleted or not item.sender or item.sender.id != recipient:
                continue
            created_at = int(item.create_time)
            if created_at <= after_ms:
                continue
            replies.append({
                "message_id": item.message_id,
                "parent_id": item.parent_id,
                "root_id": item.root_id,
                "created_at_ms": created_at,
                "type": item.msg_type,
                "content": item.body.content if item.body else None,
            })
    return replies


def parse_args():
    parser = argparse.ArgumentParser(description="通过飞书应用机器人在指定外部群发送题目和读取回复")
    commands = parser.add_subparsers(dest="command", required=True)
    setup = commands.add_parser("configure", help="绑定机器人已加入的外部群")
    setup.add_argument("--chat-id", required=True)
    setup.add_argument("--respondent-name", default="时明明")
    send_cmd = commands.add_parser("send", help="从标准输入读取并向已绑定群发送文本")
    send_cmd.add_argument("--uuid", type=UUID, required=True)
    replies = commands.add_parser("replies", help="读取发送回执之后由指定群成员发来的消息")
    replies.add_argument("--chat-id", required=True)
    replies.add_argument("--after-ms", type=int, required=True)
    return parser.parse_args()


def main():
    args = parse_args()
    if args.command in {"configure", "replies"} and not re.fullmatch(r"oc_[a-zA-Z0-9]+", args.chat_id):
        raise ValueError("请提供以 oc_ 开头的群 ID")
    if args.command == "configure":
        configure(args.chat_id, args.respondent_name)
        print("机器人已绑定外部群，并通过群消息读取检查")
        return

    config = load_config()
    chat_id = config["chat_id"]
    if args.command == "replies" and (args.chat_id != chat_id or args.after_ms <= 0):
        raise ValueError("请使用当前绑定群的发送回执里的 chat_id 和 sent_at_ms")
    message_api = build_client(config["app_id"], config["app_secret"]).message
    if args.command == "send":
        message = sys.stdin.read()
        if not message.strip():
            raise ValueError("请通过标准输入提供群消息正文")
        result = send(message_api, chat_id, message, args.uuid)
    else:
        result = read_replies(message_api, config["respondent_open_id"], chat_id, args.after_ms)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    try:
        main()
    except requests.RequestException as error:
        sys.exit(f"飞书连接失败（{type(error).__name__}）")
    except (OSError, ValueError, KeyError) as error:
        sys.exit(str(error))
