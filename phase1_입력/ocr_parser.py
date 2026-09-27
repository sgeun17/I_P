"""
ocr_parser.py : PNG·JPG 증적을 EasyOCR 로 읽어 팀 합의 포맷(v2)으로 돌려줍니다.

    from ocr_parser import parse_image
    parsed = parse_image("증적.png")     # 파서 6종과 같은 모양 (blocks 는 전부 paragraph)

EasyOCR 호출은 easyocr_test.py 와 같고, 앞에 두 가지만 더 합니다.
  ① EXIF 회전 보정 — 휴대폰 사진은 눕혀 저장돼서, 안 하면 결과가 통째로 깨집니다 (0/12 → 8/12)
  ② 크기 조정 — 작은 캡처는 2배 확대, 큰 사진은 2500px 축소 (정확도 30% → 50%)
     ※ 크기를 안 맞추면 EasyOCR 이 cv2.resize 오류로 멈추는 이미지가 있습니다

블록의 source·confidence 는 ★ 팀 합의가 필요한 칸입니다.
(판단팀이 "이 근거는 OCR" 을 알아야 하고, 낮으면 사람이 확인하도록 쓰려는 것)
합의 전에는 EXTRA_KEYS = False 로 두면 두 칸이 빠져 지금 규칙(블록 칸 6개)을 지킵니다.

⚠ OCR 결과는 정확하지 않습니다 (가상 증적 기준 50~67%). 글자를 자동으로 고치지 않습니다.
"""

import os
os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")

import tempfile
from pathlib import Path

UPSCALE, TARGET_WIDTH, PHOTO_WIDTH = 2, 2500, 2000
EXTRA_KEYS = True        # ★ 블록에 source·confidence 를 넣을지
IMAGE_TYPES = {"png": "png", "jpg": "jpg", "jpeg": "jpg"}
_reader = None


def parse_image(path):
    global _reader
    path = Path(path)
    result = {"source_file": path.name,
              "file_type": IMAGE_TYPES.get(path.suffix.lower().lstrip("."), ""),
              "page_count": None, "blocks": [], "errors": []}
    try:
        from PIL import Image, ImageOps
        img = ImageOps.exif_transpose(Image.open(path)).convert("RGB")   # 휴대폰 사진 회전 보정
        if img.width >= PHOTO_WIDTH:                                      # 큰 사진은 줄이고
            img = img.resize((TARGET_WIDTH, round(img.height * TARGET_WIDTH / img.width)),
                             Image.LANCZOS)
        else:                                                             # 작은 캡처는 키움
            img = img.resize((img.width * UPSCALE, img.height * UPSCALE), Image.LANCZOS)
        work = Path(tempfile.mkdtemp(prefix="ocr_")) / "w.png"
        img.save(work)
    except Exception:
        result["errors"].append("corrupted_file")
        return result

    import easyocr
    if _reader is None:
        _reader = easyocr.Reader(["ko", "en"])
    items = [{"text": t.strip(), "conf": float(c),
              "y0": min(p[1] for p in b), "x0": min(p[0] for p in b)}
             for b, t, c in _reader.readtext(str(work)) if t.strip()]

    for order, it in enumerate(sorted(items, key=lambda i: (i["y0"], i["x0"])), 1):
        block = {"order": order, "block_type": "paragraph", "level": None,
                 "page": None, "text": it["text"], "table": None}
        if EXTRA_KEYS:
            block["source"] = "ocr"
            block["confidence"] = round(it["conf"], 3)
        result["blocks"].append(block)

    if not result["blocks"]:
        result["errors"].append("empty_document")
    return result


if __name__ == "__main__":          # python ocr_parser.py 증적.jpg
    import sys
    parsed = parse_image(sys.argv[1] if len(sys.argv) > 1 else "test.jpg")
    for b in parsed["blocks"]:
        print(f"[{b['order']:3}] {b.get('confidence', 0):.2f}  {b['text']}")
    print("블록", len(parsed["blocks"]), "개  errors:", parsed["errors"])
