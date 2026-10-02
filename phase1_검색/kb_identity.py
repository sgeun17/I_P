"""공유 KB 식별 규칙: LF/CRLF만 CRLF로 통일한 바이트의 SHA-256.

기존 CRLF 배포본의 식별자를 유지한다. JSON을 재직렬화하지 않으므로 내용,
들여쓰기, 키 순서, BOM, 마지막 개행 등의 다른 변경은 여전히 감지한다.
일반 파일 무결성 해시에는 사용하지 않고 controls.json 식별에만 사용한다.
"""
import hashlib

KB_HASH_RULE = "sha256-crlf-v1"


def kb_sha256(raw: bytes) -> str:
    normalized = raw.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")
    return hashlib.sha256(normalized).hexdigest()
