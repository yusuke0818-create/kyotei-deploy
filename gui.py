# 目的：競艇舟券予想ツールのFlet UI（スマホ優先Web版）
# 作成日：2026-05-19
# 対象OS：Linux（Render Web）/ Windows
# 依存ライブラリ：flet

import json
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

import flet as ft

import predictor
import scraper

# ── カラーパレット（承認済み・変更禁止） ─────────────────────────
C_BG      = "#0A1628"
C_CARD    = "#0D2137"
C_ACCENT  = "#00B4D8"
C_TEXT    = "#FFFFFF"
C_SUB     = "#9EAABB"
C_BORDER  = "#1A4A6B"
C_ERROR   = "#FF6B6B"
C_SUCCESS = "#4CAF50"
C_WARN    = "#FFA726"
C_TITLE   = "#C8960C"  # ツール名ゴールド（ロト6と共通）
C_DIS_BG   = "#0A1220"
C_DIS_TEXT = "#3A4A5A"
C_DIS_SIDE = "#1A2A3A"

# 公式艇番カラー
BOAT_COLORS = {
    1: {"bg": "#FFFFFF", "text": "#000000"},
    2: {"bg": "#000000", "text": "#FFFFFF"},
    3: {"bg": "#FF0000", "text": "#FFFFFF"},
    4: {"bg": "#0033CC", "text": "#FFFFFF"},
    5: {"bg": "#FFD700", "text": "#000000"},
    6: {"bg": "#008000", "text": "#FFFFFF"},
}

FEATURE_LABELS = {
    "boat_no":         "艇番（コース）",
    "racer_grade_num": "選手グレード（A1〜B2）",
    "win_rate":        "全国勝率",
    "local_win_rate":  "当地勝率",
    "national_2rate":  "全国2連率",
    "local_2rate":     "当地2連率",
    "st_avg":          "平均スタートタイム",
    "fly_count":       "フライング件数",
    "motor_rate":      "モーター2連率",
    "exhibition_time": "展示タイム（直前）",
    "exhibition_st":   "展示ST（直前）",
    "tilt":            "チルト角（直前）",
    "is_night":        "ナイター開催フラグ（自動判定）",
    "wind_speed":      "風速",
}
SETTINGS_KEY = "kyotei_feature_settings"

# 基本情報グループ（最低1項目必須）
BASIC_FEATURES = [
    "boat_no", "racer_grade_num", "win_rate", "local_win_rate",
    "national_2rate", "local_2rate", "st_avg", "fly_count", "motor_rate",
]

# scraper.VENUE_CODES（name→code dict）から順序を保って生成
VENUE_LIST: list[tuple[str, str]] = list(scraper.VENUE_CODES.items())


# ── ヘルパー関数 ────────────────────────────────────────────────
def _pad(h=0, v=0, top=None, bottom=None, left=None, right=None):
    return ft.Padding(
        left=left if left is not None else h,
        right=right if right is not None else h,
        top=top if top is not None else v,
        bottom=bottom if bottom is not None else v,
    )


def _mar(h=0, v=0, top=None, bottom=None, left=None, right=None):
    return ft.Margin(
        left=left if left is not None else h,
        right=right if right is not None else h,
        top=top if top is not None else v,
        bottom=bottom if bottom is not None else v,
    )


def _label(text: str) -> ft.Container:
    """アクセントカラーの縦線付きセクションラベル。"""
    return ft.Container(
        content=ft.Row([
            ft.Container(width=3, height=16, bgcolor=C_ACCENT, border_radius=2),
            ft.Text(text, size=13, color=C_SUB, weight=ft.FontWeight.W_500),
        ], spacing=6),
        margin=_mar(top=16, bottom=6),
    )


def _score_bar(item: dict) -> ft.Container:
    """予測スコアバー1行: [艇番バッジ] [選手名] [スコアpt] [1着X%]"""
    pct = item["score"]
    boat_no = item["boat_no"]
    col = BOAT_COLORS.get(boat_no, {"bg": C_ACCENT, "text": C_BG})
    return ft.Container(
        content=ft.Column([
            ft.Row([
                ft.Container(
                    content=ft.Text(
                        str(boat_no), size=13,
                        color=col["text"], weight=ft.FontWeight.BOLD,
                    ),
                    width=26, height=26,
                    bgcolor=col["bg"], border_radius=13,
                    alignment=ft.Alignment.CENTER,
                ),
                ft.Text(item["racer_name"], size=13, color=C_TEXT, expand=True),
                ft.Text(
                    f"{item['score']}pt", size=13, color=C_ACCENT,
                    width=44, text_align=ft.TextAlign.RIGHT,
                ),
                ft.Text(
                    f"1着{int(item['prob'] * 100)}%", size=12, color=C_SUB, width=52,
                ),
            ], spacing=6),
            ft.Container(
                content=ft.Container(
                    width=pct * 2.4, height=4,
                    bgcolor=C_ACCENT, border_radius=2,
                ),
                bgcolor=C_BORDER, border_radius=2, height=4,
            ),
        ], spacing=4),
        padding=_pad(h=4, v=6),
    )


# ── 結果画面 ────────────────────────────────────────────────────
def build_result_screen(page: ft.Page, result: dict) -> None:
    controls = []

    # ヘッダー（戻るボタン付き）
    def go_back(e):
        page.scroll = ft.ScrollMode.AUTO
        build_top_screen(page)

    controls.append(ft.Container(
        content=ft.Row([
            ft.Column([
                ft.Text(
                    "競艇舟券予想ツール", size=16,
                    color=C_TITLE, weight=ft.FontWeight.BOLD,
                ),
                ft.Text(
                    f"{result['venue_name']} 第{result['race_no']}R",
                    size=12, color=C_SUB,
                ),
            ], spacing=2, expand=True),
            ft.TextButton(
                "< 戻る",
                style=ft.ButtonStyle(color=C_SUB),
                on_click=go_back,
            ),
        ], alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
        bgcolor=C_CARD,
        padding=_pad(h=14, v=10),
        border=ft.Border(bottom=ft.BorderSide(1, C_BORDER)),
    ))

    # バックテスト的中率
    acc = result.get("backtest_accuracy")
    if acc is not None:
        controls.append(ft.Container(
            content=ft.Text(
                f"モデル的中率（バックテスト）: {acc:.1%}",
                size=12, color=C_ACCENT,
            ),
            bgcolor=C_CARD,
            padding=_pad(h=12, v=8),
            border_radius=8,
            margin=_mar(top=10),
        ))

    # 予測スコア
    controls.append(_label("予測スコア"))
    controls.append(ft.Container(
        content=ft.Column(
            [_score_bar(s) for s in result["scores"]],
            spacing=2,
        ),
        bgcolor=C_CARD, padding=10, border_radius=8,
    ))

    # おすすめ舟券
    controls.append(_label("おすすめ舟券"))
    recs = result.get("recommendations", [])
    if recs:
        rec_rows = []
        for r in recs:
            boats_str = "→".join(map(str, r["boats"]))
            rec_rows.append(ft.Row([
                ft.Text(r["type"],  size=12, color=C_TEXT, width=44),
                ft.Text(boats_str, size=14, color=C_ACCENT,
                        weight=ft.FontWeight.BOLD, width=50),
                ft.Text(f"オッズ {r['odds']}", size=12, color=C_SUB, expand=True),
                ft.Text(f"期待値 {r['ev']:.2f}", size=12, color=C_SUCCESS),
            ], spacing=6))
        controls.append(ft.Container(
            content=ft.Column(rec_rows, spacing=8),
            bgcolor=C_CARD, padding=10, border_radius=8,
        ))
    else:
        controls.append(ft.Container(
            content=ft.Text(
                "現時点でオッズ有利な舟券はありません\n（オッズ未確定または期待値 < 1.0）",
                size=12, color=C_SUB,
            ),
            bgcolor=C_CARD, padding=10, border_radius=8,
        ))

    # 展示情報
    controls.append(_label("展示情報"))
    bi_list = result.get("before_info", [])
    if bi_list:
        bi_rows = [
            ft.Row([
                ft.Text("艇",    size=11, color=C_SUB, width=18),
                ft.Text("タイム", size=11, color=C_SUB, width=52),
                ft.Text("ST",    size=11, color=C_SUB, width=40),
                ft.Text("チルト", size=11, color=C_SUB, width=42),
                ft.Text("交換",   size=11, color=C_SUB, expand=True),
            ], spacing=4)
        ]
        for bi in sorted(bi_list, key=lambda x: x["boat_no"]):
            et = (
                f"{bi['exhibition_time']:.2f}"
                if bi.get("exhibition_time") is not None else "－"
            )
            st = (
                f"{bi['exhibition_st']:.2f}"
                if bi.get("exhibition_st") is not None else "－"
            )
            tl = (
                f"{bi['tilt']:+.1f}"
                if bi.get("tilt") is not None else "－"
            )
            pc = bi.get("parts_changed") or "－"
            bi_rows.append(ft.Row([
                ft.Text(str(bi["boat_no"]), size=13, color=C_ACCENT, width=18),
                ft.Text(et, size=13, color=C_TEXT, width=52),
                ft.Text(st, size=13, color=C_TEXT, width=40),
                ft.Text(tl, size=13, color=C_TEXT, width=42),
                ft.Text(
                    pc, size=13,
                    color=C_WARN if pc != "－" else C_TEXT,
                    expand=True,
                ),
            ], spacing=4))
        controls.append(ft.Container(
            content=ft.Column(bi_rows, spacing=6),
            bgcolor=C_CARD, padding=10, border_radius=8,
        ))
    else:
        controls.append(ft.Container(
            content=ft.Text("展示情報は未公開です", size=12, color=C_SUB),
            bgcolor=C_CARD, padding=10, border_radius=8,
        ))

    # 免責
    controls.append(ft.Container(
        content=ft.Text(
            "※本ツールは統計データの分析補助ツールです。的中・収益を保証しません。",
            size=10, color=C_SUB,
        ),
        margin=_mar(top=12, bottom=40),
    ))

    page.controls.clear()
    page.add(ft.Column(
        controls, spacing=0,
        scroll=ft.ScrollMode.AUTO, expand=True,
    ))
    page.update()


# ── 設定画面 ────────────────────────────────────────────────────
async def build_settings_screen(page: ft.Page) -> None:
    settings_json = await page.shared_preferences.get(SETTINGS_KEY)
    settings = (
        json.loads(settings_json)
        if settings_json
        else {f: True for f in FEATURE_LABELS}
    )

    toggle_btn = ft.TextButton(
        "すべて解除" if all(settings.values()) else "すべて選択",
        style=ft.ButtonStyle(color=C_ACCENT),
    )

    def _update_toggle_label():
        toggle_btn.content = "すべて解除" if all(settings.values()) else "すべて選択"

    def _make_handler(feat):
        async def _handler(e):
            if not e.control.value and feat in BASIC_FEATURES:
                basic_checked = [f for f in BASIC_FEATURES if settings.get(f, True)]
                if len(basic_checked) <= 1:
                    e.control.value = True
                    e.control.update()
                    page.snack_bar = ft.SnackBar(
                        content=ft.Text("基本情報は最低1項目必要です"),
                        open=True,
                    )
                    page.update()
                    return
            settings[feat] = e.control.value
            _update_toggle_label()
            toggle_btn.update()
            await page.shared_preferences.set(SETTINGS_KEY, json.dumps(settings))
        return _handler

    async def toggle_all(e):
        new_val = not all(settings.values())
        for feat in FEATURE_LABELS:
            # すべて解除のとき、基本情報の先頭（艇番）だけ残す
            keep = (not new_val and feat == BASIC_FEATURES[0])
            settings[feat] = True if keep else new_val
            checkboxes_by_feat[feat].value = settings[feat]
            checkboxes_by_feat[feat].update()
        await page.shared_preferences.set(SETTINGS_KEY, json.dumps(settings))
        _update_toggle_label()
        toggle_btn.update()

    toggle_btn.on_click = toggle_all

    checkboxes_by_feat = {}
    checkboxes = []
    for feat, label in FEATURE_LABELS.items():
        cb = ft.Checkbox(
            label=label,
            value=settings.get(feat, True),
            active_color=C_ACCENT,
            label_style=ft.TextStyle(color=C_TEXT, size=14),
            on_change=_make_handler(feat),
        )
        checkboxes.append(cb)
        checkboxes_by_feat[feat] = cb

    def _divider_row(text: str) -> ft.Row:
        return ft.Row([
            ft.Container(content=ft.Divider(color=C_BORDER, height=1), expand=True),
            ft.Text(f" {text} ", size=11, color=C_SUB),
            ft.Container(content=ft.Divider(color=C_BORDER, height=1), expand=True),
        ], vertical_alignment=ft.CrossAxisAlignment.CENTER, spacing=0)

    items = []
    for i, cb in enumerate(checkboxes):
        if i == 0:
            items.append(_divider_row("基本情報（最低1項目必須）"))
        elif i == 9:
            items.append(_divider_row("直前情報"))
        elif i == 12:
            items.append(_divider_row("環境情報"))
        items.append(cb)

    def go_back(e):
        build_top_screen(page)

    header = ft.Container(
        content=ft.Row([
            ft.Text("予測設定", size=16, color=C_TEXT, weight=ft.FontWeight.BOLD),
            ft.TextButton(
                "< 戻る",
                style=ft.ButtonStyle(color=C_SUB),
                on_click=go_back,
            ),
        ], alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
        bgcolor=C_CARD,
        padding=_pad(h=14, v=10),
        border=ft.Border(bottom=ft.BorderSide(1, C_BORDER)),
    )

    page.controls.clear()
    page.add(
        header,
        ft.Container(
            content=ft.Column([
                ft.Row(
                    [_label("使用する予測項目（14項目）"), toggle_btn],
                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                ),
                ft.Container(
                    content=ft.Column(items, spacing=2),
                    bgcolor=C_CARD, padding=10, border_radius=8,
                ),
                ft.Container(
                    content=ft.Column([
                        ft.Text(
                            "※ チェックを外した項目は NaN として予測モデルに渡されます（精度は低下します）",
                            size=12, color=C_SUB,
                        ),
                        ft.Text(
                            "※ 設定はこのブラウザに自動保存されます",
                            size=12, color=C_SUB,
                        ),
                    ], spacing=4),
                    margin=_mar(top=12),
                ),
            ], spacing=0),
            padding=_pad(h=14, bottom=40),
        ),
    )
    page.update()


# ── トップ画面 ──────────────────────────────────────────────────
def build_top_screen(page: ft.Page) -> None:
    state = {
        "venue_code": None,
        "venue_name": None,
        "race_no": None,
        "venue_btn": {},
        "race_btn": {},
        "schedule": {},  # venue_code -> {venue_code, venue_name, current_race}
    }

    # ヘッダー
    header = ft.Container(
        content=ft.Row([
            ft.Text(
                "競艇舟券予想ツール", size=18,
                color=C_TITLE, weight=ft.FontWeight.BOLD,
            ),
            ft.Row([
                ft.Text("v2.0", size=11, color=C_SUB),
                ft.IconButton(
                    icon=ft.icons.Icons.SETTINGS,
                    icon_color=C_SUB,
                    icon_size=20,
                    tooltip="予測設定",
                    on_click=lambda e: page.run_task(build_settings_screen, page),
                ),
            ], spacing=0),
        ], alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
        bgcolor=C_CARD,
        padding=_pad(h=14, v=12),
        border=ft.Border(bottom=ft.BorderSide(1, C_BORDER)),
    )

    # ── 会場ボタン ────────────────────────────────────────────
    def make_venue_btn(name: str, code: str) -> ft.OutlinedButton:
        btn = ft.OutlinedButton(
            name,
            style=ft.ButtonStyle(
                color=C_SUB,
                side={"": ft.BorderSide(1, C_BORDER)},
                padding=_pad(h=4, v=4),
                shape=ft.RoundedRectangleBorder(radius=6),
            ),
        )
        state["venue_btn"][code] = btn

        def on_click(e, c=code, n=name):
            state["race_no"] = None
            for k, b in state["venue_btn"].items():
                if b.disabled:
                    continue
                b.style = ft.ButtonStyle(
                    color="#FFFFFF" if k == c else C_SUB,
                    bgcolor=C_ACCENT if k == c else "transparent",
                    side={"": ft.BorderSide(1, C_ACCENT if k == c else C_BORDER)},
                    padding=_pad(h=4, v=4),
                    shape=ft.RoundedRectangleBorder(radius=6),
                )
                b.update()
            state["venue_code"] = c
            state["venue_name"] = n
            _apply_race_schedule(c)

        btn.on_click = on_click
        return btn

    venue_btns = [make_venue_btn(n, c) for n, c in VENUE_LIST]
    venue_grid = ft.Container(
        content=ft.ResponsiveRow(
            [ft.Container(col={"xs": 3}, content=b) for b in venue_btns],
            spacing=4, run_spacing=4,
        ),
        bgcolor=C_CARD, padding=10, border_radius=8,
    )

    # ── レース番号ボタン ──────────────────────────────────────
    def make_race_btn(rno: int) -> ft.OutlinedButton:
        btn = ft.OutlinedButton(
            f"{rno}R",
            style=ft.ButtonStyle(
                color=C_SUB,
                side={"": ft.BorderSide(1, C_BORDER)},
                padding=_pad(h=4, v=4),
                shape=ft.RoundedRectangleBorder(radius=6),
            ),
        )
        state["race_btn"][rno] = btn

        def on_click(e, r=rno):
            for k, b in state["race_btn"].items():
                if b.disabled:
                    continue
                b.style = ft.ButtonStyle(
                    color="#FFFFFF" if k == r else C_SUB,
                    bgcolor=C_ACCENT if k == r else "transparent",
                    side={"": ft.BorderSide(1, C_ACCENT if k == r else C_BORDER)},
                    padding=_pad(h=4, v=4),
                    shape=ft.RoundedRectangleBorder(radius=6),
                )
                b.update()
            state["race_no"] = r

        btn.on_click = on_click
        return btn

    race_btns = [make_race_btn(r) for r in range(1, 13)]
    race_grid = ft.Container(
        content=ft.ResponsiveRow(
            [ft.Container(col={"xs": 2}, content=b) for b in race_btns],
            spacing=4, run_spacing=4,
        ),
        bgcolor=C_CARD, padding=10, border_radius=8,
    )

    # ── メッセージ・取得ボタン ─────────────────────────────────
    msg = ft.Text("", size=13, color=C_ERROR)

    fetch_btn = ft.ElevatedButton(
        "予想を取得する",
        style=ft.ButtonStyle(
            bgcolor=C_ACCENT,
            color=C_BG,
            padding=_pad(h=24, v=14),
            shape=ft.RoundedRectangleBorder(radius=8),
        ),
        width=300,
    )

    # UI更新をスレッドから安全に行うためのロック
    _fetch_lock = threading.Lock()

    def _update_ui(msg_text: str, msg_color: str, btn_disabled: bool) -> None:
        """バックグラウンドスレッドからのUI更新を一元化する。"""
        with _fetch_lock:
            msg.value = msg_text
            msg.color = msg_color
            fetch_btn.disabled = btn_disabled
            try:
                page.update()
            except Exception:
                pass  # セッション切断時など更新失敗は無視

    async def on_fetch(e):
        settings_json = await page.shared_preferences.get(SETTINGS_KEY)
        settings = (
            json.loads(settings_json)
            if settings_json
            else {f: True for f in FEATURE_LABELS}
        )
        disabled = [f for f, v in settings.items() if not v]

        if len(disabled) == len(FEATURE_LABELS):
            _update_ui("予測項目が0件です。設定画面で1つ以上チェックしてください", C_ERROR, False)
            return

        if not state["venue_code"]:
            _update_ui("会場を選択してください", C_ERROR, False)
            return
        if not state["race_no"]:
            _update_ui("レース番号を選択してください", C_ERROR, False)
            return

        _update_ui("データ取得中... しばらくお待ちください", C_WARN, True)

        def _run():
            try:
                today = scraper.today_jst()
                vc = state["venue_code"]
                rno = state["race_no"]
                vn = state["venue_name"]

                # entries を含む5リクエストを全て並列取得
                with ThreadPoolExecutor(max_workers=5) as ex:
                    fut_entries = ex.submit(scraper.get_race_entries, vc, rno, today)
                    fut_bi      = ex.submit(scraper.get_before_info,  vc, rno, today)
                    fut_odds    = ex.submit(scraper.get_odds,          vc, rno, today)
                    fut_2f      = ex.submit(scraper.get_odds_2f,       vc, rno, today)
                    fut_3f      = ex.submit(scraper.get_odds_3f,       vc, rno, today)
                entries     = fut_entries.result()
                before_info = fut_bi.result()
                odds        = fut_odds.result()
                odds_2f     = fut_2f.result()
                odds_3f     = fut_3f.result()

                if not entries:
                    _update_ui("出走情報を取得できませんでした（E001）", C_ERROR, False)
                    return

                result = predictor.predict(
                    entries, before_info, odds, odds_2f, odds_3f,
                    venue_code=vc, race_no=rno,
                    disabled_features=disabled if disabled else None,
                )
                result["venue_name"] = vn
                result["race_no"] = rno

                build_result_screen(page, result)

            except Exception as ex:
                _update_ui(f"エラーが発生しました: {ex}", C_ERROR, False)

        threading.Thread(target=_run, daemon=True).start()

    fetch_btn.on_click = on_fetch

    # ── スケジュール非活性化ヘルパー ──────────────────────────────
    def _apply_venue_schedule() -> None:
        """本日非開催の会場ボタンを非活性化する。"""
        if not state["schedule"]:
            return  # 取得失敗時は全会場を有効のまま維持
        active = set(state["schedule"].keys())
        for code, btn in state["venue_btn"].items():
            if code not in active:
                btn.style = ft.ButtonStyle(
                    color=C_DIS_TEXT, bgcolor=C_DIS_BG,
                    side={"": ft.BorderSide(1, C_DIS_SIDE)},
                    padding=_pad(h=4, v=4),
                    shape=ft.RoundedRectangleBorder(radius=6),
                )
                btn.disabled = True
        try:
            page.update()
        except Exception:
            pass

    def _apply_race_schedule(vc: str) -> None:
        """終了済みレースボタンを非活性化する。スケジュール未取得時は全レース有効。"""
        current = state["schedule"].get(vc, {}).get("current_race")
        selected = state["race_no"]
        for rno, btn in state["race_btn"].items():
            ended = current is not None and rno < current
            if ended:
                btn.style = ft.ButtonStyle(
                    color=C_DIS_TEXT, bgcolor=C_DIS_BG,
                    side={"": ft.BorderSide(1, C_DIS_SIDE)},
                    padding=_pad(h=4, v=4),
                    shape=ft.RoundedRectangleBorder(radius=6),
                )
                btn.disabled = True
                if state["race_no"] == rno:
                    state["race_no"] = None
            elif rno == selected:
                btn.style = ft.ButtonStyle(
                    color="#FFFFFF", bgcolor=C_ACCENT,
                    side={"": ft.BorderSide(1, C_ACCENT)},
                    padding=_pad(h=4, v=4),
                    shape=ft.RoundedRectangleBorder(radius=6),
                )
                btn.disabled = False
            else:
                btn.style = ft.ButtonStyle(
                    color=C_SUB, bgcolor="transparent",
                    side={"": ft.BorderSide(1, C_BORDER)},
                    padding=_pad(h=4, v=4),
                    shape=ft.RoundedRectangleBorder(radius=6),
                )
                btn.disabled = False
        try:
            page.update()
        except Exception:
            pass

    def _load_schedule() -> None:
        state["schedule"] = {s["venue_code"]: s for s in scraper.get_today_schedule()}
        _apply_venue_schedule()
        if state["venue_code"]:
            _apply_race_schedule(state["venue_code"])

    # ── ページ組み立て ─────────────────────────────────────────
    page.controls.clear()
    page.add(
        header,
        ft.Container(
            content=ft.Column([
                _label("会場を選択"),
                venue_grid,
                _label("レース番号を選択"),
                race_grid,
                ft.Container(
                    content=fetch_btn,
                    alignment=ft.Alignment.CENTER,
                    margin=_mar(v=16),
                ),
                msg,
            ], spacing=0),
            padding=_pad(bottom=40),
        ),
    )
    page.update()

    # ページ描画後にスケジュール取得を開始
    threading.Thread(target=_load_schedule, daemon=True).start()
