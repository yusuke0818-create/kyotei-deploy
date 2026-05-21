import argparse
import asyncio
import json
import os
import re
import unicodedata
from datetime import date, datetime, timedelta
from typing import Optional

import aiohttp
import pandas as pd
from bs4 import BeautifulSoup

from constants import GRADE_MAP, NIGHT_VENUES

OPEN_API_URL = "https://boatraceopenapi.github.io/results/v2/{year}/{date_str}.json"
ENTRY_URL = "https://www.boatrace.jp/owpc/pc/race/racelist?rno={race_no}&jcd={venue_code}&hd={date_str}"
BEFORE_URL = "https://www.boatrace.jp/owpc/pc/race/beforeinfo?rno={race_no}&jcd={venue_code}&hd={date_str}"
HEADERS = {"User-Agent": "kyotei-yosou-tool/1.0 (contact: yusuke0818@gmail.com)"}

TRAINING_CSV = "data/training_data.csv"
STATE_FILE = "data/collection_state.json"
ROLLING_DAYS = 730
MAX_CONCURRENCY = 10
SLEEP_SEC = 0.5


def _norm(text: str) -> str:
    return unicodedata.normalize("NFKC", text).strip()


def _is_night_race(venue_code: str, race_no: int) -> int:
    return 1 if venue_code in NIGHT_VENUES and race_no >= 9 else 0


async def _fetch_json(session: aiohttp.ClientSession, url: str) -> Optional[dict]:
    try:
        async with session.get(url, headers=HEADERS, timeout=aiohttp.ClientTimeout(total=30)) as resp:
            if resp.status == 200:
                return await resp.json(content_type=None)
            return None
    except Exception as e:
        print(f"[WARN] JSON fetch failed: {url} -> {e}")
        return None


async def _fetch_html(
    session: aiohttp.ClientSession, url: str, semaphore: asyncio.Semaphore
) -> Optional[str]:
    async with semaphore:
        await asyncio.sleep(SLEEP_SEC)
        try:
            async with session.get(url, headers=HEADERS, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                if resp.status == 200:
                    return await resp.text(encoding="utf-8", errors="replace")
                return None
        except Exception as e:
            print(f"[WARN] HTML fetch failed: {url} -> {e}")
            return None


def _parse_entry_page(html: str) -> dict[int, dict]:
    """出走表HTMLを解析してboat_no -> 選手・モーター情報のdictを返す。"""
    soup = BeautifulSoup(html, "html.parser")
    entries: dict[int, dict] = {}
    seen: set[int] = set()

    for tr in soup.find_all("tr"):
        tds = tr.find_all("td")
        if len(tds) < 7:
            continue
        boat_text = _norm(tds[0].get_text(strip=True))
        if not (boat_text.isdigit() and 1 <= int(boat_text) <= 6):
            continue
        boat_no = int(boat_text)
        if boat_no in seen:
            continue

        # モーターセルが空のサブ行はスキップ（データ行を待つ）
        mot_check = tds[6].get_text(strip=True) if len(tds) > 6 else ""
        if not mot_check:
            continue

        seen.add(boat_no)

        try:
            cell_lines = [
                l.strip()
                for l in tds[2].get_text(separator="\n", strip=True).split("\n")
                if l.strip() and l.strip() != "/"
            ]
            racer_grade = next(
                (l for l in cell_lines if re.match(r"^[AB][12]$", l)), "B2"
            )

            fls_parts = [
                p.strip()
                for p in tds[3].get_text(separator="\n", strip=True).split("\n")
                if p.strip()
            ]
            fly_count = 0
            st_avg = None
            for p in fls_parts:
                if p.startswith("F") and p[1:].isdigit():
                    fly_count = int(p[1:])
                elif re.match(r"^\d+\.\d+$", p):
                    st_avg = float(p)

            nat_parts = [
                p.strip()
                for p in tds[4].get_text(separator="\n", strip=True).split("\n")
                if p.strip()
            ]
            win_rate = float(nat_parts[0]) if nat_parts else None
            national_2rate = float(nat_parts[1]) / 100.0 if len(nat_parts) > 1 else None

            loc_parts = [
                p.strip()
                for p in tds[5].get_text(separator="\n", strip=True).split("\n")
                if p.strip()
            ]
            local_win_rate = float(loc_parts[0]) if loc_parts else None
            local_2rate = float(loc_parts[1]) / 100.0 if len(loc_parts) > 1 else None

            mot_parts = [
                p.strip()
                for p in tds[6].get_text(separator="\n", strip=True).split("\n")
                if p.strip()
            ]
            motor_rate = float(mot_parts[1]) / 100.0 if len(mot_parts) > 1 else None

            entries[boat_no] = {
                "racer_grade": racer_grade,
                "win_rate": win_rate,
                "national_2rate": national_2rate,
                "local_win_rate": local_win_rate,
                "local_2rate": local_2rate,
                "st_avg": st_avg,
                "fly_count": fly_count,
                "motor_rate": motor_rate,
            }
        except (ValueError, IndexError):
            entries[boat_no] = {
                "racer_grade": "B2",
                "win_rate": None,
                "national_2rate": None,
                "local_win_rate": None,
                "local_2rate": None,
                "st_avg": None,
                "fly_count": 0,
                "motor_rate": None,
            }

    return entries


def _parse_before_info(html: str) -> dict[int, dict]:
    """直前情報HTMLを解析してboat_no -> 展示タイム・展示ST・チルトのdictを返す。

    実際の列構造（2026年確認済み）:
      10列行: [0]艇番 [1]写真 [2]選手名 [3]体重 [4]展示タイム [5]チルト ...
       3列行: [0]展示ST値 [1]"ST" [2]空 ← 直前の10列行の艇番に対応
    """
    soup = BeautifulSoup(html, "html.parser")
    before: dict[int, dict] = {}
    last_boat_no: int | None = None

    for tr in soup.find_all("tr"):
        tds = tr.find_all("td")

        # 10列行: メインの艇データ行
        if len(tds) >= 8:
            boat_text = _norm(tds[0].get_text(strip=True))
            if boat_text.isdigit() and 1 <= int(boat_text) <= 6:
                boat_no = int(boat_text)
                last_boat_no = boat_no
                try:
                    ex_text = tds[4].get_text(strip=True)
                    exhibition_time = float(ex_text) if re.match(r"^\d+\.\d+$", ex_text) else None
                    tilt_text = tds[5].get_text(strip=True)
                    tilt = (
                        float(tilt_text)
                        if re.match(r"^[+-]?\d+\.?\d*$", tilt_text) and tilt_text
                        else None
                    )
                    before[boat_no] = {
                        "exhibition_time": exhibition_time,
                        "exhibition_st": None,
                        "tilt": tilt,
                    }
                except (ValueError, IndexError):
                    before[boat_no] = {"exhibition_time": None, "exhibition_st": None, "tilt": None}

        # 3列行: STサブ行（直前の艇番のST値）
        elif len(tds) == 3 and last_boat_no is not None:
            st_label = tds[1].get_text(strip=True)
            if st_label == "ST":
                st_text = tds[0].get_text(strip=True)
                try:
                    exhibition_st = float(st_text) if re.match(r"^\d+\.\d+$", st_text) else None
                    if last_boat_no in before:
                        before[last_boat_no]["exhibition_st"] = exhibition_st
                except (ValueError, IndexError):
                    pass
                last_boat_no = None  # ST行を読んだらリセット

    return before


async def _fetch_entry(
    session: aiohttp.ClientSession,
    venue_code: str,
    race_no: int,
    date_str: str,
    semaphore: asyncio.Semaphore,
) -> tuple[str, int, dict[int, dict]]:
    url = ENTRY_URL.format(race_no=race_no, venue_code=venue_code, date_str=date_str)
    html = await _fetch_html(session, url, semaphore)
    if html is None:
        return venue_code, race_no, {}
    return venue_code, race_no, _parse_entry_page(html)


async def _fetch_before(
    session: aiohttp.ClientSession,
    venue_code: str,
    race_no: int,
    date_str: str,
    semaphore: asyncio.Semaphore,
) -> tuple[str, int, dict[int, dict]]:
    url = BEFORE_URL.format(race_no=race_no, venue_code=venue_code, date_str=date_str)
    html = await _fetch_html(session, url, semaphore)
    if html is None:
        return venue_code, race_no, {}
    return venue_code, race_no, _parse_before_info(html)


async def collect_one_day(
    target_date: date,
    session: aiohttp.ClientSession,
    semaphore: asyncio.Semaphore,
) -> list[dict]:
    date_str = target_date.strftime("%Y%m%d")

    # 1. Open API でその日の全レース結果を取得（1リクエスト）
    api_url = OPEN_API_URL.format(year=target_date.year, date_str=date_str)
    api_data = await _fetch_json(session, api_url)
    if not api_data or "results" not in api_data:
        return []

    results = api_data["results"]

    # 2. 出走表・直前情報を全レース分まとめて非同期取得
    race_keys = list({(f"{r['race_stadium_number']:02d}", r["race_number"]) for r in results})
    entry_tasks = [_fetch_entry(session, vc, rn, date_str, semaphore) for vc, rn in race_keys]
    before_tasks = [_fetch_before(session, vc, rn, date_str, semaphore) for vc, rn in race_keys]
    entry_results, before_results = await asyncio.gather(
        asyncio.gather(*entry_tasks),
        asyncio.gather(*before_tasks),
    )
    entries_map: dict[tuple, dict[int, dict]] = {
        (vc, rn): ent for vc, rn, ent in entry_results
    }
    before_map: dict[tuple, dict[int, dict]] = {
        (vc, rn): bi for vc, rn, bi in before_results
    }

    # 3. API結果・出走表・直前情報を結合してレコード生成
    records = []
    for race in results:
        venue_code = f"{race['race_stadium_number']:02d}"
        race_no = race["race_number"]
        wind_speed = race.get("race_wind")
        is_night = _is_night_race(venue_code, race_no)
        entries = entries_map.get((venue_code, race_no), {})
        before = before_map.get((venue_code, race_no), {})

        for boat in race.get("boats", []):
            boat_no = boat.get("racer_boat_number")
            rank = boat.get("racer_place_number")
            if not boat_no or not rank:
                continue

            entry = entries.get(boat_no, {})
            bi = before.get(boat_no, {})
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
                "wind_speed": wind_speed,
                "rank": rank,
                "is_first": 1 if rank == 1 else 0,
            })

    return records


def _load_state(state_file: str) -> dict:
    if os.path.exists(state_file):
        with open(state_file) as f:
            return json.load(f)
    return {"forward_max": None, "reverse_min": None}


def _save_state(state: dict, state_file: str) -> None:
    os.makedirs("data", exist_ok=True)
    with open(state_file, "w") as f:
        json.dump(state, f)


def collect_incremental(
    days: int = 14,
    reverse: bool = False,
    output_csv: str = TRAINING_CSV,
    state_file: str = STATE_FILE,
) -> int:
    os.makedirs("data", exist_ok=True)
    today = date.today()
    oldest = date(today.year - 5, today.month, today.day)
    state = _load_state(state_file)

    if reverse:
        if state["reverse_min"]:
            cur_min = datetime.strptime(state["reverse_min"], "%Y%m%d").date()
            end_date = cur_min - timedelta(days=1)
        else:
            end_date = today - timedelta(days=1)
        start_date = max(end_date - timedelta(days=days - 1), oldest)
        if start_date > end_date:
            print(f"収集対象なし(reverse_min: {state['reverse_min']})")
            return 0
        # 新しい順に処理: 途中停止しても次回は停止日の翌日から再開できる
        date_range = [
            end_date - timedelta(days=i)
            for i in range((end_date - start_date).days + 1)
        ]
    else:
        if state["forward_max"]:
            cur_max = datetime.strptime(state["forward_max"], "%Y%m%d").date()
            start_date = cur_max + timedelta(days=1)
        else:
            start_date = oldest
        end_date = min(start_date + timedelta(days=days - 1), today - timedelta(days=1))
        if start_date > end_date:
            print(f"収集対象なし(forward_max: {state['forward_max']})")
            return 0
        date_range = [
            start_date + timedelta(days=i)
            for i in range((end_date - start_date).days + 1)
        ]

    print(f"収集範囲: {date_range[-1]} 〜 {date_range[0]}({len(date_range)}日間)")

    semaphore = asyncio.Semaphore(MAX_CONCURRENCY)
    total_records = 0

    async def run():
        nonlocal total_records
        async with aiohttp.ClientSession() as session:
            for current in date_range:
                print(f"  収集中: {current.strftime('%Y-%m-%d')}")
                records = await collect_one_day(current, session, semaphore)

                if records:
                    df_day = pd.DataFrame(records)
                    if os.path.exists(output_csv):
                        df_day.to_csv(output_csv, mode="a", header=False, index=False)
                    else:
                        df_day.to_csv(output_csv, index=False)
                    total_records += len(records)

                # 1日ごとにstate保存（途中停止→次回から正しく再開）
                if reverse:
                    state["reverse_min"] = current.strftime("%Y%m%d")
                else:
                    state["forward_max"] = current.strftime("%Y%m%d")
                _save_state(state, state_file)

    asyncio.run(run())

    if total_records == 0:
        print("収集レコードなし")
        return 0

    # ローリングウィンドウ: 2年超のデータ削除 + 重複除去
    cutoff = (today - timedelta(days=ROLLING_DAYS)).strftime("%Y%m%d")
    df_all = pd.read_csv(output_csv)
    before = len(df_all)
    df_all = df_all[df_all["date"].astype(str) >= cutoff]
    df_all = df_all.drop_duplicates(subset=["date", "venue_code", "race_no", "boat_no"])
    df_all.to_csv(output_csv, index=False)
    removed = before - len(df_all)

    print(f"追記: {total_records}件  削除(2年超): {removed}件  合計: {len(df_all)}件")
    return total_records


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=14)
    parser.add_argument("--reverse", action="store_true")
    parser.add_argument("--output", type=str, default=TRAINING_CSV)
    parser.add_argument("--state", type=str, default=STATE_FILE)
    args = parser.parse_args()
    collect_incremental(days=args.days, reverse=args.reverse, output_csv=args.output, state_file=args.state)
