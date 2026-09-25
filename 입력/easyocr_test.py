import os
os.environ["KMP_DUPLICATE_LIB_OK"] = "TRUE"

import sys
import easyocr

path = sys.argv[1] if len(sys.argv) > 1 else "test.jpg"

reader = easyocr.Reader(['ko', 'en'])
result = reader.readtext(path)

for box, text, score in result:
    print(f"{score:.2f}  {text}")

print("인식된 결과 개수:", len(result))