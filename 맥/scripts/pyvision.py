# 맥 전용 도구(bin/ocr, labelfit, stack, retouch 누끼)를 파이썬으로 옮긴 것 — 윈도우·맥 공통.
#   글씨 읽기 = RapidOCR(PP-OCRv5 한국어 모델, onnxruntime — PaddleOCR보다 훨씬 가볍고 빠름), 겹쳐 맞추기 = OpenCV(SIFT + 호모그래피), 누끼 = rembg
# 결과 모양(좌표·JSON 칸 이름)은 맥 도구와 똑같이 맞춰서 pipeline.py 가 엔진만 바꿔 쓸 수 있게 함.
import os, threading
import numpy as np
import cv2
from PIL import Image, ImageOps

_lock = threading.Lock()
_ocr = None
MAX_SIDE = 4000   # 글씨 읽기 전에 키울 때 최대 크기 (CPU에서 너무 느려지지 않게)

def _engine():
    global _ocr
    if _ocr is None:
        from rapidocr import RapidOCR, LangRec, OCRVersion, ModelType
        _ocr = RapidOCR(params={"Rec.lang_type": LangRec.KOREAN, "Rec.ocr_version": OCRVersion.PPOCRV5, "Rec.model_type": ModelType.MOBILE,
                                "Det.ocr_version": OCRVersion.PPOCRV5, "Det.model_type": ModelType.MOBILE,
                                "Global.use_cls": False, "Global.log_level": "error",
                                "Det.limit_type": "max", "Det.limit_side_len": MAX_SIDE})
    return _ocr

def load(path):
    """사진을 RGB numpy로 (휴대폰 사진 회전 정보 반영, 투명은 흰 바탕)"""
    im = ImageOps.exif_transpose(Image.open(path))
    if im.mode in ("RGBA", "LA", "P"):
        im = im.convert("RGBA"); bg = Image.new("RGBA", im.size, (255, 255, 255, 255)); im = Image.alpha_composite(bg, im)
    return np.array(im.convert("RGB"))

def _read(rgb):
    """[(글자, 점수, (x0,y0,x1,y1))] — rgb 그림 좌표"""
    with _lock:
        r = _engine()(np.ascontiguousarray(rgb[:, :, ::-1]))   # BGR로 넘김
    out = []
    if r is None or r.txts is None: return out
    for t, sc, p in zip(r.txts, r.scores, r.boxes):
        p = np.array(p).reshape(-1, 2)
        if not str(t).strip(): continue
        out.append((str(t), float(sc), (float(p[:, 0].min()), float(p[:, 1].min()), float(p[:, 0].max()), float(p[:, 1].max()))))
    return out

def _char_box(text, find, box, vertical=False):
    """줄 상자 안에서 find 글자가 차지하는 자리 (글자 수 비율로 나눔 — 글자별 상자를 안 줌)"""
    i = text.find(find)
    if i < 0: return None
    x0, y0, x1, y1 = box
    def wt(c): return 0.55 if (c.isascii() and not c.isupper()) or c in " .,:;()·-" else 1.0
    tot = sum(wt(c) for c in text) or 1
    a = sum(wt(c) for c in text[:i]) / tot; b = sum(wt(c) for c in text[:i + len(find)]) / tot
    if vertical: return (x0, y0 + (y1 - y0) * a, x1, y0 + (y1 - y0) * b)
    return (x0 + (x1 - x0) * a, y0, x0 + (x1 - x0) * b, y1)

def ocr(path, scale=2.0, region=None, rot=False, find=None, img=None):
    """맥 bin/ocr 와 같은 결과: [{"text","conf","x","y","w","h","rot"}] 좌표는 원본 사진 픽셀(왼쪽 위 기준)"""
    full = img if img is not None else load(path)
    FH, FW = full.shape[:2]
    rx, ry, rw, rh = 0, 0, FW, FH
    if region:
        x, y, w, h = [float(v) for v in region]
        x0, y0 = max(0, int(x)), max(0, int(y)); x1, y1 = min(FW, int(x + w)), min(FH, int(y + h))
        if x1 <= x0 or y1 <= y0: return []
        rx, ry, rw, rh = x0, y0, x1 - x0, y1 - y0
    crop = full[ry:ry + rh, rx:rx + rw]
    s = min(float(scale), MAX_SIDE / max(rw, rh)) if max(rw, rh) * float(scale) > MAX_SIDE else float(scale)
    s = max(s, 0.2)
    im = cv2.resize(crop, (max(1, int(rw * s)), max(1, int(rh * s))), interpolation=cv2.INTER_LANCZOS4) if s != 1 else crop
    out = []
    def emit(t, c, b, r):
        x0, y0, x1, y1 = b
        if find:
            cb = _char_box(t, find, b, vertical=bool(r))
            if cb is None: return
            x0, y0, x1, y1 = cb
        out.append({"text": t, "conf": round(c, 2), "x": int(rx + x0 / s), "y": int(ry + y0 / s),
                    "w": int((x1 - x0) / s), "h": int((y1 - y0) / s), "rot": r})
    for t, c, b in _read(im):
        emit(t, c, b, False)
    if rot:
        H, W = im.shape[:2]
        # left: 시계 반대로 90도 돌려 읽기 (아래→위 세로 글씨), right: 시계 방향 (위→아래 세로 글씨)
        for name, code in (("left", cv2.ROTATE_90_CLOCKWISE), ("right", cv2.ROTATE_90_COUNTERCLOCKWISE)):
            r_im = cv2.rotate(im, code)
            for t, c, (a0, b0, a1, b1) in _read(r_im):
                if code == cv2.ROTATE_90_CLOCKWISE:      # 돌린 그림 (u,v) ← 원래 (x,y): u = H-1-y, v = x
                    x0, x1 = b0, b1; y0, y1 = H - a1, H - a0
                else:                                    # u = y, v = W-1-x
                    x0, x1 = W - b1, W - b0; y0, y1 = a0, a1
                if (y1 - y0) < (x1 - x0) * 1.5: continue   # 가로 글씨는 이미 읽었음 (세로 글씨만)
                if find:
                    cb = _char_box(t, find, (x0, y0, x1, y1), vertical=True)
                    if cb is None: continue
                    if code == cv2.ROTATE_90_CLOCKWISE:   # 아래→위로 쓰인 글씨는 글자 순서가 y와 반대
                        cy0, cy1 = cb[1], cb[3]; cb = (x0, y0 + (y1 - cy1), x1, y0 + (y1 - cy0))
                    out.append({"text": t, "conf": round(c, 2), "x": int(rx + cb[0] / s), "y": int(ry + cb[1] / s),
                                "w": int((cb[2] - cb[0]) / s), "h": int((cb[3] - cb[1]) / s), "rot": name})
                else:
                    out.append({"text": t, "conf": round(c, 2), "x": int(rx + x0 / s), "y": int(ry + y0 / s),
                                "w": int((x1 - x0) / s), "h": int((y1 - y0) / s), "rot": name})
    return out

def stack(out, width, files):
    """그림 두 장씩 한 쌍으로 같은 폭에 맞춰 위아래로 쌓기 (쌍 사이 회색 줄)"""
    W = int(float(width)); imgs = []
    for f in files:
        try: a = load(f)
        except Exception: continue
        h = max(1, int(a.shape[0] * W / a.shape[1]))
        imgs.append(cv2.resize(a, (W, h), interpolation=cv2.INTER_LANCZOS4))
    gin, gpair = 16, 70
    total = sum(i.shape[0] for i in imgs) + sum(gin if k % 2 == 0 else gpair for k in range(len(imgs) - 1))
    can = np.full((max(1, total), W, 3), 255, np.uint8); y = 0
    for k, i in enumerate(imgs):
        can[y:y + i.shape[0]] = i; y += i.shape[0]
        if k < len(imgs) - 1:
            g = gin if k % 2 == 0 else gpair
            if k % 2 == 1: can[y + g // 2 - 3:y + g // 2 + 3] = 153
            y += g
    Image.fromarray(can).save(out)

def labelfit(base, patch, x, y, w, h, out, scale=2.0, mode="text"):
    """AI 조각(patch)을 AI 보정본(base)의 (x,y,w,h) 자리에 정확히 겹쳐 맞춤. 결과는 base를 scale배 키운 크기. 실패하면 False"""
    B0 = load(base); P0 = load(patch); scale = float(scale)
    B = cv2.resize(B0, (int(B0.shape[1] * scale), int(B0.shape[0] * scale)), interpolation=cv2.INTER_LANCZOS4) if scale != 1 else B0.copy()
    X, Y, Wd, Hd = [int(round(float(v) * scale)) for v in (x, y, w, h)]
    X, Y = max(0, X), max(0, Y); Wd = min(Wd, B.shape[1] - X); Hd = min(Hd, B.shape[0] - Y)
    ref = B[Y:Y + Hd, X:X + Wd]
    P = cv2.resize(P0, (Wd, Hd), interpolation=cv2.INTER_LANCZOS4)
    # 정밀 맞춤: SIFT 특징점 → 호모그래피 (patch → ref)
    g1, g2 = cv2.cvtColor(P, cv2.COLOR_RGB2GRAY), cv2.cvtColor(ref, cv2.COLOR_RGB2GRAY)
    sift = cv2.SIFT_create(nfeatures=6000)
    k1, d1 = sift.detectAndCompute(g1, None); k2, d2 = sift.detectAndCompute(g2, None)
    if d1 is None or d2 is None or len(k1) < 8 or len(k2) < 8:
        print("맞춤 실패: 특징점 부족"); return False
    m = cv2.BFMatcher(cv2.NORM_L2).knnMatch(d1, d2, k=2)
    good = [a for a, b in (p for p in m if len(p) == 2) if a.distance < 0.75 * b.distance]
    if len(good) < 12: print("맞춤 실패: 맞는 점", len(good)); return False
    src = np.float32([k1[g.queryIdx].pt for g in good]); dst = np.float32([k2[g.trainIdx].pt for g in good])
    M, inl = cv2.findHomography(src, dst, cv2.RANSAC, 3.0)
    if M is None or inl is None or inl.sum() < 10: print("맞춤 실패: 호모그래피"); return False
    # 너무 크게 비틀리면 잘못 맞춘 것
    corners = np.float32([[0, 0], [Wd, 0], [Wd, Hd], [0, Hd]]).reshape(-1, 1, 2)
    moved = cv2.perspectiveTransform(corners, M).reshape(-1, 2)
    if np.abs(moved - corners.reshape(-1, 2)).max() > 0.25 * max(Wd, Hd): print("맞춤 실패: 너무 많이 비틀림"); return False
    warped = cv2.warpPerspective(P, M, (Wd, Hd), flags=cv2.INTER_LANCZOS4, borderMode=cv2.BORDER_REPLICATE)
    print("맞춤 변환:", " ".join(f"{v:.3f}" for v in M.ravel()), "맞는 점", int(inl.sum()))

    feather = max(4.0, min(Wd, Hd) / 10)
    def rect_mask():
        mk = np.zeros((Hd, Wd), np.float32); f = int(feather * 1.5)
        mk[f:Hd - f, f:Wd - f] = 1
        return cv2.GaussianBlur(mk, (0, 0), feather / 2)
    def text_mask():
        boxes = [l for l in ocr(None, 1, img=ref)]
        if not boxes: return rect_mask()
        hs = sorted(b["h"] for b in boxes); lh = max(6, hs[len(hs) // 2])
        x0 = min(b["x"] for b in boxes) - lh * 1.5; y0 = min(b["y"] for b in boxes) - lh * 1.5
        x1 = max(b["x"] + b["w"] for b in boxes) + lh * 1.5; y1 = max(b["y"] + b["h"] for b in boxes) + lh * 1.5
        x0, y0 = max(x0, lh * 0.5), max(y0, lh * 0.5); x1, y1 = min(x1, Wd - lh * 0.5), min(y1, Hd - lh * 0.5)
        mk = np.zeros((Hd, Wd), np.float32); mk[int(y0):int(y1), int(x0):int(x1)] = 1
        return cv2.GaussianBlur(mk, (0, 0), lh * 0.4)
    def label_mask():
        r, g, b = [ref[:, :, i].astype(int) for i in range(3)]
        pink = ((r - g > 18) & (r - b > 12) & (r > 120)).astype(np.uint8) * 255
        close, shrink = max(1, int(Wd / 40)), max(1, int(Wd / 150))
        k1 = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * close + 1, 2 * close + 1))
        k2 = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * (close + shrink) + 1, 2 * (close + shrink) + 1))
        mk = cv2.erode(cv2.dilate(pink, k1), k2).astype(np.float32) / 255
        return cv2.GaussianBlur(mk, (0, 0), max(0.5, shrink * 0.6))
    mk = {"rect": rect_mask, "label": label_mask}.get(mode, text_mask)()
    mk3 = np.clip(mk, 0, 1)[:, :, None]
    blended = (warped.astype(np.float32) * mk3 + ref.astype(np.float32) * (1 - mk3)).round().clip(0, 255).astype(np.uint8)
    B[Y:Y + Hd, X:X + Wd] = blended
    Image.fromarray(B).save(out)
    Image.fromarray((mk * 255).clip(0, 255).astype(np.uint8)).save(out + ".mask.png")
    print("저장:", out, B.shape[1], "x", B.shape[0])
    return True

_rembg = None
def cutout(src, outdir):
    """보정본 한 장에서 누끼(투명 PNG) — 제품 둘레만 2% 여유 두고 잘라 냄. 누끼.png, 누끼-web.png"""
    global _rembg
    from rembg import remove, new_session
    with _lock:
        if _rembg is None: _rembg = new_session("isnet-general-use")
    os.makedirs(outdir, exist_ok=True)
    im = Image.open(src).convert("RGB")
    rgba = remove(im, session=_rembg, post_process_mask=True)
    a = np.array(rgba)
    a[:, :, :3] = np.array(im)   # 색은 보정본 그대로 (rembg는 투명한 곳 색을 지워 둠)
    alpha = a[:, :, 3]
    # 흰 라벨·반사처럼 배경과 비슷한 안쪽이 반투명으로 뚫리는 것 막기: 제품 바깥 윤곽 안쪽은 꽉 채움 (테두리 부드러움만 남김)
    solid0 = (alpha > 128).astype(np.uint8)
    cnts, _ = cv2.findContours(solid0, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    if cnts:
        big = max(cv2.contourArea(c) for c in cnts)
        fill = np.zeros_like(solid0)
        cv2.drawContours(fill, [c for c in cnts if cv2.contourArea(c) > big * 0.02], -1, 1, thickness=-1)
        r = max(2, int(max(alpha.shape) / 600))
        inner = cv2.erode(fill, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * r + 1, 2 * r + 1)))
        alpha = np.maximum(alpha, inner * 255).astype(np.uint8)
        alpha[cv2.dilate(fill, np.ones((3, 3), np.uint8)) == 0] = 0   # 윤곽 밖 잔여물 지우기
        a[:, :, 3] = alpha
    # 경계 정리: 반투명 테두리 픽셀의 색을 안쪽 제품 색으로 (옛 흰 배경이 테두리에 묻어나는 것 줄이기)
    solid = (alpha > 240).astype(np.uint8)
    if solid.any():
        edge = (alpha > 0) & (alpha <= 240)
        if edge.any():
            blur = cv2.GaussianBlur(a[:, :, :3].astype(np.float32) * solid[:, :, None], (0, 0), 3)
            wsum = cv2.GaussianBlur(solid.astype(np.float32), (0, 0), 3)[:, :, None]
            fill = np.where(wsum > 0.05, blur / np.maximum(wsum, 1e-3), a[:, :, :3])
            a[:, :, :3][edge] = fill[edge].clip(0, 255).astype(np.uint8)
    ys, xs = np.where(alpha > 8)
    if not len(xs): print("누끼 영역이 비었어요"); return False
    x0, x1, y0, y1 = xs.min(), xs.max(), ys.min(), ys.max()
    pad = int(max(x1 - x0, y1 - y0) * 0.02)
    H, W = alpha.shape
    a = a[max(0, y0 - pad):min(H, y1 + pad + 1), max(0, x0 - pad):min(W, x1 + pad + 1)]
    out = Image.fromarray(a, "RGBA"); out.save(os.path.join(outdir, "누끼.png"))
    web = out.copy(); web.thumbnail((1400, 1400), Image.LANCZOS); web.save(os.path.join(outdir, "누끼-web.png"))
    print("누끼:", out.size)
    return True
