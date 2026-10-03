import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest


def load_tool():
    path = Path(__file__).resolve().parents[1]/'tools/run_span_judgments.py'
    spec = importlib.util.spec_from_file_location('span_cli_test', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize('fail', [False, True])
def test_cli_saves_results_without_changing_input(tmp_path, monkeypatch, fail):
    tool = load_tool()
    source, out = tmp_path/'source', tmp_path/'out'
    (source/'E0001').mkdir(parents=True)
    payload = source/'E0001/search.json'
    payload.write_text('{"source_file":"test.pdf"}', encoding='utf-8')
    before = payload.read_bytes()
    def run(*args, **kwargs):
        if fail:
            raise RuntimeError('test failure')
        result = {'processing_status':'COMPLETED', 'match_status':'NO_MATCH', 'mapped_controls':[],
                  'validation':{'passed':True, 'issues':[]}, 'human_review':{'required':True, 'reasons':['R206']}}
        return SimpleNamespace(mapping_result=SimpleNamespace(model_dump=lambda **kw: result),
                               llm_run=SimpleNamespace(attempt_records=(), retry_count=0))
    monkeypatch.setitem(sys.modules, 'judgment_pipeline', SimpleNamespace(run_judgment=run))
    args = ['--mode','baseline','--source-dir',str(source),'--out-dir',str(out)]
    assert tool.main(args) == int(fail)
    summary = json.loads((out/'summary.json').read_text(encoding='utf-8'))
    assert summary['request_or_runtime_failures'] == int(fail)
    assert summary['validation_passed'] == int(not fail)
    assert payload.read_bytes() == before
    with pytest.raises(SystemExit):
        tool.main(args)  # Never overwrite a previous result directory.


def test_cli_rejects_nested_output(tmp_path):
    source = tmp_path/'source'
    (source/'E0001').mkdir(parents=True)
    (source/'E0001/search.json').write_text('{}')
    with pytest.raises(SystemExit):
        load_tool().main(['--mode','spans','--source-dir',str(source),'--out-dir',str(source/'nested')])
