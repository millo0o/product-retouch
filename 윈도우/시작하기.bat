@echo off
chcp 65001 >nul
cd /d "%~dp0"
title 제품 보정 대시보드 (이 창을 닫으면 꺼집니다)
if not exist ".venv\Scripts\python.exe" (
  echo 먼저 "설치하기.bat" 을 실행해 주세요.
  pause
  exit /b
)
echo 새 버전 확인 중...
".venv\Scripts\python.exe" -X utf8 scripts\updater.py
echo 대시보드를 여는 중... 브라우저가 열리면 사진을 끌어다 놓으세요.
echo 이 창을 닫으면 보정이 멈춥니다. 쓰는 동안은 열어 두세요.
".venv\Scripts\python.exe" -X utf8 scripts\server.py --open
pause
