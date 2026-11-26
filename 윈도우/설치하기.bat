@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0.."
title 제품 보정 설치
echo ==================================================
echo   제품 사진 보정 - 설치 (처음 한 번만)
echo ==================================================
echo.

echo [1/5] 파이썬 확인
set "PY="
py -3 -c "import sys" >nul 2>nul && set "PY=py -3"
if not defined PY python -c "import sys" >nul 2>nul && set "PY=python"
if not defined PY (
  echo   파이썬이 없어 설치합니다. 설치 창이 뜨면 허용해 주세요.
  winget install -e --id Python.Python.3.12 --accept-package-agreements --accept-source-agreements
  echo.
  echo   파이썬 설치가 끝났습니다. 이 창을 닫고 "윈도우" 폴더의 "설치하기.bat" 을 한 번 더 실행해 주세요.
  pause
  exit /b
)

echo [2/5] Codex(GPT 연결 프로그램) 확인
where codex >nul 2>nul
if errorlevel 1 (
  where npm >nul 2>nul
  if errorlevel 1 (
    echo   Node.js 가 없어 설치합니다. 설치 창이 뜨면 허용해 주세요.
    winget install -e --id OpenJS.NodeJS.LTS --accept-package-agreements --accept-source-agreements
    echo.
    echo   Node.js 설치가 끝났습니다. 이 창을 닫고 "윈도우" 폴더의 "설치하기.bat" 을 한 번 더 실행해 주세요.
    pause
    exit /b
  )
  call npm install -g @openai/codex
)

echo [3/5] 보정 프로그램 설치 (몇 분 걸립니다)
if not exist ".venv\Scripts\python.exe" %PY% -m venv .venv
".venv\Scripts\python.exe" -m pip install --upgrade pip -q
".venv\Scripts\python.exe" -m pip install -r requirements.txt -q
if errorlevel 1 (
  echo   설치 중 오류가 났습니다. 위 메시지를 캡처해서 보내 주세요.
  pause
  exit /b
)

echo [4/5] 글씨 인식·누끼 모델 내려받기
".venv\Scripts\python.exe" -X utf8 scripts\warmup.py

echo [5/5] ChatGPT 계정 로그인
echo   브라우저가 열리면 본인 ChatGPT 계정으로 로그인해 주세요. (이미 로그인돼 있으면 넘어갑니다)
call codex login status >nul 2>nul
if errorlevel 1 call codex login

echo.
echo 설치 끝! 이제 "윈도우" 폴더의 "시작하기.bat" 을 두 번 눌러 쓰시면 됩니다.
pause
