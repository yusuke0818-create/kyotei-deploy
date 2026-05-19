# 目的：boatrace.jp から過去レース結果を収集してモデル学習用CSVを生成する
# 作成日：2026-05-19
# 対象OS：Linux（GitHub Actions）/ Windows（ローカル学習時）
# 依存ライブラリ：requests, beautifulsoup4, pandas

import os
import re
import time
import argparse
from datetime import date, timedelta, datetime

import pandas as pd
import requests
from bs4 import BeautifulSoup

import scraper

BASE_URL = "https://www.boatrace.jp"
HEADERS = {"User-Agent": "kyotei-yosou-tool/1.0 (contact: yusuke0818@gmail.com)"}
SLEEP_SEC = 1.5
TRAINING_CSV = "data/training_data.csv"
ROLLING_DAYS = 730  # 保持する日数（約2年）

VENUE_CODES = [
    "01", "02", "03", "04", "05", "06", "07", "08",
    "09", "10", "11", "12", "13", "14", "15", "16",
    "17", "18", "19", "20", "21", "22", "23", "24",
]

GRADE_MAP = {"A1": 4, "A2": 3, "B1": 2, "B2": 1}
NIGHT_VENUES = {"01", "04", "12", "13", "15", "16", "21", "22", "23", "24"}


def _is_night_race(venue_code: str, race_no: int) -> int:
    return 1 if venue_code in NIGHT_VENUES and race_no >= 9 else 0


def _fetch(url: str) -> BeautifulSoup | None:
    try:
        time.sleep(SLEEP_SEC)
        resp = requests.get(url, headers=HEADERS, timeout=30)
        resp.raise_for_status()
        return BeautifulSoup(resp.text, "html.parser")
    except Exception:
        return None


def collect_race_result(venue_code: str, race_no: int, date_str: str) -> list[dict]:
    url = f"{BASE_URL}/owpc/pc/race/raceresult?rno={race_no}&jcd={venue_code}&hd={date_str}"
    soup = _fetch(url)
    if soup is None:
        return []

    results = []
    for table in soup.find_all("table"):
        for tr in table.find_all("tr"):
            tds = tr.find_all("td")
            if len(tds) < 2:
                continue
            try:
                rank_val = int(tds[0].get_text(strip=True))
                if not (1 <= rank_val <= 6):
                    continue
                boat_no = int(tds[1].get_text(strip=True))
                if not (1 <= boat_no <= 6):
                    continue
                results.append({"boat_no": boat_no, "rank": rank_val})
            except (ValueError, IndexError):
                continue
        if len(results) >= 3:
            break

    return results


def collect_one_day(target_date: date) -> list[dict]:
    date_str = target_date.strftime("%Y%m%d")
    records = []

    for venue_code in VENUE_CODES:
        for race_no in range(1, 13):
            entries = scraper.get_race_entries(venue_code, race_no, date_str)
            results = collect_race_result(venue_code, race_no, date_str)
            if not entries or not results:
                continue

            before_info_list = scraper.get_before_info(venue_code, race_no, date_str)
            before_map = {bi["boat_no"]: bi for bi in before_info_list}
            rank_map = {r["boat_no"]: r["rank"] for r in results}
            is_night = _is_night_race(venue_code, race_no)

            for entry in entries:
                boat_no = entry["boat_no"]
                rank = rank_map.get(boat_no)
                if rank is None:
                    continue

                bi = before_map.get(boat_no, {})
                records.append({
                    "date": date_str,
                    "venue_code": venue_code,
                    "race_no": race_no,
                    "boat_no": boat_no,
                    "racer_grade_num": GRADE_MAP.get(entry.get("racer_grade", "B2"), 1),
                    "win_rate": entry.get("win_rate"),
                    "local_win_rate": entry.get("local_win_rate"),
                    "national_2rate": entry.get("national_2rate"),
                    "local_2rate": entry.get("local_2rate"),
                    "st_avg": entry.get("st_avg"),
                    "fly_count": entry.get("fly_count", 0),
                    "motor_rate": entry.get("motor_rate"),
                    "exhibition_time": bi.get("exhibition_time"),
                    "exhibition_st": bi.get("exhibition_st"),
                    "tilt": bi.get("tilt"),
                    "is_night": is_night,
                    "wind_speed": None,
                    "rank": rank,
                    "is_first": 1 if rank == 1 else 0,
                })

    return records


def collect_incremental(days: int = 14) -> int:
    """
    前回の最終収集日の翌日から指定日数分を収集してCSVに追記する。
    古いデータは ROLLING_DAYS 日より古いものを自動削除する。
    返り値: 追記したレコード数
    """
    os.makedirs("data", exist_ok=True)

    # 前回の最終収集日を特定
    if os.path.exists(TRAINING_CSV):
        df_existing = pd.read_csv(TRAINING_CSV, usecols=["date"])
        last_date_str = str(int(df_existing["date"].max()))
        last_date = datetime.strptime(last_date_str, "%Y%m%d").date()
        start_date = last_date + timedelta(days=1)
    else:
        # 初回: 5年前から開始
        today = date.today()
        start_date = date(today.year - 5, today.month, today.day)

    end_date = min(
        start_date + timedelta(days=days - 1),
        date.today() - timedelta(days=1),  # 当日は未確定のため除外
    )

    if start_date > end_date:
        print(f"収集対象なし（最新: {start_date - timedelta(days=1)}）")
        return 0

    print(f"収集範囲: {start_date} 〜 {end_date}（{(end_date - start_date).days + 1}日間）")

    all_records = []
    current = start_date
    while current <= end_date:
        print(f"  収集中: {current.strftime('%Y-%m-%d')}")
        records = collect_one_day(current)
        all_records.extend(records)
        current += timedelta(days=1)

    if not all_records:
        print("収集レコードなし")
        return 0

    df_new = pd.DataFrame(all_records)

    # CSVに追記
    if os.path.exists(TRAINING_CSV):
        df_new.to_csv(TRAINING_CSV, mode="a", header=False, index=False)
    else:
        df_new.to_csv(TRAINING_CSV, index=False)

    # ローリングウィンドウ: 古いデータを削除
    cutoff = (date.today() - timedelta(days=ROLLING_DAYS)).strftime("%Y%m%d")
    df_all = pd.read_csv(TRAINING_CSV)
    before = len(df_all)
    df_all = df_all[df_all["date"].astype(str) >= cutoff]
    df_all.to_csv(TRAINING_CSV, index=False)
    removed = before - len(df_all)

    print(f"追記: {len(df_new)}件  削除（2年超）: {removed}件  合計: {len(df_all)}件")
    return len(df_new)


def collect_training_data(years: int = 5) -> pd.DataFrame:
    """過去N年分を一括収集する（初回ローカル実行用）。"""
    end_date = date.today() - timedelta(days=1)
    start_date = date(end_date.year - years, end_date.month, end_date.day)

    all_records = []
    current = start_date
    while current <= end_date:
        print(f"収集中: {current.strftime('%Y-%m-%d')}")
        records = collect_one_day(current)
        all_records.extend(records)
        current += timedelta(days=1)

    df = pd.DataFrame(all_records)
    os.makedirs("data", exist_ok=True)
    df.to_csv(TRAINING_CSV, index=False)
    print(f"保存完了: {TRAINING_CSV}（{len(df)}件）")
    return df


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="競艇レースデータ収集")
    parser.add_argument("--days",  type=int, default=None,
                        help="インクリメンタル収集: 1回あたりの収集日数（例: --days 14）")
    parser.add_argument("--years", type=int, default=5,
                        help="一括収集: 過去何年分か（例: --years 5）")
    args = parser.parse_args()

    if args.days is not None:
        collect_incremental(days=args.days)
    else:
        collect_training_data(years=args.years)
