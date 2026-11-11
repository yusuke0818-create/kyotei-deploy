#!/bin/bash
# 目的：週1回の更新（データ収集 → マージ → モデル再学習 → model.pkl の push）を一括実行する
#       Windows 版 weekly_update.ps1 の Mac 版
# 作成日：2026-11-11
# 対象OS：macOS
# 依存：git、Python 3.11（.venv があればそれを使う）
#
# 使い方（ターミナルで実行）：
#   cd /Users/chaba/work/AI_Projects/kyotei-deploy
#   ./weekly_update.sh
#
# - 実行中は Mac がスリープしないようにしている（caffeinate）。蓋を閉じると止まるので注意
# - 実行記録は logs/weekly_update_YYYYMMDD_HHMM.log に保存する

set -u

# スリープ防止：caffeinate の下で自分自身を実行し直す
if [ -z "${KYOTEI_CAFFEINATED:-}" ]; then
    export KYOTEI_CAFFEINATED=1
    exec caffeinate -i "$0" "$@"
fi

# スクリプトの置き場所（kyotei-deploy）へ移動する
cd "$(dirname "$0")" || exit 1

# Python：.venv があれば使い、なければ python3
if [ -x ".venv/bin/python" ]; then
    PY=".venv/bin/python"
else
    PY="python3"
fi

# 実行記録：画面とファイルの両方に出す
mkdir -p logs
LOG_FILE="logs/weekly_update_$(date '+%Y%m%d_%H%M').log"
exec > >(tee -a "$LOG_FILE") 2>&1

fail() {
    echo "$1 中断します。"
    exit 1
}

echo "開始: $(date '+%Y-%m-%d %H:%M:%S')（Python: $PY）"

echo "[1/4] データ収集..."
"$PY" data_collector.py --days 60 || fail "収集失敗。"

echo "[2/4] マージ..."
"$PY" merge_csv.py || fail "マージ失敗。"

echo "[3/4] モデル再学習..."
"$PY" model.py || fail "再学習失敗。"

echo "[4/4] Git push..."
git add data/model.pkl
git commit -m "Weekly model update $(date '+%Y-%m-%d')"
git push || fail "Push失敗。"

echo "終了: $(date '+%Y-%m-%d %H:%M:%S')"
echo "完了！Renderの自動デプロイを確認してください。"
