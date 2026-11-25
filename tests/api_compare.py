#!/usr/bin/env python3
"""Side-by-side comparison of two lg-api instances (original jar vs. patched jar).

Sends the same requests to both and compares count, every field of the result list (md5),
statistics groups and exported CSV (md5), plus timings. Use it in a local test environment
(see docs/operations.md "Local test environment"), not against production.

    LG_USER=... LG_PASS=... python api_compare.py [ORIG_BASE] [NEW_BASE]

Defaults: http://localhost:15633/lg-api (original) and http://localhost:15634/lg-api (patched).
Requires: requests
"""
import hashlib
import io
import json
import os
import sys
import time

import requests

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
H = {'X-Requested-With': 'XMLHttpRequest'}
ORIG = sys.argv[1] if len(sys.argv) > 1 else 'http://localhost:15633/lg-api'
NEW = sys.argv[2] if len(sys.argv) > 2 else 'http://localhost:15634/lg-api'


def session(base):
    s = requests.Session()
    s.headers.update(H)
    r = s.post(base + '/auth/login', json={'loginId': os.environ['LG_USER'], 'password': os.environ['LG_PASS']})
    assert r.status_code == 200, r.text
    return s


def call(s, base, path, params):
    t = time.time()
    url = base + path
    r = s.post(url, data=params, timeout=600) if path.endswith(('.csv', '.map')) else s.get(url, params=params, timeout=600)
    return r.status_code, r.text, time.time() - t


def summ(path, code, text):
    if code != 200:
        return 'HTTP%d %s' % (code, text[:120])
    if path.endswith('/count'):
        return 'count=' + text.strip()
    if path.endswith(('.csv', '.map')):
        return 'lines=%d md5=%s' % (len(text.splitlines()), hashlib.md5(text.encode()).hexdigest()[:8])
    try:
        j = json.loads(text)
    except ValueError:
        return 'raw:' + text[:80]
    data = j.get('data', j) if isinstance(j, dict) else j
    if isinstance(data, list):
        if data and isinstance(data[0], dict) and 'id' in data[0] and 'count' in data[0]:
            return 'groups=%d top=%s' % (len(data), [(d['id'], d['count']) for d in data[:4]])
        if data and isinstance(data[0], dict):
            keys = [k for k in ('id', 'page_no', 'section_name') if k in data[0]]
            full = hashlib.md5(json.dumps(data, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:8]
            return 'rows=%d md5(all fields)=%s first=%s' % (len(data), full, [tuple(d.get(k) for k in keys) for d in data[:2]])
        return 'rows=%d' % len(data)
    return 'json:' + text[:100]


LIST = {'page': 1, 'rows': 25, 'sortField': 'book_year', 'sortOrder': 'asc', 'isTurbo': 0}
cases = []
kws = ['舉人', '程伯祥', '李安仁', '李安仁 高攀龍', '+舉人 +知縣', '舉人 -知縣', '加○級○封', '史○蒼○以子',
       '周燦', '水利', "李安仁'", '李安仁|高攀龍', '(李安仁)', '^李安仁$', '李安仁"', '之']
for k in kws:
    cases.append(('/pagetexts/count', {'keywords': k, 'filters': '', 'isTurbo': 0}))
for f in ['book_name:江南通志;', 'dynasty:清;', 'dynasty:清,明;source:eastview;']:
    cases.append(('/pagetexts/count', {'keywords': '舉人', 'filters': f, 'isTurbo': 0}))
for k in ['舉人', '李安仁 高攀龍', '周燦']:
    cases.append(('/pagetexts', dict(LIST, keywords=k, filters='')))
for sf in ['book_name', 'section_name', 'dynasty', 'page_no']:
    cases.append(('/pagetexts', dict(LIST, keywords='李安仁', filters='', sortField=sf)))
for pg in (1, 2):
    cases.append(('/pagetexts', dict(LIST, keywords='舉人', filters='book_name:江南通志;', page=pg,
                                     sortField='page_no', sortOrder='desc')))
cases.append(('/pagetexts', dict(LIST, keywords='李安仁 高攀龍', filters='', get_section_ids=1)))
for cf in ['dynasty', 'book_name', 'level1', 'source', 'short_section_name']:
    cases.append(('/pagetexts/statistics', {'keywords': '舉人', 'filters': '', 'countField': cf,
                                            'sortField': 'count', 'sortOrder': 'desc', 'isTurbo': 0}))
cases.append(('/pagetexts/drillDownStatistics', {'keywords': '舉人', 'filters': '', 'countField': 'book_name',
                                                 'drillDownParams': 'dynasty:清;', 'sortField': 'count',
                                                 'sortOrder': 'desc', 'isTurbo': 0}))
cases.append(('/pagetexts/export.csv', {'keywords': '李安仁 高攀龍', 'filters': '', 'isTurbo': 0, 'excludes': '', 'fileName': 'test'}))
cases.append(('/pagetexts/export.csv', {'keywords': '江南', 'filters': 'book_name:江南通志;', 'isTurbo': 0, 'excludes': '', 'fileName': 'test'}))
cases.append(('/comparison', dict(LIST, keywords='周燦', filters='')))


def main():
    a, b = session(ORIG), session(NEW)
    tot = [0.0, 0.0]
    ndiff = 0
    for path, p in cases:
        ca, ta, sa = call(a, ORIG, path, p)
        cb, tb, sb = call(b, NEW, path, p)
        tot[0] += sa
        tot[1] += sb
        x, y = summ(path, ca, ta), summ(path, cb, tb)
        flag = 'SAME' if x == y else 'DIFF'
        ndiff += flag == 'DIFF'
        extra = {k: v for k, v in p.items() if k in ('filters', 'countField', 'page', 'sortField') and v}
        print('%s %-30s %-12s %s  orig %.2fs / new %.2fs' % (flag, path, p['keywords'], extra or '', sa, sb))
        if flag == 'DIFF':
            print('     orig:', x)
            print('     new :', y)
        else:
            print('     ', x[:160])
    print('TOTAL orig %.1fs  new %.1fs  | %d DIFF of %d (expected DIFFs: see README "Behaviour differences")'
          % (tot[0], tot[1], ndiff, len(cases)))


if __name__ == '__main__':
    main()
