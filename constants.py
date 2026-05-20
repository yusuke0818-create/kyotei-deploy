# 目的：複数モジュールで共有する定数を一元管理する
# 作成日：2026-05-20
# 対象OS：Linux（Render / GitHub Actions）/ Windows

# 選手グレードの数値変換マップ（predictor.py / data_collector.py で共用）
GRADE_MAP: dict[str, int] = {"A1": 4, "A2": 3, "B1": 2, "B2": 1}

# ナイター開催場コード（レースNo.9以降がナイター扱い）
NIGHT_VENUES: set[str] = {"01", "04", "12", "13", "15", "16", "21", "22", "23", "24"}
