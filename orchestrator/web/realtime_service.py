"""Embedded HTTP + WebSocket service for realtime voice UI telemetry."""

from __future__ import annotations

import asyncio
from collections import deque
import json
import logging
import math
import ssl
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any, Awaitable, Callable

import websockets

logger = logging.getLogger("orchestrator.web.realtime")


def _build_ui_html(
    ws_port: int,
    mic_starts_disabled: bool = True,
    audio_authority: str = "native",
    server_instance_id: str = "",
) -> str:
    mic_disabled_js = "true" if mic_starts_disabled else "false"
    return f"""<!doctype html>
<html lang="en" class="dark">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>OpenClaw Voice</title>
  <script src="https://cdn.tailwindcss.com"></script>
    <script src="https://cdn.jsdelivr.net/npm/marked/marked.min.js"></script>
    <script src="https://cdn.jsdelivr.net/npm/dompurify/dist/purify.min.js"></script>
  <script>
        if (window.tailwind) {{
            tailwind.config = {{
                darkMode: 'class',
                theme: {{ extend: {{ colors: {{
                    gray: {{ 950: '#0f1117', 900: '#171b26', 800: '#242a3b', 700: '#2f3548' }}
                }} }} }}
            }};
        }}
  </script>
  <style>
    .mic-btn {{ transition: border-width 80ms linear, background-color 200ms ease; }}
    .chat-msg {{ animation: fadeIn 0.18s ease; }}
    @keyframes fadeIn {{ from {{ opacity:0; transform:translateY(4px); }} to {{ opacity:1; transform:none; }} }}
    ::-webkit-scrollbar {{ width:6px; }} ::-webkit-scrollbar-track {{ background:transparent; }}
    ::-webkit-scrollbar-thumb {{ background:#3e4d8a; border-radius:999px; }}
    .md-content h1,.md-content h2,.md-content h3 {{ font-weight:600; margin:0.5em 0 0.25em; line-height:1.3; }}
    .md-content h1 {{ font-size:1.2em; }} .md-content h2 {{ font-size:1.1em; }} .md-content h3 {{ font-size:1em; }}
    .md-content p {{ margin:0.35em 0; }}
    .md-content ul,.md-content ol {{ padding-left:1.4em; margin:0.35em 0; }}
    .md-content li {{ margin:0.1em 0; }}
    .md-content code {{ background:#1e2436; border-radius:3px; padding:1px 4px; font-size:0.85em; font-family:monospace; }}
    .md-content pre {{ background:#1e2436; border-radius:6px; padding:0.6em 0.8em; overflow-x:auto; margin:0.45em 0; }}
    .md-content pre code {{ background:none; padding:0; }}
    .md-content blockquote {{ border-left:3px solid #4b5a8a; padding-left:0.8em; color:#9aa3c0; margin:0.35em 0; }}
    .md-content a {{ color:#7ca3ff; text-decoration:underline; }}
    .md-content table {{ border-collapse:collapse; width:100%; margin:0.45em 0; }}
    .md-content th,.md-content td {{ border:1px solid #3f4a64; padding:0.25em 0.5em; text-align:left; }}
    .md-content th {{ background:#242a3b; font-weight:600; }}
    .md-content hr {{ border:none; border-top:1px solid #3f4a64; margin:0.5em 0; }}
    .md-content>*:first-child {{ margin-top:0; }} .md-content>*:last-child {{ margin-bottom:0; }}
  </style>
</head>
<body class="bg-gray-950 text-gray-100 h-screen flex flex-col overflow-hidden">

<!-- HEADER -->
<header class="flex items-center justify-between px-3 h-14 bg-gray-900 border-b border-gray-800 flex-none gap-2 z-10">
  <div class="relative flex-none" id="menuWrap">
    <button id="menuBtn" class="p-2 rounded-lg hover:bg-gray-700 transition-colors" title="Menu">
      <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
        <line x1="3" y1="6" x2="21" y2="6"/><line x1="3" y1="12" x2="21" y2="12"/><line x1="3" y1="18" x2="21" y2="18"/>
      </svg>
    </button>
    <div id="menuDropdown" class="hidden absolute left-0 top-11 w-44 bg-gray-800 border border-gray-700 rounded-xl shadow-xl z-50 py-1">
      <a href="#/home"  class="flex items-center gap-2 px-4 py-2.5 hover:bg-gray-700 text-sm" data-nav="home">🏠 Home</a>
      <a href="#/music" class="flex items-center gap-2 px-4 py-2.5 hover:bg-gray-700 text-sm" data-nav="music">🎵 Music</a>
    </div>
  </div>

  <div id="musicHeader" class="hidden flex-1 flex items-center gap-2 min-w-0 px-2">
    <button id="musicToggleBtn" class="flex-none w-8 h-8 flex items-center justify-center rounded-full bg-gray-700 hover:bg-gray-600 transition-colors text-xs" title="Play/Pause">&#9654;</button>
    <div class="min-w-0">
      <div id="musicTitle"  class="text-sm font-medium truncate text-white">&#8212;</div>
      <div id="musicArtist" class="text-xs text-gray-400 truncate">&#8212;</div>
    </div>
  </div>

    <div class="relative flex items-center flex-none gap-3" id="micControlWrap">
        <button id="micBtn" class="mic-btn w-11 h-11 flex items-center justify-center font-bold text-xl rounded-full border-4 border-transparent" title="Microphone">&#127908;</button>
        <button id="micMenuBtn" class="w-7 h-7 flex items-center justify-center rounded-full text-gray-300 bg-gray-800/60 border border-gray-700 hover:bg-gray-700 transition-colors" title="Microphone options">&#9662;</button>
        <div id="micControlDropdown" class="hidden absolute right-0 top-12 w-72 bg-gray-800 border border-gray-700 rounded-xl shadow-xl z-50 p-2 flex flex-col gap-2">
            <div class="flex items-center justify-between px-2 py-1">
                <span class="text-xs text-gray-300">Mute TTS output</span>
                <button id="ttsMuteToggle" data-action="toggle-tts-mute" class="relative w-11 h-6 rounded-full bg-gray-700 border border-gray-600 transition-colors"><span class="absolute top-1 left-1 w-4 h-4 rounded-full bg-gray-200 transition-transform"></span></button>
            </div>
            <div class="flex items-center justify-between px-2 py-1">
                <span class="text-xs text-gray-300">Browser audio streaming</span>
                <button id="browserAudioToggle" data-action="toggle-browser-audio" class="relative w-11 h-6 rounded-full bg-gray-700 border border-gray-600 transition-colors"><span class="absolute top-1 left-1 w-4 h-4 rounded-full bg-gray-200 transition-transform"></span></button>
            </div>
            <div class="flex items-center justify-between px-2 py-1">
                <span class="text-xs text-gray-300">Continuous mode</span>
                <button id="continuousModeToggle" data-action="toggle-continuous-mode" class="relative w-11 h-6 rounded-full bg-gray-700 border border-gray-600 transition-colors"><span class="absolute top-1 left-1 w-4 h-4 rounded-full bg-gray-200 transition-transform"></span></button>
            </div>
        </div>
    </div>
</header>

<div id="wsDebugBanner" class="flex-none px-3 py-1 text-[11px] bg-gray-900/95 border-b border-gray-800 text-gray-300 truncate">
    WS: initializing...
</div>
<div id="timerBar" class="hidden flex-none px-3 py-2 bg-amber-950/60 border-b border-amber-800/50 flex gap-2 flex-wrap items-center text-sm"></div>

<main id="main" class="flex-1 overflow-y-auto min-h-0"></main>
<div id="chatComposerDock" class="hidden flex-none border-t border-gray-800 bg-gray-900 px-3 py-2 z-10">
    <form id="chatComposer" class="flex items-center gap-2">
        <input id="chatInput" type="text" placeholder="Type a message" class="flex-1 min-w-0 rounded-lg bg-gray-800 border border-gray-700 px-3 py-2 text-sm text-gray-100 placeholder-gray-400 focus:outline-none focus:ring-2 focus:ring-blue-600" />
        <button id="chatSendBtn" type="submit" class="px-3 py-2 rounded-lg text-sm bg-blue-700 hover:bg-blue-600 transition-colors">Send</button>
    </form>
</div>

<script>
const WS_PORT = {ws_port};
const MIC_STARTS_DISABLED = {mic_disabled_js};
const AUDIO_AUTHORITY = '{audio_authority}';
const SERVER_INSTANCE_ID = '{server_instance_id}';

const S = {{
  ws:null, wsConnected:false,
  micEnabled:!MIC_STARTS_DISABLED,
  voice_state:'idle', wake_state:'asleep', tts_playing:false, mic_rms:0,
  chat:[], music:{{state:'stop',title:'',artist:'',queue_length:0,elapsed:0,duration:0,position:-1}},
    chatThreads:[], activeChatId:'active', selectedChatId:'active', chatSidebarOpen:true,
  musicQueue:[], timers:[], page:'home',
    audioCtx:null, mediaStream:null, processor:null, lastLevel:0,
    ttsMuted:false, browserAudioEnabled:true, continuousMode:false,
    pendingChatSends:new Set(), nextClientMsgId:1,
    wsDebug:{{ status:'init', lastCloseCode:null, lastCloseReason:'', lastError:'' }},
    wsManualDisconnect:false, wsReconnectTimer:null,
    captureRetryTimer:null,
    lastAudioInputCount:null,
}};

function applyToggle(btn, enabled){{
        if(!btn) return;
        btn.classList.toggle('bg-emerald-700', !!enabled);
        btn.classList.toggle('border-emerald-500', !!enabled);
        btn.classList.toggle('bg-gray-700', !enabled);
        btn.classList.toggle('border-gray-600', !enabled);
        const knob=btn.querySelector('span');
        if(knob) knob.style.transform = enabled ? 'translateX(18px)' : 'translateX(0px)';
        btn.setAttribute('aria-checked', enabled ? 'true' : 'false');
}}

function applyMicControlToggles(){{
        applyToggle(document.getElementById('ttsMuteToggle'), !!S.ttsMuted);
        applyToggle(document.getElementById('browserAudioToggle'), !!S.browserAudioEnabled);
        applyToggle(document.getElementById('continuousModeToggle'), !!S.continuousMode);
}}

function updateWsDebugBanner(){{
    const el=document.getElementById('wsDebugBanner');
    if(!el) return;
    const url=wsUrl();
    const s=S.wsDebug||{{}};
    const parts=['WS '+(s.status||'unknown'), url];
    if(s.lastCloseCode!==null&&s.lastCloseCode!==undefined){{
        let closeTxt='close='+s.lastCloseCode;
        if(s.lastCloseReason) closeTxt+=' ('+s.lastCloseReason+')';
        parts.push(closeTxt);
    }}
    if(s.lastError) parts.push('err='+s.lastError);
    el.textContent=parts.join(' • ');
}}

function updateMicInteractivity(){{
    const btn=document.getElementById('micBtn');
    const menuBtn=document.getElementById('micMenuBtn');
    if(!btn) return;
    const connected=!!S.wsConnected;
    btn.disabled=!connected;
    btn.classList.toggle('opacity-60', !connected);
    btn.classList.toggle('cursor-not-allowed', !connected);
    if(menuBtn){{
      menuBtn.disabled=!connected;
      menuBtn.classList.toggle('opacity-60', !connected);
      menuBtn.classList.toggle('cursor-not-allowed', !connected);
    }}
    btn.classList.toggle('bg-gray-700', !connected);
    btn.classList.toggle('border-gray-500', !connected);
    if(!connected){{
        btn.title='Microphone disabled: WebSocket not connected';
    }} else {{
        btn.title='Microphone';
    }}
}}

function updateChatComposerState(){{
    const input=document.getElementById('chatInput');
    const sendBtn=document.getElementById('chatSendBtn');
    const isPending=S.pendingChatSends.size>0;
    if(input){{
        input.disabled=isPending;
        input.placeholder=isPending?'Sending...':'Type a message';
    }}
    if(sendBtn){{
        sendBtn.disabled=isPending;
        sendBtn.classList.toggle('opacity-60',isPending);
        sendBtn.classList.toggle('cursor-not-allowed',isPending);
        sendBtn.textContent=isPending?'Sending...':'Send';
    }}
}}

function getPage(){{ const h=location.hash.replace('#',''); return h==='/music'?'music':'home'; }}
function navigate(p){{ location.hash='#/'+p; }}
window.addEventListener('hashchange',()=>{{ S.page=getPage(); renderPage(); closeMenu(); }});

function closeMenu(){{ document.getElementById('menuDropdown').classList.add('hidden'); }}
function closeMicControlMenu(){{ const m=document.getElementById('micControlDropdown'); if(m) m.classList.add('hidden'); }}
document.getElementById('menuBtn').addEventListener('click',e=>{{ e.stopPropagation(); document.getElementById('menuDropdown').classList.toggle('hidden'); }});
document.getElementById('micMenuBtn').addEventListener('click',e=>{{ e.stopPropagation(); const m=document.getElementById('micControlDropdown'); if(m) m.classList.toggle('hidden'); }});
document.addEventListener('click', e=>{{ closeMenu(); if(!e.target.closest('#micControlWrap')) closeMicControlMenu(); }});
document.addEventListener('keydown', e=>{{ if(e.key==='Escape'){{ closeMenu(); closeMicControlMenu(); }} }});
document.querySelectorAll('[data-nav]').forEach(el=>el.addEventListener('click',e=>{{ e.preventDefault(); navigate(el.dataset.nav); }}));
document.addEventListener('click', e => {{
    const newChatBtn = e.target.closest('[data-action="chat-new"]');
    if (newChatBtn) {{
        sendAction({{type:'chat_new'}});
        S.selectedChatId = 'active';
        renderPage();
        return;
    }}

    const toggleSidebarBtn = e.target.closest('[data-action="chat-sidebar-toggle"]');
    if (toggleSidebarBtn) {{
        S.chatSidebarOpen = !S.chatSidebarOpen;
        renderPage();
        return;
    }}

    const selectThreadBtn = e.target.closest('[data-action="chat-select"]');
    if (selectThreadBtn) {{
        const tid = selectThreadBtn.dataset.threadId || 'active';
        S.selectedChatId = tid;
        renderPage();
        return;
    }}

    const timerBtn = e.target.closest('[data-action="timer-cancel"]');
    if (timerBtn) {{
        sendAction({{type:'timer_cancel', timer_id: timerBtn.dataset.timerId}});
        return;
    }}

    const alarmBtn = e.target.closest('[data-action="alarm-cancel"]');
    if (alarmBtn) {{
        sendAction({{type:'alarm_cancel', alarm_id: alarmBtn.dataset.alarmId}});
        return;
    }}

    const browserAudioToggle = e.target.closest('[data-action="toggle-browser-audio"]');
    if (browserAudioToggle) {{
        e.stopPropagation();
        S.browserAudioEnabled = !S.browserAudioEnabled;
        sendAction({{type:'browser_audio_set', enabled: !!S.browserAudioEnabled}});
        if (!S.browserAudioEnabled) {{
            try{{ if(S.processor) S.processor.disconnect(); }}catch(_){{}}
            try{{ if(S.audioCtx) S.audioCtx.close(); }}catch(_){{}}
            if(S.mediaStream) try{{ S.mediaStream.getTracks().forEach(t=>t.stop()); }}catch(_){{}}
            S.processor=null; S.audioCtx=null; S.mediaStream=null;
        }} else {{
            startBrowserCapture().catch(err=>reportCaptureFailure(err,'browserAudioToggle'));
        }}
        applyMicControlToggles();
        return;
    }}

    const ttsMuteToggle = e.target.closest('[data-action="toggle-tts-mute"]');
    if (ttsMuteToggle) {{
        e.stopPropagation();
        S.ttsMuted = !S.ttsMuted;
        sendAction({{type:'tts_mute_set', enabled: !!S.ttsMuted}});
        applyMicControlToggles();
        return;
    }}

    const continuousToggle = e.target.closest('[data-action="toggle-continuous-mode"]');
    if (continuousToggle) {{
        e.stopPropagation();
        S.continuousMode = !S.continuousMode;
        sendAction({{type:'continuous_mode_set', enabled: !!S.continuousMode}});
        applyMicControlToggles();
        return;
    }}

    const musicRow = e.target.closest('[data-action="music-play-track"]');
    if (musicRow) {{
        sendAction({{type:'music_play_track', position: Number(musicRow.dataset.position)}});
        return;
    }}

    const musicToggle = e.target.closest('[data-action="music-toggle"]');
    if (musicToggle) {{
        sendAction({{type: (S.music.state === 'play' ? 'music_stop' : 'music_toggle')}});
    }}
}});

document.addEventListener('submit', e => {{
    const form = e.target;
    if (!form || form.id !== 'chatComposer') return;
    e.preventDefault();
    const input = document.getElementById('chatInput');
    if (!input) return;
    const text = String(input.value || '').trim();
    if (!text) return;
    const clientMsgId='c'+(S.nextClientMsgId++);
    S.pendingChatSends.add(clientMsgId);
    updateChatComposerState();
    sendAction({{type:'chat_text', text, client_msg_id:clientMsgId}});
    input.value = '';
    if (S.selectedChatId !== 'active') {{
        S.selectedChatId = 'active';
        renderPage();
    }}
}});

function applyMicState(){{
  const btn=document.getElementById('micBtn');
  const rms=S.mic_rms||0;
    if(!S.wsConnected){{
        btn.style.borderWidth='4px';
        btn.classList.remove('bg-red-900','border-red-600','bg-green-900','border-green-500','bg-gray-700','border-gray-500');
        btn.classList.add('bg-gray-700','border-gray-500');
        return;
    }}
  const bw=S.micEnabled?Math.round(2+Math.min(8,Math.pow(rms,0.55)*40)):4;
  btn.style.borderWidth=bw+'px';
  btn.classList.remove('bg-red-900','border-red-600','bg-green-900','border-green-500','bg-gray-700','border-gray-500');
  if(!S.micEnabled) btn.classList.add('bg-red-900','border-red-600');
  else if(S.wake_state==='awake') btn.classList.add('bg-green-900','border-green-500');
  else btn.classList.add('bg-red-900','border-red-600');
}}
document.getElementById('micBtn').addEventListener('click',()=>{{
    if(S.browserAudioEnabled){{
        ensureBrowserCapture().catch((err)=>{{
            S.wsDebug.status='capture_error';
            S.wsDebug.lastError=(err&&err.message)?String(err.message):'browser mic capture failed';
            updateWsDebugBanner();
        }});
    }}
    if(!S.wsConnected || !S.ws || S.ws.readyState!==WebSocket.OPEN){{
        S.wsDebug.status='closed';
        S.wsDebug.lastError='mic click ignored: websocket disconnected';
        updateWsDebugBanner();
        return;
    }}
  sendAction({{type:'mic_toggle'}});
  if(!S.micEnabled){{ S.micEnabled=true; S.wake_state='awake'; }}
  else if(S.wake_state==='awake'){{ S.wake_state='asleep'; }}
  else{{ S.wake_state='awake'; }}
  applyMicState();
}});

document.getElementById('musicToggleBtn').addEventListener('click',()=>sendAction({{type: (S.music.state === 'play' ? 'music_stop' : 'music_toggle')}}));
function applyMusicHeader(){{
  const m=S.music;
  const active=m.state==='play'||(m.state==='pause'&&(m.title||m.queue_length>0));
  document.getElementById('musicHeader').classList.toggle('hidden',!active);
  document.getElementById('musicTitle').textContent=m.title||'\u2014';
  document.getElementById('musicArtist').textContent=m.artist||'\u2014';
    document.getElementById('musicToggleBtn').textContent=m.state==='play'?'\u23f9':'\u25b6';
}}

function renderTimerBar(){{
  const bar=document.getElementById('timerBar');
  if(!S.timers.length){{ bar.classList.add('hidden'); bar.innerHTML=''; return; }}
  bar.classList.remove('hidden');
  bar.innerHTML=S.timers.map(t=>{{
    const rem=Math.max(0,Math.round(t.remaining_seconds));
    const mm=String(Math.floor(rem/60)).padStart(2,'0'),ss=String(rem%60).padStart(2,'0');
        const kind=String(t.kind||'timer').toLowerCase();
        const isAlarm=kind==='alarm';
        const actionAttr=isAlarm?'alarm-cancel':'timer-cancel';
        const idAttr=isAlarm?' data-alarm-id="'+esc(t.id)+'"':' data-timer-id="'+esc(t.id)+'"';
        const icon=isAlarm?'\u23f0':'\u23f1';
        const baseCls=isAlarm
            ?'flex items-center gap-1 px-3 py-1 rounded-full bg-red-800 hover:bg-red-700 text-xs transition-colors'
            :'flex items-center gap-1 px-3 py-1 rounded-full bg-amber-700 hover:bg-amber-600 text-xs transition-colors';
        const label=isAlarm?(t.label||'Alarm'):(t.label||'Timer');
        return '<button class="'+baseCls+'" data-action="'+actionAttr+'"'+idAttr+' title="Click to cancel">'+icon+' '+esc(label)+' '+mm+':'+ss+'</button>';
  }}).join('');
}}

function renderPage(){{
  const main=document.getElementById('main');
    const dock=document.getElementById('chatComposerDock');
    if(S.page==='music'){{
        if(dock) dock.classList.add('hidden');
        renderMusicPage(main);
    }} else {{
        if(dock) dock.classList.remove('hidden');
        renderHomePage(main);
        updateChatComposerState();
    }}
}}

function renderHomePage(main){{
    const sidebarClass = S.chatSidebarOpen ? 'w-72 border-r border-gray-800' : 'w-0 border-r-0';
    const sidebarInnerClass = S.chatSidebarOpen ? 'opacity-100' : 'opacity-0 pointer-events-none';
    const sidebarToggleText = S.chatSidebarOpen ? 'Hide chats' : 'Show chats';
    const selected = S.selectedChatId || 'active';

    main.dataset.page='home';
    main.innerHTML='<div class="w-full h-full flex min-h-0">'
        +'<aside id="chatSidebar" class="'+sidebarClass+' transition-all duration-200 overflow-hidden">'
            +'<div class="'+sidebarInnerClass+' h-full flex flex-col">'
                +'<div class="px-0 py-3 text-xs uppercase tracking-wide text-gray-400 border-b border-gray-800">Previous chats</div>'
                +'<div id="chatThreadList" class="flex-1 overflow-y-auto p-0 space-y-0"></div>'
            +'</div>'
        +'</aside>'
        +'<div class="flex-1 min-w-0 flex flex-col h-full">'
            +'<div class="px-3 py-2 border-b border-gray-800 flex items-center justify-between gap-2">'
                +'<button data-action="chat-sidebar-toggle" class="px-2.5 py-1.5 rounded-lg text-xs bg-gray-800 hover:bg-gray-700 transition-colors">'+sidebarToggleText+'</button>'
                +'<button data-action="chat-new" class="px-3 py-1.5 rounded-lg text-xs bg-blue-700 hover:bg-blue-600 transition-colors">New</button>'
            +'</div>'
            +'<div id="chatArea" class="flex-1 overflow-y-auto px-4 py-4 space-y-3 min-h-0"></div>'
        +'</div>'
    +'</div>';

    renderThreadList(selected);
    renderChatMessages(selected);
}}
function getSelectedMessages(selectedId){{
    if(!selectedId || selectedId==='active') return S.chat;
    const t=(S.chatThreads||[]).find(x=>x.id===selectedId);
    return (t&&Array.isArray(t.messages)) ? t.messages : [];
}}
function renderThreadList(selectedId){{
    const list=document.getElementById('chatThreadList');
    if(!list) return;
    const currentActive = selectedId==='active' ? 'bg-blue-800 text-white' : 'bg-gray-800 text-gray-200 hover:bg-gray-700';
    const items = [];
    items.push('<button data-action="chat-select" data-thread-id="active" class="w-full text-left px-3 py-2 rounded-none text-sm transition-colors border-b border-gray-800 '+currentActive+'">Current chat</button>');
    (S.chatThreads||[]).forEach(t=>{{
        const title = esc((t.title||'Untitled').trim()||'Untitled');
        const activeCls = (t.id===selectedId) ? 'bg-blue-800 text-white' : 'bg-gray-800 text-gray-200 hover:bg-gray-700';
        items.push('<button data-action="chat-select" data-thread-id="'+esc(t.id)+'" class="w-full text-left px-3 py-2 rounded-none transition-colors border-b border-gray-800 '+activeCls+'"><div class="text-sm truncate">'+title+'</div></button>');
    }});
    list.innerHTML = items.join('');
}}
function renderChatMessages(selectedId){{
    const area=document.getElementById('chatArea');
    if(!area) return;
    area.innerHTML='';
    const msgs = getSelectedMessages(selectedId);
        const collated = collateChatMessages(msgs);
        collated.forEach(m=>area.appendChild(mkBubble(m)));
    scrollChat();
}}
function scrollChat(){{ const a=document.getElementById('chatArea'); if(a) a.scrollTop=a.scrollHeight; }}
function collateChatMessages(msgs){{
    const out=[];
    const groupOrder=[];
    const groups={{}};

    const ensureReqBucket=(reqId)=>{{
        const key='req:'+(reqId===null?'_null':reqId);
        if(!groups[key]){{
            groups[key]={{type:'request',reqId,streams:[],steps:[],interim:[],events:[],finals:[]}};
            groupOrder.push(key);
        }}
        return groups[key];
    }};

    (msgs||[]).forEach(m=>{{
        const role=(m&&m.role)||'';
        const reqId=(m.request_id===undefined||m.request_id===null)?null:String(m.request_id);
        if(role==='user'||role==='system'){{
            const key='standalone:'+groupOrder.length;
            groups[key]={{type:'raw',item:m}};
            groupOrder.push(key);
        }} else if(role==='step'){{
            const bucket=ensureReqBucket(reqId);
            bucket.steps.push(m);
            bucket.events.push({{kind:'tool',payload:m}});
        }} else if(role==='interim'){{
            const bucket=ensureReqBucket(reqId);
            bucket.interim.push(m);
            bucket.events.push({{kind:'lifecycle',payload:m}});
        }} else if(role==='assistant'){{
            const segKind=String((m&&m.segment_kind)||'final').toLowerCase();
            if(segKind==='stream'){{ ensureReqBucket(reqId).streams.push(m); }}
            else {{ ensureReqBucket(reqId).finals.push(m); }}
        }}
    }});

    groupOrder.forEach(key=>{{
        const g=groups[key];
        if(!g) return;
        if(g.type==='raw'){{ out.push(g.item); return; }}

        if(g.events.length>0){{
            out.push({{role:'context_group',request_id:g.reqId,events:g.events,steps:g.steps,interim:g.interim}});
        }}

        const validStreams=g.streams.filter(s=>String(s.text||'').trim().length>0);
        if(validStreams.length>0 && g.finals.length===0){{
            out.push({{
                role:'assistant_stream_group',
                request_id:g.reqId,
                source:validStreams[0].source||'assistant',
                text:validStreams.map(s=>String(s.text||'').trim()).filter(Boolean).join(' '),
                segments:validStreams,
                latest:validStreams[validStreams.length-1],
            }});
        }}

        g.finals.forEach(f=>out.push(f));
    }});

    return out;
}}
function mkBubble(m){{
  const d=document.createElement('div');
    const role = (m&&m.role)||'';
    d.className='chat-msg flex '+(role==='user'?'justify-end':'justify-start');

    if(role==='assistant_stream_group'){{
        const wrap=document.createElement('div');
        wrap.className='max-w-xs sm:max-w-sm lg:max-w-md';

        const b=document.createElement('div');
        b.className='px-4 py-2 rounded-2xl rounded-bl-md text-sm leading-relaxed bg-gray-700 text-gray-100';
        b.textContent=(m.text||'');
        wrap.appendChild(b);

        d.appendChild(wrap);
        return d;
            const b=document.createElement('div');
            b.className='px-4 py-2 rounded-2xl rounded-bl-md text-sm leading-relaxed bg-gray-700 text-gray-100 md-content';
            const _rawText=(m.text||'');
            if(typeof marked!=='undefined'&&typeof DOMPurify!=='undefined'&&_rawText){{
                marked.setOptions({{breaks:true,gfm:true}});
                b.innerHTML=DOMPurify.sanitize(marked.parse(_rawText),{{ALLOWED_TAGS:['p','strong','em','h1','h2','h3','h4','h5','h6','ul','ol','li','code','pre','blockquote','a','hr','table','thead','tbody','tr','th','td','br','del','s'],ALLOWED_ATTR:['href','title']}});
            }}else{{
                b.textContent=_rawText;
            }}
        const wrap=document.createElement('div');
        wrap.className='max-w-xs sm:max-w-sm lg:max-w-md space-y-1';

        const parseDetails=(raw)=>{{
            const txt=String(raw||'').trim();
            if(!txt) return null;
            try{{ return JSON.parse(txt); }}catch(_){{ return null; }}
        }};

        const toPretty=(val)=>{{
            if(val===undefined||val===null) return '';
            if(typeof val==='string') return val;
            try{{ return JSON.stringify(val, null, 2); }}catch(_){{ return String(val); }}
        }};

        const summarizeRequest=(toolName, parsed)=>{{
            if(!parsed||typeof parsed!=='object') return '';
            const req=parsed.args!==undefined?parsed.args:(parsed.arguments!==undefined?parsed.arguments:parsed.input);
            if(req===undefined||req===null) return '';
            if(typeof req==='string') return req;
            const lname=String(toolName||'').toLowerCase();
            if(lname.includes('exec')){{
                const cmd=req.command||req.cmd||req.argv||req.script;
                if(cmd!==undefined) return 'command: '+toPretty(cmd);
            }}
            if(lname.includes('read')||lname.includes('write')||lname.includes('file')){{
                const fp=req.filePath||req.path||req.old_path||req.new_path||req.uri;
                if(fp!==undefined) return 'file: '+String(fp);
            }}
            const fp=req.filePath||req.path||req.old_path||req.new_path||req.uri;
            if(fp!==undefined) return 'file: '+String(fp);
            const cmd=req.command||req.cmd||req.argv;
            if(cmd!==undefined) return 'command: '+toPretty(cmd);
            return toPretty(req);
        }};

        const extractResult=(parsed)=>{{
            if(!parsed||typeof parsed!=='object') return '';
            const outCandidates=[parsed.result, parsed.partialResult, parsed.output, parsed.stdout, parsed.stderr, parsed.message, parsed.text, parsed.meta, parsed.content];
            for(const c of outCandidates){{
                const rendered=toPretty(c);
                if(rendered&&String(rendered).trim()) return rendered;
            }}
            return '';
        }};

        const events=(m.events||[]);
        if(events.length>0){{
            const toolGroups=new Map();
            const lifecycleItems=[];
            const reasoningLines=[];
            const seenReasoning=new Set();
            const addReasoning=(line)=>{{
                const s=String(line||'').trim();
                if(!s) return;
                if(seenReasoning.has(s)) return;
                seenReasoning.add(s);
                reasoningLines.push(s);
            }};

            let hasLifecycleStart=false;
            let hasLifecycleEnd=false;
            let anonIdx=0;

            for(const ev of events){{
                const kind=(ev&&ev.kind)||'lifecycle';
                const payload=(ev&&ev.payload)||{{}};

                if(kind==='tool'){{
                    const toolName=String(payload.name||payload.text||'tool').trim()||'tool';
                    const phase=String(payload.phase||'update').toLowerCase();
                    const callId=String(payload.tool_call_id||payload.toolCallId||'').trim();
                    const key=callId||('anon:'+toolName+':'+(++anonIdx));
                    if(!toolGroups.has(key)){{
                        toolGroups.set(key,{{
                            name:toolName,
                            callId,
                            phases:[],
                            command:'',
                            files:[],
                            metas:[],
                            isError:null,
                            requestPreview:'',
                            payloads:[],
                            results:[],
                        }});
                    }}
                    const g=toolGroups.get(key);
                    if(!g.phases.includes(phase)) g.phases.push(phase);

                    const parsed=parseDetails(payload.details);
                    const rawPayload=String(payload.details||'').trim() || (parsed?toPretty(parsed):'');
                    if(rawPayload && !g.payloads.includes(rawPayload)) g.payloads.push(rawPayload);

                    const req=parsed&&typeof parsed==='object'
                        ? (parsed.args!==undefined?parsed.args:(parsed.arguments!==undefined?parsed.arguments:parsed.input))
                        : undefined;
                    if(!g.requestPreview) g.requestPreview=summarizeRequest(toolName, parsed);

                    if(req && typeof req==='object'){{
                        const cmd=req.command||req.cmd||req.argv||req.script;
                        if(cmd!==undefined && !g.command) g.command=typeof cmd==='string'?cmd:toPretty(cmd);
                        const fp=req.filePath||req.path||req.old_path||req.new_path||req.uri;
                        if(fp!==undefined){{
                            const f=String(fp);
                            if(!g.files.includes(f)) g.files.push(f);
                        }}
                    }}

                    if(parsed && typeof parsed==='object'){{
                        if(parsed.meta!==undefined){{
                            const metaTxt=toPretty(parsed.meta);
                            if(metaTxt && !g.metas.includes(metaTxt)) g.metas.push(metaTxt);
                        }}
                        if(parsed.isError!==undefined) g.isError=!!parsed.isError;
                    }}

                    const resultText=extractResult(parsed) || '';
                    if(resultText && !g.results.includes(resultText)) g.results.push(resultText);
                    continue;
                }}

                const name=String(payload.text||payload.name||payload.phase||'lifecycle').toLowerCase();
                const phase=String(payload.phase||'').toLowerCase();
                if(name==='lifecycle' && phase==='start'){{ hasLifecycleStart=true; continue; }}
                if(name==='lifecycle' && phase==='end'){{ hasLifecycleEnd=true; continue; }}

                const parsed=parseDetails(payload.details);
                let details='';
                if(parsed&&typeof parsed==='object'){{
                    const t=parsed.text!==undefined?parsed.text:(parsed.message!==undefined?parsed.message:parsed.result);
                    details=toPretty(t!==undefined?t:parsed);
                }} else {{
                    details=String(payload.details||'').trim();
                }}
                lifecycleItems.push({{name:String(payload.text||payload.name||payload.phase||'lifecycle'), details}});
                addReasoning((payload.text||payload.name||'event')+': '+details);
            }}

            for(const g of toolGroups.values()){{
                for(const mtxt of g.metas) addReasoning(mtxt);
            }}

            const waiting=(hasLifecycleStart && !hasLifecycleEnd);
            const waitingRow=waiting
                ? '<div class="px-2 py-1 border-b border-gray-800/60 text-[11px] text-yellow-300 flex items-center gap-2">'
                    +'<span class="inline-block w-3 h-3 border-2 border-yellow-300/80 border-t-transparent rounded-full animate-spin"></span>'
                    +'<span>waiting…</span>'
                  +'</div>'
                : '';

            const toolRows=[...toolGroups.values()].map(g=>{{
                const success=(g.isError===false || g.phases.includes('result') || g.phases.includes('end'));
                const failed=(g.isError===true);
                const icon=failed?'✕':(success?'✓':'•');
                const phaseLabel=g.phases.filter(Boolean).join(', ')||'update';

                const summaryParts=[];
                if(g.command) summaryParts.push('command: '+g.command);
                if(g.files.length) summaryParts.push('file: '+g.files.join(', '));
                if(!summaryParts.length && g.requestPreview) summaryParts.push(g.requestPreview);
                if(g.isError!==null) summaryParts.push('isError: '+String(g.isError));
                if(g.metas.length) summaryParts.push('meta: '+g.metas.join(' | '));
                const summary=summaryParts.join(' · ') || '(no request details)';

                const resultJoined=(g.results.length?g.results.join('\\n\\n---\\n\\n'):'') || '(no result)';
                const payloadJoined=(g.payloads.length?g.payloads.join('\\n\\n---\\n\\n'):'') || '(no payload)';
                const payloadBlock='<details class="mt-1 rounded border border-gray-700/70 bg-gray-900/40">'
                    +'<summary class="px-1.5 py-1 cursor-pointer text-[10px] text-gray-300 hover:text-gray-100">payload</summary>'
                    +'<pre class="px-1.5 pb-1.5 whitespace-pre-wrap break-words text-[11px] text-gray-300">'+esc(payloadJoined)+'</pre>'
                    +'</details>';
                const resultInline=(resultJoined && resultJoined!=='(no result)')
                    ? '<div class="mt-1 text-[11px] text-gray-200 whitespace-pre-wrap break-words">'+esc(resultJoined)+'</div>'
                    : '';

                return '<div class="px-2 py-1 border-b border-gray-800/60 last:border-b-0">'
                    +'<div class="text-[11px] text-amber-300 flex items-center gap-1"><span class="w-3 text-center">'+icon+'</span><span class="font-mono">'+esc(g.name)+'</span><span class="text-[10px] text-gray-500">'+esc(phaseLabel)+'</span></div>'
                    +'<div class="text-[11px] text-gray-300 mt-0.5">'+esc(summary)+'</div>'
                    +resultInline
                    +payloadBlock
                    +'</div>';
            }}).join('');

            const lifecycleRows=lifecycleItems.map(item=>{{
                const detailsBlock='<details class="mt-1 rounded border border-gray-700/70 bg-gray-900/40">'
                    +'<summary class="px-1.5 py-1 cursor-pointer text-[10px] text-gray-300 hover:text-gray-100">payload</summary>'
                    +'<pre class="px-1.5 pb-1.5 whitespace-pre-wrap break-words text-[11px] text-gray-300">'+esc(item.details||'(no details)')+'</pre>'
                    +'</details>';
                return '<div class="px-2 py-1 border-b border-gray-800/60 last:border-b-0">'
                    +'<div class="text-[10px] text-sky-500 font-mono">'+esc(item.name)+'</div>'
                    +detailsBlock
                    +'</div>';
            }}).join('');

            const interimBlock=reasoningLines.length
                ? '<details class="mt-2 rounded border border-gray-700/70 bg-gray-900/40">'
                    +'<summary class="px-2 py-1 cursor-pointer text-[10px] text-gray-300 hover:text-gray-100">Interim/Reasoning</summary>'
                    +'<pre class="px-2 pb-2 whitespace-pre-wrap break-words text-[11px] text-gray-300">'+esc(reasoningLines.join('\\n'))+'</pre>'
                  +'</details>'
                : '';

            const timeline=document.createElement('div');
            timeline.innerHTML='<details class="rounded-xl bg-gray-900/60 border border-gray-700 text-xs overflow-hidden" open>'
                +'<summary class="px-3 py-1.5 cursor-pointer text-gray-200 hover:text-gray-100 select-none">Execution Sequence</summary>'
                +'<div class="px-2 pb-2 pt-1">'+waitingRow+toolRows+lifecycleRows+interimBlock+'</div>'
                +'</details>';
            wrap.appendChild(timeline);
        }}

        d.appendChild(wrap);
        return d;
    }}

  const b=document.createElement('div');
    b.className='max-w-xs sm:max-w-sm lg:max-w-md px-4 py-2 rounded-2xl text-sm leading-relaxed '+
        (role==='user'?'bg-blue-700 text-white rounded-br-md':
         role==='system'?'bg-gray-700 text-gray-300 italic text-xs':
     'bg-gray-700 text-gray-100 rounded-bl-md');
  b.textContent=m.text||''; d.appendChild(b); return d;
}}

function renderMusicPage(main){{
  main.dataset.page='music';
  const m=S.music, q=S.musicQueue||[];
  const rows=q.map(item=>{{
    const active=item.pos===m.position;
    return '<tr class="hover:bg-gray-800 cursor-pointer '+(active?'bg-gray-800 font-semibold text-green-400':'')+'" data-action="music-play-track" data-position="'+item.pos+'"><td class="px-4 py-2 w-8 text-gray-500 text-xs">'+(item.pos+1)+'</td><td class="px-2 py-2 text-sm truncate max-w-xs">'+esc(item.title||item.file||'\u2014')+'</td><td class="px-2 py-2 text-xs text-gray-400 truncate">'+esc(item.artist||'')+'</td><td class="px-2 py-2 text-xs text-gray-500 text-right pr-4">'+fmtDur(item.duration)+'</td></tr>';
  }}).join('');
    main.innerHTML='<div class="max-w-2xl mx-auto px-2 py-4"><div class="flex items-center justify-between mb-4 px-2"><h2 class="font-semibold text-lg">Queue <span class="text-gray-400 font-normal text-sm ml-1">'+m.queue_length+' tracks</span></h2><button data-action="music-toggle" class="px-3 py-1 rounded-lg text-sm bg-gray-700 hover:bg-gray-600 transition-colors">'+(m.state==='play'?'\u23f9 Stop':'\u25b6 Play')+'</button></div>'+(q.length?'<div class="overflow-x-auto rounded-xl border border-gray-800"><table class="w-full text-left"><tbody>'+rows+'</tbody></table></div>':'<p class="text-gray-500 text-center py-8 text-sm">No tracks in queue</p>')+'</div>';
}}

function fmtDur(s){{ if(!s) return '\u2014'; const t=Math.round(Number(s)); return Math.floor(t/60)+':'+String(t%60).padStart(2,'0'); }}
function esc(s){{ return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); }}

function wsUrl(){{ return (location.protocol==='https:'?'wss':'ws')+'://'+location.hostname+':'+WS_PORT+'/ws'; }}
function sendAction(payload){{ if(S.ws&&S.ws.readyState===WebSocket.OPEN) S.ws.send(JSON.stringify(payload)); }}

function formatCaptureError(err){{
    const name=(err&&err.name)?String(err.name):'';
    const msg=(err&&err.message)?String(err.message):String(err||'capture failed');
    if(name==='NotFoundError') return 'No microphone device found (retrying)';
    if(name==='NotAllowedError') return 'Microphone permission denied';
    if(name==='NotReadableError') return 'Microphone is busy/unavailable';
    if(name==='OverconstrainedError') return 'Requested audio constraints not supported';
    return msg;
}}

function clearCaptureRetry(){{
    if(S.captureRetryTimer){{
        clearTimeout(S.captureRetryTimer);
        S.captureRetryTimer=null;
    }}
}}

function scheduleCaptureRetry(delayMs=2500){{
    if(S.captureRetryTimer || S.wsManualDisconnect || !S.wsConnected) return;
    S.captureRetryTimer=setTimeout(()=>{{
        S.captureRetryTimer=null;
        ensureBrowserCapture().catch((err)=>reportCaptureFailure(err,'retry'));
    }}, Math.max(500, Number(delayMs)||2500));
}}

function reportCaptureFailure(err, phase='capture'){{
    S.wsDebug.status='capture_error';
    S.wsDebug.lastError=formatCaptureError(err);
    updateWsDebugBanner();
    try{{ console.error('Browser capture '+phase+' failed:', err); }}catch(_ ){{}}
    try{{ sendCaptureDiagnostics(err, phase); }}catch(_ ){{}}
    const retryMs=(err&&err.name==='NotFoundError'&&S.lastAudioInputCount===0)?10000:2500;
    scheduleCaptureRetry(retryMs);
}}

async function sendCaptureDiagnostics(err, phase='capture'){{
    const payload={{
        type:'browser_capture_error',
        phase:String(phase||'capture'),
        name:(err&&err.name)?String(err.name):'',
        message:(err&&err.message)?String(err.message):String(err||''),
        secure_context:!!window.isSecureContext,
        has_media_devices:!!(navigator.mediaDevices&&navigator.mediaDevices.getUserMedia),
        user_agent:String(navigator.userAgent||''),
    }};

    try{{
        if(navigator.mediaDevices&&navigator.mediaDevices.enumerateDevices){{
            const devices=await navigator.mediaDevices.enumerateDevices();
            const audioInputs=(devices||[]).filter(d=>d&&d.kind==='audioinput');
            payload.audio_input_count=audioInputs.length;
            payload.audio_input_labels=audioInputs.map(d=>String(d.label||'')).filter(Boolean).slice(0,6);
            S.lastAudioInputCount=audioInputs.length;
            if(audioInputs.length===0){{
                S.wsDebug.lastError='No browser-visible microphone (audioinput=0). Use full browser + allow mic permissions.';
                updateWsDebugBanner();
            }}
        }}
    }}catch(diagErr){{
        payload.enumerate_error=(diagErr&&diagErr.message)?String(diagErr.message):String(diagErr||'');
    }}

    if(S.ws&&S.ws.readyState===WebSocket.OPEN){{
        S.ws.send(JSON.stringify(payload));
    }}
}}

async function stopBrowserCapture(){{
    try{{ if(S.processor) S.processor.disconnect(); }}catch(_ ){{}}
    try{{ if(S.audioCtx) await S.audioCtx.close(); }}catch(_ ){{}}
    if(S.mediaStream){{
        try{{ S.mediaStream.getTracks().forEach(t=>t.stop()); }}catch(_ ){{}}
    }}
    S.processor=null;
    S.audioCtx=null;
    S.mediaStream=null;
}}

async function disconnectWs(manual=true){{
    if(manual) S.wsManualDisconnect=true;
    if(S.wsReconnectTimer){{ clearTimeout(S.wsReconnectTimer); S.wsReconnectTimer=null; }}
    clearCaptureRetry();

    const ws=S.ws;
    S.ws=null;
    S.wsConnected=false;
    S.wsDebug.status='closed';
    S.wsDebug.lastCloseReason=manual?'manual disconnect':(S.wsDebug.lastCloseReason||'');
    updateWsDebugBanner();
    updateMicInteractivity();

    if(ws && (ws.readyState===WebSocket.OPEN || ws.readyState===WebSocket.CONNECTING)){{
        try{{ ws.close(1000, manual?'Manual disconnect':'Disconnect'); }}catch(_ ){{}}
    }}
    await stopBrowserCapture();
}}

function reconnectWs(){{
    S.wsManualDisconnect=false;
    if(S.wsReconnectTimer){{ clearTimeout(S.wsReconnectTimer); S.wsReconnectTimer=null; }}
    connectWs();
}}

function connectWs(){{
  if(S.wsManualDisconnect) return;
  if(S.ws&&(S.ws.readyState===WebSocket.OPEN||S.ws.readyState===WebSocket.CONNECTING)) return;
    S.wsDebug.status='connecting';
    S.wsDebug.lastError='';
    updateWsDebugBanner();
    updateMicInteractivity();
  S.ws=new WebSocket(wsUrl()); S.ws.binaryType='arraybuffer';
    S.ws.onopen=()=>{{
            S.wsConnected=true;
            S.wsDebug.status='open';
                S.wsDebug.lastError='';
                updateWsDebugBanner();
                updateMicInteractivity();
            if(S.browserAudioEnabled) ensureBrowserCapture().catch((err)=>reportCaptureFailure(err,'connect'));
            S.ws.send(JSON.stringify({{type:'ui_ready'}}));
    }};
    S.ws.onclose=(evt)=>{{
        S.wsConnected=false;
        S.wsDebug.status='closed';
        S.wsDebug.lastCloseCode=(evt&&evt.code!==undefined)?evt.code:null;
        S.wsDebug.lastCloseReason=(evt&&evt.reason)?String(evt.reason):'';
        updateWsDebugBanner();
        updateMicInteractivity();
        S.ws=null;
        if (evt && evt.code === 4001) return;
        if(S.wsManualDisconnect) return;
        S.wsReconnectTimer=setTimeout(()=>{{ S.wsReconnectTimer=null; connectWs(); }},1500);
    }};
    S.ws.onerror=(evt)=>{{
        S.wsDebug.status='error';
        S.wsDebug.lastError=(evt&&evt.message)?String(evt.message):'socket error';
        updateWsDebugBanner();
        updateMicInteractivity();
    }};
  S.ws.onmessage=evt=>{{ if(!(evt.data instanceof ArrayBuffer)){{ try{{ handleMsg(JSON.parse(evt.data)); }}catch(_){{}} }} }};
}}

function handleMsg(msg){{
  switch(msg.type){{
    case 'hello': break;
    case 'state_snapshot':
      if(msg.orchestrator) applyOrch(msg.orchestrator);
            if(msg.ui_control){{
                if(msg.ui_control.mic_enabled!==undefined) S.micEnabled=!!msg.ui_control.mic_enabled;
                if(msg.ui_control.tts_muted!==undefined) S.ttsMuted=!!msg.ui_control.tts_muted;
                if(msg.ui_control.browser_audio_enabled!==undefined) S.browserAudioEnabled=!!msg.ui_control.browser_audio_enabled;
                if(msg.ui_control.continuous_mode!==undefined) S.continuousMode=!!msg.ui_control.continuous_mode;
                applyMicState();
                applyMicControlToggles();
            }}
      if(msg.music){{ applyMusic(msg.music); if(msg.music.queue) S.musicQueue=msg.music.queue; }}
      if(msg.timers) applyTimers(msg.timers);
            if(msg.chat) S.chat=msg.chat;
            if(Array.isArray(msg.chat_threads)) S.chatThreads=msg.chat_threads;
            if(msg.active_chat_id) S.activeChatId=msg.active_chat_id;
            if(!S.selectedChatId) S.selectedChatId='active';
            renderPage();
      break;
    case 'orchestrator_status': applyOrch(msg); break;
    case 'status': if(msg.orchestrator) applyOrch(msg.orchestrator); break;
        case 'chat_append': if(msg.message){{ S.chat.push(msg.message); if(S.page==='home'&&(!S.selectedChatId||S.selectedChatId==='active')) renderChatMessages('active'); }} break;
        case 'chat_threads_update':
            if(Array.isArray(msg.chat_threads)) S.chatThreads=msg.chat_threads;
            if(msg.active_chat_id) S.activeChatId=msg.active_chat_id;
            if(S.page==='home') renderPage();
            break;
        case 'chat_reset':
            S.chat=[];
            if(Array.isArray(msg.chat_threads)) S.chatThreads=msg.chat_threads;
            if(msg.active_chat_id) S.activeChatId=msg.active_chat_id;
            S.selectedChatId='active';
            if(S.page==='home') renderPage();
            break;
        case 'chat_text_ack':
            if(msg.client_msg_id) S.pendingChatSends.delete(String(msg.client_msg_id));
            if(S.page==='home') updateChatComposerState();
            break;
    case 'music_state': applyMusic(msg); if(msg.queue!==undefined) S.musicQueue=msg.queue; if(S.page==='music') renderPage(); else applyMusicHeader(); break;
    case 'timers_state': applyTimers(msg.timers||[]); break;
        case 'ui_control':
            if(msg.mic_enabled!==undefined) S.micEnabled=!!msg.mic_enabled;
            if(msg.tts_muted!==undefined) S.ttsMuted=!!msg.tts_muted;
            if(msg.browser_audio_enabled!==undefined) S.browserAudioEnabled=!!msg.browser_audio_enabled;
            if(msg.continuous_mode!==undefined) S.continuousMode=!!msg.continuous_mode;
            applyMicState();
            applyMicControlToggles();
            break;
            case 'feedback_sound':
                playFeedbackSound(msg.audio_b64, msg.gain||1.0);
                break;
  }}
}}

async function playFeedbackSound(b64, gain) {{
    try {{
        if (!b64) return;
        const bytes = Uint8Array.from(atob(b64), c => c.charCodeAt(0));
        const ctx = new (window.AudioContext || window.webkitAudioContext)();
        const buf = await ctx.decodeAudioData(bytes.buffer.slice(0));
        const src = ctx.createBufferSource();
        src.buffer = buf;
        const gainNode = ctx.createGain();
        gainNode.gain.value = Math.max(0, Math.min(4, Number(gain) || 1.0));
        src.connect(gainNode);
        gainNode.connect(ctx.destination);
        src.onended = () => ctx.close();
        src.start();
    }} catch(e) {{
        console.debug('Feedback sound error:', e);
    }}
}}
function applyOrch(o){{
  if(o.voice_state!==undefined) S.voice_state=o.voice_state;
  if(o.wake_state!==undefined)  S.wake_state=o.wake_state;
  if(o.tts_playing!==undefined) S.tts_playing=!!o.tts_playing;
  if(o.mic_rms!==undefined)     S.mic_rms=Number(o.mic_rms)||0;
  if(o.mic_enabled!==undefined) S.micEnabled=!!o.mic_enabled;
  applyMicState();
}}
function applyMusic(m){{ Object.assign(S.music,m); applyMusicHeader(); }}
function applyTimers(t){{
  const now=Date.now()/1000;
  S.timers=t.map(timer=>Object.assign({{}},timer,{{_clientAnchorTs:now, _clientAnchorRem:timer.remaining_seconds}}));
  renderTimerBar();
}}

async function startBrowserCapture(){{
    if(!S.browserAudioEnabled) return;
    const hasLiveTrack = !!(S.mediaStream && S.mediaStream.getAudioTracks().some(t=>t.readyState==='live'));
    if (hasLiveTrack && S.processor) {{
        if (S.audioCtx && S.audioCtx.state === 'suspended') await S.audioCtx.resume();
        clearCaptureRetry();
        return;
    }}

    // Cleanup stale graph if present, then rebuild.
    try{{ if(S.processor) S.processor.disconnect(); }}catch(_ ){{}}
    try{{ if(S.audioCtx) await S.audioCtx.close(); }}catch(_ ){{}}
    if(S.mediaStream){{
        try{{ S.mediaStream.getTracks().forEach(t=>t.stop()); }}catch(_ ){{}}
    }}
    S.processor=null; S.audioCtx=null; S.mediaStream=null;

  if(!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia){{
      throw new Error('Browser mediaDevices.getUserMedia is unavailable');
  }}

  const captureConstraints=[
      {{audio:{{echoCancellation:false,noiseSuppression:false,autoGainControl:false}},video:false}},
      {{audio:true,video:false}},
  ];

  let lastErr=null;
  for(const constraints of captureConstraints){{
      try{{
          S.mediaStream=await navigator.mediaDevices.getUserMedia(constraints);
          break;
      }}catch(err){{
          lastErr=err;
      }}
  }}
  if(!S.mediaStream) throw (lastErr||new Error('getUserMedia failed'));

  S.audioCtx=new(window.AudioContext||window.webkitAudioContext)();
    if (S.audioCtx.state === 'suspended') await S.audioCtx.resume();
  const src=S.audioCtx.createMediaStreamSource(S.mediaStream);
  const proc=S.audioCtx.createScriptProcessor(2048,1,1);
  const mute=S.audioCtx.createGain(); mute.gain.value=0;
  src.connect(proc); proc.connect(mute); mute.connect(S.audioCtx.destination);
  proc.onaudioprocess=evt=>{{
    const inp=evt.inputBuffer.getChannelData(0);
    let ss=0; for(let i=0;i<inp.length;i++) ss+=inp[i]*inp[i];
    const rms=Math.sqrt(ss/Math.max(1,inp.length));
        if(!S.ws||S.ws.readyState!==WebSocket.OPEN) return;
        if(!S.browserAudioEnabled) return;
    const now=performance.now();
        if(now-S.lastLevel>=120){{ S.lastLevel=now; S.ws.send(JSON.stringify({{type:'browser_audio_level',rms,peak:rms}})); }}
        const out=new Int16Array(inp.length);
        for(let i=0;i<inp.length;i++){{const s=Math.max(-1,Math.min(1,inp[i]));out[i]=s<0?s*0x8000:s*0x7fff;}}
        S.ws.send(out.buffer);
  }};
  S.processor=proc;
    clearCaptureRetry();
}}

async function ensureBrowserCapture(){{
    await startBrowserCapture();
}}

document.addEventListener('visibilitychange',()=>{{
    if(document.visibilityState==='visible' && !S.wsManualDisconnect){{
        ensureBrowserCapture().catch(()=>{{}});
    }}
}});

if(navigator.mediaDevices && typeof navigator.mediaDevices.addEventListener==='function'){{
    navigator.mediaDevices.addEventListener('devicechange',()=>{{
        if(!S.wsManualDisconnect && S.wsConnected){{
            ensureBrowserCapture().catch((err)=>reportCaptureFailure(err,'devicechange'));
        }}
    }});
}}

function setupServerRefreshWatcher(){{
    let inFlight=false;
    setInterval(async ()=>{{
        if(inFlight) return;
        inFlight=true;
        try{{
            const resp=await fetch('/health?ts='+Date.now(), {{ cache:'no-store' }});
            if(!resp.ok) return;
            const data=await resp.json();
            const remoteId=String((data&&data.instance_id)||'');
            if(remoteId && SERVER_INSTANCE_ID && remoteId!==SERVER_INSTANCE_ID){{
                location.reload();
            }}
        }}catch(_ ){{
            // Ignore transient network/server hiccups; watcher is best-effort.
        }}finally{{
            inFlight=false;
        }}
    }}, 2000);
}}

S.page=getPage(); renderPage(); applyMicState(); applyMicControlToggles(); updateWsDebugBanner(); updateMicInteractivity(); connectWs();
setupServerRefreshWatcher();
setInterval(()=>{{ if(!S.timers.length) return; const now=Date.now()/1000; S.timers.forEach(t=>{{ if(t._clientAnchorTs===undefined){{ t._clientAnchorTs=now; t._clientAnchorRem=t.remaining_seconds; }} t.remaining_seconds=Math.max(0, t._clientAnchorRem-(now-t._clientAnchorTs)); }}); renderTimerBar(); }},500);
startBrowserCapture().catch((err)=>{{
    reportCaptureFailure(err,'startup');
}});
</script>
</body>
</html>
"""


class EmbeddedVoiceWebService:
    """Small embedded HTTP/WebSocket service for realtime UI and audio streaming."""

    def __init__(
        self,
        host: str = "0.0.0.0",
        ui_port: int = 18910,
        ws_port: int = 18911,
        status_hz: int = 12,
        hotword_active_ms: int = 2000,
        mic_starts_disabled: bool = True,
        audio_authority: str = "native",
        chat_history_limit: int = 200,
        ssl_certfile: str = "",
        ssl_keyfile: str = "",
        http_redirect_port: int = 0,
    ):
        self.host = host
        self.ui_port = ui_port
        self.ws_port = ws_port
        self.status_interval_s = 1.0 / max(1, status_hz)
        self.hotword_active_s = max(0.1, hotword_active_ms / 1000.0)
        self.mic_starts_disabled = mic_starts_disabled
        self.audio_authority = audio_authority
        self.chat_history_limit = max(20, chat_history_limit)
        self.ssl_certfile = ssl_certfile
        self.ssl_keyfile = ssl_keyfile
        self.http_redirect_port = http_redirect_port
        self._instance_id = uuid.uuid4().hex

        self._http_server: HTTPServer | None = None
        self._http_thread: threading.Thread | None = None
        self._http_redirect_server: HTTPServer | None = None
        self._http_redirect_thread: threading.Thread | None = None
        self._ws_server: Any = None
        self._status_task: asyncio.Task | None = None
        self._ssl_context: ssl.SSLContext | None = None

        self._clients: set[Any] = set()
        self._active_client: Any | None = None
        self._latest_browser_audio: dict[str, float] = {"rms": 0.0, "peak": 0.0}
        self._browser_pcm_frames: deque[bytes] = deque(maxlen=400)
        self._last_browser_pcm_ts: float | None = None
        self._last_hotword_ts: float | None = None

        self._orchestrator_status: dict[str, Any] = {
            "voice_state": "idle",
            "wake_state": "asleep",
            "speech_active": False,
            "tts_playing": False,
            "mic_rms": 0.0,
            "queue_depth": 0,
        }

        self._chat_messages: list[dict[str, Any]] = []
        self._chat_seq: int = 0
        self._chat_threads: list[dict[str, Any]] = []
        self._active_chat_id: str = "active"
        self._chat_thread_limit = 100
        self._music_state: dict[str, Any] = {
            "state": "stop", "title": "", "artist": "", "album": "",
            "queue_length": 0, "elapsed": 0.0, "duration": 0.0, "position": -1,
        }
        self._timers_state: list[dict[str, Any]] = []
        self._ui_control_state: dict[str, Any] = {
            "mic_enabled": not mic_starts_disabled,
            "tts_muted": False,
            "browser_audio_enabled": True,
            "continuous_mode": False,
        }

        self._on_mic_toggle: Callable[[str], Awaitable[None]] | None = None
        self._on_music_toggle: Callable[[str], Awaitable[None]] | None = None
        self._on_music_stop: Callable[[str], Awaitable[None]] | None = None
        self._on_music_play_track: Callable[[int, str], Awaitable[None]] | None = None
        self._on_timer_cancel: Callable[[str, str], Awaitable[None]] | None = None
        self._on_alarm_cancel: Callable[[str, str], Awaitable[None]] | None = None
        self._on_chat_new: Callable[[str], Awaitable[None]] | None = None
        self._on_chat_text: Callable[[str, str], Awaitable[None]] | None = None
        self._on_tts_mute_set: Callable[[bool, str], Awaitable[None]] | None = None
        self._on_browser_audio_set: Callable[[bool, str], Awaitable[None]] | None = None
        self._on_continuous_mode_set: Callable[[bool, str], Awaitable[None]] | None = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def _tls_enabled(self) -> bool:
        return bool(self.ssl_certfile and self.ssl_keyfile)

    def _ensure_ssl_context(self) -> ssl.SSLContext | None:
        if not self._tls_enabled():
            return None
        if self._ssl_context is None:
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            context.load_cert_chain(self.ssl_certfile, self.ssl_keyfile)
            self._ssl_context = context
        return self._ssl_context

    async def start(self) -> None:
        ssl_context = self._ensure_ssl_context()
        self._start_http_server()
        self._ws_server = await websockets.serve(
            self._ws_handler,
            self.host,
            self.ws_port,
            ssl=ssl_context,
        )
        self._status_task = asyncio.create_task(self._status_loop())
        ui_scheme = "https" if ssl_context else "http"
        ws_scheme = "wss" if ssl_context else "ws"
        logger.info(
            "Embedded web UI started: %s://%s:%d (%s://%s:%d)",
            ui_scheme,
            self.host,
            self.ui_port,
            ws_scheme,
            self.host,
            self.ws_port,
        )
        if ssl_context and self.http_redirect_port:
            logger.info(
                "Embedded web UI HTTP redirector started: http://%s:%d -> https://%s:%d",
                self.host,
                self.http_redirect_port,
                self.host,
                self.ui_port,
            )

    async def stop(self) -> None:
        if self._status_task:
            self._status_task.cancel()
            try:
                await self._status_task
            except asyncio.CancelledError:
                pass
            self._status_task = None

        if self._ws_server is not None:
            self._ws_server.close()
            await self._ws_server.wait_closed()
            self._ws_server = None

        if self._http_server is not None:
            self._http_server.shutdown()
            self._http_server.server_close()
            self._http_server = None

        if self._http_redirect_server is not None:
            self._http_redirect_server.shutdown()
            self._http_redirect_server.server_close()
            self._http_redirect_server = None

        if self._http_thread and self._http_thread.is_alive():
            self._http_thread.join(timeout=1.0)
        self._http_thread = None
        if self._http_redirect_thread and self._http_redirect_thread.is_alive():
            self._http_redirect_thread.join(timeout=1.0)
        self._http_redirect_thread = None
        self._clients.clear()

    # ------------------------------------------------------------------
    # State update helpers (called from main.py)
    # ------------------------------------------------------------------

    def update_orchestrator_status(self, **status: Any) -> None:
        self._orchestrator_status.update(status)

    def note_hotword_detected(self) -> None:
        self._last_hotword_ts = time.monotonic()

    def update_chat_history(self, messages: list[dict[str, Any]]) -> None:
        self._chat_messages = list(messages[-self.chat_history_limit:])

    def _derive_chat_title(self, messages: list[dict[str, Any]]) -> str:
        for m in messages:
            if str(m.get("role", "")).lower() == "user":
                raw = str(m.get("text", "")).strip()
                if raw:
                    return raw[:72]
        return f"Chat {len(self._chat_threads) + 1}"

    def _archive_active_chat_if_needed(self) -> None:
        if not self._chat_messages:
            return
        now = time.time()
        archived = {
            "id": uuid.uuid4().hex[:12],
            "title": self._derive_chat_title(self._chat_messages),
            "messages": list(self._chat_messages),
            "created_ts": now,
            "updated_ts": now,
        }
        self._chat_threads.insert(0, archived)
        if len(self._chat_threads) > self._chat_thread_limit:
            self._chat_threads = self._chat_threads[: self._chat_thread_limit]

    def start_new_chat(self) -> None:
        self._archive_active_chat_if_needed()
        self._chat_messages = []
        self._active_chat_id = "active"
        asyncio.create_task(
            self.broadcast(
                {
                    "type": "chat_reset",
                    "active_chat_id": self._active_chat_id,
                    "chat": [],
                    "chat_threads": list(self._chat_threads),
                }
            )
        )

    def append_chat_message(self, message: dict[str, Any]) -> None:
        self._chat_seq += 1
        msg = dict(message)
        msg.setdefault("id", self._chat_seq)
        msg.setdefault("ts", time.time())
        self._chat_messages.append(msg)
        if len(self._chat_messages) > self.chat_history_limit:
            self._chat_messages = self._chat_messages[-self.chat_history_limit:]
        asyncio.create_task(self.broadcast({"type": "chat_append", "message": msg}))

    def update_music_state(self, queue: list[dict[str, Any]] | None = None, **state: Any) -> None:
        self._music_state.update(state)
        payload: dict[str, Any] = {"type": "music_state"}
        payload.update(self._music_state)
        if queue is not None:
            payload["queue"] = queue
        asyncio.create_task(self.broadcast(payload))

    def update_timers_state(self, timers: list[dict[str, Any]]) -> None:
        self._timers_state = list(timers)
        asyncio.create_task(self.broadcast({"type": "timers_state", "timers": self._timers_state}))

    def update_ui_control_state(self, **state: Any) -> None:
        self._ui_control_state.update(state)
        asyncio.create_task(self.broadcast({"type": "ui_control", **self._ui_control_state}))

    def has_active_client(self) -> bool:
        return self._active_client is not None and self._active_client in self._clients

    def has_recent_browser_audio(self, max_age_s: float = 1.0) -> bool:
        if not self.has_active_client():
            return False
        if self._last_browser_pcm_ts is None:
            return False
        return (time.monotonic() - self._last_browser_pcm_ts) <= max(0.05, float(max_age_s))

    async def read_browser_frame(self, timeout: float = 0.0) -> bytes | None:
        if self._browser_pcm_frames:
            return self._browser_pcm_frames.popleft()
        if timeout <= 0:
            return None
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self._browser_pcm_frames:
                return self._browser_pcm_frames.popleft()
            await asyncio.sleep(0.005)
        return None

    def latest_browser_audio(self) -> dict[str, float]:
        return dict(self._latest_browser_audio)

    # ------------------------------------------------------------------
    # Action handler registration
    # ------------------------------------------------------------------

    def set_action_handlers(
        self,
        on_mic_toggle: Callable[[str], Awaitable[None]] | None = None,
        on_music_toggle: Callable[[str], Awaitable[None]] | None = None,
        on_music_stop: Callable[[str], Awaitable[None]] | None = None,
        on_music_play_track: Callable[[int, str], Awaitable[None]] | None = None,
        on_timer_cancel: Callable[[str, str], Awaitable[None]] | None = None,
        on_alarm_cancel: Callable[[str, str], Awaitable[None]] | None = None,
        on_chat_new: Callable[[str], Awaitable[None]] | None = None,
        on_chat_text: Callable[[str, str], Awaitable[None]] | None = None,
        on_tts_mute_set: Callable[[bool, str], Awaitable[None]] | None = None,
        on_browser_audio_set: Callable[[bool, str], Awaitable[None]] | None = None,
        on_continuous_mode_set: Callable[[bool, str], Awaitable[None]] | None = None,
    ) -> None:
        if on_mic_toggle is not None:
            self._on_mic_toggle = on_mic_toggle
        if on_music_toggle is not None:
            self._on_music_toggle = on_music_toggle
        if on_music_stop is not None:
            self._on_music_stop = on_music_stop
        if on_music_play_track is not None:
            self._on_music_play_track = on_music_play_track
        if on_timer_cancel is not None:
            self._on_timer_cancel = on_timer_cancel
        if on_alarm_cancel is not None:
            self._on_alarm_cancel = on_alarm_cancel
        if on_chat_new is not None:
            self._on_chat_new = on_chat_new
        if on_chat_text is not None:
            self._on_chat_text = on_chat_text
        if on_tts_mute_set is not None:
            self._on_tts_mute_set = on_tts_mute_set
        if on_browser_audio_set is not None:
            self._on_browser_audio_set = on_browser_audio_set
        if on_continuous_mode_set is not None:
            self._on_continuous_mode_set = on_continuous_mode_set

    # ------------------------------------------------------------------
    # Broadcast helper
    # ------------------------------------------------------------------

    async def broadcast(self, payload: dict[str, Any]) -> None:
        # ------------------------------------------------------------------
        # Feedback sound helper
        # ------------------------------------------------------------------

        def send_feedback_sound(self, wav_bytes: bytes, gain: float = 1.0) -> None:
            """Broadcast a short feedback sound to all browser clients as base64-encoded WAV."""
            import base64
            asyncio.create_task(self.broadcast({
                "type": "feedback_sound",
                "audio_b64": base64.b64encode(wav_bytes).decode(),
                "gain": float(gain),
            }))

        if not self._clients:
            return
        message = json.dumps(payload)
        stale: list[Any] = []
        for client in list(self._clients):
            try:
                await client.send(message)
            except Exception:
                stale.append(client)
        for c in stale:
            self._clients.discard(c)

    # ------------------------------------------------------------------
    # WebSocket handler
    # ------------------------------------------------------------------

    async def _ws_handler(self, websocket: Any) -> None:
        client_id = uuid.uuid4().hex[:8]

        # Single-client mode: newest connection replaces existing one.
        for existing in list(self._clients):
            if existing is not websocket:
                try:
                    await existing.close(code=4001, reason="Replaced by newer Web UI client")
                except Exception:
                    pass
                self._clients.discard(existing)

        self._clients.add(websocket)
        self._active_client = websocket
        logger.info("Web UI client connected (%s); clients=%d", client_id, len(self._clients))
        try:
            await websocket.send(json.dumps({
                "type": "hello",
                "client_id": client_id,
                "ws_port": self.ws_port,
                "ui_port": self.ui_port,
            }))
            await websocket.send(json.dumps(self._build_state_snapshot()))
            async for message in websocket:
                if isinstance(message, str):
                    asyncio.create_task(self._handle_text_action(message, client_id, websocket))
                elif isinstance(message, (bytes, bytearray)):
                    self._handle_pcm_chunk(bytes(message))
        except Exception as exc:
            logger.debug("Web UI client %s disconnected: %s", client_id, exc)
        finally:
            self._clients.discard(websocket)
            if self._active_client is websocket:
                self._active_client = None
            self._browser_pcm_frames.clear()
            logger.info("Web UI client disconnected (%s); clients=%d", client_id, len(self._clients))

    # ------------------------------------------------------------------
    # Incoming action dispatch
    # ------------------------------------------------------------------

    async def _handle_text_action(self, message: str, client_id: str, websocket: Any | None = None) -> None:
        try:
            payload = json.loads(message)
        except json.JSONDecodeError:
            return
        msg_type = payload.get("type", "")

        if msg_type == "browser_audio_level":
            try:
                self._latest_browser_audio["rms"] = float(payload.get("rms", 0.0))
                self._latest_browser_audio["peak"] = float(payload.get("peak", 0.0))
            except Exception:
                pass
            return

        if msg_type == "browser_capture_error":
            try:
                logger.warning(
                    "Browser capture error [%s] from %s: %s | msg=%s | secure=%s | mediaDevices=%s | audioInputs=%s | labels=%s",
                    payload.get("phase", "capture"),
                    client_id,
                    payload.get("name", ""),
                    payload.get("message", ""),
                    payload.get("secure_context", None),
                    payload.get("has_media_devices", None),
                    payload.get("audio_input_count", None),
                    payload.get("audio_input_labels", []),
                )
            except Exception:
                pass
            return

        if msg_type in ("ui_ready", "navigate"):
            return

        if msg_type == "mic_toggle" and self._on_mic_toggle:
            try:
                await self._on_mic_toggle(client_id)
            except Exception as exc:
                logger.warning("mic_toggle handler error: %s", exc)
            return

        if msg_type == "music_toggle" and self._on_music_toggle:
            try:
                await self._on_music_toggle(client_id)
            except Exception as exc:
                logger.warning("music_toggle handler error: %s", exc)
            return

        if msg_type == "music_stop" and self._on_music_stop:
            try:
                await self._on_music_stop(client_id)
            except Exception as exc:
                logger.warning("music_stop handler error: %s", exc)
            return

        if msg_type == "music_play_track" and self._on_music_play_track:
            pos = payload.get("position")
            if pos is not None:
                try:
                    await self._on_music_play_track(int(pos), client_id)
                except Exception as exc:
                    logger.warning("music_play_track handler error: %s", exc)
            return

        if msg_type == "timer_cancel" and self._on_timer_cancel:
            timer_id = payload.get("timer_id", "")
            if timer_id:
                try:
                    await self._on_timer_cancel(str(timer_id), client_id)
                except Exception as exc:
                    logger.warning("timer_cancel handler error: %s", exc)
            return

        if msg_type == "alarm_cancel" and self._on_alarm_cancel:
            alarm_id = payload.get("alarm_id", "")
            if alarm_id:
                try:
                    await self._on_alarm_cancel(str(alarm_id), client_id)
                except Exception as exc:
                    logger.warning("alarm_cancel handler error: %s", exc)
            return

        if msg_type == "tts_mute_set" and self._on_tts_mute_set:
            enabled = bool(payload.get("enabled", False))
            try:
                await self._on_tts_mute_set(enabled, client_id)
            except Exception as exc:
                logger.warning("tts_mute_set handler error: %s", exc)
            return

        if msg_type == "browser_audio_set" and self._on_browser_audio_set:
            enabled = bool(payload.get("enabled", True))
            try:
                await self._on_browser_audio_set(enabled, client_id)
            except Exception as exc:
                logger.warning("browser_audio_set handler error: %s", exc)
            return

        if msg_type == "continuous_mode_set" and self._on_continuous_mode_set:
            enabled = bool(payload.get("enabled", False))
            try:
                await self._on_continuous_mode_set(enabled, client_id)
            except Exception as exc:
                logger.warning("continuous_mode_set handler error: %s", exc)
            return

        if msg_type == "chat_new" and self._on_chat_new:
            try:
                await self._on_chat_new(client_id)
            except Exception as exc:
                logger.warning("chat_new handler error: %s", exc)
            return

        if msg_type == "chat_text" and self._on_chat_text:
            text = str(payload.get("text", "")).strip()
            client_msg_id = payload.get("client_msg_id")
            if text:
                try:
                    await self._on_chat_text(text, client_id)
                    if websocket is not None:
                        await websocket.send(
                            json.dumps(
                                {
                                    "type": "chat_text_ack",
                                    "client_msg_id": client_msg_id,
                                    "ok": True,
                                }
                            )
                        )
                except Exception as exc:
                    logger.warning("chat_text handler error: %s", exc)
                    if websocket is not None:
                        try:
                            await websocket.send(
                                json.dumps(
                                    {
                                        "type": "chat_text_ack",
                                        "client_msg_id": client_msg_id,
                                        "ok": False,
                                        "error": str(exc),
                                    }
                                )
                            )
                        except Exception:
                            pass
            return

        logger.debug("Web UI: unhandled action '%s' from %s", msg_type, client_id)

    def _handle_pcm_chunk(self, pcm_bytes: bytes) -> None:
        if len(pcm_bytes) < 2:
            return
        sample_count = len(pcm_bytes) // 2
        if sample_count <= 0:
            return
        pcm_view = memoryview(pcm_bytes)[:sample_count * 2].cast("h")
        sum_sq = 0.0
        peak = 0
        for sample in pcm_view:
            s = int(sample)
            abs_s = -s if s < 0 else s
            if abs_s > peak:
                peak = abs_s
            sum_sq += float(s * s)
        rms = math.sqrt(sum_sq / float(sample_count)) / 32768.0
        self._last_browser_pcm_ts = time.monotonic()
        self._latest_browser_audio["rms"] = max(0.0, min(1.0, rms))
        self._latest_browser_audio["peak"] = max(0.0, min(1.0, float(peak) / 32768.0))
        self._browser_pcm_frames.append(pcm_bytes)

    # ------------------------------------------------------------------
    # Status broadcast loop
    # ------------------------------------------------------------------

    async def _status_loop(self) -> None:
        while True:
            await asyncio.sleep(self.status_interval_s)
            if not self._clients:
                continue
            payload = self._build_status_payload()
            message = json.dumps(payload)
            stale: list[Any] = []
            for client in list(self._clients):
                try:
                    await client.send(message)
                except Exception:
                    stale.append(client)
            for c in stale:
                self._clients.discard(c)

    def _build_status_payload(self) -> dict[str, Any]:
        now = time.monotonic()
        hotword_active = (
            self._last_hotword_ts is not None
            and (now - self._last_hotword_ts) <= self.hotword_active_s
        )
        orch = dict(self._orchestrator_status)
        orch["hotword_active"] = hotword_active
        orch["mic_enabled"] = self._ui_control_state.get("mic_enabled", False)
        return {
            "type": "orchestrator_status",
            "ts": time.time(),
            **orch,
            "browser_audio": dict(self._latest_browser_audio),
        }

    def _build_state_snapshot(self) -> dict[str, Any]:
        now = time.monotonic()
        hotword_active = (
            self._last_hotword_ts is not None
            and (now - self._last_hotword_ts) <= self.hotword_active_s
        )
        orch = dict(self._orchestrator_status)
        orch["hotword_active"] = hotword_active
        return {
            "type": "state_snapshot",
            "orchestrator": orch,
            "ui_control": dict(self._ui_control_state),
            "music": dict(self._music_state),
            "timers": list(self._timers_state),
            "chat": list(self._chat_messages[-50:]),
            "chat_threads": list(self._chat_threads),
            "active_chat_id": self._active_chat_id,
        }

    # ------------------------------------------------------------------
    # HTTP server
    # ------------------------------------------------------------------

    def _start_http_server(self) -> None:
        html = _build_ui_html(
            self.ws_port,
            self.mic_starts_disabled,
            self.audio_authority,
            self._instance_id,
        )
        service = self

        class UIHandler(BaseHTTPRequestHandler):
            def log_message(self, format: str, *args: Any) -> None:  # noqa: A003
                return

            def _send(self, body: bytes, status: int = 200, content_type: str = "text/html; charset=utf-8") -> None:
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
                self.send_header("Pragma", "no-cache")
                self.send_header("Expires", "0")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Access-Control-Allow-Headers", "*")
                self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
                self.end_headers()
                self.wfile.write(body)

            def do_OPTIONS(self) -> None:  # noqa: N802
                self._send(b"", status=204, content_type="text/plain")

            def do_GET(self) -> None:  # noqa: N802
                path = self.path.split("?")[0]
                if path in ("/", "/index.html"):
                    self._send(html.encode("utf-8"))
                elif path == "/favicon.ico":
                    self._send(b"", status=204, content_type="image/x-icon")
                elif path == "/health":
                    self._send(
                        json.dumps(
                            {
                                "status": "ok",
                                "service": "embedded-voice-ui",
                                "instance_id": self.server._embedded_instance_id,
                            }
                        ).encode(),
                        content_type="application/json",
                    )
                else:
                    self._send(b"Not found", status=404, content_type="text/plain")

        class RedirectHandler(BaseHTTPRequestHandler):
            def log_message(self, format: str, *args: Any) -> None:  # noqa: A003
                return

            def _redirect_target(self) -> str:
                raw_host = self.headers.get("Host", "")
                host = raw_host.split(":", 1)[0].strip() or service.host or "localhost"
                port_suffix = "" if service.ui_port == 443 else f":{service.ui_port}"
                return f"https://{host}{port_suffix}{self.path}"

            def _redirect(self) -> None:
                target = self._redirect_target()
                self.send_response(307)
                self.send_header("Location", target)
                self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
                self.send_header("Pragma", "no-cache")
                self.send_header("Expires", "0")
                self.end_headers()

            def do_GET(self) -> None:  # noqa: N802
                self._redirect()

            def do_HEAD(self) -> None:  # noqa: N802
                self._redirect()

            def do_OPTIONS(self) -> None:  # noqa: N802
                self._redirect()

        self._http_server = HTTPServer((self.host, self.ui_port), UIHandler)
        ssl_context = self._ensure_ssl_context()
        if ssl_context is not None:
            self._http_server.socket = ssl_context.wrap_socket(self._http_server.socket, server_side=True)
        self._http_server._embedded_instance_id = self._instance_id  # type: ignore[attr-defined]
        self._http_thread = threading.Thread(target=self._http_server.serve_forever, daemon=True)
        self._http_thread.start()

        if ssl_context is not None and self.http_redirect_port:
            self._http_redirect_server = HTTPServer((self.host, self.http_redirect_port), RedirectHandler)
            self._http_redirect_thread = threading.Thread(
                target=self._http_redirect_server.serve_forever,
                daemon=True,
            )
            self._http_redirect_thread.start()
