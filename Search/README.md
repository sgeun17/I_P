# ISMS-P 증적 검색 첫 버전

## 9/23 추가: 전처리 청크 검색과 팀 인계

`05_search_chunks.cmd`는 입력팀 DOCX 샘플의 실제 청크를 받아 전체 101개 ChromaDB에서 검색하고 **문서 전체 Top-5**를 반환합니다. 결과는 `reports/chunk_search_result.json`의 **retrieval.candidates**에서 봅니다. 청크별 상세 후보는 retrieval.chunk_results에 있습니다. 입력팀 소스는 수정하지 않았고 현재 출력의 필드 차이는 명시적 호환 모드로 경고와 함께 처리합니다.

- [진행 현황](handoff/검색팀_진행현황.md)
- [입력팀 확인·수정 요청](handoff/입력팀_확인수정요청.md)
- [판단팀 인계·실행·입출력 안내](handoff/판단팀_검색모듈_인계.md)
- [청크 연결 검증 결과](reports/chunk_integration_result.md)
- [현재 KB 101개 검증](reports/kb_validation_result.md)
- [검토 샘플 20건 검색 결과](reports/review_evaluation.md)

새 연동은 `chunk_retriever.retrieve()` 또는 `chunk_retriever.py --input ... --output ...`을 사용합니다. 기존 한 줄 검색 실행 파일은 그대로 유지됩니다. `retriever-0.2`는 검색팀 제안 규격이며 팀 간 최종 합의와 독립 정답 검수·실제 증적 품질 평가는 남아 있습니다. 문서 점수는 해당 통제항목의 청크별 유사도 중 최댓값입니다.

## 9/22 추가: 전체 101개 ChromaDB 색인

`04_index_chroma.cmd`를 더블클릭하면 현재 KB 101개를 `data/chroma_kb`의 `isms_p_controls` 컬렉션에 저장·색인하고 검증합니다. 실행 절차와 재색인 방법은 [전체 KB 색인 안내](ChromaDB_전체색인_안내.md)에 있습니다. 결과는 `reports/chroma_index_result.md`, DB에서 조회한 전체 벡터 사본은 `data/chroma_kb_vectors_view.json`입니다. KB 최종 버전 고정과 검색 품질 평가는 별도 작업입니다.

## 9/21 추가: ChromaDB 샘플 구동 확인

`03_check_chroma.cmd`를 실행하면 실제 BGE-M3로 샘플 10개를 ChromaDB에 저장하고 검색·프로세스 재시작 후 데이터 유지를 검사합니다. 자세한 절차는 [ChromaDB 실행 안내](ChromaDB_실행안내.md), 성공 결과는 `reports/chroma_sample_result.md`에서 확인합니다. 아래의 기존 `02_search.cmd` 검색은 벡터 파일 직접 비교 방식입니다.

증적 내용을 한 줄 입력하면 관련 통제항목 5개를 보여줍니다.
`controls.json`의 이름, 요구사항, 키워드, 증거자료 예시를 합쳐 BGE-M3로 비교합니다.

## 처음 사용하는 방법

1. 인터넷이 되는 환경에서 이 폴더의 **`01_setup.cmd`를 더블클릭**합니다.
2. 설치와 모델 다운로드, 101개 통제항목 준비가 끝날 때까지 기다립니다. 수 GB의 여유 공간이 필요하며 컴퓨터와 네트워크에 따라 시간이 걸립니다.
3. 완료되면 **`02_search.cmd`를 더블클릭**합니다.
4. `증적 내용 >` 옆에 아래와 같은 문장을 입력하고 Enter를 누릅니다.

```text
퇴직자의 계정을 퇴직 당일 삭제하고 처리 이력을 기록했다.
```

모델이 계산한 실제 순위와 유사도 점수가 표시됩니다. 다른 문장을 계속 입력할 수 있고, `exit`를 입력하면 종료합니다.

다음부터는 `02_search.cmd`만 실행하면 됩니다. 첫 모델 로딩에는 시간이 걸리지만, 같은 창에서 다음 검색은 모델을 다시 불러오지 않습니다.

### 설치가 하는 일

- Python 3.10~3.12를 찾습니다. 없다면 이 컴퓨터에 있는 Codex의 Python을 사용합니다.
- 이 폴더 안에 `.venv`를 만들고 필요한 라이브러리를 설치합니다.
- 공식 `BAAI/bge-m3` 모델을 `models/bge-m3`에 내려받고, 받은 모델 버전을 기록합니다.
- KB를 벡터로 바꾸어 `data/control_embeddings.npz`에 저장합니다.

이 컴퓨터에는 일반 Python이 없어서 Codex에 포함된 Python을 사용할 수 있도록 했습니다. 다른 컴퓨터에서 두 Python 모두 없다면 Python 3.12 설치 후 다시 실행하세요. Windows 실행 정책 우회 옵션은 해당 설치 프로세스에만 적용되며 시스템 정책을 바꾸지 않습니다.

다운로드가 끊겼다면 `01_setup.cmd`를 다시 실행할 수 있습니다. 이미 받은 파일과 큰 파일의 일부는 보존하며, 빠진 파일만 자동으로 여러 번 이어받습니다. 설치 오류가 나면 창에 표시된 오류 내용을 확인하세요. CPU 실행이 기본이므로 NVIDIA 그래픽카드는 필수가 아닙니다.

## 파일 안내

| 파일 | 역할 |
|---|---|
| `controls.json` | 검색 대상인 101개 통제항목 |
| `retriever.py` | KB 읽기, 임베딩, 유사도 비교, 결과 출력 |
| `01_setup.cmd` / `setup.ps1` | 최초 설치 및 모델 준비 |
| `02_search.cmd` | 검색 창 실행 |
| `requirements.txt` | 설치할 라이브러리 범위 |
| `tests/test_retriever.py` | 데이터 연결과 계산을 확인하는 테스트 |

현재 폴더의 `controls.json`을 사용합니다. 상위 개발 폴더에도 같은 이름의 파일이 있지만 자동으로 동기화하지 않으므로, 이후 검색 KB 수정은 **이 폴더의 파일**에 반영해 주세요.

## 코드로 실행하기

이 폴더에서 PowerShell을 열면 아래 명령으로도 실행할 수 있습니다. 가상환경을 별도로 활성화할 필요가 없습니다.

```powershell
# 문장 하나 검색
.\.venv\Scripts\python.exe retriever.py "VPN을 이용해 외부에서 내부 시스템에 접속했다."

# JSON schema.md의 retrieval.candidates 형식으로 출력
.\.venv\Scripts\python.exe retriever.py "분기별 접근권한 검토 결과" --json --evidence-id EVID-001

# 후보 수 변경
.\.venv\Scripts\python.exe retriever.py "백업 데이터를 복구 시험했다." --top-k 3

# KB 구조만 검사 (모델 로딩/다운로드 없음)
.\.venv\Scripts\python.exe retriever.py --validate

# 테스트 실행 (모델 다운로드 없음)
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

파이썬 코드 안에서는 다음과 같이 사용할 수 있습니다.

```python
from retriever import Retriever

retriever = Retriever()  # 프로그램 시작 시 한 번 생성
result = retriever.search(
    "분기마다 사용자의 접근권한을 검토했다.",
    top_k=5,
    evidence_id="EVID-001",
)
print(result)
```

## 기존 한 줄 검색(retriever.py)의 내부 처리 순서

`controls.json → 검색 문장 101개 → BGE-M3 벡터 → 증적 벡터와 코사인 유사도 비교 → Top-5`

- 이름, 요구사항, 키워드, 증거자료를 모두 사용합니다. 키워드만 정확히 일치시키는 검색은 아닙니다.
- 증적과 통제항목 모두 동일한 모델로 변환합니다.
- 101개 벡터를 전부 비교하므로 별도 Vector DB는 사용하지 않습니다.
- KB 내용/순서, 모델 파일의 크기·수정 시각, 주요 라이브러리 버전이 바뀌면 저장된 벡터를 다시 만듭니다.
- 모델 입력 한도를 넘는 글은 오류로 알립니다. 파일 업로드, OCR, 자동 청킹, LLM 최종 판단은 후속 단계입니다.
- 관련성이 낮은 글도 설정한 수만큼 후보가 나옵니다. 관련성 판단 임계값은 아직 정하지 않았습니다.
- 유사도는 정답일 확률이나 LLM의 confidence가 아닙니다.

## 로컬 실행과 확인 범위

검색은 `local_files_only=True`와 오프라인 설정으로 로컬 모델을 사용합니다. 인터넷 접속은 최초 설치 및 `--prepare`의 모델 다운로드에 필요합니다. 폐쇄망에서는 같은 운영체제·Python 버전에 맞는 라이브러리와 모델을 사전에 반입해 설치해야 합니다. `.venv` 폴더는 다른 컴퓨터로 단순 복사하는 방식의 배포를 권장하지 않습니다.

코드 검증은 실제 101개 KB와 계산용 테스트 벡터를 사용합니다. 테스트용 가짜 모델은 캐시 및 입출력 검사용이며 BGE-M3의 검색 품질을 입증하지 않습니다. 이 컴퓨터에서는 BGE-M3 모델 다운로드, 101개 KB 벡터 생성, 예시 검색까지 실행했습니다. 실제 증적 사례를 더 모아 정답이 Top-5에 들어오는지 계속 확인해야 합니다.

참고: [BGE-M3 공식 모델](https://huggingface.co/BAAI/bge-m3), [Sentence Transformers 공식 문서](https://sbert.net/docs/package_reference/sentence_transformer/model.html)
