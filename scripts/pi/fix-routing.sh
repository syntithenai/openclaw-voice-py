#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
"$ROOT_DIR/ssh-script.sh" <<'REMOTE'
python3 - <<'PY'
import json, urllib.request
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
target_stream=(
    next((s for s in status['streams'] if s.get('id') == 'Pi Two'), None)
    or next((s for s in status['streams'] if s.get('id','').startswith('PipeWire')), None)
)
if not target_stream:
    raise SystemExit('No Snapcast stream found (expected Pi Two or PipeWire-*)')

pi_group=next((g for g in status['groups'] if any(c.get('id')=='Pi-Two' for c in g.get('clients',[]))), None)
if not pi_group:
    raise SystemExit('No Pi-Two group found')

rpc('Group.SetStream', {'id': pi_group['id'], 'stream_id': target_stream['id']}, rid=2)
rpc('Group.SetMute', {'id': pi_group['id'], 'mute': False}, rid=3)
rpc('Group.SetClients', {'id': pi_group['id'], 'clients': [{'id': 'Pi-Two', 'config': {'volume': {'percent': 100, 'muted': False}}}]}, rid=4)

updated=rpc('Server.GetStatus', rid=5)['result']['server']
updated_group=next((g for g in updated['groups'] if any(c.get('id')=='Pi-Two' for c in g.get('clients',[]))), None)
client=next((c for c in (updated_group.get('clients',[]) if updated_group else []) if c.get('id')=='Pi-Two'), {})
print('target_stream=', target_stream.get('id'))
print('pi_group_stream=', updated_group.get('stream_id') if updated_group else None)
print('pi_connected=', client.get('connected'))
print('pi_volume=', client.get('config',{}).get('volume',{}).get('percent'))
PY
REMOTE
