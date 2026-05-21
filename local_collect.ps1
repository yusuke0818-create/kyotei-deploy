# Kyotei data collection script

$REPO = "c:\work\kyotei_deploy"
$LOG_DIR = "$REPO\logs"
$LOG_FILE = "$LOG_DIR\collect_$(Get-Date -Format 'yyyyMMdd_HHmm').log"

if (-not (Test-Path $LOG_DIR)) { New-Item -ItemType Directory $LOG_DIR | Out-Null }

function Log($msg) {
    $line = "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') $msg"
    Write-Host $line
    Add-Content -Path $LOG_FILE -Value $line
}

Set-Location $REPO
Log "===== START ====="

Log "[1/4] git pull..."
git pull origin main 2>&1 | ForEach-Object { Log $_ }
if ($LASTEXITCODE -ne 0) {
    Log "[ERROR] git pull failed."
    exit 1
}

Log "[2/4] data_collector.py --days 7 --reverse ..."
python data_collector.py --days 7 --reverse 2>&1 | ForEach-Object { Log $_ }
if ($LASTEXITCODE -ne 0) {
    Log "[ERROR] data_collector.py failed."
    exit 1
}

$changed = git status --porcelain data/training_data.csv data/model.pkl data/collection_state.json
if (-not $changed) {
    Log "[3/4] No new data. Skip commit."
    Log "===== END ====="
    exit 0
}

Log "[3/4] Committing..."
git add data/training_data.csv data/model.pkl data/collection_state.json
$dateStr = Get-Date -Format 'yyyy-MM-dd HH:mm'
git commit -m "chore: local collect $dateStr JST" 2>&1 | ForEach-Object { Log $_ }

Log "[4/4] Pushing..."
git push origin main 2>&1 | ForEach-Object { Log $_ }
if ($LASTEXITCODE -ne 0) {
    Log "[WARN] Push failed. Retrying with rebase..."
    git pull --rebase origin main 2>&1 | ForEach-Object { Log $_ }
    git push origin main 2>&1 | ForEach-Object { Log $_ }
    if ($LASTEXITCODE -ne 0) {
        Log "[ERROR] Push retry failed."
        exit 1
    }
}

Log "===== DONE ====="
