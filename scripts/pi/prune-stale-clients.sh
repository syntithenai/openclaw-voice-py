#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
"$ROOT_DIR/ssh-script.sh" <<'REMOTE'
python3 - <<'PY'
import json
import urllib.request

url='http://127.0.0.1:1780/jsonrpc'

def rpc(method, params=None, rid=1):
    payload={'id': rid, 'jsonrpc': '2.0', 'method': method}
    if params is not None:
        payload['params'] = params
    req=urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers={'Content-Type': 'application/json'},
    )
    with urllib.request.urlopen(req, timeout=5) as r:
        return json.loads(r.read().decode())

status=rpc('Server.GetStatus', rid=1)['result']['server']
stale_ids=[]
for group in status.get('groups', []):
    for client in group.get('clients', []):
        cid=client.get('id')
        if cid and not client.get('connected'):
            stale_ids.append(cid)

changes=0
for cid in stale_ids:
    res=rpc('Server.DeleteClient', {'id': cid}, rid=100 + changes)
    if 'error' not in res:
        changes += 1

updated=rpc('Server.GetStatus', rid=2)['result']['server']
print(f'changes={changes}')
for group in updated.get('groups', []):
    ids=[c.get('id') for c in group.get('clients', [])]
    stream_id=group.get('stream_id')
    print(f'group={group.get("id")} stream={stream_id} clients={ids}')
PY
REMOTE
