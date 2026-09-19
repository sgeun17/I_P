"""ISMS-P 검색 첫 버전: 증적 문장 → BGE-M3 → 관련 통제항목 Top-5.

처음 준비: python retriever.py --prepare  (인터넷 필요)
검색하기: python retriever.py "퇴직자의 계정을 삭제한 내역"  (로컬 실행)
"""
from __future__ import annotations

import argparse
from contextlib import redirect_stdout
import hashlib
from importlib.metadata import PackageNotFoundError, version
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import time
import uuid
from zipfile import BadZipFile

ROOT = Path(__file__).resolve().parent
MODEL_ID = "BAAI/bge-m3"
MODEL_REVISION = "5617a9f61b028005a4858fdac845db406aefb181"
MODEL_DIR = ROOT / "models" / "bge-m3"
MODEL_FILES = (
    "config_sentence_transformers.json",
    "config.json",
    "modules.json",
    "sentence_bert_config.json",
    "sentencepiece.bpe.model",
    "special_tokens_map.json",
    "tokenizer_config.json",
    "tokenizer.json",
    "1_Pooling/config.json",
    "pytorch_model.bin",
)


def notice(message):
    # 안내문은 stderr에 써서 --json 출력에 섞이지 않게 합니다.
    print(message, file=sys.stderr)


def load_controls(path):
    """참고서(JSON)를 읽고 잘못된 항목이 없는지 확인합니다."""
    controls = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    if not isinstance(controls, list) or len(controls) != 101:
        raise ValueError("controls.json에는 101개 통제항목이 배열로 있어야 합니다.")
    seen = set()
    for index, control in enumerate(controls, 1):
        if not isinstance(control, dict):
            raise ValueError(f"{index}번째 통제항목이 객체가 아닙니다.")
        for field in ("control_id", "control_name", "requirement"):
            value = control.get(field)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{index}번째 항목의 {field}가 비어 있거나 문자열이 아닙니다.")
        control_id = control["control_id"]
        if not re.fullmatch(r"[123]\.\d+\.\d+", control_id) or control_id in seen:
            raise ValueError(f"통제항목 ID 형식 오류 또는 중복: {control_id}")
        seen.add(control_id)
        for field in ("keyword", "evidence_examples"):
            values = control.get(field)
            if not isinstance(values, list) or not all(
                isinstance(x, str) and x.strip() for x in values
            ):
                raise ValueError(f"{control_id}: {field}는 문자열 배열이어야 합니다.")
    return controls


def control_text(control):
    """모델에 읽힐 문장: 이름 + 요구사항 + 키워드 + 증거자료."""
    return (
        f"통제항목: {control['control_id']} {control['control_name']}\n"
        f"인증기준: {control['requirement']}\n"
        f"핵심 키워드: {', '.join(control['keyword'])}\n"
        f"증거자료 예시: {'; '.join(control['evidence_examples'])}"
    )


def prepare_model(model_dir):
    """공식 모델 파일을 최초 한 번 내려받습니다. 증적을 전송하지 않습니다."""
    # 사내망처럼 연결이 불안정한 환경에서도 큰 파일을 이어받을 수 있게 합니다.
    # huggingface_hub는 import할 때 이 설정을 읽으므로 import보다 먼저 지정합니다.
    os.environ.setdefault("HF_HUB_DOWNLOAD_TIMEOUT", "120")
    os.environ.setdefault("HF_HUB_ETAG_TIMEOUT", "60")
    os.environ.setdefault("HF_HUB_DISABLE_XET", "1")
    os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
    from huggingface_hub import hf_hub_download

    model_dir = Path(model_dir)
    marker = model_dir / "download_complete.json"
    complete = lambda name: (model_dir / name).is_file() and (model_dir / name).stat().st_size > 0
    if marker.is_file() and all(complete(name) for name in MODEL_FILES):
        notice("이미 준비된 로컬 모델을 사용합니다.")
        return

    missing = [name for name in MODEL_FILES if not complete(name)]
    notice(
        f"BGE-M3의 빠진 파일 {len(missing)}개를 이어받습니다. "
        "이미 받은 파일은 다시 받지 않습니다."
    )
    waits = (2, 4, 8, 15, 30, 45, 60)
    for file_index, filename in enumerate(missing, 1):
        for attempt in range(1, len(waits) + 2):
            try:
                notice(f"[{file_index}/{len(missing)}] {filename} (시도 {attempt})")
                hf_hub_download(
                    repo_id=MODEL_ID,
                    filename=filename,
                    revision=MODEL_REVISION,
                    local_dir=str(model_dir),
                    token=False,
                    etag_timeout=60,
                )
                break
            except Exception as error:
                if attempt > len(waits):
                    raise RuntimeError(
                        f"{filename} 다운로드를 여러 번 시도했지만 연결이 계속 끊겼습니다."
                    ) from error
                wait_seconds = waits[attempt - 1]
                notice(
                    f"연결이 끊겼습니다. 받은 부분은 보존하고 "
                    f"{wait_seconds}초 뒤 이어받습니다."
                )
                time.sleep(wait_seconds)

    if not all(complete(name) for name in MODEL_FILES):
        raise ValueError("모델 다운로드가 불완전합니다. 처음 준비를 다시 실행하세요.")
    marker.write_text(
        json.dumps({"model_id": MODEL_ID, "revision": MODEL_REVISION}),
        encoding="utf-8",
    )
    notice("BGE-M3 모델 준비가 끝났습니다.")


def load_model(model_dir, device):
    if not Path(model_dir).is_dir():
        raise ValueError("모델이 없습니다. 먼저 01_setup.cmd를 실행해 주세요.")
    # 실제 검색은 인터넷 연결 없이 로컬 파일만 사용합니다.
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
    from sentence_transformers import SentenceTransformer

    notice("검색 모델을 불러오는 중입니다...")
    with redirect_stdout(sys.stderr):
        return SentenceTransformer(
            str(Path(model_dir).resolve()), device=device,
            local_files_only=True, trust_remote_code=False,
        )


def normalized_vectors(values):
    import numpy as np

    vectors = np.asarray(values, dtype=np.float32)
    if vectors.ndim != 2 or not all(vectors.shape) or not np.isfinite(vectors).all():
        raise ValueError("모델이 유효하지 않은 벡터를 반환했습니다.")
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    if np.any(norms == 0) or not np.isfinite(norms).all():
        raise ValueError("길이가 0이거나 너무 큰 벡터는 비교할 수 없습니다.")
    return vectors / norms


def rank_controls(controls, kb_vectors, query_vector, top_k, evidence_id):
    """코사인 유사도로 101개를 모두 비교하고 높은 순서로 반환합니다."""
    import numpy as np

    if type(top_k) is not int or not 1 <= top_k <= len(controls):
        raise ValueError(f"top_k는 1~{len(controls)} 사이의 정수여야 합니다.")
    kb = normalized_vectors(kb_vectors)
    query = normalized_vectors(query_vector)
    if kb.shape[0] != len(controls) or query.shape != (1, kb.shape[1]):
        raise ValueError("통제항목과 벡터 개수 또는 벡터 차원이 맞지 않습니다.")
    scores = np.clip(kb @ query[0], -1.0, 1.0)
    order = np.argsort(-scores, kind="stable")[:top_k]
    candidates = [
        {"rank": rank, "control_id": controls[i]["control_id"],
         "control_name": controls[i]["control_name"],
         "similarity_score": round(float(scores[i]), 6)}
        for rank, i in enumerate(order, 1)
    ]
    return {"evidence_id": evidence_id,
            "retrieval": {"top_k": top_k, "candidates": candidates}}


class Retriever:
    def __init__(self, kb_path=ROOT / "controls.json", model_dir=MODEL_DIR,
                 cache_dir=ROOT / "data", device="cpu"):
        self.controls = load_controls(kb_path)
        self.texts = [control_text(c) for c in self.controls]
        self.model = load_model(model_dir, device)
        self.cache_path = Path(cache_dir) / "control_embeddings.npz"
        # KB 내용/순서, 모델 파일, 라이브러리가 달라지면 캐시를 다시 만듭니다.
        files = [(str(p.relative_to(model_dir)), p.stat().st_size, p.stat().st_mtime_ns)
                 for p in sorted(Path(model_dir).rglob("*"))
                 if p.is_file() and ".cache" not in p.relative_to(model_dir).parts]
        versions = {}
        for package in ("sentence-transformers", "transformers", "torch"):
            try:
                versions[package] = version(package)
            except PackageNotFoundError:
                versions[package] = "unavailable"
        identity = {"format": 1, "texts": self.texts, "files": files,
                    "versions": versions, "max_length": self.model.max_seq_length}
        self.cache_key = hashlib.sha256(json.dumps(identity, ensure_ascii=False).encode()).hexdigest()
        self.vectors = self._load_or_build_vectors()

    def _encode(self, texts):
        # 긴 입력을 모델이 조용히 잘라버리지 않도록 길이를 먼저 확인합니다.
        tokens = self.model.tokenizer(texts, truncation=False, padding=False)["input_ids"]
        if any(len(t) > self.model.max_seq_length for t in tokens):
            raise ValueError(
                f"입력이 모델 한도({self.model.max_seq_length} 토큰)를 넘습니다. "
                "문서를 짧게 나누어 검색해 주세요."
            )
        with redirect_stdout(sys.stderr):
            values = self.model.encode(
                texts, batch_size=4, normalize_embeddings=True,
                convert_to_numpy=True, show_progress_bar=False,
            )
        return normalized_vectors(values)

    def _load_or_build_vectors(self):
        import numpy as np

        if self.cache_path.is_file():
            try:
                with np.load(self.cache_path, allow_pickle=False) as cached:
                    vectors = normalized_vectors(cached["vectors"])
                    if hasattr(self.model, "get_embedding_dimension"):
                        dimension = self.model.get_embedding_dimension()
                    else:
                        dimension = self.model.get_sentence_embedding_dimension()
                    shape = (len(self.controls), dimension)
                    if str(cached["key"].item()) == self.cache_key and vectors.shape == shape:
                        notice("저장된 통제항목 벡터를 불러왔습니다.")
                        return vectors
            except (ValueError, OSError, KeyError, EOFError, BadZipFile):
                notice("저장된 벡터를 다시 만들겠습니다.")
        notice("101개 통제항목을 숫자로 바꾸는 중입니다. 처음 한 번은 시간이 걸립니다.")
        vectors = self._encode(self.texts)
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        # 쓰기 중 중단되어도 기존 캐시가 손상되지 않도록 교체합니다.
        name = None
        try:
            with tempfile.NamedTemporaryFile(dir=self.cache_path.parent, suffix=".npz", delete=False) as f:
                name = f.name
                np.savez_compressed(f, key=self.cache_key, vectors=vectors)
            os.replace(name, self.cache_path)
        finally:
            if name and Path(name).exists():
                Path(name).unlink()
        return vectors

    def search(self, text, top_k=5, evidence_id=None):
        if not isinstance(text, str) or not text.strip():
            raise ValueError("검색할 증적 내용을 입력해 주세요.")
        if type(top_k) is not int or not 1 <= top_k <= len(self.controls):
            raise ValueError("top_k는 1~101 사이의 정수여야 합니다.")
        if evidence_id is not None and (not isinstance(evidence_id, str) or not evidence_id.strip()):
            raise ValueError("evidence_id는 비어 있지 않은 문자열이어야 합니다.")
        query = self._encode([text.strip()])
        return rank_controls(self.controls, self.vectors, query, top_k,
                             evidence_id or f"EVID-{uuid.uuid4().hex[:12]}")


def show_result(result, as_json=False):
    if as_json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return
    print("\n관련 통제항목 후보 (유사도는 정답 확률이 아닙니다)")
    for item in result["retrieval"]["candidates"]:
        print(f"{item['rank']}. {item['control_id']} {item['control_name']}"
              f"  | 유사도 {item['similarity_score']:.4f}")
    print()


def main():
    parser = argparse.ArgumentParser(description="증적 내용을 입력하면 관련 ISMS-P 통제항목을 찾습니다.")
    parser.add_argument("text", nargs="?", help="검색할 증적 내용. 생략하면 반복 입력합니다.")
    parser.add_argument("--top-k", type=int, default=5, help="후보 수 (기본 5)")
    parser.add_argument("--evidence-id", help="결과에 넣을 증적 ID")
    parser.add_argument("--json", action="store_true", help="스키마에 맞는 JSON 출력")
    parser.add_argument("--validate", action="store_true", help="모델 없이 KB만 검사")
    parser.add_argument("--prepare", action="store_true", help="인터넷으로 모델 다운로드 후 첫 색인 생성")
    parser.add_argument("--kb", type=Path, default=ROOT / "controls.json")
    parser.add_argument("--model-dir", type=Path, default=MODEL_DIR)
    parser.add_argument("--device", choices=["cpu", "cuda"], default="cpu")
    args = parser.parse_args()
    if not 1 <= args.top_k <= 101:
        parser.error("--top-k는 1~101 사이여야 합니다.")
    if args.json and args.text is None:
        parser.error("--json 사용 시 검색 문장을 함께 입력하세요.")
    if args.text is not None and not args.text.strip():
        parser.error("빈 문장은 검색할 수 없습니다.")
    try:
        controls = load_controls(args.kb)
        if args.validate:
            print(f"KB 검사 통과: {len(controls)}개 통제항목")
            return 0
        if args.prepare:
            prepare_model(args.model_dir)
        retriever = Retriever(args.kb, args.model_dir, device=args.device)
        if args.prepare:
            notice("준비 완료! 이제 02_search.cmd를 실행하세요.")
            return 0
        if args.text is not None:
            show_result(retriever.search(args.text, args.top_k, args.evidence_id), args.json)
        else:
            print("증적 내용을 한 줄로 입력하세요. 종료하려면 exit를 입력하세요.")
            while True:
                text = input("증적 내용 > ").strip()
                if text.lower() in {"exit", "quit", "종료"}:
                    break
                if not text:
                    continue
                try:
                    show_result(retriever.search(text, args.top_k, args.evidence_id))
                except ValueError as error:
                    notice(f"입력 확인: {error}")
        return 0
    except (KeyboardInterrupt, EOFError):
        notice("검색을 종료합니다.")
        return 0
    except ImportError as error:
        notice(f"필요한 프로그램이 없습니다. 01_setup.cmd를 먼저 실행하세요. ({error})")
        return 1
    except Exception as error:
        notice(f"실행하지 못했습니다: {error}")
        return 1


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    raise SystemExit(main())
