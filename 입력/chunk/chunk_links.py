"""
chunk_links.py : 청킹-A(chunker.py) 와 청킹-B 를 잇는 연결 파일

청킹-A 는 이 파일 하나만 import 하면 돼요.
  from chunk_links import make_chunk_id, table_chunks, fill_continued_headers

실제 코드는 chunk_format.py (청크 형식) 와 table_chunker.py (표 청크) 에 있어요.
"""

from chunk_format import (CHUNK_MAX_CHARS, INDEX_START, OVERLAP_CHARS,  # noqa: F401
                          finalize_chunks, make_chunk_id, validate_chunks)
from table_chunker import fill_continued_headers, table_chunks  # noqa: F401
