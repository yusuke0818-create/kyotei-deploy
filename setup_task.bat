@echo off
chcp 65001 > nul
echo タスクスケジューラに夜間収集タスクを登録します...

schtasks /create ^
  /tn "競艇データ夜間収集" ^
  /tr "powershell.exe -ExecutionPolicy Bypass -NonInteractive -File \"C:\work\kyotei_deploy\local_collect.ps1\"" ^
  /sc daily ^
  /st 00:30 ^
  /rl highest ^
  /f

if %errorlevel% == 0 (
    echo.
    echo [完了] タスクの登録が完了しました。
    echo 毎日0:30に自動でデータ収集が始まります。
) else (
    echo.
    echo [エラー] 登録に失敗しました。管理者として実行してください。
)

pause
