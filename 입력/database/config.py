"""
config.py : 설정을 한 곳에 모아두는 파일

.env 파일에 적어둔 값(비밀번호, 폴더 위치 등)을 읽어서
다른 파이썬 파일들이 가져다 쓸 수 있게 해줍니다.
설정은 반드시 여기서만 읽으세요. (파일마다 따로 읽으면 값이 달라지는 사고가 납니다)
"""

import os
from datetime import timedelta, timezone
from pathlib import Path

from dotenv import load_dotenv

# 이 파일(config.py)이 들어 있는 폴더 = 프로젝트 폴더
BASE_DIR = Path(__file__).resolve().parent

# 프로젝트 폴더의 .env 파일을 읽어서 환경변수로 등록
load_dotenv(BASE_DIR / ".env")

# ── DB 접속 정보 ─────────────────────────────
DB_HOST = os.getenv("DB_HOST", "localhost")
DB_PORT = int(os.getenv("DB_PORT", "3306"))
DB_USER = os.getenv("DB_USER", "root")
DB_PASSWORD = os.getenv("DB_PASSWORD", "")
DB_NAME = os.getenv("DB_NAME", "evidence_db")   # schema.sql 과 같은 이름

# ── 파일 저장 폴더 ───────────────────────────
# 상대경로로 적어도 항상 "프로젝트 폴더 기준"이 되도록 BASE_DIR에 붙입니다.
# (어느 폴더에서 python을 실행하든 같은 곳에 저장되게 하려는 것)
STORAGE_DIR = BASE_DIR / os.getenv("STORAGE_DIR", "data/evidence")
TEMP_DIR = BASE_DIR / os.getenv("TEMP_DIR", "data/tmp")

# ── 규칙들 ──────────────────────────────────
# 증적 번호 모양 = ID_PREFIX + 숫자(ID_DIGITS 자리)
#   ID_PREFIX = "E",         ID_DIGITS = 4  →  E0001        (A 파트 원래 모양)
ID_PREFIX = "E"
ID_DIGITS = 4

# 받을 수 있는 파일 형식 (소문자, 점 없이)
ALLOWED_FILE_TYPES = {"pdf", "docx", "xlsx", "pptx", "png", "jpg", "txt", "csv"}

# 팀이 확정한 상태값 8개. 이 목록에 없는 값은 DB에 들어가지 못하게 막습니다.
VALID_STATUSES = {
    "UPLOADED",
    "PREPROCESSING",
    "PREPROCESSED",
    "MAPPING",
    "VALIDATING",
    "COMPLETED",
    "REVIEW_REQUIRED",
    "FAILED",
}

# 한국 시간(+09:00). Windows에서도 추가 설치 없이 동작하는 방식입니다.
KST = timezone(timedelta(hours=9))
