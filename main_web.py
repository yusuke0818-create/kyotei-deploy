# 目的：競艇舟券予想ツール Renderエントリーポイント
# 作成日：2026-05-19
# 対象OS：Render（Linux）/ ローカルWebブラウザ確認
# 依存ライブラリ：flet

import os

import flet as ft

import gui
import predictor


def main(page: ft.Page) -> None:
    page.title = "競艇舟券予想ツール"
    page.bgcolor = "#0A1628"
    page.padding = ft.Padding(left=12, right=12, top=0, bottom=0)
    page.scroll = ft.ScrollMode.AUTO

    gui.build_top_screen(page)


# モデルをプロセス起動時に1回だけロード（接続ごとに呼ばれる main() の外に置く）
try:
    predictor.initialize()
except FileNotFoundError:
    pass  # モデル未生成時はエラーにしない（GUI側でハンドリング）

ft.run(
    main,
    view=ft.AppView.WEB_BROWSER,
    host="0.0.0.0",
    port=int(os.environ.get("PORT", 8080)),
)
