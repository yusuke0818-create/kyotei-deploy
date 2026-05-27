# 目的：boatrace.jp から当日レースデータをスクレイピングする
# 作成日：2026-05-19
# 対象OS：Linux（Render）/ Windows
# 依存ライブラリ：requests, beautifulsoup4

import re
import time
import unicodedata
from datetime import datetime, timezone, timedelta

import requests
from bs4 import BeautifulSoup

_JST = timezone(timedelta(hours=9))


def today_jst() -> str:
    """Renderサーバー（UTC）でも日本時間の今日の日付を返す。"""
    return datetime.now(_JST).strftime("%Y%m%d")

BASE_URL = "https://www.boatrace.jp"
HEADERS = {
    "User-Agent": "kyotei-yosou-tool/1.0 (contact: yusuke0818@gmail.com)"
}
SLEEP_SEC = 0.8

_session = requests.Session()
_session.headers.update(HEADERS)

VENUE_CODES = {
    "桐生": "01", "戸田": "02", "江戸川": "03", "平和島": "04",
    "多摩川": "05", "浜名湖": "06", "蒲郡": "07", "常滑": "08",
    "津": "09", "三国": "10", "びわこ": "11", "住之江": "12",
    "尼崎": "13", "鳴門": "14", "丸亀": "15", "児島": "16",
    "宮島": "17", "徳山": "18", "下関": "19", "若松": "20",
    "芦屋": "21", "福岡": "22", "からつ": "23", "大村": "24",
}
VENUE_NAMES = {v: k for k, v in VENUE_CODES.items()}


def _fetch(url: str, skip_sleep: bool = False) -> BeautifulSoup | None:
    try:
        if not skip_sleep:
            time.sleep(SLEEP_SEC)
        resp = _session.get(url, timeout=30)
        resp.raise_for_status()
        return BeautifulSoup(resp.text, "html.parser")
    except Exception as e:
        print(f"[WARN] fetch failed: {url} → {e}")
        return None


def _norm(text: str) -> str:
    """全角英数字を半角に正規化する。"""
    return unicodedata.normalize("NFKC", text).strip()


def get_race_entries(venue_code: str, race_no: int, date_str: str) -> list[dict]:
    """
    出走表をスクレイピングして6艇分の情報を返す。

    検証で確認した実際のHTML列構造:
      [0] 艇番（全角数字: '１'〜'６'）
      [1] 写真（空）
      [2] 登録番号/級別/選手名（結合セル）
      [3] F/L/ST
      [4] 全国 勝率/2連率/3連率
      [5] 当地 勝率/2連率/3連率
      [6] モーター 番号/2連率/3連率
    メイン行(td≧7)のみ対象。艇番セルが空のサブ行はスキップ。
    """
    url = f"{BASE_URL}/owpc/pc/race/racelist?rno={race_no}&jcd={venue_code}&hd={date_str}"
    soup = _fetch(url)
    if soup is None:
        return []

    entries = []
    seen_boats: set[int] = set()

    for tr in soup.find_all("tr"):
        tds = tr.find_all("td")
        if len(tds) < 7:
            continue

        # 艇番チェック（全角 → 半角に変換してから判定）
        boat_text = _norm(tds[0].get_text(strip=True))
        if not (boat_text.isdigit() and 1 <= int(boat_text) <= 6):
            continue
        boat_no = int(boat_text)

        # 本行判定: tds[2] に4桁の登録番号が含まれる行のみを本行とする
        # （節間成績の着順サブ行には4桁番号がなく誤認識されるため除外）
        cell2_text = tds[2].get_text(separator="\n", strip=True)
        if not re.search(r"\d{4}", cell2_text):
            continue

        if boat_no in seen_boats:
            continue
        seen_boats.add(boat_no)

        try:
            # ── tds[2]: 登録番号/級別/選手名（結合セル）────────────────
            # 実際の内容例: '3827\n/\nB1\n今泉\n　徹'
            cell_lines = [
                l.strip()
                for l in tds[2].get_text(separator="\n", strip=True).split("\n")
                if l.strip() and l.strip() != "/"
            ]
            racer_id = next((l for l in cell_lines if re.match(r"^\d{4}$", l)), "")
            racer_grade = next(
                (l for l in cell_lines if re.match(r"^[AB][12]$", l)), "B2"
            )
            name_parts = [
                l for l in cell_lines
                if l != racer_id
                and not re.match(r"^[AB][12]$", l)
                and not re.match(r"^[\d/]+$", l)
                # 支部/出身地（例: "群馬/群馬"）を除外
                and not re.match(r"^[一-鿿぀-ヿ]+/[一-鿿぀-ヿ]+$", l)
                # 年齢/体重（例: "52歳/52.0kg"）を除外
                and not re.match(r"^\d+歳", l)
            ]
            racer_name = "".join(name_parts) or f"{boat_no}号艇"

            # ── tds[3]: F/L/ST ───────────────────────────────────────
            fls_parts = [
                p.strip()
                for p in tds[3].get_text(separator="\n", strip=True).split("\n")
                if p.strip()
            ]
            fly_count = 0
            st_avg = None  # 取得失敗時はNaN → モデルが欠損として処理する
            for p in fls_parts:
                if p.startswith("F") and p[1:].isdigit():
                    fly_count = int(p[1:])
                elif re.match(r"^\d+\.\d+$", p):
                    st_avg = float(p)

            # ── tds[4]: 全国成績（勝率/2連率/3連率）────────────────────
            nat_parts = [
                p.strip()
                for p in tds[4].get_text(separator="\n", strip=True).split("\n")
                if p.strip()
            ]
            win_rate = float(nat_parts[0]) if nat_parts else None
            national_2rate = (
                float(nat_parts[1]) / 100.0 if len(nat_parts) > 1 else None
            )

            # ── tds[5]: 当地成績 ──────────────────────────────────────
            loc_parts = [
                p.strip()
                for p in tds[5].get_text(separator="\n", strip=True).split("\n")
                if p.strip()
            ]
            local_win_rate = float(loc_parts[0]) if loc_parts else None
            local_2rate = (
                float(loc_parts[1]) / 100.0 if len(loc_parts) > 1 else None
            )

            # ── tds[6]: モーター ──────────────────────────────────────
            mot_parts = [
                p.strip()
                for p in tds[6].get_text(separator="\n", strip=True).split("\n")
                if p.strip()
            ]
            motor_no = int(mot_parts[0]) if mot_parts and mot_parts[0].isdigit() else 0
            motor_rate = (
                float(mot_parts[1]) / 100.0 if len(mot_parts) > 1 else None
            )

            entries.append({
                "boat_no": boat_no,
                "racer_id": racer_id,
                "racer_name": racer_name,
                "racer_grade": racer_grade,
                "win_rate": win_rate,
                "national_2rate": national_2rate,
                "local_win_rate": local_win_rate,
                "local_2rate": local_2rate,
                "st_avg": st_avg,
                "fly_count": fly_count,
                "motor_no": motor_no,
                "motor_rate": motor_rate,
            })

        except (ValueError, IndexError):
            entries.append({
                "boat_no": boat_no,
                "racer_id": "",
                "racer_name": f"{boat_no}号艇",
                "racer_grade": "B2",
                "win_rate": None,
                "national_2rate": None,
                "local_win_rate": None,
                "local_2rate": None,
                "st_avg": None,
                "fly_count": 0,
                "motor_no": 0,
                "motor_rate": None,
            })

    entries.sort(key=lambda x: x["boat_no"])
    return entries


def get_before_info(venue_code: str, race_no: int, date_str: str) -> list[dict]:
    """
    直前情報（展示タイム・展示ST・チルト）を返す。
    未公開時は空リストを返す。

    beforeinfo ページの実際の列構造（2026-05-24 実機検証）:
      本行: [0]艇番 / [1]空 / [2]選手名 / [3]体重 / [4]展示タイム / [5]チルト
      サブ行（本行直後・tds[1]=="ST"）: [0]? / [1]"ST" / [2]展示ST（".09"形式）

    返り値例:
    [{"boat_no": 1, "exhibition_time": 6.78, "exhibition_st": 0.11,
      "tilt": 0.0, "parts_changed": None}, ...]
    """
    url = f"{BASE_URL}/owpc/pc/race/beforeinfo?rno={race_no}&jcd={venue_code}&hd={date_str}"
    soup = _fetch(url)
    if soup is None:
        return []

    # 3列以上のTR行をすべて収集してペア処理する
    all_rows = [
        tr.find_all("td")
        for tr in soup.find_all("tr")
        if len(tr.find_all("td")) >= 3
    ]

    before_info = []
    seen_boats: set[int] = set()
    i = 0
    while i < len(all_rows):
        tds = all_rows[i]
        boat_text = _norm(tds[0].get_text(strip=True))
        if not (boat_text.isdigit() and 1 <= int(boat_text) <= 6):
            i += 1
            continue
        boat_no = int(boat_text)
        if boat_no in seen_boats:
            i += 1
            continue
        seen_boats.add(boat_no)

        # 本行: tds[4]=展示タイム, tds[5]=チルト
        try:
            ex_text = tds[4].get_text(strip=True) if len(tds) > 4 else ""
            exhibition_time = float(ex_text) if re.match(r"^\d+\.\d+$", ex_text) else None

            tilt_text = tds[5].get_text(strip=True) if len(tds) > 5 else ""
            tilt = (
                float(tilt_text)
                if re.match(r"^[+-]?\d+\.?\d*$", tilt_text) and tilt_text
                else None
            )
        except (ValueError, IndexError):
            exhibition_time = None
            tilt = None

        # サブ行: tds[1]=="ST" なら tds[2] が展示ST（".09" → 0.09）
        exhibition_st = None
        i += 1
        if i < len(all_rows):
            sub = all_rows[i]
            if len(sub) > 1 and sub[1].get_text(strip=True) == "ST":
                st_raw = sub[2].get_text(strip=True) if len(sub) > 2 else ""
                if st_raw:
                    try:
                        # ".09" 形式（先頭ドット）を "0.09" に補正
                        exhibition_st = float("0" + st_raw if st_raw.startswith(".") else st_raw)
                    except ValueError:
                        exhibition_st = None
                i += 1  # サブ行を消費

        before_info.append({
            "boat_no": boat_no,
            "exhibition_time": exhibition_time,
            "exhibition_st": exhibition_st,
            "tilt": tilt,
            "parts_changed": None,
        })

    return sorted(before_info, key=lambda x: x["boat_no"])


def get_odds(venue_code: str, race_no: int, date_str: str) -> dict[str, float]:
    """
    単勝オッズを返す。{"1": 1.8, "2": 4.5, ...}
    失敗時は空dictを返す。
    """
    url = f"{BASE_URL}/owpc/pc/race/oddstf?rno={race_no}&jcd={venue_code}&hd={date_str}"
    soup = _fetch(url)
    if soup is None:
        return {}

    odds: dict[str, float] = {}
    for table in soup.find_all("table"):
        for tr in table.find_all("tr"):
            tds = tr.find_all("td")
            if len(tds) < 2:
                continue
            try:
                boat_text = _norm(tds[0].get_text(strip=True))
                if not (boat_text.isdigit() and 1 <= int(boat_text) <= 6):
                    continue
                boat_no = int(boat_text)
                # 単勝オッズは2列目（複勝は3列目）
                odds_val = float(tds[1].get_text(strip=True))
                if odds_val > 0:
                    odds[str(boat_no)] = odds_val
            except (ValueError, IndexError):
                continue
        if len(odds) >= 6:
            break

    return odds


def get_odds_2f(venue_code: str, race_no: int, date_str: str) -> dict[str, float]:
    """
    2連単オッズを返す。{"1-2": 5.1, "1-3": 8.2, ...}
    失敗・締め切り前は空dictを返す。
    """
    url = f"{BASE_URL}/owpc/pc/race/odds2tf?rno={race_no}&jcd={venue_code}&hd={date_str}"
    soup = _fetch(url)
    if soup is None:
        return {}

    odds: dict[str, float] = {}
    for table in soup.find_all("table"):
        for tr in table.find_all("tr"):
            tds = tr.find_all("td")
            if len(tds) < 2:
                continue
            try:
                combo_text = _norm(tds[0].get_text(strip=True))
                # "1-2" または "1=2" 形式
                combo_text = re.sub(r"[=＝]", "-", combo_text)
                if not re.match(r"^\d-\d$", combo_text):
                    continue
                a_str, b_str = combo_text.split("-")
                a, b = int(a_str), int(b_str)
                if not (1 <= a <= 6 and 1 <= b <= 6 and a != b):
                    continue
                odds_val = float(
                    tds[-1].get_text(strip=True).replace(",", "")
                )
                if odds_val > 0:
                    odds[f"{a}-{b}"] = odds_val
            except (ValueError, IndexError):
                continue

    return odds


def get_odds_3f(venue_code: str, race_no: int, date_str: str) -> dict[str, float]:
    """
    3連単オッズを返す。{"1-2-3": 22.0, ...}
    失敗・締め切り前は空dictを返す。
    """
    url = f"{BASE_URL}/owpc/pc/race/odds3tf?rno={race_no}&jcd={venue_code}&hd={date_str}"
    soup = _fetch(url)
    if soup is None:
        return {}

    odds: dict[str, float] = {}
    for table in soup.find_all("table"):
        for tr in table.find_all("tr"):
            tds = tr.find_all("td")
            if len(tds) < 2:
                continue
            try:
                combo_text = _norm(tds[0].get_text(strip=True))
                combo_text = re.sub(r"[=＝]", "-", combo_text)
                if not re.match(r"^\d-\d-\d$", combo_text):
                    continue
                parts = combo_text.split("-")
                a, b, c = int(parts[0]), int(parts[1]), int(parts[2])
                if not (
                    len({a, b, c}) == 3 and all(1 <= x <= 6 for x in (a, b, c))
                ):
                    continue
                odds_val = float(
                    tds[-1].get_text(strip=True).replace(",", "")
                )
                if odds_val > 0:
                    odds[f"{a}-{b}-{c}"] = odds_val
            except (ValueError, IndexError):
                continue

    return odds


def get_today_schedule() -> list[dict]:
    """本日の開催場一覧と現在/次レース番号を返す。
    [{"venue_code": "01", "venue_name": "桐生", "current_race": 5}, ...]
    current_race: インデックスページの会場リンクから抽出したレース番号。
                  取得できない場合は None（GUIでは全レース有効扱い）。
    """
    today_str = today_jst()

    url = f"{BASE_URL}/owpc/pc/race/index"
    soup = _fetch(url, skip_sleep=True)
    if soup is None:
        return []

    # venue_code -> 結果ページが存在するrnoの集合
    finished: dict[str, set[int]] = {}
    # venue_code -> 出走表ページが存在するrnoの集合
    racelist_set: dict[str, set[int]] = {}
    # 開催中会場の集合
    active: set[str] = set()

    for a in soup.find_all("a", href=True):
        href = a["href"]
        # 昨日以前のリンク（hd=YYYYMMDD が今日と異なる）は除外する
        hd_match = re.search(r"hd=(\d{8})", href)
        if hd_match and hd_match.group(1) != today_str:
            continue
        m = re.search(r"jcd=(\d{2})", href)
        if not m:
            continue
        code = m.group(1)
        active.add(code)
        rno_match = re.search(r"rno=(\d+)", href)
        if not rno_match:
            continue
        rno = int(rno_match.group(1))
        if "raceresult" in href:
            finished.setdefault(code, set()).add(rno)
        if "racelist" in href:
            racelist_set.setdefault(code, set()).add(rno)

    result = []
    for code in active:
        # racelist と raceresult が同時に存在するレースは「ナビリンク」であり未終了
        # raceresult のみ存在するレース = 真に終了済み
        truly_done = finished.get(code, set()) - racelist_set.get(code, set())
        cr = (max(truly_done) + 1) if truly_done else None
        result.append({"venue_code": code, "venue_name": VENUE_NAMES.get(code, code), "current_race": cr})
    return result
