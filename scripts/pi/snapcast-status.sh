#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
"$ROOT_DIR/ssh-script.sh" <<'REMOTE'
python3 - <<'PY'
import json, urllib.request
url='http://127.0.0.1:1780/jsonrpc'
req=urllib.request.Request(
    url,
    data=b'{"id":1,"jsonrpc":"2.0","method":"Server.GetStatus"}',
    headers={'Content-Type': 'application/json'},
)
with urllib.request.urlopen(req, timeout=5) as r:
    d=json.loads(r.read().decode())['result']['server']

group=next((g for g in d['groups'] if any(c.get('id')=='Pi-Two' for c in g.get('clients',[]))), None)
client=next((c for c in (group.get('clients',[]) if group else []) if c.get('id')=='Pi-Two'), {})

print('streams=')
for s in d['streams']:
    print('  - {}:{}'.format(s.get('id'), s.get('status')))
print('pi_group_stream=', group.get('stream_id') if group else None)
print('pi_connected=', client.get('connected'))
print('pi_volume=', client.get('config',{}).get('volume',{}).get('percent'))
PY
REMOTE
