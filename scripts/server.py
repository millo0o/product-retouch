#!/usr/bin/env python3
"""제품 보정 대시보드 서버 (이 맥 127.0.0.1에서만 열림). 표준 라이브러리만 씀.
  GET  /                  대시보드 화면 (templates/index.html)
  POST /api/upload        사진 한 장 올리기 (본문 = 파일, 헤더 X-Name = encodeURIComponent(파일 이름)) → 바로 보정 대기열에
  GET  /api/jobs          작업 목록 (화면이 1초마다 읽음 = 실시간)
  POST /api/retry?id=     처음부터 다시 보정 (&mode=fix 보정본은 두고 글씨 검사·고치기만 / &mode=deep 빠른 보정본을 이어 꼼꼼 보정 / &mode=fast 처음부터 빠른 보정)
  (올리기 헤더 X-Mode = fast|deep, 기본 fast)
  POST /api/remove?id=    목록에서 빼기 (파일은 휴지통 폴더로 이동, 지우지 않음)
  GET  /files/<id>/<파일>  결과 이미지 (?dl=1 이면 "원본이름_누끼.png" 이름으로 다운로드)
  GET  /api/zip?ids=a,b&kinds=retouched,cutout,white   여러 장 한꺼번에 zip 다운로드
사용법: server.py [작업 폴더] [포트]   (안 주면 config.json → 기본 ~/Desktop/제품보정, 8767)
  --open : 켜고 나서 기본 브라우저로 대시보드 열기 (윈도우·맥 바로가기용)
"""
import http.server, json, os, sys, threading, subprocess, datetime, uuid, zipfile, io, shutil, urllib.parse, time, queue

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config
SKILL = config.ROOT
ARGS = [a for a in sys.argv[1:] if not a.startswith("--")]
WORK = os.path.abspath(os.path.expanduser(ARGS[0])) if ARGS else config.WORK_DIR
PORT = int(ARGS[1]) if len(ARGS) > 1 else config.PORT
ORIG, OUT, TRASH = (os.path.join(WORK, d) for d in ("원본", "결과물", "_휴지통"))
for d in (ORIG, OUT, TRASH): os.makedirs(d, exist_ok=True)
JOBS = os.path.join(WORK, "jobs.json")
INDEX = os.path.join(SKILL, "templates", "index.html")
KINDS = {"retouched": ("보정.png", "보정"), "cutout": ("누끼.png", "누끼")}
PIPELINE = os.path.join(SKILL, "scripts", "pipeline.py")
WORKERS = config.WORKERS   # 동시에 보정하는 사진 수 (한 장 안에서도 GPT를 여러 번 동시에 부름)
PYENV = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8")   # 윈도우에서도 한글 파일을 UTF-8로

lock = threading.Lock()
q = queue.Queue()
jobs = []          # 새 것이 앞

def now(): return datetime.datetime.now().astimezone().isoformat(timespec="seconds")

def save():
    tmp = JOBS + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f: json.dump(jobs, f, ensure_ascii=False, indent=1)
    os.replace(tmp, JOBS)

def load():
    global jobs
    try: jobs = json.load(open(JOBS))
    except Exception: jobs = []
    for j in jobs:   # 서버가 꺼질 때 하던 일은 다시 대기열로
        if j["status"] in ("대기", "보정 중"): j["status"] = "대기"; q.put(j["id"])

def find(jid):
    return next((j for j in jobs if j["id"] == jid), None)

def worker():
    while True:
        jid = q.get()
        with lock:
            j = find(jid)
            if not j or j["status"] != "대기": continue
            j["status"] = "보정 중"; j["started"] = now(); save()
        d = os.path.join(OUT, jid)
        mode = j.pop("mode", "")
        keep = mode in ("fix", "deep") and os.path.exists(os.path.join(d, "progress.json"))
        if not keep: shutil.rmtree(d, ignore_errors=True)
        os.makedirs(d, exist_ok=True)
        flag = {"fix": ["--fix-only"], "deep": ["--deep"]}.get(mode, []) if keep else []
        if j.get("quality", "deep") == "fast" and mode != "deep" and not flag: flag = ["--fast"]
        if mode == "deep" or (mode == "" and j.get("quality") == "deep"): j["quality"] = "deep"
        if mode == "deep" and not keep: flag = []   # 이어 쓸 게 없으면 처음부터 꼼꼼 보정
        with lock: save()
        cmd = [sys.executable, PIPELINE, os.path.join(ORIG, j["file"]), d] + flag
        try:
            with open(os.path.join(d, "실행기록.txt"), "w", encoding="utf-8") as lf:
                r = subprocess.run(cmd, stdout=lf, stderr=subprocess.STDOUT, text=True, timeout=3600, env=PYENV)
            rep = {}
            try: rep = json.load(open(os.path.join(d, "progress.json")))
            except Exception: pass
            msg = [rep.get("error", "")]
            ok = r.returncode == 0 and rep.get("done")
            with lock:
                j["status"] = "완료" if ok else "실패"; j.pop("deepFrom", None)
                j["report"] = rep; j["message"] = rep.get("error") or msg[0]; j["done"] = now(); save()
        except Exception as e:
            with lock: j["status"] = "실패"; j["message"] = str(e); j["done"] = now(); save()

class H(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a): pass

    def send(self, code, body=b"", ctype="application/json; charset=utf-8", extra=None):
        if isinstance(body, (dict, list)): body = json.dumps(body, ensure_ascii=False).encode()
        if isinstance(body, str): body = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype); self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items(): self.send_header(k, v)
        self.end_headers(); self.wfile.write(body)

    def parsed(self):
        u = urllib.parse.urlparse(self.path)
        return urllib.parse.unquote(u.path), {k: v[0] for k, v in urllib.parse.parse_qs(u.query).items()}

    def do_GET(self):
        path, qs = self.parsed()
        if path in ("/", "/index.html"):
            try: return self.send(200, open(INDEX, "rb").read(), "text/html; charset=utf-8")
            except FileNotFoundError: return self.send(200, "<h1>화면 준비 중</h1>", "text/html; charset=utf-8")
        if path == "/api/jobs":
            with lock:
                out = []
                for j in jobs:
                    j2 = dict(j)
                    if j["status"] in ("보정 중", "완료", "실패"):   # 진행 파일을 바로 읽어 붙임 (실시간, 저장된 옛 기록보다 정확)
                        try: j2["report"] = json.load(open(os.path.join(OUT, j["id"], "progress.json")))
                        except Exception: pass
                    out.append(j2)
                return self.send(200, {"jobs": out, "queue": q.qsize()})
        if path.startswith("/files/"):
            parts = path[len("/files/"):].split("/")
            if len(parts) < 2 or ".." in parts or not find(parts[0]): return self.send(404, {"error": "없음"})
            fp = os.path.join(OUT, *parts)
            if not os.path.isfile(fp): return self.send(404, {"error": "없음"})
            ext = os.path.splitext(fp)[1].lower()
            ctype = {".png": "image/png", ".jpg": "image/jpeg", ".json": "application/json"}.get(ext, "application/octet-stream")
            extra = {}
            if qs.get("dl"):
                base = os.path.splitext(find(parts[0])["name"])[0]
                label = next((v[1] for v in KINDS.values() if v[0] == parts[-1]), os.path.splitext(parts[-1])[0])
                extra["Content-Disposition"] = "attachment; filename*=UTF-8''" + urllib.parse.quote(f"{base}_{label}{ext}")
            return self.send(200, open(fp, "rb").read(), ctype, extra)
        if path == "/api/zip":
            ids = [i for i in qs.get("ids", "").split(",") if i]
            kinds = [k for k in qs.get("kinds", "retouched,cutout").split(",") if k in KINDS]
            buf = io.BytesIO(); used = set()
            with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as z:
                for jid in ids:
                    j = find(jid)
                    if not j or j["status"] != "완료": continue
                    base = os.path.splitext(j["name"])[0]
                    for k in kinds:
                        fn, label = KINDS[k]; fp = os.path.join(OUT, jid, fn)
                        if not os.path.isfile(fp): continue
                        arc = f"{label}/{base}_{label}{os.path.splitext(fn)[1]}"
                        n = 2
                        while arc in used: arc = f"{label}/{base}_{label}({n}){os.path.splitext(fn)[1]}"; n += 1
                        used.add(arc); z.write(fp, arc)
            name = "제품보정_" + datetime.datetime.now().strftime("%Y%m%d_%H%M") + ".zip"
            return self.send(200, buf.getvalue(), "application/zip", {"Content-Disposition": "attachment; filename*=UTF-8''" + urllib.parse.quote(name)})
        return self.send(404, {"error": "없음"})

    def do_POST(self):
        path, qs = self.parsed()
        if path == "/api/upload":
            name = os.path.basename(urllib.parse.unquote(self.headers.get("X-Name", "photo.jpg"))) or "photo.jpg"
            n = int(self.headers.get("Content-Length", 0))
            if n <= 0 or n > 200 * 1024 * 1024: return self.send(400, {"error": "파일 크기 문제"})
            data = self.rfile.read(n)
            jid = datetime.datetime.now().strftime("%m%d-%H%M%S-") + uuid.uuid4().hex[:4]
            ext = os.path.splitext(name)[1].lower() or ".jpg"
            fn = jid + ext
            with open(os.path.join(ORIG, fn), "wb") as f: f.write(data)
            qual = "deep" if self.headers.get("X-Mode") == "deep" else "fast"
            j = {"id": jid, "name": name, "file": fn, "status": "대기", "created": now(), "quality": qual}
            with lock: jobs.insert(0, j); save()
            q.put(jid)
            return self.send(200, j)
        if path == "/api/retry":
            with lock:
                j = find(qs.get("id", ""))
                if not j: return self.send(404, {"error": "없음"})
                m = qs.get("mode", "")
                if m in ("fix", "deep"): j["mode"] = m
                if m == "deep": j["quality"] = "deep"; j["deepFrom"] = True
                if m == "fast": j["quality"] = "fast"
                j["status"] = "대기"; j.pop("report", None); j.pop("message", None); save()
            q.put(j["id"]); return self.send(200, j)
        if path == "/api/remove":
            with lock:
                j = find(qs.get("id", ""))
                if not j: return self.send(404, {"error": "없음"})
                jobs.remove(j); save()
            for src in (os.path.join(ORIG, j["file"]), os.path.join(OUT, j["id"])):
                if os.path.exists(src): shutil.move(src, os.path.join(TRASH, os.path.basename(src)))
            return self.send(200, {"ok": True})
        return self.send(404, {"error": "없음"})

if __name__ == "__main__":
    try: srv = http.server.ThreadingHTTPServer(("127.0.0.1", PORT), H)
    except OSError:   # 이미 켜져 있음 → 화면만 열고 끝
        print(f"이미 켜져 있어요: http://127.0.0.1:{PORT}", flush=True)
        if "--open" in sys.argv: import webbrowser; webbrowser.open(f"http://127.0.0.1:{PORT}/")
        sys.exit(0)
    load()
    for _ in range(WORKERS): threading.Thread(target=worker, daemon=True).start()
    print(f"제품 보정 대시보드: http://127.0.0.1:{PORT}  (작업 폴더 {WORK})", flush=True)
    if "--open" in sys.argv:
        import webbrowser; threading.Timer(1.0, lambda: webbrowser.open(f"http://127.0.0.1:{PORT}/")).start()
    srv.serve_forever()
