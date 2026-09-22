# ChromaDB 전체 인증기준 색인·재색인

`I_P\Search` 폴더의 **04_index_chroma.cmd**를 더블클릭합니다. 이미 준비된 로컬 모델과 ChromaDB를 사용하며, 마지막에 `PASS: 101개 ChromaDB 색인 검증 완료`가 나오면 성공입니다.

## 실행 과정

1. 현재 `controls.json`의 101개 항목을 읽고 ID 중복·필수 필드를 검사합니다.
2. 기존 Retriever의 캐시 검사를 사용해 KB 내용·순서·모델 파일·라이브러리 버전과 저장된 벡터가 맞는지 확인합니다. 일치하면 재사용하고, 불일치하면 로컬 BGE-M3로 재임베딩합니다.
3. 101개 벡터(각 1,024차원)와 ID·명칭·검색용 원문·KB 해시·모델 정보를 ChromaDB에 저장하고 코사인 거리로 색인합니다.
4. 같은 내용을 한 번 더 저장해도 ID별 갱신으로 101개가 유지되는지 확인합니다.
5. 저장 프로세스를 완전히 종료한 후 새 프로세스에서 DB를 엽니다.
6. 101개 ID·원문·메타데이터·벡터를 모두 대조합니다. 각 벡터로 검색했을 때 자기 ID가 1위인지, 예시 3건의 Top-5 순위·점수가 재시작 전후 및 직접 코사인 계산과 일치하는지 검사합니다.
7. 성공 보고서와 전체 DB 벡터 조회 사본을 생성합니다.

## 저장 위치

| 경로 | 내용 |
|---|---|
| `data/chroma_kb` | 전체 101개 ChromaDB. 컬렉션은 `isms_p_controls` |
| `reports/chroma_index_result.md` | 실행 시각·검증 결과·예시 검색 결과 |
| `reports/chroma_index_result.json` | 구조화된 검증 결과 |
| `data/chroma_kb_vectors_view.json` | DB에서 실제 조회한 전체 원문·벡터 사본. 자동 동기화되지 않음 |
| `data/chroma_kb_manifest.json` / `data/chroma_kb_validation.npz` | 재시작 검증에 사용하는 원문·메타데이터·벡터 사본 |

10개 샘플은 `data/chroma_sample`의 `isms_p_sample_10` 컬렉션에 별도로 보관됩니다. 전체 조회는 `chroma_kb_vectors_view.json`, 10개 샘플 조회는 기존 `chroma_vectors_view.json`을 보세요.

이 PC에서는 ChromaDB 1.5.9에 한글 절대 경로를 전달하면 HNSW 검색 인덱스 파일이 제대로 남지 않아 재실행 검색에 실패하는 현상을 확인했습니다. 실행 코드가 작업 기준 폴더를 `Search`로 고정한 뒤 ASCII 상대 경로 `data/chroma_kb`로 DB를 여는 방식으로 수정했습니다. 별도 연동 코드에서도 이 실행 기준과 상대 경로를 유지해야 합니다. 저장 위치 자체는 위 폴더 그대로입니다.

## KB를 수정한 뒤 재색인

1. `controls.json`을 수정하고 저장합니다. 색인 중에는 KB를 수정하지 않습니다.
2. **04_index_chroma.cmd**를 다시 실행합니다.
3. 변경된 입력이면 벡터를 다시 만들고 같은 ID의 DB 내용을 갱신합니다.
4. 마지막 PASS와 보고서의 실행 시각·KB SHA-256을 확인합니다.

자동 캐시 갱신은 `embedding_text`에 해당하는 문장, 모델 파일 정보, 관련 라이브러리 버전을 기준으로 합니다. 별도 강제 초기화는 필요하지 않습니다. DB에 현재 KB에 없는 ID가 발견되면 자동 삭제하지 않고 오류를 표시합니다.

PowerShell에서 단계별 실행할 수도 있습니다. 명령은 `I_P\Search` 폴더에서 실행합니다.

```powershell
# 전체 색인과 새 프로세스 검증
.\.venv\Scripts\python.exe -X utf8 chroma_index.py

# 이전에 색인한 DB 검증만 다시 실행 (모델 로딩 없음)
.\.venv\Scripts\python.exe -X utf8 chroma_index.py --stage verify
```

검증만 실행할 때 KB가 바뀌었다면 중단합니다. 이 경우 전체 실행으로 재색인하세요. 모델을 바꾼 경우에도 전체 실행이 필요합니다. 실패 메시지가 나오면 이전 성공 보고서를 이번 실행의 결과로 사용하지 마세요.

## 이번에 완료한 범위

**WBS의 KB 임베딩 생성/색인: 현재 작업본 101개를 대상으로 완료.** KB 해시는 사용한 파일을 식별하는 기록이며, KB 최종 검수·버전 고정을 의미하지 않습니다.

예시 3건은 DB 동작과 점수 계산 검사입니다. 실제 정답·무관 증적의 검색 품질 평가, 전처리팀 청크 연결, 판단팀 JSON 연동은 별도입니다. `02_search.cmd`의 기존 검색은 여전히 벡터 파일을 직접 비교하며, 이번 작업은 전체 ChromaDB 저장·색인과 검증까지 수행합니다.
