# 설치 뒤 한 번: 글씨 인식·누끼 모델을 미리 내려받고 잘 도는지 확인 (처음 보정이 느려지지 않게)
import os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np
from PIL import Image, ImageDraw
import pyvision as v
print("글씨 인식 모델 준비 중...", flush=True)
img = Image.new("RGB", (600, 160), "white"); ImageDraw.Draw(img).text((20, 60), "TEST 123", fill="black")
print("  읽은 글씨:", [l["text"] for l in v.ocr(None, 2, img=np.array(img))])
print("누끼 모델 준비 중 (처음엔 약 170MB 내려받음)...", flush=True)
d = tempfile.mkdtemp(); p = os.path.join(d, "t.png")
im = Image.new("RGB", (400, 400), "white"); ImageDraw.Draw(im).rectangle((100, 100, 300, 300), fill=(200, 60, 60)); im.save(p)
print("  누끼:", "OK" if v.cutout(p, d) else "실패")
print("준비 끝", flush=True)
