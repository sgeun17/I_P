# ISMS-P 증적 검색 첫 버전

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

## 내부 처리 순서

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
