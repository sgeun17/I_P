"""
db.py : MySQL에 접속하는 일만 담당하는 파일

다른 파일에서는 이렇게 씁니다.
    from db import get_connection
    conn = get_connection()
"""

import pymysql

from config import DB_HOST, DB_NAME, DB_PASSWORD, DB_PORT, DB_USER


def get_connection():
    """MySQL 접속(연결)을 하나 만들어서 돌려줍니다."""
    return pymysql.connect(
        host=DB_HOST,
        port=DB_PORT,
        user=DB_USER,
        password=DB_PASSWORD,
        database=DB_NAME,
        # utf8mb4 : 한글 파일명이 ??? 로 깨지지 않게 하는 설정 (빼면 안 됨)
        charset="utf8mb4",
        # DictCursor : 결과를 {"evidence_id": "000001", ...} 처럼 이름으로 꺼낼 수 있게 함
        cursorclass=pymysql.cursors.DictCursor,
        # autocommit=False : commit()을 불러야 진짜 저장됨. 실패하면 rollback()으로 되돌릴 수 있음
        autocommit=False,
    )
