import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from local_search_app import SearchJobs


class JobsTest(unittest.TestCase):
    def test_success_persists_and_restart_marks_interrupted(self):
        with tempfile.TemporaryDirectory() as t:
            info=lambda e:{'version':1,'file_hash':'a','status':'PREPROCESSED','file_name':'a.txt','file_type':'txt'}
            path=Path(t)/'jobs.db'
            jobs=SearchJobs(path,info,lambda *a:[{}],lambda *a,**k:{'success':True,'index':{},'retrieval':{'candidates':[]}})
            started=jobs.create('E1',5)
            jobs.pool.shutdown(wait=True)
            self.assertEqual(jobs.get(started['id'])['status'],'SUCCEEDED')
            jobs.save({'id':'unfinished','status':'RUNNING'})
            reopened=SearchJobs(path,info,None,None)
            self.assertEqual(reopened.get(started['id'])['status'],'SUCCEEDED')
            self.assertEqual(reopened.get('unfinished')['error']['code'],'SERVER_RESTARTED')
            reopened.pool.shutdown()

    def test_changed_version_and_search_failure(self):
        for mode in ('stale','failure'):
            with self.subTest(mode=mode),tempfile.TemporaryDirectory() as t:
                info={'version':1,'file_hash':'a','status':'PREPROCESSED','file_name':'a.txt','file_type':'txt'}
                def search(*a,**k):
                    if mode=='failure':return {'success':False,'error':{'code':'WORKER_FAILED'}}
                    info['version']=2
                    return {'success':True,'index':{},'retrieval':{}}
                jobs=SearchJobs(Path(t)/'jobs.db',lambda e:info,lambda *a:[{}],search)
                started=jobs.create('E1',5);jobs.pool.shutdown(wait=True)
                self.assertEqual(jobs.get(started['id'])['error']['code'],'STALE_EVIDENCE' if mode=='stale' else 'WORKER_FAILED')

    def test_unprocessed_and_bad_k_rejected(self):
        with tempfile.TemporaryDirectory() as t:
            jobs=SearchJobs(Path(t)/'jobs.db',lambda e:{'status':'UPLOADED'},None,None)
            for k in (0,102,True,5):
                with self.subTest(k=k),self.assertRaises(ValueError):jobs.create('E1',k)
            jobs.pool.shutdown()
