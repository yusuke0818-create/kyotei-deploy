# 目的：複数の収集CSVをマージ・重複除去してmerged_data.csvを生成する
# 作成日：2026-05-23
# 対象OS：Windows / Linux
# 依存ライブラリ：pandas

import os

import pandas as pd

FILES = [
    "data/fallback_data_snapshot.csv",
    "data/reverse_data.csv",
    "data/training_data.csv",
]
KEY_COLS = ["date", "venue_code", "race_no", "boat_no"]
OUTPUT = "data/merged_data.csv"

dfs = []
for f in FILES:
    if not os.path.exists(f):
        print(f"スキップ（ファイル未存在）: {f}")
        continue
    df = pd.read_csv(f)
    print(f"{f}: {len(df)}行 ({df['date'].min()} 〜 {df['date'].max()})")
    dfs.append(df)

if not dfs:
    raise FileNotFoundError("読み込めるCSVが1件もありません")

merged = pd.concat(dfs, ignore_index=True)
print(f"\nマージ後（重複除去前）: {len(merged)}行")

merged = merged.drop_duplicates(subset=KEY_COLS)
merged = merged.sort_values("date").reset_index(drop=True)
print(f"重複除去後: {len(merged)}行 ({merged['date'].min()} 〜 {merged['date'].max()})")

merged.to_csv(OUTPUT, index=False)
print(f"\n保存完了: {OUTPUT}")

print("\n=== 生データ特徴量 Null率 ===")
features = [
    "boat_no", "venue_code", "racer_grade_num", "win_rate", "local_win_rate",
    "national_2rate", "local_2rate", "st_avg", "fly_count",
    "motor_rate", "exhibition_time", "exhibition_st", "tilt",
    "is_night", "wind_speed",
]
for col in features:
    if col not in merged.columns:
        print(f"  {col}: 列なし（要確認）")
        continue
    null_rate = merged[col].isna().sum() / len(merged) * 100
    print(f"  {col}: {null_rate:.1f}%")
