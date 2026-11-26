#!/bin/zsh
# 대시보드 켜기. 이 창을 닫으면 보정이 멈춥니다.
cd "$(dirname "$0")"
PY=/usr/bin/python3; [[ -x .venv/bin/python ]] && [[ ! -x bin/ocr ]] && PY=.venv/bin/python
echo "대시보드를 여는 중... 브라우저가 열리면 사진을 끌어다 놓으세요. (이 창을 닫으면 꺼집니다)"
"$PY" scripts/server.py --open
