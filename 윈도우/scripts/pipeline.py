#!/usr/bin/env python3
"""제품 사진 AI 보정 자동 순서 (2026-11-24 확정 방식)
  1 원본 글씨 받아쓰기 : 글씨 인식(맥 Vision 또는 RapidOCR) + GPT 받아쓰기 → 정답 글씨 (두 쪽이 같이 읽은 낱말 = 확실)
  2 AI 보정           : GPT 이미지(config.json 의 계정)로 사진 전체를 새로 보정 (흰 배경·주름·조명·이물 정리)
  3 라벨 다시 그리기   : 틀린 낱말이 많으면 글씨 부분만 크게 다시 그려 정확히 겹쳐 맞춤 (AI 그림 위에 AI 그림 → 빛·질감 같음, 원본 조각 안 씀)
  4 글씨 검사          : 결과 글씨를 읽어 정답과 낱말 단위로 비교, 확대해서 한 번 더 읽어 기계 실수 걸러냄
  5 틀린 낱말 고치기    : 틀린 낱말만 작게 잘라 GPT로 고치고 겹쳐 맞춤 (최대 2바퀴). 라벨을 통째로 또 그리면 맞던 글씨가 틀어지므로 하지 않음
  6 마무리             : 보정본(2배 크기), 누끼 PNG, 미리보기, 보고서
진행은 <결과폴더>/progress.json 에 계속 기록 → 대시보드가 1초마다 읽음.
사용법: pipeline.py <원본 사진> <결과 폴더> [--fast | --deep | --fix-only]
  --fix-only : 결과 폴더의 보정.png 를 이어받아 글씨 검사·낱말 고치기·마무리만 다시
"""
import json, os, re, subprocess, sys, time, difflib, shutil, concurrent.futures as cf

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config
SKILL = config.ROOT
BIN = os.path.join(SKILL, "bin")
PROMPTS = os.path.join(SKILL, "prompts")
CODEX = config.CODEX
ENV = config.codex_env()          # GPT 계정 (config.json 의 codex_home, 없으면 ~/.codex)
MAC = config.ENGINE == "mac"      # mac = 맥 내장 Vision 도구(bin/), 아니면 파이썬(pyvision.py, 윈도우 공통)
if not MAC:
    import pyvision
    from PIL import Image, ImageOps

SRC = os.path.abspath(sys.argv[1]); OUT = os.path.abspath(sys.argv[2])
FIXONLY = "--fix-only" in sys.argv   # 이미 만든 보정본에서 글씨 검사·낱말 고치기만 다시 (GPT 적게 씀)
FAST = "--fast" in sys.argv          # 빠른 보정: AI 보정 1번 + 글씨 검사(표시만), 라벨 다시 그리기·낱말 고치기 안 함
DEEP = "--deep" in sys.argv          # 꼼꼼 보정 이어하기: 빠른 보정의 AI 보정본(작업/1-AI보정.png)에서 라벨·낱말 고치기
CONT = FIXONLY or DEEP               # 앞 단계 결과를 이어 씀
WORK = os.path.join(OUT, "작업"); os.makedirs(WORK, exist_ok=True)
PROG = os.path.join(OUT, "progress.json")
OLD_P = {}
if CONT and os.path.exists(PROG):
    try: OLD_P = json.load(open(PROG))
    except Exception: pass

STEPS = [("read", "원본 글씨 읽기"), ("retouch", "AI 보정"), ("label", "라벨 다시 그리기"),
         ("check", "글씨 검사"), ("fix", "틀린 낱말 고치기"), ("finish", "누끼·마무리")]
P = {"steps": [{"key": k, "label": l, "state": "대기", "detail": ""} for k, l in STEPS],
     "gpt": 0, "started": time.time(), "versions": [], "text": None, "outputs": {}}

def save():
    P["elapsed"] = round(time.time() - P["started"])
    tmp = PROG + ".tmp"; json.dump(P, open(tmp, "w"), ensure_ascii=False, indent=1); os.replace(tmp, PROG)

def step(key, state, detail=None):
    for s in P["steps"]:
        if s["key"] == key:
            s["state"] = state
            if detail is not None: s["detail"] = detail
    save()

def log(*a): print(time.strftime("%H:%M:%S"), *a, flush=True)

def run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, **kw)

# ---------- 그림 다루기 (맥 = sips, 그 밖 = Pillow) ----------
def sips(*args): run(["sips", *args])

def _open(path): return ImageOps.exif_transpose(Image.open(path))
def _save(im, dst, q=None):
    if dst.lower().endswith((".jpg", ".jpeg")): im.convert("RGB").save(dst, quality=q or 92)
    else: im.save(dst)

def size(path):
    if not MAC: return _open(path).size
    r = run(["sips", "-g", "pixelWidth", "-g", "pixelHeight", path]).stdout
    return int(re.search(r"pixelWidth: (\d+)", r).group(1)), int(re.search(r"pixelHeight: (\d+)", r).group(1))

def fit(src, dst, maxside):
    """긴 변을 maxside로 (키우기·줄이기 둘 다)"""
    if MAC: sips("-Z", str(maxside), src, "--out", dst); return
    im = _open(src); s = maxside / max(im.size)
    _save(im.resize((max(1, round(im.width * s)), max(1, round(im.height * s))), Image.LANCZOS), dst)

def jpeg(src, dst, q, maxside):
    if MAC: sips("-s", "format", "jpeg", "-s", "formatOptions", str(q), "-Z", str(maxside), src, "--out", dst); return
    im = _open(src).convert("RGB"); s = maxside / max(im.size)
    im.resize((max(1, round(im.width * s)), max(1, round(im.height * s))), Image.LANCZOS).save(dst, quality=q)

def rotate_cw(path, deg):
    """시계 방향으로 deg도 돌려 같은 파일에 저장"""
    if MAC: sips("-r", str(deg), path, "--out", path); return
    _save(_open(path).rotate(-deg, expand=True), path)

def crop(src, dst, x, y, w, h, maxside=None, rotate=False):
    W, H = size(src)
    x = max(0, int(x)); y = max(0, int(y)); w = int(min(w, W - x)); h = int(min(h, H - y))
    if MAC:
        sips("-c", str(h), str(w), "--cropOffset", str(y), str(x), src, "--out", dst)
        if rotate: sips("-r", "90", dst, "--out", dst)
        if maxside: sips("-Z", str(maxside), dst, "--out", dst)
    else:
        im = _open(src).crop((x, y, x + w, y + h))
        if rotate: im = im.rotate(-90, expand=True)
        if maxside:
            s = maxside / max(im.size); im = im.resize((max(1, round(im.width * s)), max(1, round(im.height * s))), Image.LANCZOS)
        _save(im, dst)
    return x, y, w, h

def stack(out, width, files):
    if MAC: run([os.path.join(BIN, "stack"), out, str(width), *files])
    else: pyvision.stack(out, width, files)

def labelfit(base, patch, x, y, w, h, out, scale, mode):
    if MAC: return run([os.path.join(BIN, "labelfit"), base, patch, *map(str, (x, y, w, h)), out, str(scale), mode])
    try: pyvision.labelfit(base, patch, x, y, w, h, out, scale, mode)
    except Exception as e: log("겹쳐 맞추기 오류", e)

def cutout(src, outdir):
    if MAC: run([os.path.join(BIN, "retouch"), src, outdir, "--no-adjust", "--no-shadow"]); return
    try: pyvision.cutout(src, outdir)
    except Exception as e: log("누끼 오류", e)

# ---------- 글씨 읽기 ----------
def ocr(img, scale=2, region=None, rot=False, find=None):
    if not MAC: return pyvision.ocr(img, scale, region, rot, find)
    cmd = [os.path.join(BIN, "ocr"), img, "--scale", str(scale)]
    if region: cmd += ["--crop", ",".join(str(int(v)) for v in region)]
    if rot: cmd += ["--rot"]
    if find: cmd += ["--find", find]
    out = []
    for line in run(cmd).stdout.splitlines():
        try: out.append(json.loads(line))
        except Exception: pass
    return out

def read_all(img):
    """가로 글씨는 한 번에, 세로 글씨는 3×3 조각으로 나눠 크게 읽기"""
    W, H = size(img)
    sc = max(1.0, min(3.0, 3000 / max(W, H)))
    lines = ocr(img, sc)
    tw, th = W / 2.4, H / 2.4
    for i in range(3):
        for j in range(3):
            x, y = i * (W - tw) / 2, j * (H - th) / 2
            for l in ocr(img, 2.5, (x, y, tw, th), rot=True):
                # 파이썬 엔진은 빽빽한 작은 글씨를 한 번에 다 못 읽어서 조각에서 읽은 가로 줄도 보탬
                if (l["rot"] in ("left", "right") or not MAC) and not any(overlap(l, m) > 0.5 for m in lines):
                    lines.append(l)
    return [l for l in lines if l["conf"] >= 0.3]

def overlap(a, b):
    ix = max(0, min(a["x"] + a["w"], b["x"] + b["w"]) - max(a["x"], b["x"]))
    iy = max(0, min(a["y"] + a["h"], b["y"] + b["h"]) - max(a["y"], b["y"]))
    s = min(a["w"] * a["h"], b["w"] * b["h"]) or 1
    return ix * iy / s

def norm(s): return re.sub(r"[\s|:·,.\-_'\"()\[\]{}/]", "", s).lower()

def tokens(line):
    line = re.sub(r"^바코드\s*:", "", line.strip())
    return [t for t in re.split(r"[\s|]+", line) if len(norm(t)) >= 2]

def text_box(lines, W, H, margin=0.04):
    big = [l for l in lines if len(norm(l["text"])) >= 2]
    if not big: return (0, 0, W, H)
    x0 = min(l["x"] for l in big); y0 = min(l["y"] for l in big)
    x1 = max(l["x"] + l["w"] for l in big); y1 = max(l["y"] + l["h"] for l in big)
    m = margin * max(W, H)
    x0, y0 = max(0, x0 - m), max(0, y0 - m); x1, y1 = min(W, x1 + m), min(H, y1 + m)
    return (int(x0), int(y0), int(x1 - x0), int(y1 - y0))

# ---------- GPT ----------
def gpt_text(prompt, images, name):
    pf = os.path.join(WORK, name + ".질문.txt"); open(pf, "w").write(prompt)
    ans = os.path.join(WORK, name + ".답.txt")
    cmd = [CODEX, "exec", "--skip-git-repo-check", "-s", "read-only", "-C", WORK, "-o", ans]
    for i in images: cmd += ["-i", i]
    cmd += ["-"]
    subprocess.run(cmd, input=prompt, text=True, encoding="utf-8", capture_output=True, env=ENV, timeout=600)
    P["gpt"] += 1; save()
    return open(ans).read().strip() if os.path.exists(ans) else ""

def _gptimg(out, prompt, images):
    """GPT(Codex) 이미지 생성 1장 → out 에 저장 (예전 gptimg.sh 를 파이썬으로: 윈도우에서도 돌게)"""
    d, name = os.path.dirname(os.path.abspath(out)), os.path.basename(out)
    stem = os.path.splitext(name)[0]
    q = prompt + "\n\n" + f"이미지 생성 도구(image_gen)로 1장만 만들어서 현재 폴더에 '{name}' 이름으로 저장해줘. 다른 파일은 만들지 마.\n"
    if images: q += "첨부 이미지는 참고용이야. 제품(병·라벨)이 첨부돼 있으면 라벨 글자·캐릭터·병 모양·색을 절대 바꾸지 말고 그대로 써.\n"
    q += "저장한 파일 경로만 짧게 답해줘. 못 만들었으면 추측하지 말고 못 만들었다고 말해줘."
    cmd = [CODEX, "exec", "--skip-git-repo-check", "-s", "workspace-write", "-C", d, "-o", os.path.join(d, f".{stem}.answer.md")]
    for i in images: cmd += ["-i", i]
    cmd += ["-"]
    with open(os.path.join(d, f".{stem}.log"), "w", encoding="utf-8") as lf:
        subprocess.run(cmd, input=q, text=True, encoding="utf-8", stdout=lf, stderr=subprocess.STDOUT, env=ENV, timeout=900)
    return os.path.exists(out) and os.path.getsize(out) > 0

def gpt_image(out, prompt, images):
    open(out + ".질문.txt", "w", encoding="utf-8").write(prompt)
    ok = _gptimg(out, prompt, images)
    P["gpt"] += 1; save()
    if not ok:   # 한 번 더
        ok = _gptimg(out, prompt, images)
        P["gpt"] += 1; save()
    return ok

def version(path, label):
    web = path.rsplit(".", 1)[0] + "-web.jpg"
    jpeg(path, web, 85, 1400)
    P["versions"].append({"label": label, "file": os.path.relpath(web, OUT)}); save()

# ---------- 비교 ----------
def compare(truth_lines, certain, lines):
    """정답 낱말 중 결과에 없는 것 → [{right, wrong, line(결과 줄), sure}]"""
    got_all = norm(" ".join(l["text"] for l in lines))
    res = []
    for tl in truth_lines:
        nt = norm(tl)
        if not nt: continue
        best = max(lines, key=lambda l: difflib.SequenceMatcher(None, nt, norm(l["text"])).ratio(), default=None)
        for tok in tokens(tl):
            nk = norm(tok)
            if nk in got_all or "□" in tok: continue
            wrong = None
            if best:
                # 정답 줄과 결과 줄을 글자 단위로 맞춰, 이 낱말 자리에 결과는 뭐라고 적혀 있는지 찾기
                raw = best["text"]; idx = [i for i, ch in enumerate(raw) if norm(ch)]
                nr = "".join(norm(raw[i]) for i in idx)
                pos = nt.find(nk)
                if pos >= 0:
                    sm = difflib.SequenceMatcher(None, nt, nr, autojunk=False)
                    a0, a1 = pos, pos + len(nk); b0 = b1 = None
                    for tag, i1, i2, j1, j2 in sm.get_opcodes():
                        if i2 <= a0 or i1 >= a1: continue
                        lo = j1 + (max(a0, i1) - i1 if tag == "equal" else 0)
                        hi = j1 + (min(a1, i2) - i1 if tag == "equal" else j2 - j1)
                        b0 = lo if b0 is None else min(b0, lo); b1 = hi if b1 is None else max(b1, hi)
                    if b0 is not None and b1 > b0 and b1 <= len(idx):
                        s, e = idx[b0], idx[b1 - 1] + 1
                        # 낱말 경계까지 넓히기
                        while s > 0 and not raw[s - 1].isspace() and raw[s - 1] != "|": s -= 1
                        while e < len(raw) and not raw[e].isspace() and raw[e] != "|": e += 1
                        wrong = raw[s:e].strip()
            line = best
            if not wrong:
                # 줄 짝짓기로 못 찾으면: 결과 전체 낱말 중 이 낱말과 가장 비슷한 것 (바코드 숫자 등)
                cand = [(difflib.SequenceMatcher(None, nk, norm(t)).ratio(), t, l) for l in lines for t in tokens(l["text"])]
                cand = [c for c in cand if c[0] >= 0.6 and norm(c[1]) != nk]
                if cand:
                    _, wrong, line = max(cand, key=lambda c: c[0])
            res.append({"right": tok, "wrong": wrong, "line": line, "sure": nk in certain})
    return res

def confirm(img, item):
    """그 줄을 3배로 키워 다시 읽어서 정말 틀렸는지 확인 (기계 실수 거르기)"""
    l = item["line"]
    if not l: return True
    pad = l["h"] * 0.8
    again = ocr(img, 3, (l["x"] - pad, l["y"] - pad, l["w"] + 2 * pad, l["h"] + 2 * pad), rot=l["rot"] != False)
    return norm(item["right"]) not in norm(" ".join(x["text"] for x in again))

PROOF_N = [0]
OLINES = []   # 원본 사진에서 읽은 줄 (main에서 채움)
LAST_SHEETS = []   # 마지막 줄 비교표 (대시보드에서 눈으로 확인)
def gpt_pairs(img, lines, truth_lines):
    """GPT 줄 맞대기 비교: 원본 사진의 줄(위)과 보정본의 같은 줄(아래)을 붙여 보여주고 모양이 다른 낱말을 찾게 함.
    - 맥 글씨 인식은 비슷한 글자를 맞게 읽어 버리고, GPT에게 그냥 읽히면 '회석'을 '희석'으로 고쳐 읽어 버려서
      원본 그림과 직접 맞대는 방식이 가장 정확했음 (2026-11-24 시험)."""
    pairs = []
    used = set()
    for o in OLINES:
        if o["rot"] or len(norm(o["text"])) < 4: continue
        best = max((l for l in lines if not l["rot"]), key=lambda l: difflib.SequenceMatcher(None, norm(o["text"]), norm(l["text"])).ratio(), default=None)
        if not best: continue
        r = difflib.SequenceMatcher(None, norm(o["text"]), norm(best["text"])).ratio()
        key = (best["x"], best["y"])
        if r < 0.5 or key in used: continue
        used.add(key); pairs.append((o, best))
    if not pairs: return []
    PROOF_N[0] += 1
    sheets = []
    for g in range(0, len(pairs), 6):
        files = []
        for k, (o, l) in enumerate(pairs[g:g + 6]):
            fo = os.path.join(WORK, f"비교{PROOF_N[0]}-{g + k}-원본.png"); fr = os.path.join(WORK, f"비교{PROOF_N[0]}-{g + k}-보정.png")
            crop(SRC, fo, o["x"] - o["h"] * 0.4, o["y"] - o["h"] * 0.15, o["w"] + o["h"] * 0.8, o["h"] * 1.3)
            crop(img, fr, l["x"] - l["h"] * 0.4, l["y"] - l["h"] * 0.15, l["w"] + l["h"] * 0.8, l["h"] * 1.3)
            files += [fo, fr]
        sheet = os.path.join(WORK, f"비교{PROOF_N[0]}-표{len(sheets) + 1}.png")
        stack(sheet, 1800, files)
        sheets.append((sheet, pairs[g:g + 6]))
    LAST_SHEETS[:] = [os.path.relpath(sh, OUT) for sh, _ in sheets]
    q = open(os.path.join(PROMPTS, "5-줄비교.txt")).read()
    ans = gpt_text(q, [s for s, _ in sheets], f"비교{PROOF_N[0]}")
    m = re.search(r"\[.*\]", ans, re.S)
    try: items = json.loads(m.group(0)) if m else []
    except Exception: items = []
    alltok = [t for tl in truth_lines for t in tokens(tl)]
    out = []
    for it in items:
        try:
            gi = int(it.get("그림", 1)) - 1; pi = int(it.get("쌍")) - 1
            o, l = sheets[gi][1][pi]
        except Exception: continue
        wrong, orig_w = str(it.get("보정본", "")).strip(), str(it.get("원본", "")).strip()
        if not wrong or not orig_w or orig_w == "흐림" or norm(wrong) == norm(orig_w): continue
        # 정답 낱말: 받아쓰기 정답 중 원본 낱말과 가장 비슷한 것 (그 원본 줄에 있는 낱말 우선)
        cands = sorted(alltok, key=lambda t: -difflib.SequenceMatcher(None, norm(t), norm(orig_w)).ratio())
        right = cands[0] if cands and difflib.SequenceMatcher(None, norm(cands[0]), norm(orig_w)).ratio() >= 0.6 else None
        if not right and norm(orig_w) in norm(" ".join(truth_lines)): right = orig_w
        if not right or norm(right) == norm(wrong): continue
        out.append({"right": right, "wrong": wrong, "line": l, "sure": True, "by": "GPT", "findby": right})
    return out

def safe_fix(b, alltok_n):
    """자동으로 고쳐도 되는지: 받침·자음 하나 틀린 것 같은 '비슷한 글자 실수'만.
    2026-11-24 표 머리칸을 잘못 짝지어 '지방'→'포화지방', '영양'→'영양성분'으로 바꿔 버린 사고 → 아래 조건으로 막음."""
    w, r = norm(b.get("wrong") or ""), norm(b["right"])
    if not (b["sure"] and w and b["line"]): return False
    if w in alltok_n: return False                      # 틀렸다는 낱말이 정답에 원래 있는 낱말 → 짝을 잘못 지은 것
    if w in r or r in w: return False                   # 한쪽이 다른 쪽에 들어 있음 → 글자 실수가 아니라 줄 나눔 차이
    if abs(len(w) - len(r)) > 1: return False
    return difflib.SequenceMatcher(None, w, r).ratio() >= 0.5

def find_line(lines, *texts):
    for t in texts:
        nt = norm(t or "")
        if not nt: continue
        for l in lines:
            if nt in norm(l["text"]): return l
    return None

def problems(img, truth_lines, certain, truth=None, proof=True):
    lines = read_all(img)
    raw = compare(truth_lines, certain, lines)
    real = []
    for it in raw:
        if confirm(img, it): it["by"] = "기계"; real.append(it)
    if proof and truth:
        for g in gpt_pairs(img, lines, truth_lines):
            if any(norm(r["right"]) == norm(g["right"]) and r["wrong"] for r in real): continue
            real.append(g)
    return lines, real

# ---------- 낱말 고치기 ----------
def fix_word(base, it, n):
    l = it["line"]; wrong = it["wrong"]
    if not l or not wrong: return None
    reg = (l["x"] - 4, l["y"] - 4, l["w"] + 8, l["h"] + 8)
    sc = 2 if not l["rot"] else 2.5
    hit = []
    # 확대 배율에 따라 기계가 다르게 읽으므로(칼줌↔칼슘) 틀린 글자·정답 글자 × 여러 배율로 자리 찾기
    for q in (wrong, it["right"]):
        for scx in (sc, 1, 3):
            hit = ocr(base, scx, reg, rot=bool(l["rot"]), find=q)
            if hit: break
        if hit: break
    if not hit:   # 그래도 못 찾으면: 그 자리만 다시 읽고(배율마다 다르게 읽힘, 칼줌↔칼숨) 거기서 정답과 가장 비슷한 덩어리로 찾기
        for scx in (sc, 1, 3):
            again = ocr(base, scx, reg, rot=bool(l["rot"]))
            toks = [t for a in again for t in a["text"].split()]
            cand = max(toks, key=lambda t: difflib.SequenceMatcher(None, norm(t), norm(it["right"])).ratio(), default="")
            if cand and difflib.SequenceMatcher(None, norm(cand), norm(it["right"])).ratio() >= 0.5:
                hit = ocr(base, scx, reg, rot=bool(l["rot"]), find=cand)
            if hit: break
    box = hit[0] if hit else None
    if not box: return None
    rot = box["rot"] != False
    th = box["w"] if rot else box["h"]          # 글자 높이
    padx = max(th * 2.2, box["w" if not rot else "h"] * 0.5)
    pady = th * 1.8
    if rot: padx, pady = pady, padx
    x, y, w, h = box["x"] - padx, box["y"] - pady, box["w"] + 2 * padx, box["h"] + 2 * pady
    piece = os.path.join(WORK, f"낱말{n}.png")
    x, y, w, h = crop(base, piece, x, y, w, h, maxside=1040, rotate=rot)
    prompt = open(os.path.join(PROMPTS, "4-낱말고치기.txt")).read().replace("{틀림}", wrong).replace("{맞음}", it["right"]).replace("{줄}", l["text"])
    fixed = os.path.join(WORK, f"낱말{n}-고침.png")
    if not gpt_image(fixed, prompt, [piece]): return None
    if rot: rotate_cw(fixed, 270)
    return (fixed, x, y, w, h)

def line_better(before, after, b, truth_lines):
    """고친 자리 줄을 고치기 전·후로 다시 읽어, 정답 줄에 더 가까워졌고 정답 낱말이 생겼을 때만 OK"""
    l = b["line"]; pad = l["h"] * 0.6
    reg = (l["x"] - pad, l["y"] - pad, l["w"] + 2 * pad, l["h"] + 2 * pad)
    def score(img):
        t = norm(" ".join(x["text"] for x in ocr(img, 2.5, reg, rot=bool(l["rot"]))))
        best = max((difflib.SequenceMatcher(None, norm(tl), t).ratio() for tl in truth_lines), default=0)
        return best, norm(b["right"]) in t
    s0, _ = score(before); s1, has = score(after)
    return has and s1 >= s0

def clean_truth(truth):
    """이미지 생성용 정답: □(못 읽은 글자)는 GPT가 그대로 □로 그려 버리므로 빼고 '원본 그대로'로 안내"""
    out = []
    for t in truth.splitlines():
        if not t.replace("□", "").strip(): continue
        out.append(t.replace("□", "(원본 모양 그대로)"))
    return "\n".join(out)

# ================= 순서 =================
def main():
    W0, H0 = size(SRC)
    shutil.copy(SRC, os.path.join(WORK, "원본" + os.path.splitext(SRC)[1].lower()))
    orig_web = os.path.join(OUT, "원본-web.jpg")
    jpeg(SRC, orig_web, 85, 1400)
    if CONT: P["outputs"].update(OLD_P.get("outputs", {}))   # 이어 고치는 동안 앞 보정본을 계속 보여 줌
    P["outputs"]["original"] = "원본-web.jpg"; save()

    # 1. 원본 글씨 읽기
    step("read", "진행", "맥 글씨 인식으로 읽는 중")
    olines = read_all(SRC)
    OLINES[:] = olines
    zoom = os.path.join(WORK, "글씨확대.jpg")
    if olines:
        bx = text_box(olines, W0, H0); crop(SRC, zoom, *bx, maxside=2000)
    else:
        shutil.copy(SRC, zoom)
    ocr_text = "\n".join(l["text"] for l in sorted(olines, key=lambda l: (l["y"], l["x"])))
    tf = os.path.join(WORK, "정답글씨.txt")
    if CONT:
        old = OLD_P
        P["versions"] = old.get("versions", [])
        if DEEP: P["versions"] = [v for v in P["versions"] if v.get("label") == "AI 보정"]
        for k in ("read", "retouch") if DEEP else ("read", "retouch", "label"):
            for st in old.get("steps", []):
                if st["key"] == k: step(k, "완료" if st["state"] == "진행" else st["state"], st["detail"])
    if CONT and os.path.exists(tf):
        truth = open(tf).read()
    elif not olines:
        truth = ""; step("read", "완료", "제품에 글씨가 없어요")
    else:
        step("read", "진행", f"기계가 {len(olines)}줄 읽음 · GPT 받아쓰기 중")
        q = open(os.path.join(PROMPTS, "1-받아쓰기.txt")).read().replace("{OCR}", ocr_text)
        truth = gpt_text(q, [SRC, zoom], "받아쓰기")
        truth = "\n".join(t for t in truth.splitlines() if t.strip() and not t.startswith("```"))
    open(os.path.join(WORK, "정답글씨.txt"), "w").write(truth)
    truth_lines = [t for t in truth.splitlines() if t.strip()]
    # 확실한 낱말 판정용: 글씨 부분을 2×2 조각으로 나눠 3배로 크게 한 번 더 읽음 (원본 사진 글씨가 작고 흐릴 때 대비)
    extra = []
    if olines:
        x, y, w, h = bx
        for i in range(2):
            for k in range(2):
                extra += ocr(SRC, 3, (x + i * w * 0.45, y + k * h * 0.45, w * 0.55, h * 0.55), rot=True)
    onorm = norm(ocr_text + " " + " ".join(l["text"] for l in extra))
    certain = {norm(t) for tl in truth_lines for t in tokens(tl) if norm(t) in onorm}
    alltok = [norm(t) for tl in truth_lines for t in tokens(tl)]
    alltok_n = set(alltok)
    unsure = sorted({t for tl in truth_lines for t in tokens(tl) if norm(t) not in onorm})
    P["text"] = {"truth": truth_lines, "tokens": len(alltok), "certain": len(certain), "unsure": unsure}
    if truth_lines and not CONT: step("read", "완료", f"글씨 {len(truth_lines)}줄 · 낱말 {len(alltok)}개 (원본에서 기계·GPT가 똑같이 읽은 낱말 {len([t for t in alltok if t in certain])}개)")

    if FIXONLY:
        cur = os.path.join(WORK, "5-이어고치기.png"); shutil.copy(os.path.join(OUT, "보정.png"), cur)
        fix_and_finish(cur, truth, truth_lines, certain, alltok_n); return

    # 2. AI 보정
    v1 = os.path.join(WORK, "1-AI보정.png")
    if DEEP and os.path.exists(v1):
        pass   # 빠른 보정 때 만든 AI 보정본을 그대로 이어 씀
    else:
        step("retouch", "진행", "GPT가 사진을 새로 보정하는 중 (약 1~2분)")
        q = open(os.path.join(PROMPTS, "2-AI보정.txt")).read().replace("{글씨}", clean_truth(truth) or "(글씨 없음)")
        if not gpt_image(v1, q, [SRC, zoom]):
            step("retouch", "실패", "GPT 이미지 생성 실패"); P["error"] = "AI 보정 이미지를 만들지 못했어요"; save(); sys.exit(2)
        version(v1, "AI 보정")
        step("retouch", "완료", "보정본 1장")
    if FAST:
        for k in ("label", "fix"): step(k, "건너뜀", "빠른 보정이라 안 함 (꼼꼼 보정을 누르면 고쳐요)")
        P["quality"] = "빠른"
        fix_and_finish(v1, truth, truth_lines, certain, alltok_n); return
    P["quality"] = "꼼꼼"
    big = os.path.join(WORK, "2-크게.png")
    V1W, V1H = size(v1)
    fit(v1, big, max(V1W, V1H) * 2)
    cur = big

    if not truth_lines:
        for k in ("label", "check", "fix"): step(k, "건너뜀", "글씨 없음")
    else:
        # 3. 글씨 검사 → 많이 틀리면 라벨 다시 그리기
        step("check", "진행", "AI 보정본 글씨 검사 중")
        lines, bad = problems(v1, truth_lines, certain, truth, proof=False)
        log("1차 검사 틀린 낱말", len(bad), [(b["wrong"], b["right"]) for b in bad])
        step("check", "대기", f"AI 보정본에서 틀린 낱말 {len(bad)}개 찾음")
        if len(bad) >= 3:
            step("label", "진행", f"틀린 낱말 {len(bad)}개 → 글씨 부분만 크게 다시 그리는 중")
            bx = text_box(lines, V1W, V1H, 0.03)
            piece = os.path.join(WORK, "글씨부분_수정할것.png"); crop(v1, piece, *bx, maxside=1700)
            wrongs = "\n".join(f'- "{b["wrong"]}" → "{b["right"]}"' for b in bad if safe_fix(b, alltok_n))[:3000] or "- (기계가 찾은 곳 없음, 정답과 다른 곳을 직접 찾아 고쳐)"
            q = open(os.path.join(PROMPTS, "3-라벨다시.txt")).read().replace("{글씨}", clean_truth(truth)).replace("{틀린곳}", wrongs)
            patch = os.path.join(WORK, "글씨부분-고침.png")
            if gpt_image(patch, q, [piece, zoom]):
                out = os.path.join(WORK, "3-라벨.png")
                labelfit(v1, patch, *bx, out, 2, "text")
                if os.path.exists(out):
                    cur = out; version(out, "라벨 다시 그림")
                    step("label", "완료", f"틀린 낱말 {len(bad)}개였던 글씨 부분을 다시 그려 겹침")
                else: step("label", "실패", "겹쳐 맞추기 실패 → 그대로 진행")
            else: step("label", "실패", "GPT 이미지 생성 실패 → 그대로 진행")
        else:
            step("label", "건너뜀", f"틀린 낱말이 {len(bad)}개뿐이라 낱말만 고침")

        pass
    fix_and_finish(cur, truth, truth_lines, certain, alltok_n)

def fix_and_finish(cur, truth, truth_lines, certain, alltok_n):
    if truth_lines:
        # 4·5. 검사 → 틀린 낱말 고치기 (최대 2바퀴)
        fixed_log = []
        last = None
        failed = set()   # 1바퀴에서 고쳐 봤는데 안 된 낱말은 2바퀴에서 또 하지 않음 (같은 실패 반복 = 시간 낭비)   # 마지막 검사 뒤로 보정본이 안 바뀌었으면 그 결과를 최종 검사로 그대로 씀 (같은 검사 두 번 안 함)
        for rnd in ((1, 2) if not FAST else ()):
            step("check", "진행", f"{rnd}번째 글씨 검사 중")
            lines, bad = problems(cur, truth_lines, certain, truth)
            last = (cur, lines, bad, list(LAST_SHEETS))
            log(f"{rnd}바퀴 검사", [(b["wrong"], b["right"], b["sure"]) for b in bad])
            todo = [b for b in bad if safe_fix(b, alltok_n) and (b["wrong"], b["right"]) not in failed][:8]
            skipped = [b for b in bad if b not in todo and b["wrong"]]
            if skipped: log("자동 수정 안 함(오수정 위험)", [(b["wrong"], b["right"]) for b in skipped])
            if not todo: break
            step("fix", "진행", f"{rnd}바퀴: 틀린 낱말 {len(todo)}개 고치는 중 (" + ", ".join(f'{b["wrong"]}→{b["right"]}' for b in todo[:4]) + ")")
            with cf.ThreadPoolExecutor(4) as ex:
                results = list(ex.map(lambda a: fix_word(cur, a[1], f"{rnd}-{a[0]}"), enumerate(todo)))
            n_ok = 0
            for b, r in zip(todo, results):
                if not r: failed.add((b["wrong"], b["right"])); continue
                fixed, x, y, w, h = r
                nxt = os.path.join(WORK, f"4-고침{rnd}-{n_ok}.png")
                labelfit(cur, fixed, x, y, w, h, nxt, 1, "rect")
                if not os.path.exists(nxt): continue
                if line_better(cur, nxt, b, truth_lines):
                    cur = nxt; n_ok += 1; fixed_log.append(f'{b["wrong"]}→{b["right"]}')
                else:
                    log("고친 결과가 나아지지 않아 버림", b["wrong"], b["right"]); failed.add((b["wrong"], b["right"]))
            if n_ok: version(cur, f"낱말 {n_ok}개 고침")
            else: break   # 하나도 못 고쳤으면 다음 바퀴도 똑같음
        if not FAST: step("fix", "완료" if fixed_log else "건너뜀", ("고친 낱말: " + ", ".join(fixed_log)) if fixed_log else "고칠 낱말 없음")

        # 최종 검사
        if last and last[0] == cur:
            _, lines, bad, sh = last; LAST_SHEETS[:] = sh
        else:
            step("check", "진행", "글씨 검사 중 (맥 글씨 인식 + 원본 줄과 맞대기)")
            lines, bad = problems(cur, truth_lines, certain, truth)
        got = norm(" ".join(l["text"] for l in lines))
        rows = []
        # 맥 글씨 인식이 아예 못 읽은 낱말(표 머리칸 등)은 GPT 교정이 문제없다고 하면 통과로 봄
        bad = [b for b in bad if not (b.get("by") == "기계" and not b["wrong"])]
        # 원본 사진에서도 기계가 확인 못 한 낱말(sure=False)은 '다름'이 아니라 '눈으로 확인'으로 (예: 마크 속 '비닐류')
        bad = [b for b in bad if b["sure"]]
        badset = {norm(b["right"]) for b in bad}
        for tl in truth_lines:
            ts = tokens(tl)
            wrong = [t for t in ts if norm(t) in badset]
            rows.append({"text": tl, "ok": not wrong, "wrong": wrong,
                         "unsure": [t for t in ts if norm(t) not in certain and norm(t) not in badset]})
        # 더 생긴 낱말 찾기: 정답보다 여러 번 나오는 낱말 (예: '포화지방'이 두 번) → 그 줄 확인 필요
        from collections import Counter
        tc = Counter(norm(t) for tl in truth_lines for t in tokens(tl))
        rc = Counter(norm(t) for l in lines for t in tokens(l["text"]))
        extra = [t for t, n in rc.items() if t in tc and n > tc[t]]
        for r in rows:
            ex = [t for t in tokens(r["text"]) if norm(t) in extra]
            if ex: r["ok"] = False; r["wrong"] = list(dict.fromkeys(r["wrong"] + ex)); r["extra"] = ex
        okn = sum(r["ok"] for r in rows)
        P["text"].update({"sheets": list(LAST_SHEETS), "lines": rows, "ok": okn, "total": len(rows), "fixed": fixed_log,
                          "extra": extra, "remaining": [{"wrong": b["wrong"], "right": b["right"], "sure": b["sure"], "by": b.get("by")} for b in bad]})
        blur = sum(1 for r in rows if "□" in r["text"]); tot = len(rows) - blur; okb = sum(1 for r in rows if r["ok"] and "□" not in r["text"])
        step("check", "완료", f"글씨 {tot}줄 중 {okb}줄 그대로" + (f" · 확인 필요 {tot - okb}줄" if okb < tot else "") + (f" · 원본이 흐려 눈으로 확인할 줄 {blur}" if blur else ""))

    # 6. 마무리
    step("finish", "진행", "누끼 따는 중")
    final = os.path.join(OUT, "보정.png"); shutil.copy(cur, final)
    web = os.path.join(OUT, "보정-web.jpg"); jpeg(final, web, 88, 1400)
    cutdir = os.path.join(WORK, "누끼")
    cutout(final, cutdir)
    for a, b in (("누끼.png", "누끼.png"), ("누끼-web.png", "누끼-web.png")):
        if os.path.exists(os.path.join(cutdir, a)): shutil.copy(os.path.join(cutdir, a), os.path.join(OUT, b))
    P["outputs"].update({"retouched": "보정.png", "previewRetouched": "보정-web.jpg", "cutout": "누끼.png", "previewCutout": "누끼-web.png"})
    W2, H2 = size(final); P["outputs"]["size"] = [W2, H2]
    step("finish", "완료", f"보정본 {W2}×{H2} · 누끼 PNG")
    P["done"] = True; save()
    log("끝", P["gpt"], "번 GPT 사용")


if __name__ == "__main__":
    try: main()
    except SystemExit: raise
    except Exception as e:
        import traceback; traceback.print_exc()
        P["error"] = f"{type(e).__name__}: {e}"
        for s in P["steps"]:
            if s["state"] == "진행": s["state"] = "실패"
        save(); sys.exit(1)
