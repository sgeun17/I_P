# 9/21 김유빈 담당: 로컬 임베딩·Vector DB 구동 확인

이번 작업은 ChromaDB를 로컬 내장형 DB로 열고, 실제 BGE-M3로 만든 샘플 벡터를 저장·검색하는 단계입니다. 별도 서버 창이나 웹 화면은 없습니다. 실행 중 DB를 열고 종료 후에도 디스크에 데이터를 보관합니다.

## 가장 간단한 실행 방법

1. 파일 탐색기에서 이 문서가 있는 `I_P\Search` 폴더를 엽니다.
2. `03_check_chroma.cmd`를 더블클릭합니다.
3. ChromaDB가 없으면 설치합니다. 최초 설치에는 인터넷이 필요합니다. 기존 `01_setup.cmd`로 준비한 Python 환경과 BGE-M3 모델을 사용합니다.
4. 모델 로딩 후 `[1/4]`부터 `[4/4]`까지 진행됩니다. CPU로 실행하므로 잠시 기다립니다.
5. 마지막에 `PASS: 샘플 DB 구동 확인 완료`가 표시되는지 확인합니다.
6. `reports\chroma_sample_result.md`를 열어 실행 시각과 결과를 확인합니다. 화면을 캡처해 작업 증적으로 남겨도 됩니다.

이 실행 파일은 지정한 샘플 10개를 다시 저장하므로 반복 실행해도 같은 ID가 중복으로 늘어나지 않습니다. 매번 결과 보고서를 최신 성공 결과로 갱신합니다. 실패하면 `FAIL` 또는 오류가 표시되며, 이전 보고서는 이번 실행의 성공을 뜻하지 않습니다.

## 내부에서 하는 일

1. `controls.json`에서 10개 통제항목을 선택합니다: 2.5.1~2.5.6, 2.6.1, 2.6.2, 2.6.6, 2.9.3.
2. 각 항목의 이름·요구사항·키워드·증적 예시를 합쳐 기존 로컬 BGE-M3로 벡터화합니다.
3. ChromaDB의 `isms_p_sample_10` 컬렉션(벡터와 자료를 묶어 저장하는 단위)에 ID·명칭·원문·벡터를 저장합니다.
4. ‘퇴사자의 사용자 계정을 삭제하고 승인자와 처리 일자를 계정 관리대장에 기록했다.’를 같은 BGE-M3로 벡터화해 Top-5를 검색합니다.
5. 저장 프로세스를 완전히 종료한 뒤 새 프로세스에서 DB를 엽니다.
6. 저장 개수 10개, ID, 원문, 메타데이터, 벡터와 재검색 결과가 유지되는지 검사합니다. 직접 계산한 코사인 유사도와도 대조합니다.

Chroma의 기본 임베딩 기능은 끄고 직접 만든 BGE-M3 벡터를 전달합니다. 코사인 거리를 사용하므로 출력 유사도는 `1 - 거리`이며 정답 확률은 아닙니다.

## 단계별로 직접 실행하려면

파일 탐색기에서 `I_P\Search` 폴더를 연 뒤 주소창에 `powershell`을 입력하고 Enter를 누릅니다. 아래 명령은 그 폴더에서 실행합니다.

최초 설치:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-chroma.txt --cache-dir .cache/pip
```

샘플 생성·저장·첫 검색:

```powershell
.\.venv\Scripts\python.exe -X utf8 chroma_sample.py --stage build
```

저장 프로세스가 끝난 후 DB를 다시 열어 검증:

```powershell
.\.venv\Scripts\python.exe -X utf8 chroma_sample.py --stage verify
```

전체 과정을 한 번에 실행:

```powershell
.\.venv\Scripts\python.exe -X utf8 chroma_sample.py
```

설치 후 이 검사 자체는 인터넷 없이 실행합니다. `verify`는 직전에 `build`로 저장한 샘플과 비교하는 것으로, 이후 수정한 KB를 새로 반영하지는 않습니다. 최신 KB 반영이 필요하면 전체 실행을 사용하세요.

## 결과물과 완료 범위

| 위치 | 내용 |
|---|---|
| `data\chroma_sample` | 실제 Chroma 로컬 DB 저장 폴더 |
| `data\chroma_sample_manifest.json` | 재시작 후 비교에 사용하는 샘플·벡터·첫 검색 결과 |
| `reports\chroma_sample_result.md` | 사람이 읽는 실행 결과 보고서 |
| `reports\chroma_sample_result.json` | 실행 정보와 검색 점수의 구조화된 기록 |

성공 시 완료 가능한 WBS: **로컬 임베딩·DB 실행 성공(샘플 저장·검색 및 재실행 확인 포함)**.

후속 작업: 101개 전체 Chroma 색인, 기존 Retriever의 DB 검색 연결, 실제 정답·무관 증적을 이용한 검색 품질 평가. 기존 `02_search.cmd`는 계속 기존 벡터 파일 직접 비교 방식으로 검색합니다.

공식 참고: [Chroma 로컬 클라이언트](https://docs.trychroma.com/reference/python/client), [컬렉션 거리 설정](https://docs.trychroma.com/docs/collections/configure), [벡터 입력 검색](https://docs.trychroma.com/docs/querying-collections/query-and-get).
