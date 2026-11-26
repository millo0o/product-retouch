#!/usr/bin/env python3
"""자동 업데이트: 시작하기를 누를 때마다 깃허브에 새 버전이 있는지 보고, 있으면 프로그램 파일을 바꿔 끼운다.
   - 내 폴더의 버전.txt 와 깃허브(main)의 버전.txt 가 다르면, 저장소 zip을 받아 내 컴퓨터 폴더(맥/윈도우) 것만 덮어씀
   - 건드리지 않는 것: config.json(내 설정), .venv, bin(설치된 도구), 설치하기·시작하기(실행 중에 바꾸면 꼬임)
   - 인터넷이 없거나 깃허브가 안 되면 그냥 넘어감 (지금 버전으로 실행)
   - 표준 라이브러리만 씀 (맥 기본 파이썬, 윈도우 .venv 파이썬 둘 다)"""
import io, os, platform, shutil, socket, subprocess, sys, urllib.request, zipfile

REPO = "millo0o/product-retouch"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PLAT = "윈도우" if platform.system() == "Windows" else "맥"
KEEP = {"config.json", ".venv", "bin", "__pycache__", ".DS_Store", "새소식.json"}
NO_TOUCH = {"설치하기.bat", "시작하기.bat", "설치하기.command", "시작하기.command"}

def get(url, t=15):
    req = urllib.request.Request(url, headers={"User-Agent": "product-retouch-updater"})
    with urllib.request.urlopen(req, timeout=t) as r: return r.read()

def local_ver():
    try: return open(os.path.join(ROOT, "버전.txt"), encoding="utf-8").read().strip()
    except OSError: return ""

def running(port):
    s = socket.socket(); s.settimeout(0.5)
    try: return s.connect_ex(("127.0.0.1", port)) == 0
    finally: s.close()

def main():
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import config
        if running(config.PORT): return   # 이미 켜져 있으면 지금 돌고 있는 걸 건드리지 않음
    except Exception: pass
    mine = local_ver()
    try:
        from urllib.parse import quote
        latest = get(f"https://raw.githubusercontent.com/{REPO}/main/{quote(PLAT)}/{quote('버전.txt')}", 8).decode("utf-8").strip()
    except Exception:
        print("  (업데이트 확인 못 함: 인터넷 연결을 확인하세요. 지금 버전으로 켭니다)"); return
    if not latest or latest == mine:
        print(f"  최신 버전이에요 ({mine or '?'})"); return
    print(f"  새 버전이 있어요: {mine or '?'} → {latest}. 바꾸는 중...", flush=True)
    try: data = get(f"https://codeload.github.com/{REPO}/zip/refs/heads/main", 60)
    except Exception as e:
        print("  (새 버전 받기 실패, 지금 버전으로 켭니다)", e); return
    z = zipfile.ZipFile(io.BytesIO(data))
    pre = None
    for n in z.namelist():   # "product-retouch-main/윈도우/..." (zip 안 이름은 UTF-8)
        parts = n.split("/")
        if len(parts) > 2 and parts[1] == PLAT: pre = parts[0] + "/" + PLAT + "/"; break
    if not pre: print("  (새 버전에서 내 컴퓨터 폴더를 못 찾음)"); return
    old_req = open(os.path.join(ROOT, "requirements.txt"), "rb").read() if os.path.exists(os.path.join(ROOT, "requirements.txt")) else b""
    old_swift = {f: open(os.path.join(ROOT, "scripts", "mac", f), "rb").read() for f in os.listdir(os.path.join(ROOT, "scripts", "mac"))} if os.path.isdir(os.path.join(ROOT, "scripts", "mac")) else {}
    new = {}
    for n in z.namelist():
        if not n.startswith(pre) or n.endswith("/"): continue
        rel = n[len(pre):]
        if rel.split("/")[0] in KEEP: continue
        new[rel] = z.read(n)
    # 덮어쓰기 (실행 중인 설치하기·시작하기는 그대로)
    changed_launcher = []
    for rel, b in new.items():
        dst = os.path.join(ROOT, *rel.split("/"))
        if rel in NO_TOUCH:
            if not os.path.exists(dst) or open(dst, "rb").read() != b: changed_launcher.append(rel)
            continue
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        with open(dst, "wb") as f: f.write(b)
    # 새 버전에서 없어진 프로그램 파일 지우기 (scripts·prompts·templates 안만)
    for d in ("scripts", "prompts", "templates"):
        base = os.path.join(ROOT, d)
        for dp, dns, fns in os.walk(base):
            dns[:] = [x for x in dns if x not in KEEP]
            for fn in fns:
                rel = os.path.relpath(os.path.join(dp, fn), ROOT).replace(os.sep, "/")
                if rel not in new and not fn.endswith(".pyc"): os.remove(os.path.join(dp, fn))
    # 필요한 프로그램 목록이 바뀌었으면 다시 설치
    py = os.path.join(ROOT, ".venv", "Scripts" if PLAT == "윈도우" else "bin", "python.exe" if PLAT == "윈도우" else "python")
    if new.get("requirements.txt", old_req) != old_req and os.path.exists(py):
        print("  필요한 프로그램 다시 설치 중...", flush=True)
        subprocess.run([py, "-m", "pip", "install", "-q", "-r", os.path.join(ROOT, "requirements.txt")])
    # 맥 도구 소스가 바뀌었으면 다시 만들기
    if PLAT == "맥" and os.path.isdir(os.path.join(ROOT, "bin")):
        for rel, b in new.items():
            if rel.startswith("scripts/mac/") and rel.endswith(".swift") and old_swift.get(rel.split("/")[-1]) != b:
                name = rel.split("/")[-1][:-6]
                print(f"  맥 도구 다시 만드는 중: {name}", flush=True)
                subprocess.run(["swiftc", "-O", "-o", os.path.join(ROOT, "bin", name), os.path.join(ROOT, rel)])
    # 바뀐 점: 바뀐점.txt 에서 내 옛 버전보다 새로운 항목만 골라 새소식.json 으로 → 대시보드 맨 위에 보여 줌
    notes = []
    try:
        cur = None
        for line in new.get("바뀐점.txt", b"").decode("utf-8").splitlines():
            m = line.strip()
            if m.startswith("## "):
                cur = m[3:].strip()
                if cur == mine: break
                notes.append({"ver": cur, "items": []})
            elif m.startswith("- ") and notes: notes[-1]["items"].append(m[2:])
        import json
        with open(os.path.join(ROOT, "새소식.json"), "w", encoding="utf-8") as f:
            json.dump({"from": mine, "to": latest, "notes": notes}, f, ensure_ascii=False)
    except Exception: pass
    print(f"  업데이트 끝 → {latest}")
    for n in notes:
        for it in n["items"]: print(f"   · {it}")
    if changed_launcher:
        print("  * 설치하기·시작하기 파일도 새로 바뀌었어요. 필요하면 깃허브에서 새로 받아 주세요:", ", ".join(changed_launcher))

if __name__ == "__main__":
    try: main()
    except Exception as e: print("  (업데이트 중 문제가 있어 건너뜀)", e)
