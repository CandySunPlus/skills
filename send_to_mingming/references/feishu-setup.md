# 飞书外部群首次配置

1. 在[飞书开放平台](https://open.feishu.cn/app)打开企业自建应用「访谈问题转发」，启用机器人能力。在「权限管理」开通以下**应用身份**权限。

   - `im:message` 用于发送和读取消息。
   - `im:message.group_msg` 用于读取机器人所在群的历史消息。飞书可能要求企业管理员审批。
   - `im:chat.members:read` 用于绑定时在目标群内核对时明明的成员身份。

2. 在「版本管理与发布」创建版本，在「对外共享」勾选「允许机器人被添加到外部群中使用」，并提交发布。应用可用范围应包含负责加机器人的企业成员。开启对外共享可能需要企业认证或个人实名认证。
3. 由群主创建与时明明的外部群，在「群设置 → 群机器人 → 添加机器人」选择应用机器人「访谈问题转发」。从群设置复制以 `oc_` 开头的群 ID。
4. 在本机终端进入此技能目录，执行以下命令。把占位符换成目标群 ID，再按提示输入同一个应用的 App ID 和 App Secret。

   ```bash
   uv run --script scripts/feishu.py configure --chat-id oc_xxx
   ```

   脚本从群成员列表里按显示名「时明明」定位收件人，并检查机器人能否读该群历史。如果她在群里显示为其他名字，先核对身份，再给命令添加 `--respondent-name "实际显示名"`。配置保存在 `~/.config/send_to_mingming/bot.json`，文件权限为 `0600`，可用 `XDG_CONFIG_HOME` 指定配置根目录。

发送回执关联到本批问题。时明明在群里引用原消息并按题号回复时，可以用引用关系区分不同批次。读取按需发生，使用机器人所在的固定群及批次时间范围。

[机器人对外共享](https://open.feishu.cn/document/develop-robots/add-bot-to-external-group?lang=zh-CN) · [获取会话历史](https://open.feishu.cn/document/uAjLw4CM/ukTMukTMukTM/reference/im-v1/message/list) · [飞书 Python SDK](https://github.com/larksuite/oapi-sdk-python)
