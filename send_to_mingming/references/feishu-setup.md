# 飞书 SDK 首次配置

1. 在[飞书开放平台](https://open.feishu.cn/app)准备一个企业自建应用，启用机器人能力。将发信账号纳入应用的可用范围，在权限管理中开通以下**用户身份**权限并发布应用版本。可用权限和审批取决于组织设置。

   - `im:message` 用于消息读写。
   - `im:message.send_as_user` 用于以授权账号发送消息。
   - `im:message.p2p_msg:get_as_user` 用于以授权账号读取单聊。

2. 在应用的「安全设置」中添加重定向 URL `https://127.0.0.1`。在[飞书 API 调试台](https://open.feishu.cn/api-explorer)选择同一个自建应用，在支持选择 `open_id` 的用户字段中，通过组织架构核对并复制时明明的 `open_id`。不同应用的 `open_id` 各自独立。
3. 在本机终端进入此技能目录，运行以下命令，将占位符换成刚核对的 ID。

   ```bash
   uv run --script scripts/feishu.py auth --recipient-open-id ou_xxx
   ```

   在终端输入 App ID 和 App Secret。用自己的飞书账号打开脚本给出的授权链接。授权后浏览器会跳到本地地址，即使页面显示无法连接，也可以将地址栏中的完整 URL 粘贴回终端。用本机终端输入凭证。

脚本将凭证保存在 `~/.config/send_to_mingming/credentials.json`（设置了 `XDG_CONFIG_HOME` 时使用该目录），文件权限为 `0600`。授权包含 `offline_access`，脚本会在使用时自动续期。若续期凭证失效，重新运行 `auth`。

发送成功后用 JSON 回执里的 `chat_id` 和 `sent_at_ms` 读取当批消息之后的回复。通过 `parent_id` 或 `root_id` 可以识别引用原消息的回复。普通私信仍需根据内容判断属于哪一题。

[Python SDK](https://github.com/larksuite/oapi-sdk-python) · [发送消息](https://open.feishu.cn/document/server-docs/im-v1/message/create) · [获取单聊历史](https://open.feishu.cn/document/uAjLw4CM/ukTMukTMukTM/reference/im-v1/message/list) · [飞书用户授权说明](https://aily.feishu.cn/hc/8qluoxsa_xdqbv95g)
