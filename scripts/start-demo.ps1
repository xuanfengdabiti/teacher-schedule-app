<#
.SYNOPSIS
  启动本地虚构演示环境（Django API + PostgreSQL），导入 12 个示例 CSV，并写入演示账号与样例。

.DESCRIPTION
  仅用于本地演示：
  - 独立 Compose 项目 teacher-schedule-demo，使用 infra/deploy/.env.demo 的虚构参数。
  - 不删除/覆盖其他项目的数据卷，也不读取真实学校数据。
  - 失败时返回清楚错误；成功提示 API 地址与演示账号。

.EXAMPLE
  pwsh scripts/start-demo.ps1
#>
[CmdletBinding()]
param(
    [int]$HealthTimeoutSeconds = 240
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
$composeFile = Join-Path $repoRoot "infra/deploy/compose.yaml"
$envFile = Join-Path $repoRoot "infra/deploy/.env.demo"
# 可选的本机 AI 配置（已 gitignore）；不存在时普通课表照常启动。
$aiEnvFile = Join-Path $repoRoot "infra/deploy/.env.ai.local"
$seedFile = Join-Path $repoRoot "scripts/seed_demo.py"
$seedExtraFile = Join-Path $repoRoot "scripts/seed_demo_extra.py"
$seedTeamFile = Join-Path $repoRoot "scripts/seed_demo_team.py"
$seedTeamExtraFile = Join-Path $repoRoot "scripts/seed_demo_team_extra.py"
$templatesDir = Join-Path $repoRoot "templates/import"
$projectName = "teacher-schedule-demo"
$apiUrl = "http://127.0.0.1:18000/api/v1/"
$healthUrl = $apiUrl + "health/"

function Invoke-DemoCompose {
    param([Parameter(ValueFromRemainingArguments = $true)][string[]]$ComposeArgs)
    $envArgs = @("--env-file", $envFile)
    if (Test-Path $aiEnvFile) {
        # 后者覆盖同名变量；只在确实存在时加载，不打印文件内容。
        $envArgs += @("--env-file", $aiEnvFile)
    }
    & docker compose -p $projectName @envArgs -f $composeFile @ComposeArgs
    if ($LASTEXITCODE -ne 0) {
        throw "docker compose 命令失败（退出码 $LASTEXITCODE）：docker compose -p $projectName $($envArgs -join ' ') -f `"$composeFile`" $($ComposeArgs -join ' ')"
    }
}

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    throw "未找到 docker 命令。请先安装并启动 Docker Desktop。"
}
if (-not (Test-Path $envFile)) {
    throw "缺少演示环境文件：$envFile"
}
if (-not (Test-Path $seedFile)) {
    throw "缺少演示种子脚本：$seedFile"
}
if (-not (Test-Path $seedExtraFile)) {
    throw "缺少演示扩充种子脚本：$seedExtraFile"
}
if (-not (Test-Path $seedTeamFile)) {
    throw "缺少演示团队种子脚本：$seedTeamFile"
}
if (-not (Test-Path $seedTeamExtraFile)) {
    throw "缺少演示团队整周课程种子脚本：$seedTeamExtraFile"
}
if (-not (Test-Path $templatesDir)) {
    throw "缺少导入模板目录：$templatesDir"
}

Write-Host "==> 构建并启动演示容器（项目 $projectName）"
Invoke-DemoCompose @("up", "-d", "--build")

Write-Host "==> 等待 API 健康检查：$healthUrl"
$deadline = (Get-Date).AddSeconds($HealthTimeoutSeconds)
$healthy = $false
while ((Get-Date) -lt $deadline) {
    try {
        $response = Invoke-WebRequest -Uri $healthUrl -UseBasicParsing -TimeoutSec 3
        if ($response.StatusCode -eq 200) {
            $healthy = $true
            break
        }
    }
    catch {
        # 容器仍在启动/迁移；继续等待直到超时。
    }
    Start-Sleep -Seconds 2
}
if (-not $healthy) {
    throw "API 健康检查未在 $HealthTimeoutSeconds 秒内返回 200（$healthUrl）。查看日志：docker compose -p $projectName --env-file `"$envFile`" -f `"$composeFile`" logs api"
}

Write-Host "==> 复制导入模板与种子脚本到 api 容器"
Invoke-DemoCompose @("exec", "-T", "api", "sh", "-c", "rm -rf /tmp/teacher-schedule-demo && mkdir -p /tmp/teacher-schedule-demo")
Get-ChildItem -Path $templatesDir -Filter "*.csv" | ForEach-Object {
    Invoke-DemoCompose @("cp", $_.FullName, "api:/tmp/teacher-schedule-demo/$($_.Name)")
}
Invoke-DemoCompose @("cp", $seedFile, "api:/tmp/seed_demo.py")
Invoke-DemoCompose @("cp", $seedExtraFile, "api:/tmp/seed_demo_extra.py")
Invoke-DemoCompose @("cp", $seedTeamFile, "api:/tmp/seed_demo_team.py")
Invoke-DemoCompose @("cp", $seedTeamExtraFile, "api:/tmp/seed_demo_team_extra.py")

Write-Host "==> 导入虚构课表并创建演示样例（重复启动会跳过已有数据）"
Invoke-DemoCompose @("exec", "-T", "api", "sh", "-c", "python manage.py shell < /tmp/seed_demo.py")

Write-Host "==> 扩充课程、教师、教室与整学期课次（重复启动只补缺失）"
Invoke-DemoCompose @("exec", "-T", "api", "sh", "-c", "python manage.py shell < /tmp/seed_demo_extra.py")

Write-Host "==> 建立李/张/王三位老师的共享任课与轮讲课程（重复启动只补缺失）"
Invoke-DemoCompose @("exec", "-T", "api", "sh", "-c", "python manage.py shell < /tmp/seed_demo_team.py")

Write-Host "==> 为李/张/王三位老师补充整周课程（重复启动只补缺失）"
Invoke-DemoCompose @("exec", "-T", "api", "sh", "-c", "python manage.py shell < /tmp/seed_demo_team_extra.py")

Write-Host ""
Write-Host "演示环境已就绪：$apiUrl"
Write-Host "教师甲  t0001@example.invalid / TeacherDemo2026!"
Write-Host "教师乙  t0002@example.invalid / TeacherDemo2026!"
Write-Host "教务    academic-staff@example.invalid / PreviewOffice2026!  （/admin/）"
Write-Host "Android 模拟器访问：http://10.0.2.2:18000/api/v1/"
Write-Host "真机访问：把 API 地址改为电脑局域网 IP，例如 http://192.168.1.10:18000/api/v1/"
Write-Host "停止：docker compose -p $projectName --env-file `"$envFile`" -f `"$composeFile`" down"
Write-Host "说明：docs/local-demo.md"
