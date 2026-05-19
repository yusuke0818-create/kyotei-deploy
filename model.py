# 目的：XGBoostモデルの学習・保存・予測・的中率計算
# 作成日：2026-05-19
# 対象OS：Linux（GitHub Actions）/ Windows
# 依存ライブラリ：xgboost, scikit-learn, pandas, joblib

import os
from datetime import datetime

import joblib
import numpy as np
import pandas as pd
from sklearn.model_selection import train_test_split
from xgboost import XGBClassifier

MODEL_PATH = "data/model.pkl"
TRAINING_CSV = "data/training_data.csv"

# 14特徴量（設計書確定版）
# XGBoostはNaNをネイティブで処理するため欠損値補完は不要
FEATURES = [
    "boat_no",          # 艇番 (1-6)
    "racer_grade_num",  # 選手グレード数値化 (A1=4, A2=3, B1=2, B2=1)
    "win_rate",         # 全国勝率
    "local_win_rate",   # 当地勝率
    "national_2rate",   # 全国2連率（小数: 14.14% → 0.1414）
    "local_2rate",      # 当地2連率（小数）
    "st_avg",           # 平均スタートタイミング
    "fly_count",        # フライング件数
    "motor_rate",       # モーター2連率（小数）
    "exhibition_time",  # 展示タイム（直前情報。未公開時はNaN）
    "exhibition_st",    # 展示スタートタイミング（未公開時はNaN）
    "tilt",             # チルト角（未公開時はNaN）
    "is_night",         # ナイター開催フラグ（0/1）
    "wind_speed",       # 風速（m/s。未取得時はNaN）
]


def train() -> float:
    """
    training_data.csv でXGBoostを学習してmodel.pklに保存する。
    返り値：バックテスト単勝的中率（例：0.423 = 42.3%）
    """
    if not os.path.exists(TRAINING_CSV):
        raise FileNotFoundError(f"学習データが見つかりません: {TRAINING_CSV}")

    df = pd.read_csv(TRAINING_CSV)
    # is_firstがNaNの行のみ除外。特徴量のNaNはXGBoostがネイティブ処理する
    df = df.dropna(subset=["is_first"])

    # FEATURES列が存在しない場合はNaNで補完（旧データとの互換性）
    for col in FEATURES:
        if col not in df.columns:
            df[col] = np.nan

    X = df[FEATURES]
    y = df["is_first"]

    # 直近1年をテスト・それ以前を学習データとして分割
    if "date" in df.columns:
        df_dates = df["date"].astype(str)
        cutoff = df_dates.max()
        # うるう年（2/29等）に対応するため datetime で安全に1年引く
        cutoff_date = datetime.strptime(cutoff, "%Y%m%d").date()
        try:
            cutoff_1y_date = cutoff_date.replace(year=cutoff_date.year - 1)
        except ValueError:
            # 2/29 → 2/28 にフォールバック
            cutoff_1y_date = cutoff_date.replace(year=cutoff_date.year - 1, day=28)
        cutoff_1y = cutoff_1y_date.strftime("%Y%m%d")
        mask_test = df_dates >= cutoff_1y
        if mask_test.sum() > 0:
            X_train, y_train = X[~mask_test], y[~mask_test]
            X_test, y_test = X[mask_test], y[mask_test]
        else:
            X_train, X_test, y_train, y_test = train_test_split(
                X, y, test_size=0.2, random_state=42
            )
        df_test = df[mask_test].copy() if mask_test.sum() > 0 else df.copy()
    else:
        X_train, X_test, y_train, y_test = train_test_split(
            X, y, test_size=0.2, random_state=42
        )
        df_test = df.copy()

    model = XGBClassifier(
        n_estimators=300,
        max_depth=4,
        learning_rate=0.05,
        subsample=0.8,
        colsample_bytree=0.8,
        eval_metric="logloss",
        random_state=42,
    )
    model.fit(X_train, y_train)

    # バックテスト的中率: 各レースで最も確率が高い艇が実際の1着かを計算
    df_test = df_test.copy()
    for col in FEATURES:
        if col not in df_test.columns:
            df_test[col] = np.nan
    df_test["pred_prob"] = model.predict_proba(df_test[FEATURES])[:, 1]

    group_cols = [c for c in ["date", "venue_code", "race_no"] if c in df_test.columns]
    if group_cols:
        df_test["pred_first"] = (
            df_test.groupby(group_cols)["pred_prob"]
            .transform(lambda x: (x == x.max()).astype(int))
        )
        hit = (df_test["pred_first"] == 1) & (df_test["is_first"] == 1)
        races = df_test.groupby(group_cols).ngroups
    else:
        df_test["pred_first"] = (
            df_test["pred_prob"] == df_test["pred_prob"].max()
        ).astype(int)
        hit = (df_test["pred_first"] == 1) & (df_test["is_first"] == 1)
        races = max(len(df_test) // 6, 1)

    accuracy = float(hit.sum()) / races if races > 0 else 0.0

    os.makedirs("data", exist_ok=True)
    joblib.dump({"model": model, "accuracy": accuracy}, MODEL_PATH)
    print(f"モデル保存完了: {MODEL_PATH}")
    print(f"バックテスト単勝的中率: {accuracy:.1%}（{races}レース）")
    return accuracy


def load_model() -> tuple:
    """
    model.pkl をロードして (model, accuracy) を返す。
    ファイル未存在時は FileNotFoundError を raise する。
    """
    if not os.path.exists(MODEL_PATH):
        raise FileNotFoundError(f"モデルファイルが見つかりません: {MODEL_PATH}")
    payload = joblib.load(MODEL_PATH)
    return payload["model"], payload["accuracy"]


def predict_proba(model, features_df: pd.DataFrame) -> np.ndarray:
    """
    6艇分の特徴量DataFrameを受け取り、各艇の1着確率配列を返す。
    返り値: shape(n,) の確率配列（合計≒1.0に正規化済み）
    """
    # FEATURES列が不足している場合はNaNで補完
    for col in FEATURES:
        if col not in features_df.columns:
            features_df = features_df.copy()
            features_df[col] = np.nan

    probs = model.predict_proba(features_df[FEATURES])[:, 1]
    total = probs.sum()
    if total > 0:
        probs = probs / total
    return probs


if __name__ == "__main__":
    acc = train()
    print(f"完了: 的中率 {acc:.1%}")
