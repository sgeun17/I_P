"""한 호출 프로세스의 검색 요청을 직렬화하고 검색 자식 프로세스를 재사용한다."""
from __future__ import annotations

import json
import math
import os
import queue
import subprocess
import sys
import threading
import time


def failure(code, message):
    return {"schema_version": "retriever-0.2", "success": False,
            "error": {"code": code, "message": message}}


class PersistentWorker:
    """JSON-lines 내부 통신. 요청 제한시간은 직렬 처리 대기시간도 포함한다."""
    def __init__(self, command, cwd):
        self.command = command
        self.cwd = cwd
        self.process = None
        self._owner_pid = os.getpid()
        self._lock = threading.Lock()
        self._sequence = 0
        self._stderr_thread = None

    def _check_owner(self):
        # fork한 자식이 부모의 검색 프로세스·잠금·파이프를 사용하지 않도록 한다.
        if self._owner_pid != os.getpid():
            self.process = None
            self._stderr_thread = None
            self._lock = threading.Lock()
            self._owner_pid = os.getpid()

    @staticmethod
    def _forward_stderr(process):
        try:
            for line in process.stderr:
                print(line, file=sys.stderr, end="")
        except (OSError, ValueError):
            pass  # 호출 프로그램 종료 중에는 stderr가 먼저 닫힐 수 있다.

    def _start(self):
        if self.process is not None and self.process.poll() is None:
            return
        self._stop()
        flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        self.process = subprocess.Popen(
            self.command, cwd=self.cwd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, encoding="utf-8", bufsize=1,
            creationflags=flags)
        self._stderr_thread = threading.Thread(
            target=self._forward_stderr, args=(self.process,), daemon=True)
        self._stderr_thread.start()

    def _stop(self):
        process, self.process = self.process, None
        if process is None:
            return
        if process.poll() is None:
            try:
                process.kill()
            except ProcessLookupError:
                pass
        process.wait(timeout=5)
        if self._stderr_thread is not None:
            self._stderr_thread.join(timeout=1)
            self._stderr_thread = None
        for stream in (process.stdin, process.stdout, process.stderr):
            stream.close()

    def close(self):
        """현재 요청 종료 후 자신이 띄운 검색 프로세스를 정리한다. 다음 요청은 새로 시작한다."""
        self._check_owner()
        with self._lock:
            self._stop()

    def request(self, body, timeout):
        self._check_owner()
        if timeout is not None and (type(timeout) not in (int, float)
                                    or not math.isfinite(timeout) or timeout <= 0):
            return failure("INVALID_INPUT", "timeout은 양수 초 또는 None이어야 합니다.")
        deadline = None if timeout is None else time.monotonic() + timeout

        def remaining():
            return None if deadline is None else max(0.0, deadline - time.monotonic())

        acquired = self._lock.acquire() if deadline is None else self._lock.acquire(timeout=remaining())
        if not acquired:
            # 대기 요청의 만료는 다른 요청이 사용 중인 프로세스를 종료하지 않는다.
            return failure("TIMEOUT", "검색 처리 대기시간이 제한을 초과했습니다.")
        exchange_thread = None
        try:
            self._sequence += 1
            request_id = self._sequence
            try:
                message = json.dumps({**body, "request_id": request_id}, ensure_ascii=False, allow_nan=False)
            except (ValueError, TypeError) as error:
                return failure("WORKER_FAILED", str(error))
            if deadline is not None and remaining() == 0:
                return failure("TIMEOUT", "검색 시간이 제한을 초과했습니다.")
            self._start()
            process = self.process
            reply = queue.Queue(maxsize=1)

            def exchange():
                try:
                    process.stdin.write(message + "\n")
                    process.stdin.flush()
                    line = process.stdout.readline()
                    if not line:
                        raise OSError("검색 프로세스가 응답 없이 종료됐습니다.")
                    envelope = json.loads(line)
                    if not isinstance(envelope, dict) or envelope.get("request_id") != request_id:
                        raise ValueError("검색 프로세스의 응답 ID가 잘못됐습니다.")
                    answer = envelope.get("result")
                    if not isinstance(answer, dict) or type(answer.get("success")) is not bool:
                        raise ValueError("검색 프로세스의 응답이 잘못됐습니다.")
                    reply.put((answer, None))
                except (OSError, ValueError, TypeError) as error:
                    reply.put((None, error))

            # 쓰기·읽기 모두 제한시간 안에서 기다린다. 큰 입력의 파이프 쓰기도 포함한다.
            exchange_thread = threading.Thread(target=exchange, daemon=True)
            exchange_thread.start()
            answer, error = reply.get(timeout=remaining())
            if error is not None:
                raise error
            return answer
        except queue.Empty:
            self._stop()  # 늦은 응답이 다음 요청의 결과로 섞이지 않게 폐기한다.
            return failure("TIMEOUT", "검색 시간이 제한을 초과했습니다.")
        except (OSError, ValueError, TypeError) as error:
            self._stop()
            return failure("WORKER_FAILED", str(error))
        finally:
            if exchange_thread is not None:
                exchange_thread.join(timeout=1)
            self._lock.release()
