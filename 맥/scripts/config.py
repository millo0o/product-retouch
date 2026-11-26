# 사람마다 다른 설정 (GPT 계정 폴더, 작업 폴더, 포트, 글씨 읽기 엔진)
# 바꾸려면 이 저장소 맨 위에 config.json 을 만든다 (config.example.json 참고). config.json 은 깃에 올리지 않는다.
import json, os, platform, shutil

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_cfg = {}
try:
    with open(os.path.join(ROOT, "config.json"), encoding="utf-8") as f: _cfg = json.load(f)
except FileNotFoundError: pass

def _p(v): return os.path.abspath(os.path.expanduser(v)) if v else v
IS_MAC = platform.system() == "Darwin"
IS_WIN = platform.system() == "Windows"

def _find_codex():
    if _cfg.get("codex"): return _p(_cfg["codex"])
    for c in ("codex", "codex.cmd", "codex.exe"):
        w = shutil.which(c)
        if w: return w
    cands = ["/Applications/ChatGPT.app/Contents/Resources/codex-cli/bin/codex", "/Applications/Codex.app/Contents/Resources/codex"]
    if IS_WIN:
        appdata = os.environ.get("APPDATA", "")
        cands += [os.path.join(appdata, "npm", "codex.cmd")]
    for c in cands:
        if os.path.exists(c): return c
    return "codex"

CODEX = _find_codex()
# GPT 계정: config.json 의 codex_home (없으면 Codex 기본 계정 ~/.codex)
CODEX_HOME = _p(_cfg.get("codex_home") or os.environ.get("CODEX_HOME") or "~/.codex")
_desk = os.path.expanduser("~/Desktop")
WORK_DIR = _p(_cfg.get("work_dir") or os.path.join(_desk if os.path.isdir(_desk) else os.path.expanduser("~"), "제품보정"))
PORT = int(_cfg.get("port") or 8767)
# 글씨 읽기·누끼·겹치기 엔진: mac = 맥 내장 Vision (bin/ 의 swift 도구), py = 파이썬 (윈도우·맥 공통)
_mac_ok = IS_MAC and os.path.exists(os.path.join(ROOT, "bin", "ocr"))
ENGINE = os.environ.get("PR_ENGINE") or _cfg.get("engine") or ("mac" if _mac_ok else "py")
WORKERS = int(_cfg.get("workers") or 2)

def codex_env():
    return dict(os.environ, CODEX_HOME=CODEX_HOME)
