@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo 啟動口說剪片台，稍等會自動打開瀏覽器…
echo 關閉請直接關掉這個視窗。
python voicecut\server.py
pause
