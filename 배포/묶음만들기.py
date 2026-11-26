#!/usr/bin/env python3
"""윈도우용·맥용 내려받기 묶음(zip)을 따로 만든다.
   받는 사람은 자기 컴퓨터 것 하나만 받아 풀고, 맨 위의 설치하기 → 시작하기만 누르면 된다.
   사용법: python3 배포/묶음만들기.py [버전=날짜]  →  배포/dist/제품보정-윈도우-<버전>.zip, 제품보정-맥-<버전>.zip
   (프로그램 본체는 저장소 하나로 고치고, 묶을 때만 컴퓨터별로 나눔)"""
import os, sys, zipfile, datetime, stat

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VER = sys.argv[1] if len(sys.argv) > 1 else datetime.date.today().strftime("%Y%m%d")
DIST = os.path.join(ROOT, "배포", "dist"); os.makedirs(DIST, exist_ok=True)

COMMON = ["scripts/config.py", "scripts/pipeline.py", "scripts/server.py", "scripts/pyvision.py", "scripts/warmup.py",
          "templates/index.html", "requirements.txt", "config.example.json"] + \
         [f"prompts/{f}" for f in sorted(os.listdir(os.path.join(ROOT, "prompts"))) if f.endswith(".txt")]
PLAT = {
    "윈도우": {"files": [], "launch": ["윈도우/설치하기.bat", "윈도우/시작하기.bat"], "guide": "배포/윈도우-사용법.txt"},
    "맥": {"files": [f"scripts/mac/{t}.swift" for t in ("ocr", "labelfit", "stack", "retouch")],
           "launch": ["맥/설치하기.command", "맥/시작하기.command"], "guide": "배포/맥-사용법.txt"},
}

def put(z, arc, data, exe=False):
    zi = zipfile.ZipInfo(arc, date_time=datetime.datetime.now().timetuple()[:6])
    zi.compress_type = zipfile.ZIP_DEFLATED
    zi.external_attr = ((0o755 if exe else 0o644) | stat.S_IFREG) << 16
    zi.flag_bits |= 0x800   # 파일 이름 UTF-8 (윈도우 탐색기에서 한글 이름 안 깨지게)
    z.writestr(zi, data)

for name, p in PLAT.items():
    top = f"제품보정-{name}"
    out = os.path.join(DIST, f"{top}-{VER}.zip")
    with zipfile.ZipFile(out, "w") as z:
        for rel in COMMON + p["files"]:
            put(z, f"{top}/{rel}", open(os.path.join(ROOT, rel), "rb").read())
        for rel in p["launch"]:
            data = open(os.path.join(ROOT, rel), "rb").read()
            # 저장소에선 윈도우/·맥/ 폴더 안에 있어서 상위로 이동했지만, 묶음에선 맨 위에 둠
            data = data.replace(b'cd /d "%~dp0.."', b'cd /d "%~dp0"').replace(b'cd "$(dirname "$0")/.."', b'cd "$(dirname "$0")"')
            data = data.replace('"윈도우" 폴더의 '.encode(), b"").replace("'맥' 폴더의 ".encode(), b"")
            put(z, f"{top}/{os.path.basename(rel)}", data, exe=rel.endswith(".command"))
        g = open(os.path.join(ROOT, p["guide"]), encoding="utf-8").read()
        put(z, f"{top}/사용법.txt", (g.replace("\n", "\r\n") if name == "윈도우" else g).encode("utf-8-sig" if name == "윈도우" else "utf-8"))
    print(out, os.path.getsize(out) // 1024, "KB")
