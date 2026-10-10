import hashlib
import importlib.util
import json
import os
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location('registry', Path(__file__).resolve().parents[1] / 'tools/registry.py')
r = importlib.util.module_from_spec(spec); spec.loader.exec_module(r)

class RegistryTest(unittest.TestCase):
    def data(self, id='example', version='1.0.0', owner='alice', body=None):
        body = body or f'plugin "{id}"; version "{version}";'.encode()
        return dict(id=id, version=version, sha256=hashlib.sha256(body).hexdigest(), source=f'https://raw.githubusercontent.com/{owner}/plugins/main/{id}.umc')
    def test_schema(self):
        d = self.data(); self.assertEqual(r.manifest('plugins/example/1.0.0.json', json.dumps(d).encode()), d)
        for path, data in [('plugins/../1.0.0.json', d), ('plugins/other/1.0.0.json', d), ('plugins/example/1.0.0.json', dict(d, extra='x')), ('plugins/example/1.0.0.json', dict(d, sha256='no'))]:
            with self.assertRaises(r.Invalid): r.manifest(path, json.dumps(data).encode())
        with self.assertRaises(r.Invalid): r.manifest('plugins/example/1.0.0.json', b'{"id":"example","id":"other"}')
    def test_urls(self):
        self.assertEqual(r.source_owner('https://github.com/alice/repo/releases/download/v1/p.umc'), 'alice')
        for url in ['http://github.com/alice/repo/releases/download/v1/p.umc', 'https://evil.com/a/p.umc', 'https://github.com/alice/repo/blob/main/p.umc', 'https://github.com@evil.com/a/p.umc', 'https://raw.githubusercontent.com/alice/r/main/../p.umc', 'https://raw.githubusercontent.com/alice/r/main/p.umc?x=1']:
            with self.assertRaises(r.Invalid): r.source_owner(url)
    def test_file_scope(self):
        class API(r.GitHub):
            def api(self, path): return self.rows
        api = API('alice/registry'); api.rows = [dict(filename='plugins/example/1.0.0.json', status='added', changes=8)]
        self.assertEqual(len(api.files(1)), 1)
        for path, status in [('.github/workflows/accept.yml','added'), ('plugins/example/1.0.0.json','modified'), ('tools/registry.py','added')]:
            api.rows = [dict(filename=path, status=status, changes=8)]
            with self.assertRaises(r.Invalid): api.files(1)
    def test_release_asset_propagation(self):
        import urllib.error
        from unittest.mock import patch
        missing = urllib.error.HTTPError('https://github.com/a/b/releases/download/v/p.umc',404,'Not Found',{},None)
        with patch.object(r,'download',side_effect=[missing,missing,b'plugin bytes']) as fetch, patch.object(r.time,'sleep') as sleep:
            self.assertEqual(r.published_download('https://github.com/a/b/releases/download/v/p.umc'),b'plugin bytes')
            self.assertEqual(fetch.call_count,3)
            self.assertEqual(sleep.call_count,2)
        forbidden = urllib.error.HTTPError('https://github.com/a/b/releases/download/v/p.umc',403,'Forbidden',{},None)
        with patch.object(r,'download',side_effect=forbidden), patch.object(r.time,'sleep') as sleep:
            with self.assertRaises(urllib.error.HTTPError): r.published_download('https://github.com/a/b/releases/download/v/p.umc')
            sleep.assert_not_called()

    def test_merge_checks_head_and_base(self):
        from unittest.mock import patch
        class API:
            repo = 'alice/registry'
            head = 'a'*40
            base = 'b'*40
            calls = []
            def api(self,path,**kwargs):
                if path == '/pulls/7': return dict(head=dict(sha=self.head),state='open',draft=False,base=dict(ref='main'))
                if path == '/git/ref/heads/main': return dict(object=dict(sha=self.base))
                self.calls.append((path,kwargs))
                if path.endswith('/merge'): return dict(merged=True)
                return None
            def files(self,number): return [dict(filename='plugins/example/1.0.0.json')]
        api = API()
        with patch.object(r,'GitHub',return_value=api):
            with self.assertRaises(r.Invalid): r.merge_pr(7,'x'*40,api.base)
            with self.assertRaises(r.Invalid): r.merge_pr(7,api.head,'x'*40)
            self.assertFalse(api.calls)
            r.merge_pr(7,api.head,api.base)
            self.assertEqual(api.calls[0][1]['data']['sha'],api.head)
            self.assertEqual(api.calls[1][0],'/actions/workflows/publish.yml/dispatches')

    def test_compiler_and_dependencies(self):
        binary = os.environ.get('UMC_TEST_BINARY')
        if not binary: self.skipTest('set UMC_TEST_BINARY for real compiler checks')
        sources = {}; records = {}
        def add(id, version='1.0.0', declarations='', owner='alice'):
            body = f'plugin "{id}"; version "{version}"; {declarations}'.encode()
            data = self.data(id,version,owner,body); data['source'] = data['source'].replace('.umc',f'-{version}.umc')
            sources[data['source']] = body; path = f'plugins/{id}/{version}.json'; records[path] = data; return path
        base = add('base'); child = add('child', declarations='depends "base" "1.0.0";')
        checked = r.validate_graph(records, [child], binary, sources.__getitem__); self.assertEqual(len(checked),2)
        sources[records[base]['source']] += b' '
        with self.assertRaises(r.Invalid): r.validate_graph(records,[child],binary,sources.__getitem__)
        sources.clear(); records.clear(); add('base',declarations='permissions storage;'); child=add('child',declarations='depends "base" "1.0.0";')
        with self.assertRaises(r.Invalid): r.validate_graph(records,[child],binary,sources.__getitem__)
        sources.clear(); records.clear(); a=add('a',declarations='depends "b" "1.0.0";'); add('b',declarations='depends "a" "1.0.0";')
        with self.assertRaises(r.Invalid): r.validate_graph(records,[a],binary,sources.__getitem__)
        sources.clear(); records.clear(); add('base'); add('base',version='2.0.0'); add('left',declarations='depends "base" "1.0.0";'); add('right',declarations='depends "base" "2.0.0";'); top=add('top',declarations='depends "left" "1.0.0"; depends "right" "1.0.0";')
        with self.assertRaises(r.Invalid): r.validate_graph(records,[top],binary,sources.__getitem__)
        sources.clear(); records.clear(); add('base'); other=add('base',version='2.0.0',owner='bob')
        with self.assertRaises(r.Invalid): r.validate_graph(records,[other],binary,sources.__getitem__)

if __name__ == '__main__': unittest.main()
