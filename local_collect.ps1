# 競艇データ夜間収集スクリプト
# タスクスケジューラから毎日0:30に自動実行される

$REPO = "c:\work\kyotei_deploy"
$LOG_DIR = "$REPO\logs"
$LOG_FILE = "$LOG_DIR\collect_$(Get-Date -Format 'yyyyMMdd_HHmm').log"

# ログフォルダ作成
if (-not (Test-Path $LOG_DIR)) { New-Item -ItemType Directory $LOG_DIR | Out-Null }

function Log($msg) {
    $line = "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') $msg"
    Write-Host $line
    Add-Content -Path $LOG_FILE -Value $line
}

Set-Location $REPO
Log "===== 夜間データ収集 開始 ====="

# 最新をpull
Log "[1/4] git pull..."
git pull origin main 2>&1 | ForEach-Object { Log $_ }
if ($LASTEXITCODE -ne 0) {
    Log "[ERROR] git pull 失敗。処理を中断します。"
    exit 1
}

# データ収集（最新から逆順で14日分・約6時間）
Log "[2/4] data_collector.py --days 14 --reverse を開始..."
python data_collector.py --days 14 --reverse 2>&1 | ForEach-Object { Log $_ }
if ($LASTEXITCODE -ne 0) {
    Log "[ERROR] data_collector.py が異常終了しました。"
    exit 1
}

# 変更確認
$changed = git status --porcelain data/training_data.csv data/model.pkl data/collection_state.json
if (-not $changed) {
    Log "[3/4] 新規データなし。コミットをスキップします。"
    Log "===== 終了 ====="
    exit 0
}

# コミット
Log "[3/4] コミット中..."
git add data/training_data.csv data/model.pkl data/collection_state.json
git commit -m "chore: ローカル夜間収集 $(Get-Date -Format 'yyyy-MM-dd HH:mm') JST" 2>&1 | ForEach-Object { Log $_ }

# push（競合時はrebaseして再push）
Log "[4/4] push中..."
git push origin main 2>&1 | ForEach-Object { Log $_ }
if ($LASTEXITCODE -ne 0) {
    Log "[WARN] push失敗。rebaseして再試行..."
    git pull --rebase origin main 2>&1 | ForEach-Object { Log $_ }
    git push origin main 2>&1 | ForEach-Object { Log $_ }
    if ($LASTEXITCODE -ne 0) {
        Log "[ERROR] push 再試行も失敗。手動確認が必要です。"
        exit 1
    }
}

Log "===== 夜間データ収集 完了 ====="
