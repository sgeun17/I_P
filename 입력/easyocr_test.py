import os
os.environ["KMP_DUPLICATE_LIB_OK"] = "TRUE"

import easyocr

reader = easyocr.Reader(['ko', 'en'])
result = reader.readtext("test.jpg")

for box, text, score in result:
    print(f"{score:.2f}  {text}")

print("인식된 결과 개수:", len(result))