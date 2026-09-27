"""모델 없이 검색 객체 수명·요청 격리·프로세스 오류 복구를 검사한다."""
from concurrent.futures import ThreadPoolExecutor
from io import StringIO
import json
import os
import sys
import unittest
from unittest.mock import Mock, patch

import chunk_retriever
from chunk_retriever import RetrievalError, retrieve, serve_requests
from tests.test_chunk_retriever import payload
from worker_client import PersistentWorker


FAKE_WORKER = r'''
import json, os, sys, time
for line in sys.stdin:
    request = json.loads(line)
    mode = request.get('mode')
    if mode == 'sleep':
        time.sleep(2)
    if mode == 'exit':
        sys.exit(2)
    if mode == 'malformed':
        print('not JSON', flush=True)
        continue
    if mode == 'wrong_id':
        print(json.dumps({'request_id': -1, 'result': {'success': True}}), flush=True)
        continue
    if mode == 'bad_success':
        print(json.dumps({'request_id': request['request_id'], 'result': {'success': 1}}), flush=True)
        continue
    if mode == 'stderr':
        sys.stderr.write('x' * 200000 + '\n')
        sys.stderr.flush()
    result = {'schema_version': 'retriever-0.2', 'success': True,
              'token': request.get('token'), 'pid': os.getpid()}
    print(json.dumps({'request_id': request['request_id'], 'result': result}), flush=True)
'''


class WorkerLoopTests(unittest.TestCase):
    def invoke(self, requests, factory):
        output = StringIO()
        serve_requests(StringIO('\n'.join(json.dumps(r) for r in requests) + '\n'), output, factory)
        return [json.loads(line)['result'] for line in output.getvalue().splitlines()]

    def test_invalid_requests_do_not_load_model_then_valid_requests_reuse_engine(self):
        factory = Mock()
        factory.return_value.search.return_value = {'success': True}
        bad = {'payload': payload(), 'top_k': 0}
        good = {'payload': payload(), 'top_k': 5}
        replies = self.invoke([bad, {'payload': {}}, good, good], factory)
        self.assertEqual([r['success'] for r in replies], [False, False, True, True])
        factory.assert_called_once_with()
        self.assertEqual(factory.return_value.search.call_count, 2)

    def test_bad_json_and_runtime_failure_do_not_break_next_request(self):
        engine = Mock()
        engine.search.side_effect = [RetrievalError('INPUT_TOO_LONG', 'limit'), {'success': True}]
        factory = Mock(return_value=engine)
        output = StringIO()
        good = json.dumps({'payload': payload()})
        serve_requests(StringIO('bad JSON\n' + good + '\n' + good + '\n'), output, factory)
        replies = [json.loads(line)['result'] for line in output.getvalue().splitlines()]
        self.assertEqual(replies[0]['error']['code'], 'INVALID_INPUT')
        self.assertEqual(replies[1]['error']['code'], 'INPUT_TOO_LONG')
        self.assertTrue(replies[2]['success'])
        factory.assert_called_once_with()

    def test_initialization_failure_can_retry(self):
        engine = Mock()
        engine.search.return_value = {'success': True}
        factory = Mock(side_effect=[RuntimeError('model unavailable'), engine])
        replies = self.invoke([{'payload': payload()}] * 2, factory)
        self.assertEqual(replies[0]['error']['code'], 'SEARCH_FAILED')
        self.assertTrue(replies[1]['success'])
        self.assertEqual(factory.call_count, 2)

    def test_public_function_rejects_preprocessing_failure_without_worker(self):
        with patch.object(chunk_retriever._WORKER_CLIENT, 'request') as request:
            bad = payload()
            bad['errors'] = ['empty_document']
            self.assertEqual(retrieve(bad)['error']['code'], 'PREPROCESSING_FAILED')
            self.assertEqual(retrieve(payload(), top_k=True)['error']['code'], 'INVALID_INPUT')
            request.assert_not_called()


class WorkerClientTests(unittest.TestCase):
    def setUp(self):
        self.client = PersistentWorker([sys.executable, '-u', '-X', 'utf8', '-c', FAKE_WORKER], os.getcwd())
        self.addCleanup(self.client.close)

    def test_concurrent_calls_keep_one_process_and_responses_match_request(self):
        with ThreadPoolExecutor(max_workers=6) as pool:
            replies = list(pool.map(lambda n: self.client.request({'token': f'문서{n}\n원문'}, 5), range(12)))
        self.assertTrue(all(r['success'] for r in replies))
        self.assertEqual([r['token'] for r in replies], [f'문서{n}\n원문' for n in range(12)])
        self.assertEqual(len({r['pid'] for r in replies}), 1)

    def test_timeout_discards_worker_and_next_call_starts_clean_process(self):
        before = self.client.request({'token': 'first'}, 5)
        old_process = self.client.process
        failed = self.client.request({'mode': 'sleep'}, .05)
        self.assertEqual(failed['error']['code'], 'TIMEOUT')
        self.assertIsNone(self.client.process)
        self.assertIsNotNone(old_process.poll())
        after = self.client.request({'token': 'next'}, 5)
        self.assertTrue(after['success'])
        self.assertNotEqual(before['pid'], after['pid'])
        self.assertEqual(after['token'], 'next')

    def test_dead_or_invalid_response_recovers_on_next_request(self):
        for mode in ['exit', 'malformed', 'wrong_id', 'bad_success']:
            with self.subTest(mode=mode):
                failed = self.client.request({'mode': mode}, 5)
                self.assertEqual(failed['error']['code'], 'WORKER_FAILED')
                self.assertTrue(self.client.request({'token': mode}, 5)['success'])

    def test_waiting_timeout_does_not_stop_existing_process(self):
        before = self.client.request({'token': 'before'}, 5)
        self.client._lock.acquire()
        try:
            failed = self.client.request({'token': 'queued'}, .01)
            self.assertEqual(failed['error']['code'], 'TIMEOUT')
            self.assertIsNone(self.client.process.poll())
        finally:
            self.client._lock.release()
        self.assertEqual(before['pid'], self.client.request({'token': 'after'}, 5)['pid'])

    def test_pipe_write_is_also_bounded_by_timeout(self):
        self.client.close()
        self.client.command = [sys.executable, '-u', '-c', 'import time; time.sleep(5)']
        self.assertEqual(self.client.request({'token': 'x' * 2000000}, .1)['error']['code'], 'TIMEOUT')
        self.assertIsNone(self.client.process)

    def test_close_is_idempotent_and_next_call_restarts(self):
        first = self.client.request({}, 5)
        process = self.client.process
        self.client.close()
        self.client.close()
        self.assertIsNotNone(process.poll())
        self.assertTrue(process.stdin.closed and process.stdout.closed and process.stderr.closed)
        self.assertNotEqual(first['pid'], self.client.request({}, 5)['pid'])

    def test_invalid_serialization_and_timeout_preserve_healthy_worker(self):
        before = self.client.request({}, 5)
        self.assertEqual(self.client.request({'bad': float('nan')}, 5)['error']['code'], 'WORKER_FAILED')
        for timeout in [0, -1, float('nan'), float('inf'), True, '1']:
            self.assertEqual(self.client.request({}, timeout)['error']['code'], 'INVALID_INPUT')
        self.assertEqual(before['pid'], self.client.request({}, None)['pid'])

    def test_verbose_stderr_does_not_block_result(self):
        with patch('sys.stderr', StringIO()) as captured:
            result = self.client.request({'mode': 'stderr'}, 5)
            self.client.close()
        self.assertTrue(result['success'])
        self.assertGreater(len(captured.getvalue()), 100000)


if __name__ == '__main__':
    unittest.main()
