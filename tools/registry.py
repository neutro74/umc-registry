#!/usr/bin/env python3
"""Registry data validation and publishing. Submitted scripts are never executed."""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

MAX_SOURCE = 256 * 1024
MAX_MANIFEST = 8192
ID = r'[a-z0-9]+(?:-+[a-z0-9]+)*'
VERSION = r'[0-9A-Za-z][0-9A-Za-z._-]{0,31}'
PATH = re.compile(rf'plugins/({ID})/({VERSION})\.json\Z')

class Invalid(ValueError):
    pass

def require(value, message):
    if not value:
        raise Invalid(message)

def source_owner(url):
    require(isinstance(url, str) and len(url) <= 1024, 'invalid source URL')
    p = urllib.parse.urlsplit(url)
    require(p.scheme == 'https' and p.netloc in ('github.com', 'raw.githubusercontent.com') and not p.query and not p.fragment, 'source must be a public GitHub release or raw file')
    require(re.fullmatch(r'/[A-Za-z0-9._~%/-]+', p.path) and '..' not in p.path, 'invalid source path')
    parts = p.path.strip('/').split('/')
    require(len(parts) >= 4 and re.fullmatch(r'[A-Za-z0-9-]{1,39}', parts[0]) and parts[-1].endswith('.umc'), 'source must be a .umc file')
    if p.netloc == 'github.com':
        require(len(parts) == 6 and parts[2:4] == ['releases', 'download'], 'use a GitHub release asset URL')
    return parts[0].lower()

class DownloadRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        p = urllib.parse.urlsplit(newurl)
        require(p.scheme == 'https' and p.hostname in ('github.com', 'raw.githubusercontent.com', 'release-assets.githubusercontent.com', 'objects.githubusercontent.com') and p.port in (None, 443) and not p.username, 'unexpected download redirect')
        return super().redirect_request(req, fp, code, msg, headers, newurl)

def download(url):
    source_owner(url)
    request = urllib.request.Request(url, headers={'User-Agent': 'UMC-registry', 'Accept': 'application/octet-stream'})
    with urllib.request.build_opener(DownloadRedirects()).open(request, timeout=20) as response:
        length = response.headers.get('Content-Length')
        require(not length or int(length) <= MAX_SOURCE, 'script exceeds 256 KB')
        body = response.read(MAX_SOURCE + 1)
    require(len(body) <= MAX_SOURCE, 'script exceeds 256 KB')
    return body

def published_download(url):
    # New release assets can briefly return 404 from the public download endpoint.
    # Verify the public bytes before adding a package to the catalog.
    for delay in (0, 1, 2, 4, 8, 16, 16, 16):
        if delay:
            time.sleep(delay)
        try:
            return download(url)
        except urllib.error.HTTPError as e:
            if e.code != 404:
                raise
    raise Invalid('published asset is not available yet: ' + url)

def manifest(path, body):
    match = PATH.fullmatch(path)
    require(match and len(body) <= MAX_MANIFEST, 'invalid manifest path or size')
    def unique(pairs):
        result = {}
        for key, value in pairs:
            require(key not in result, 'duplicate JSON key')
            result[key] = value
        return result
    data = json.loads(body, object_pairs_hook=unique)
    require(isinstance(data, dict) and set(data) == {'id', 'version', 'sha256', 'source'}, 'manifest needs exactly id, version, sha256, source')
    require(data['id'] == match[1] and data['version'] == match[2] and len(match[1]) <= 64 and '..' not in match[2], 'path and plugin identity differ')
    require(isinstance(data['sha256'], str) and re.fullmatch('[0-9a-f]{64}', data['sha256']), 'invalid SHA-256')
    source_owner(data['source'])
    return data

def limits():
    import resource
    resource.setrlimit(resource.RLIMIT_CPU, (3, 3))
    resource.setrlimit(resource.RLIMIT_AS, (256 * 1024 * 1024,) * 2)
    resource.setrlimit(resource.RLIMIT_FSIZE, (1024 * 1024,) * 2)
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))

def check_script(data, binary, downloader=download):
    body = downloader(data['source'])
    require(len(body) <= MAX_SOURCE and hashlib.sha256(body).hexdigest() == data['sha256'], 'source size or SHA-256 mismatch')
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / 'plugin.umc'
        path.write_bytes(body)
        result = subprocess.run([str(Path(binary).resolve()), 'check', str(path), '--json'], capture_output=True, timeout=5,
                                env={'PATH': os.defpath}, preexec_fn=limits if os.name == 'posix' else None)
        require(result.returncode == 0 and len(result.stdout) <= MAX_SOURCE, 'compiler rejected plugin')
        meta = json.loads(result.stdout)
    require(meta['id'] == data['id'] and meta['version'] == data['version'] and meta['sha256'] == data['sha256'], 'compiler identity mismatch')
    for field in ('name', 'description'):
        require(isinstance(meta[field], str) and len(meta[field]) <= 2048 and all(ord(c) >= 32 for c in meta[field]), 'invalid display metadata')
    return meta, body

def read_manifests(root):
    result = {}
    for path in sorted((Path(root) / 'plugins').glob('*/*.json')):
        key = path.relative_to(root).as_posix()
        require(not path.is_symlink(), 'manifest symlinks are forbidden')
        result[key] = manifest(key, path.read_bytes())
    require(len(result) <= 4096, 'catalog limit exceeded')
    return result

def validate_graph(records, changed, binary, downloader=download):
    by_key = {(v['id'], v['version']): v for v in records.values()}
    owners = {}
    for data in records.values():
        owner = source_owner(data['source'])
        require(data['id'] not in owners or owners[data['id']] == owner, 'plugin ID belongs to another owner')
        owners[data['id']] = owner
    checked = {}; active = set()
    def visit(key, depth=0):
        require(depth <= 16 and len(checked) < 128, 'dependency limit exceeded')
        require(key not in active, 'dependency cycle')
        if key in checked:
            return checked[key][0]
        require(key in by_key, 'dependency version missing from catalog')
        active.add(key)
        meta, body = check_script(by_key[key], binary, downloader)
        checked[key] = meta, body
        resolved_versions = {key[0]: key[1]}
        def gather(dep_key, seen):
            require(dep_key[0] not in resolved_versions or resolved_versions[dep_key[0]] == dep_key[1], 'conflicting dependency versions')
            resolved_versions[dep_key[0]] = dep_key[1]
            if dep_key in seen:
                return
            seen.add(dep_key)
            dep_meta = visit(dep_key, depth + 1)
            require(set(dep_meta['permissions']) <= set(meta['permissions']), 'dependency permissions exceed caller permissions')
            for d in dep_meta['dependencies']:
                gather((d['id'], d['version']), seen)
        for dep in meta['dependencies']:
            gather((dep['id'], dep['version']), set())
        active.remove(key)
        return meta
    for path in changed:
        data = records[path]
        visit((data['id'], data['version']))
    return checked

class GitHub:
    def __init__(self, repo=None):
        self.repo = repo or os.environ['GITHUB_REPOSITORY']
        require(re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', self.repo), 'invalid repository')
        self.token = os.environ.get('GH_TOKEN', '')
    def request(self, path, data=None, method=None, upload=False):
        host = 'https://uploads.github.com' if upload else 'https://api.github.com'
        url = host + path
        headers = {'Accept': 'application/vnd.github+json', 'User-Agent': 'UMC-registry', 'X-GitHub-Api-Version': '2022-11-28'}
        if self.token:
            headers['Authorization'] = 'Bearer ' + self.token
        if isinstance(data, bytes):
            headers['Content-Type'] = 'application/octet-stream'; body = data
        else:
            headers['Content-Type'] = 'application/json'; body = None if data is None else json.dumps(data).encode()
        with urllib.request.urlopen(urllib.request.Request(url, body, headers, method=method), timeout=30) as response:
            raw = response.read()
            return json.loads(raw) if raw else None
    def api(self, path, **kwargs):
        return self.request('/repos/' + self.repo + path, **kwargs)
    def files(self, number):
        rows = []
        for page in range(1, 4):
            batch = self.api(f'/pulls/{number}/files?per_page=100&page={page}')
            rows.extend(batch)
            if len(batch) < 100:
                break
        require(1 <= len(rows) <= 16, 'submit 1–16 new manifests per PR')
        require(all(row['status'] == 'added' and PATH.fullmatch(row['filename']) and row['changes'] <= 100 for row in rows), 'automatic acceptance only adds plugin manifests')
        return rows

def validate_pr(root, binary, number, api=None):
    api = api or GitHub()
    pr = api.api(f'/pulls/{number}')
    require(pr['state'] == 'open' and not pr['draft'] and pr['base']['ref'] == 'main', 'PR must be open, ready and target main')
    base = api.api('/git/ref/heads/main')['object']['sha']
    require(subprocess.check_output(['git', '-C', str(root), 'rev-parse', 'HEAD'], text=True).strip() == base, 'base changed; rerun validation')
    records = read_manifests(root); changes = api.files(number)
    head = pr['head']['sha']; author = pr['user']['login'].lower()
    for row in changes:
        path = row['filename']; require(path not in records, 'existing versions are immutable')
        content = api.api('/contents/' + path + '?ref=' + head)
        require(content['type'] == 'file' and content['encoding'] == 'base64' and content['size'] <= MAX_MANIFEST, 'invalid manifest file')
        data = manifest(path, base64.b64decode(content['content'], validate=False))
        require(source_owner(data['source']) == author, 'source must belong to the PR author')
        records[path] = data
    validate_graph(records, [row['filename'] for row in changes], binary)
    with open(os.environ['GITHUB_OUTPUT'], 'a') as output:
        output.write(f'head={head}\nbase={base}\nnumber={number}\n')
    print(f'Validated {len(changes)} manifest(s); no submitted code was executed.')

def merge_pr(number, head, base):
    api = GitHub(); pr = api.api(f'/pulls/{number}')
    require(pr['head']['sha'] == head and pr['state'] == 'open' and not pr['draft'] and pr['base']['ref'] == 'main', 'PR changed since validation')
    require(api.api('/git/ref/heads/main')['object']['sha'] == base, 'catalog changed; rerun validation')
    api.files(number)
    result = api.api(f'/pulls/{number}/merge', method='PUT', data={'sha': head, 'merge_method': 'squash', 'commit_title': f'Publish plugin submission #{number}'})
    require(result['merged'], 'GitHub declined the merge')
    # Token-triggered merges do not trigger another push workflow. Start publishing explicitly.
    api.api('/actions/workflows/publish.yml/dispatches', method='POST', data={'ref': 'main'})

def publish(root, binary, output):
    records = read_manifests(root); api = GitHub(); output = Path(output); output.mkdir(parents=True, exist_ok=True)
    checked = {}; releases = {}
    def release_for(data):
        tag = data['id'] + '-' + data['version']
        if tag not in releases:
            try:
                releases[tag] = api.api('/releases/tags/' + tag)
            except urllib.error.HTTPError as e:
                if e.code != 404:
                    raise
                releases[tag] = None
        return releases[tag]
    # Check the API before downloading; probing a nonexistent public asset can cache a 404.
    # Existing mirrored assets keep working when the author's original source disappears.
    def mirrored(url):
        data = next(v for v in records.values() if v['source'] == url)
        release = release_for(data)
        if release and any(a['name'] == data['id'] + '.umc' for a in release['assets']):
            target = f"https://github.com/{api.repo}/releases/download/{data['id']}-{data['version']}/{data['id']}.umc"
            return published_download(target)
        return download(url)
    for path in records:
        checked.update(validate_graph(records, [path], binary, mirrored))
    index = []; catalog = []
    for data in records.values():
        meta, body = checked[(data['id'], data['version'])]
        tag = data['id'] + '-' + data['version']; filename = data['id'] + '.umc'
        release = release_for(data)
        if release is None:
            release = api.api('/releases', method='POST', data={'tag_name': tag, 'name': tag, 'body': 'Sandboxed UMC plugin. SHA-256: ' + data['sha256'], 'make_latest': 'false'})
            releases[tag] = release
        asset = next((a for a in release['assets'] if a['name'] == filename), None)
        if not asset:
            api.request(f"/repos/{api.repo}/releases/{release['id']}/assets?name={filename}", data=body, method='POST', upload=True)
        url = f'https://github.com/{api.repo}/releases/download/{tag}/{filename}'
        print('Verifying published asset: ' + tag, flush=True)
        require(hashlib.sha256(published_download(url)).hexdigest() == data['sha256'], 'published asset hash differs')
        display = [meta['name'], meta['description'], source_owner(data['source'])]
        fields = '\t'.join(urllib.parse.quote(value, safe='') if value else '-' for value in display)
        index.append(f"{data['id']}\t{data['version']}\t{data['sha256']}\t{url}\t{fields}\n")
        catalog.append(dict(meta, source=data['source'], download=url, owner=source_owner(data['source'])))
    (output / 'index.tsv').write_text(''.join(index))
    (output / 'catalog.json').write_text(json.dumps(catalog, indent=2) + '\n')

def main():
    p = argparse.ArgumentParser(); p.add_argument('action', choices=('validate', 'merge', 'publish')); p.add_argument('--root', default='.'); p.add_argument('--umc'); p.add_argument('--number', type=int); p.add_argument('--head'); p.add_argument('--base'); p.add_argument('--out', default='dist')
    args = p.parse_args()
    if args.action == 'validate': validate_pr(args.root, args.umc, args.number)
    elif args.action == 'merge': merge_pr(args.number, args.head, args.base)
    else: publish(args.root, args.umc, args.out)

if __name__ == '__main__':
    try: main()
    except (Invalid, ValueError, OSError, subprocess.SubprocessError) as e:
        print('Registry check failed: ' + str(e), file=sys.stderr); sys.exit(1)
