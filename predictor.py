# 目的：スクレイピングデータとMLモデルを統合して予測結果を生成する
# 作成日：2026-05-19
# 対象OS：Linux（Render）/ Windows
# 依存ライブラリ：pandas, numpy

import numpy as np
import pandas as pd

from constants import GRADE_MAP, NIGHT_VENUES
from model import FEATURES, load_model, predict_proba

_model = None
_accuracy = None


def initialize() -> float:
    """モデルをロードして的中率を返す。アプリ起動時に1回だけ呼ぶ。"""
    global _model, _accuracy
    _model, _accuracy = load_model()
    return _accuracy


def _is_night_race(venue_code: str, race_no: int) -> int:
    return 1 if venue_code in NIGHT_VENUES and race_no >= 9 else 0


def _combo_prob(probs_by_boat: dict, boats: list[int]) -> float:
    """
    条件付き確率で連単の1着確率を近似計算する。
      2連単: P(A) × P(B|notA) ≈ P(A) × P(B)/(1-P(A))
      3連単: P(A) × P(B|notA) × P(C|notA,notB)
    """
    if len(boats) == 2:
        a, b = boats
        p_a = probs_by_boat.get(a, 0.0)
        denom = max(1.0 - p_a, 1e-6)
        p_b_given = probs_by_boat.get(b, 0.0) / denom
        return p_a * p_b_given
    if len(boats) == 3:
        a, b, c = boats
        p_a = probs_by_boat.get(a, 0.0)
        p_b = probs_by_boat.get(b, 0.0)
        p_c = probs_by_boat.get(c, 0.0)
        denom_b = max(1.0 - p_a, 1e-6)
        denom_c = max(1.0 - p_a - p_b, 1e-6)
        return p_a * (p_b / denom_b) * (p_c / denom_c)
    return 0.0


def predict(
    entries: list[dict],
    before_info: list[dict],
    odds: dict[str, float],
    odds_2f: dict[str, float] | None = None,
    odds_3f: dict[str, float] | None = None,
    venue_code: str = "",
    race_no: int = 0,
) -> dict:
    """
    出走情報・直前情報・各種オッズを受け取り予測結果を返す。

    返り値:
    {
      "scores": [{"boat_no": 1, "racer_name": "...", "score": 100, "prob": 0.42}, ...],
      "recommendations": [{"type": "単勝", "boats": [1], "odds": 1.8, "ev": 1.02}, ...],
      "backtest_accuracy": 0.423,
      "before_info": [...],
    }
    """
    if _model is None:
        raise RuntimeError("モデル未初期化。initialize() を先に呼んでください。")
    if not entries:
        raise ValueError("出走情報が空です。")

    odds_2f = odds_2f or {}
    odds_3f = odds_3f or {}
    before_map = {b["boat_no"]: b for b in before_info}
    is_night = _is_night_race(venue_code, race_no)
    try:
        _venue_code_int = int(venue_code) if venue_code else 0
    except (ValueError, TypeError):
        _venue_code_int = 0

    def _to_float(v):
        """None を np.nan に変換。数値はそのまま返す。"""
        return np.nan if v is None else float(v)

    # 特徴量DataFrame作成
    rows = []
    for entry in entries:
        boat_no = entry["boat_no"]
        bi = before_map.get(boat_no, {})
        rows.append({
            "boat_no_1": float(boat_no == 1),
            "boat_no_2": float(boat_no == 2),
            "boat_no_3": float(boat_no == 3),
            "boat_no_4": float(boat_no == 4),
            "boat_no_5": float(boat_no == 5),
            "boat_no_6": float(boat_no == 6),
            "venue_code": float(_venue_code_int),
            "racer_grade_num": float(GRADE_MAP.get(entry.get("racer_grade", "B2"), 1)),
            "win_rate": _to_float(entry.get("win_rate")),
            "local_win_rate": _to_float(entry.get("local_win_rate")),
            "national_2rate": _to_float(entry.get("national_2rate")),
            "local_2rate": _to_float(entry.get("local_2rate")),
            "st_avg": _to_float(entry.get("st_avg")),
            "fly_count": float(entry.get("fly_count") or 0),
            "motor_rate": _to_float(entry.get("motor_rate")),
            "exhibition_time": _to_float(bi.get("exhibition_time")),
            "exhibition_st": _to_float(bi.get("exhibition_st")),
            "tilt": _to_float(bi.get("tilt")),
            "is_night": float(is_night),
            "wind_speed": _to_float(bi.get("wind_speed")),
        })

    features_df = pd.DataFrame(rows, dtype=float)
    probs = predict_proba(_model, features_df)

    # スコア（0〜100）に変換してリスト化
    max_prob = probs.max()
    scores = []
    for i, entry in enumerate(entries):
        prob = float(probs[i])
        score = int(round(prob / max_prob * 100)) if max_prob > 0 else 0
        scores.append({
            "boat_no": entry["boat_no"],
            "racer_name": entry.get("racer_name", f"{entry['boat_no']}号艇"),
            "score": score,
            "prob": round(prob, 4),
        })
    scores.sort(key=lambda x: x["score"], reverse=True)

    # 期待値 > 1.0 の推奨舟券を生成
    probs_by_boat = {s["boat_no"]: s["prob"] for s in scores}
    recommendations = []

    # ── 単勝 ──────────────────────────────────────────────────
    for s in scores:
        odds_val = odds.get(str(s["boat_no"]))
        if odds_val and odds_val > 0:
            ev = round(s["prob"] * odds_val, 2)
            if ev > 1.0:
                recommendations.append({
                    "type": "単勝",
                    "boats": [s["boat_no"]],
                    "odds": odds_val,
                    "ev": ev,
                })

    # ── 2連単（上位2艇の組み合わせ）──────────────────────────
    if len(scores) >= 2 and odds_2f:
        top2 = [scores[0]["boat_no"], scores[1]["boat_no"]]
        key = f"{top2[0]}-{top2[1]}"
        odds_val = odds_2f.get(key)
        if odds_val and odds_val > 0:
            cp = _combo_prob(probs_by_boat, top2)
            ev = round(cp * odds_val, 2)
            if ev > 1.0:
                recommendations.append({
                    "type": "2連単",
                    "boats": top2,
                    "odds": odds_val,
                    "ev": ev,
                })

    # ── 3連単（上位3艇の組み合わせ）──────────────────────────
    if len(scores) >= 3 and odds_3f:
        top3 = [scores[0]["boat_no"], scores[1]["boat_no"], scores[2]["boat_no"]]
        key = f"{top3[0]}-{top3[1]}-{top3[2]}"
        odds_val = odds_3f.get(key)
        if odds_val and odds_val > 0:
            cp = _combo_prob(probs_by_boat, top3)
            ev = round(cp * odds_val, 2)
            if ev > 1.0:
                recommendations.append({
                    "type": "3連単",
                    "boats": top3,
                    "odds": odds_val,
                    "ev": ev,
                })

    # 期待値の高い順に並べ替え
    recommendations.sort(key=lambda x: x["ev"], reverse=True)

    return {
        "scores": scores,
        "recommendations": recommendations,
        "backtest_accuracy": _accuracy if _accuracy is not None else 0.0,
        "before_info": before_info,
        "odds": odds,
    }
