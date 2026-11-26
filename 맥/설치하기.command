#!/bin/zsh
# 맥 설치 (처음 한 번만). Finder에서 두 번 누르면 터미널이 열리며 실행됩니다.
cd "$(dirname "$0")/.."
echo "=================================================="
echo "  제품 사진 보정 - 맥 설치 (처음 한 번만)"
echo "=================================================="
if ! xcode-select -p >/dev/null 2>&1; then
  echo "맥 개발 도구(파이썬 포함)가 없어 설치 창을 띄웁니다. 설치가 끝나면 이 파일을 다시 실행해 주세요."
  xcode-select --install; read -k1 "?아무 키나 누르면 닫힙니다"; exit
fi
echo "[1/3] 글씨 인식·누끼 도구 만들기 (맥 내장 기능 사용)"
mkdir -p bin
ok=1
for t in ocr labelfit stack retouch; do
  swiftc -O -o "bin/$t" "scripts/mac/$t.swift" 2>/dev/null || ok=0
done
if [[ $ok == 0 ]]; then
  echo "  맥 도구를 만들지 못해 파이썬 도구로 설치합니다 (몇 분 걸림)"
  rm -rf bin
  [[ -x .venv/bin/python ]] || /usr/bin/python3 -m venv .venv
  .venv/bin/python -m pip install -q --upgrade pip && .venv/bin/python -m pip install -q -r requirements.txt && .venv/bin/python scripts/warmup.py
fi
echo "[2/3] Codex(GPT 연결 프로그램) 확인"
CODEX=""
for c in "$(command -v codex)" /Applications/ChatGPT.app/Contents/Resources/codex-cli/bin/codex /opt/homebrew/bin/codex /usr/local/bin/codex; do
  [[ -n "$c" && -x "$c" ]] && { CODEX="$c"; break; }
done
if [[ -z "$CODEX" ]]; then
  if command -v brew >/dev/null; then brew install codex; CODEX="$(command -v codex)"
  elif command -v npm >/dev/null; then npm install -g @openai/codex; CODEX="$(command -v codex)"
  else echo "  Codex를 설치할 수 없어요. ChatGPT 맥 앱을 설치하거나 https://nodejs.org 에서 Node.js를 설치한 뒤 다시 실행해 주세요."; read -k1; exit; fi
fi
echo "[3/3] ChatGPT 계정 로그인 (브라우저가 열리면 본인 계정으로)"
"$CODEX" login status >/dev/null 2>&1 || "$CODEX" login
echo ""; echo "설치 끝! 이제 '맥' 폴더의 '시작하기.command' 를 두 번 눌러 쓰시면 됩니다."
read -k1 "?아무 키나 누르면 닫힙니다"
