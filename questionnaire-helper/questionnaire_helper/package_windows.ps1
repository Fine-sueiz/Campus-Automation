$ErrorActionPreference = "Stop"

Set-Location -Path $PSScriptRoot

if (-not (Test-Path ".\.venv\Scripts\python.exe")) {
    python -m venv .venv
}

.\.venv\Scripts\python.exe -m pip install -r .\requirements.txt
.\.venv\Scripts\python.exe -m pip install pyinstaller
.\.venv\Scripts\python.exe -m playwright install chromium

$packageConfigDir = Join-Path $PSScriptRoot "build\package_template"
$packageConfigPath = Join-Path $packageConfigDir "config.json"
New-Item -ItemType Directory -Force -Path $packageConfigDir | Out-Null

$template = Get-Content -Raw -Path ".\config.json" | ConvertFrom-Json
$template.questionnaires = @(
    [pscustomobject]@{
        label = "问卷1"
        url = ""
        start_time = ""
        submit_time = ""
    }
)
$template.min_submit_after_start_seconds = 0
$template.save_qr_code_images = $true
$template.qr_code_output_dir = "saved_qr_codes"
$template.qr_code_wait_seconds = 5
$template.profile = [pscustomobject]@{}
$template.profile_fields = @()
$template.answers = @()
if ($template.ml_classifier -and $template.ml_classifier.label_answers) {
    foreach ($property in $template.ml_classifier.label_answers.PSObject.Properties) {
        if ($property.Value.PSObject.Properties["value"]) {
            $property.Value.value = ""
        }
    }
}
$templateJson = $template | ConvertTo-Json -Depth 20
$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
[System.IO.File]::WriteAllText($packageConfigPath, $templateJson, $utf8NoBom)

$dataArgs = @(
    "--add-data", "$packageConfigPath;.",
    "--add-data", "question_model.json;.",
    "--add-data", "question_model_new.json;.",
    "--add-data", "training_data.jsonl;."
)

.\.venv\Scripts\python.exe -m PyInstaller `
    --noconfirm `
    --onedir `
    --windowed `
    --name "问卷自动填写助手" `
    @dataArgs `
    .\launcher.py

$distDir = Join-Path $PSScriptRoot "dist\问卷自动填写助手"
$exePath = Join-Path $distDir "问卷自动填写助手.exe"
$sha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $exePath).Hash
$safetyNote = @"
问卷自动填写助手 - 安全说明

这个程序的用途：
1. 打开使用者自己填写的问卷链接。
2. 根据使用者在界面中填写的信息，自动填写普通问卷题目。
3. 如果使用者启用大模型兜底，并提供自己的 API Key，才会请求配置中的 DeepSeek/OpenAI-compatible 接口。

这个程序不会做的事：
1. 不会自动开机启动。
2. 不会安装后台服务。
3. 不会读取通讯录、浏览器密码、聊天记录、桌面文件或其他无关文件。
4. 不会打包发布者的姓名、学号、手机号、微信号或 API Key。
5. 不会把 API Key 写入内置配置文件；API Key 只来自使用者运行时填写或本机环境变量。

可能出现的联网行为：
1. 访问使用者填写的问卷网页。
2. Playwright/Chromium 浏览器运行所需的正常网页请求。
3. 启用大模型兜底时，访问配置中的 API 地址，例如 https://api.deepseek.com/。

需要知道的限制：
1. 这个 exe 没有商业代码签名证书，所以 Windows 可能显示“未知发布者”或“不常见应用”。
2. 这类提示不等于恶意，只表示它不是由受信任证书签名的软件。
3. 使用前可以把整个文件夹交给 Windows Defender 或杀毒软件扫描。

当前 exe SHA256：
$sha256

运行方式：
解压整个“问卷自动填写助手”文件夹后，双击“问卷自动填写助手.exe”。
不要只单独拷贝 exe，因为它需要旁边的 _internal 文件夹。
"@
[System.IO.File]::WriteAllText((Join-Path $distDir "安全说明.txt"), $safetyNote, $utf8NoBom)

Write-Host ""
Write-Host "打包完成：$exePath"
Write-Host "安全说明：$distDir\安全说明.txt"
Write-Host "如果在其他电脑运行，首次可能仍需安装 Playwright Chromium 浏览器。"
