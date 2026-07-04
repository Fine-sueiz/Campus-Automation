# Questionnaire Helper

这是一个 Playwright 浏览器自动化脚本，用于在指定时间打开问卷并填写你预先配置的普通题目。它会优先使用本地配置、本地题目分类模型和简单验证规则；如果开启 `llm_validation`，还可以把本地流程填不了的未答题交给 OpenAI-compatible 大模型兜底。遇到图片验证码、滑块、人机验证、短信验证等复杂验证时仍会停止自动提交，等待你人工处理。

## 当前重要状态

- 推荐入口是图形界面：[launcher.py](./launcher.py)，使用者填写自己的问卷链接、开始时间、每个问卷的提交时间、姓名、学号、手机号、微信号、学院、年级、是否参加等信息后点击“生成配置并开始”。
- 图形界面会基于 [config.json](./config.json) 自动生成 `generated_config.json`，再调用 [fill_questionnaire.py](./fill_questionnaire.py) 执行。
- 默认使用 DeepSeek 兼容接口：`base_url=https://api.deepseek.com/`，模型名目前在配置里是 `deepseek-v4-flash`。
- API Key 不应写进 `config.json`。可以在图形界面里填，或在 PowerShell 设置环境变量 `DEEPSEEK_API_KEY`。
- 本地已有答案优先填写；本地填不了的题交给大模型前，会先把本地已有字段/答案目录告诉大模型做路由判断。若模型判断题目其实需要姓名、学号、手机号、微信号、学院、年级、是否参加等本地信息，会转回本地程序填写；若本地没有对应隐私信息，则留给人工。
- 协议/隐私政策确认项会自动同意，包括页面 checkbox 和“同意并继续”这类弹窗按钮。
- 提交后如果页面返回群聊二维码，会自动保存二维码图片，并把问卷链接和图片路径记录下来，方便之后查看。
- 提交后如果进入“检测程序”等异常页面，会保留浏览器，方便确认是否被问卷星拦截。

## 给其他人使用

推荐打包成 Windows exe 后发给别人：

```powershell
cd D:\爬虫\questionnaire_helper
.\package_windows.ps1
```

打包完成后，把整个文件夹发给对方：

```text
D:\爬虫\questionnaire_helper\dist\问卷自动填写助手
```

对方双击：

```text
问卷自动填写助手.exe
```

即可打开图形界面。对方需要填写自己的个人信息、问卷链接和开始时间；如果启用大模型兜底，需要使用对方自己的 DeepSeek API Key，不要把你的密钥打包或发给别人。

## 安装

```powershell
cd D:\爬虫\questionnaire_helper
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m playwright install chromium
```

## 配置

编辑 `config.json`：

- `url`: 单个问卷链接；如果配置了 `questionnaires` 或 `urls`，会优先使用列表。
- `questionnaires`: 多个问卷配置列表，每项至少包含 `url`，也可以覆盖 `label`、`start_time`、`submit_time`、`answers` 等配置。
- `urls`: 多个问卷链接的简写列表，适合所有问卷共用同一套答案。
- `start_time`: 开始填写时间，格式是 `YYYY-MM-DD HH:MM:SS`，也支持当天时间 `HH:MM`。
- `submit_time`: 最早提交时间，格式同 `start_time`。写在单个问卷配置里时，可以让多个问卷分别在不同时间提交。
- `timezone`: 默认 `Asia/Shanghai`。
- `profile`: 简单固定个人信息，键名会作为关键词匹配题目。
- `profile_fields`: 需要多个关键词匹配的个人信息，例如“姓名 / 名字 / 请输入你的名字”都填同一个值。
- `answers`: 普通题答案。
- `simple_validation_rules`: 简单验证题规则，例如“此题请选择A”。
- `ml_classifier`: 可选的本地题目分类模型，用训练集把相似题目归类后自动选择答案。
- `llm_validation`: 可选的大模型兜底，默认可以处理本地流程没有填上的普通文字题和选择题。
- `auto_submit`: `true` 时在全部配置项都填成功且没有复杂验证时自动提交。
- `submit_delay_seconds`: 填完后、提交前额外等待秒数，支持小数，例如 `0.5`。
- `min_submit_after_start_seconds`: 每份问卷开始运行后，至少过多少秒才允许提交。例如设为 `10`，即使 2 秒填完，也会等到第 10 秒后再提交。
- `close_delay_seconds`: 提交后等待几秒再做最终检查并关闭页面，默认示例是 2 秒。
- `save_qr_code_images`: `true` 时，提交后会尝试保存页面上的群聊二维码图片。
- `qr_code_output_dir`: 二维码图片保存目录，默认是 `saved_qr_codes`。相对路径会基于当前配置文件所在目录。
- `qr_code_wait_seconds`: 提交后最多等待几秒寻找二维码，默认是 5 秒。
- `auto_agree_terms`: `true` 时自动勾选“我已阅读并同意 / 用户协议 / 隐私政策”等协议确认项，并点击“同意并继续”等协议弹窗按钮。

多问卷配置示例：

```json
{
  "start_time": "2026-05-13 09:00:00",
  "min_submit_after_start_seconds": 8,
  "questionnaires": [
    {
      "label": "问卷1",
      "url": "https://example.com/form-1",
      "submit_time": "2026-05-13 09:00:15"
    },
    {
      "label": "问卷2",
      "url": "https://example.com/form-2",
      "start_time": "2026-05-13 09:05:00",
      "submit_time": "2026-05-13 09:05:20"
    }
  ]
}
```

也可以用简写：

```json
{
  "urls": [
    "https://example.com/form-1",
    "https://example.com/form-2"
  ]
}
```

列表里的单个问卷配置会继承顶层配置；如果同名字段写在该问卷项里，就覆盖顶层配置。例如第二个问卷可以单独覆盖 `start_time`、`submit_time` 或 `answers`。

如果只写当天时间，也可以这样指定不同提交点：

```json
{
  "start_time": "09:00",
  "min_submit_after_start_seconds": 10,
  "questionnaires": [
    {
      "label": "问卷1",
      "url": "https://example.com/form-1",
      "submit_time": "09:00:20"
    },
    {
      "label": "问卷2",
      "url": "https://example.com/form-2",
      "submit_time": "09:01:00"
    }
  ]
}
```

题目配置示例：

```json
{
  "label": "感兴趣的方向",
  "keywords": ["你感兴趣的方向", "兴趣方向", "感兴趣"],
  "type": "multiple",
  "value": ["人工智能", "后端开发"]
}
```

`keywords` 是关键词列表，页面题目只要包含其中任意一个关键词就会尝试填写。旧写法 `"question": "题目文字"` 仍然可用，但推荐新写法。

个人信息多关键词示例：

```json
{
  "label": "姓名",
  "keywords": ["姓名", "名字", "请输入你的名字", "请输入姓名"],
  "value": "张三"
}
```

支持的 `type`：

- `text`: 填空题。
- `single`: 单选题。
- `multiple`: 多选题。

## 训练题目分类模型

如果问卷题目写法变化很大，可以使用本地小型神经网络分类器。训练集在 `training_data.jsonl`，每行一条 JSON：

```json
{"text":"请输入你的姓名","label":"name"}
{"text":"请输入你的学号","label":"student_id"}
{"text":"请输入你的微信号","label":"wechat"}
{"text":"请选择你的学院","label":"college"}
{"text":"此题请选择A","label":"validation_a"}
```

当前示例训练集使用 7 个标签：

- `name`: 姓名。
- `student_id`: 学号。
- `wechat`: 微信号。
- `phone`: 手机号。
- `college`: 学院。
- `validation_a`: 简单验证题选择 A。
- `validation_b`: 简单验证题选择 B。

训练模型：

```powershell
cd D:\爬虫\questionnaire_helper
.\.venv\Scripts\python.exe .\ml_question_model.py --train .\training_data.jsonl --out .\question_model.json
```

然后在 `config.json` 中开启：

```json
"ml_classifier": {
  "enabled": true,
  "model_path": "question_model.json",
  "confidence_threshold": 0.75,
  "label_answers": {
    "name": {
      "type": "text",
      "value": "张三"
    },
    "student_id": {
      "type": "text",
      "value": "00000000"
    },
    "wechat": {
      "type": "text",
      "value": "your_wechat_id"
    },
    "phone": {
      "type": "text",
      "value": "13800000000"
    },
    "college": {
      "type": "single",
      "value": "计算机学院"
    },
    "validation_a": {
      "type": "single",
      "value": "A"
    },
    "validation_b": {
      "type": "single",
      "value": "B"
    }
  }
}
```

运行时，脚本会把未填写的题目文本交给模型分类。只有预测置信度达到 `confidence_threshold`，并且该标签在 `label_answers` 中配置了答案，才会填写；否则跳过或停止等待人工检查。

## 大模型未答题兜底

如果本地配置、本地分类模型和简单验证规则都没填上某道题，可以开启 `llm_validation`。脚本会在本地规则都执行完以后，把仍未填写的非隐私题交给 OpenAI-compatible `/chat/completions` 接口；已经由题库或配置填好的题不会重复交给大模型。图片验证码、滑块、人机验证、短信验证、二维码验证、个人隐私题仍然不会交给大模型。

先在 PowerShell 中设置密钥：

```powershell
$env:OPENAI_API_KEY="你的密钥"
```

如果使用 DeepSeek，可以这样设置：

```powershell
$env:DEEPSEEK_API_KEY="你的DeepSeek密钥"
```

配置示例：

```json
"llm_validation": {
  "enabled": true,
  "mode": "all_unanswered",
  "base_url": "https://api.openai.com/v1",
  "api_key_env": "OPENAI_API_KEY",
  "model": "gpt-4o-mini",
  "timeout_seconds": 20,
  "confidence_threshold": 0.7,
  "max_candidates": 20,
  "max_text_length": 500,
  "temperature": 0,
  "local_routing": true,
  "share_local_values": false,
  "trigger_keywords": ["验证", "请选择", "此题", "本题", "为了验证", "计算", "等于", "多少", "机器人"],
  "privacy_keywords": ["姓名", "名字", "学号", "学生编号", "学生证号", "手机号", "手机号码", "联系电话", "联系方式", "微信", "微信号", "身份证", "证件号", "邮箱", "邮件", "地址", "宿舍", "班级", "学院", "院系"]
}
```

DeepSeek 示例：

```json
"llm_validation": {
  "enabled": true,
  "mode": "all_unanswered",
  "base_url": "https://api.deepseek.com/",
  "api_key_env": "DEEPSEEK_API_KEY",
  "model": "DeepSeek-V4-Flash",
  "timeout_seconds": 20,
  "confidence_threshold": 0.7,
  "max_candidates": 20,
  "max_text_length": 500,
  "temperature": 0,
  "local_routing": true,
  "share_local_values": false,
  "trigger_keywords": ["验证", "请选择", "此题", "本题", "为了验证", "计算", "等于", "多少", "机器人"],
  "privacy_keywords": ["姓名", "名字", "学号", "学生编号", "学生证号", "手机号", "手机号码", "联系电话", "联系方式", "微信", "微信号", "身份证", "证件号", "邮箱", "邮件", "地址", "宿舍", "班级", "学院", "院系"]
}
```

`api_key_env` 填的是环境变量名字，不是密钥本身。密钥只放在 PowerShell 环境变量里。

`mode` 支持：

- `all_unanswered`: 本地流程填不了的未答题都交给大模型。
- `validation_only`: 只把疑似验证题、注意力检测题、简单计算题交给大模型。

`local_routing` 默认为 `true`。开启后，大模型收到的不是直接作答请求，而是“本地信息目录 + 当前题目 + 当前选项”：它会先判断这道题是不是在问本地程序已有的信息，例如姓名、学号、手机号、微信号、学院、年级、是否参加、简单验证题答案等。如果判断是本地信息，会返回需要的 `local_id`，程序再用本地配置里的真实值填写；如果不是本地信息，才由大模型自己给出答案。

`share_local_values` 默认为 `false`，表示只把本地字段名、关键词和答案类型发给大模型，不发送具体姓名、手机号等值。一般不建议开启；程序转回本地填写时会自己读取真实值。

`privacy_keywords` 命中的题目如果能被大模型路由到本地信息，会由本地程序填写；如果本地没有对应信息，则不会让大模型生成隐私答案，会留给人工处理。注意：这个功能只处理页面上的文字题和普通选择题。图片验证码、滑块、人机验证、短信验证、二维码验证不会交给大模型，脚本会保持浏览器打开让你手动处理。

## 运行

图形界面方式：

```powershell
cd D:\爬虫\questionnaire_helper
.\.venv\Scripts\python.exe .\launcher.py
```

界面里填写问卷链接、开始时间、提交时间、开始后至少等待秒数、姓名、学号、手机号、微信、学院、年级、是否参加等信息，点击“生成配置并开始”。启动器会生成 `generated_config.json`，然后自动调用原来的填写程序。

多个问卷链接时，“提交时间”输入框按行对应链接：

```text
问卷链接：
https://example.com/form-1
https://example.com/form-2

提交时间：
09:00:20
09:01:00
```

如果勾选“保存提交后的二维码”，提交后会在程序目录下生成：

```text
saved_qr_codes
```

每个二维码会保存为一张 `.png` 图片，文件名里包含问卷序号、问卷标签、保存时间和问卷链接哈希。目录里还会自动生成一个方便浏览的索引页：

```text
saved_qr_codes\index.html
```

双击这个网页，就能按保存时间、问卷链接查看对应二维码。目录里还会追加一个机器可读的记录文件：

```text
saved_qr_codes\qr_code_records.jsonl
```

这份记录文件每行对应一次保存，里面有原始问卷链接、提交后的结果页链接和本地二维码图片路径。

如果启用大模型兜底，可以在界面里填写 DeepSeek API Key；也可以先在 PowerShell 里设置：

```powershell
$env:DEEPSEEK_API_KEY="你的真实DeepSeek密钥"
```

命令行方式：

```powershell
cd D:\爬虫\questionnaire_helper
.\.venv\Scripts\python.exe .\fill_questionnaire.py --config .\config.json
```

## 打包成 exe

Windows 下可以运行：

```powershell
cd D:\爬虫\questionnaire_helper
.\package_windows.ps1
```

打包完成后入口在：

```text
D:\爬虫\questionnaire_helper\dist\问卷自动填写助手\问卷自动填写助手.exe
```

打包后的程序会打开图形界面，使用者只需要填写自己的个人信息、问卷链接、开始时间和提交时间即可。大模型密钥可以在界面里填写，也可以提前用 PowerShell 设置 `DEEPSEEK_API_KEY`。

## 行为说明

- 脚本会打开真实浏览器窗口，方便你随时接管。
- 如果检测到同意协议、隐私政策、服务协议等勾选项或“同意并继续”弹窗按钮，会自动同意；包含“不同意 / 拒绝 / 取消”的选项不会点击。
- 如果检测到复杂验证，会停止自动提交并保持浏览器打开。
- 如果提交后进入“检测程序”等异常页面，会停止关闭浏览器，方便你确认是否被拦截。
- 如果提交后页面上出现“群聊二维码 / 扫码入群 / QR code”等图片、canvas 或 svg，程序会保存到 `qr_code_output_dir`。
- 如果开启了 `llm_validation` 但没有设置 API key，或大模型返回的答案不在页面选项里，会停止自动提交并保持浏览器打开。
- 如果任何配置题目没有填成功，会停止自动提交并保持浏览器打开。
- 如果 `auto_submit` 为 `false`，脚本只填写，不提交。

## 当前填写决策流程

1. 先用本地 `profile_fields` 和 `answers` 填写个人信息、学院、年级、是否参加等固定答案。
2. 再用 `simple_validation_rules` 处理明确写出答案的验证题，例如“此题请选择A”。
3. 再自动勾选协议 checkbox，例如“我已阅读并同意”。
4. 再处理大模型兜底：
   - `mode=all_unanswered` 时，本地没填上的题会交给大模型路由判断。
   - 大模型会先判断题目是否需要本地已有信息；如果需要，会点明需要哪个 `local_id`，再由本地程序用本地值填写。
   - 如果不是本地已有信息，才由大模型自己给出答案。
   - 命中 `privacy_keywords` 且无法路由到本地信息的隐私题，会留给人工。
   - 图片验证码、滑块、人机验证、短信验证、二维码验证永远不交给大模型。
5. 填完后等待 `submit_delay_seconds`。
6. 如果配置了 `min_submit_after_start_seconds`，确保这份问卷开始运行后至少经过指定秒数。
7. 如果配置了 `submit_time`，等到该问卷自己的最早提交时间。
8. 点击提交。
9. 如果出现协议弹窗，会点击“同意并继续”等按钮。
10. 提交后等待 `close_delay_seconds`，尝试保存群聊二维码。
11. 检查页面标题和正文，发现“检测程序/人机验证/提交失败”等异常会停住。

## 常见问题

- `latin-1 codec can't encode characters`: 通常是把 `$env:DEEPSEEK_API_KEY` 设置成了中文占位文字，例如“你的DeepSeek密钥”。要换成真实 `sk-...` 密钥。
- `could not parse LLM JSON`: 模型返回格式不规范。当前程序已支持 JSON mode 和短文本兜底；如果 DeepSeek 报不支持 `response_format`，把 `config.json` 里的 `"json_mode": false`。
- 后台没收到提交结果但日志显示 done：看最终页面标题，如果是“检测程序”，说明可能被问卷星拦截。
- 协议没点到：确认弹窗按钮文字是否在 `terms_agreement_button_texts` 里，例如“同意并继续”。


整体关系：

  config.json
          |
          v
  fill_questionnaire.py  -------->  打开浏览器并填写问卷
          |
          | 如果启用模型
          v
  question_model_new.json
          ^
          |
  ml_question_model.py
          ^
          |
  training_data.jsonl
