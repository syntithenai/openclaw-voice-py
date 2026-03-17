"""Embedded HTTP + WebSocket service for realtime voice UI telemetry."""

from __future__ import annotations

import asyncio
from collections import deque
import json
import logging
import math
import os
import ssl
import tempfile
import threading
import time
import uuid
from http.server import HTTPServer
from pathlib import Path
from typing import Any, Awaitable, Callable

import websockets
from orchestrator.web.http_server import start_http_servers

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
        <script src="https://cdn.jsdelivr.net/npm/mermaid/dist/mermaid.min.js"></script>
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
        .assistant-rich-content {{ display:flex; flex-direction:column; gap:0.7em; }}
        .assistant-rich-content > .md-content:empty {{ display:none; }}
        .mermaidchart-shell {{ background:#1e2436; border:1px solid #3f4a64; border-radius:10px; padding:0.8em; overflow:auto; }}
        .mermaidchart-label {{ font-size:0.68rem; letter-spacing:0.08em; text-transform:uppercase; color:#9aa3c0; margin-bottom:0.65em; }}
        .mermaidchart-shell svg {{ display:block; max-width:100%; height:auto; margin:0 auto; }}
        .mermaidchart-error {{ color:#fca5a5; }}
  </style>
</head>
<body class="bg-gray-950 text-gray-100 h-screen flex flex-col overflow-hidden">

<!-- HEADER -->
<header class="flex items-center justify-between px-3 h-14 bg-gray-900 border-b border-gray-800 flex-none gap-2 z-10">
    <div class="flex items-center gap-2 flex-none" id="menuWrap">
        <nav class="hidden sm:flex items-center gap-1" aria-label="Primary">
            <a href="#/home" class="flex items-center gap-2 px-3 py-2 rounded-lg hover:bg-gray-700 text-sm" data-nav="home" title="Home" aria-label="Home">
                <span aria-hidden="true">🏠</span>
                <span class="hidden lg:inline">Home</span>
            </a>
            <a href="#/music" class="flex items-center gap-2 px-3 py-2 rounded-lg hover:bg-gray-700 text-sm" data-nav="music" title="Music" aria-label="Music">
                <span aria-hidden="true">🎵</span>
                <span class="hidden lg:inline">Music</span>
            </a>
        </nav>
        <button id="menuBtn" class="p-2 rounded-lg hover:bg-gray-700 transition-colors sm:hidden" title="Menu">
      <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
        <line x1="3" y1="6" x2="21" y2="6"/><line x1="3" y1="12" x2="21" y2="12"/><line x1="3" y1="18" x2="21" y2="18"/>
      </svg>
    </button>
        <div id="menuDropdown" class="hidden absolute left-0 top-11 w-44 bg-gray-800 border border-gray-700 rounded-xl shadow-xl z-50 py-1 sm:hidden">
      <a href="#/home"  class="flex items-center gap-2 px-4 py-2.5 hover:bg-gray-700 text-sm" data-nav="home">🏠 Home</a>
      <a href="#/music" class="flex items-center gap-2 px-4 py-2.5 hover:bg-gray-700 text-sm" data-nav="music">🎵 Music</a>
    </div>
  </div>

    <div id="musicHeader" class="hidden flex-1 flex items-center gap-2 min-w-0 px-2">
        <button id="musicToggleBtn" class="flex-none w-11 h-11 flex items-center justify-center rounded-full bg-gray-700 hover:bg-gray-600 transition-colors text-base" title="Play/Pause">&#9654;</button>
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
                <span id="ttsMuteLabel" class="text-xs text-gray-300">Mute TTS output</span>
                <button id="ttsMuteToggle" data-action="toggle-tts-mute" class="relative w-11 h-6 rounded-full bg-gray-700 border border-gray-600 transition-colors"><span class="absolute top-1 left-1 w-4 h-4 rounded-full bg-gray-200 transition-transform"></span></button>
            </div>
            <div class="flex items-center justify-between px-2 py-1">
                <span id="browserAudioLabel" class="text-xs text-gray-300">Browser audio streaming</span>
                <button id="browserAudioToggle" data-action="toggle-browser-audio" class="relative w-11 h-6 rounded-full bg-gray-700 border border-gray-600 transition-colors"><span class="absolute top-1 left-1 w-4 h-4 rounded-full bg-gray-200 transition-transform"></span></button>
            </div>
            <div class="flex items-center justify-between px-2 py-1">
                <span id="continuousModeLabel" class="text-xs text-gray-300">Continuous mode</span>
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
    <div id="scrollDownWrap" class="hidden justify-center pb-2">
        <button id="scrollDownBtn" data-action="chat-scroll-down" type="button" class="px-3 py-1.5 rounded-lg text-xs bg-gray-800 hover:bg-gray-700 transition-colors">Scroll down</button>
    </div>
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
const PREF_TTS_MUTED = 'openclaw.ui.ttsMuted';
const PREF_BROWSER_AUDIO = 'openclaw.ui.browserAudioEnabled';
const PREF_CONTINUOUS = 'openclaw.ui.continuousMode';
const PREF_CHAT_FOLLOW = 'openclaw.ui.chatFollowLatest';
const CHAT_CACHE_VERSION = 1;
const PENDING_ACTION_TIMEOUT_MS = 8000;
const MUSIC_LOAD_PLAYLIST_TIMEOUT_MS = 30000;
const INLINE_ERROR_TTL_MS = 7000;
const MUSIC_LIBRARY_SEARCH_MIN_LEN = 3;

const S = {{
  ws:null, wsConnected:false,
  micEnabled:!MIC_STARTS_DISABLED,
  voice_state:'idle', wake_state:'asleep', tts_playing:false, mic_rms:0,
    chat:[], music:{{state:'stop',title:'',artist:'',queue_length:0,elapsed:0,duration:0,position:-1,loaded_playlist:''}},
    chatThreads:[], activeChatId:'active', selectedChatId:'active', chatSidebarOpen:true,
        chatFollowLatest:true,
    musicQueue:[], musicPlaylists:[], musicLibraryResults:[],
        musicQueueFilter:'', musicQueueSelectionByIds:{{}}, musicQueueLastCheckedId:null,
        musicAddMode:false, musicAddQuery:'', musicAddSelection:{{}}, musicAddLastCheckedFile:'', musicAddHasSearched:false,
        musicAddSearchPending:false, musicAddPendingQuery:'',
        musicAddSearchLimit:200,
        musicNewPlaylistName:'',
        musicPlaylistModalOpen:false, musicPlaylistModalMode:'', musicPlaylistModalName:'',
        timers:[], page:'home',
    audioCtx:null, mediaStream:null, processor:null, lastLevel:0,
    feedbackAudioCtx:null,
    captureWorkletModuleReady:false,
    ttsMuted:true, browserAudioEnabled:true, continuousMode:false,
    pendingChatSends:new Set(), nextClientMsgId:1,
    nextMusicActionId:1, pendingMusicActions:{{}},
    musicLoadRetryPending:null, musicLoadRetryAttempted:false,
    nextTimerActionId:1, pendingTimerActions:{{}},
    nextSettingActionId:1, pendingSettingActions:{{}},
    settingActionErrors:{{}}, timerActionErrors:{{}},
    musicActionError:'', musicActionErrorTs:0,
    lastStatusRev:0, lastMusicRev:0, lastTimersRev:0, lastUiControlRev:0,
    wsDebug:{{ status:'init', lastCloseCode:null, lastCloseReason:'', lastError:'' }},
    wsManualDisconnect:false, wsReconnectTimer:null,
    wsPingTimer:null,
    captureRetryTimer:null,
    lastAudioInputCount:null,
    scrollToBottomPending:false,
    autoScrollUntilTs:0,
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

function readBoolPref(key, fallback){{
    try {{
        const raw = localStorage.getItem(key);
        if (raw === null || raw === undefined || raw === '') return !!fallback;
        return String(raw).toLowerCase() === 'true';
    }} catch(_) {{
        return !!fallback;
    }}
}}

function writeBoolPref(key, value){{
    try {{ localStorage.setItem(key, value ? 'true' : 'false'); }} catch(_) {{}}
}}

function canSearchMusicLibrary(query){{
    return String(query||'').trim().length >= MUSIC_LIBRARY_SEARCH_MIN_LEN;
}}

function submitMusicLibrarySearch(){{
    submitMusicLibrarySearchWithLimit(undefined);
}}

function submitMusicLibrarySearchWithLimit(limitOverride){{
    const query = String(S.musicAddQuery||'').trim();
    if(!canSearchMusicLibrary(query)) return;
    const nextLimit = Math.max(1, Number(limitOverride||S.musicAddSearchLimit||200) || 200);
    S.musicAddHasSearched = true;
    S.musicAddSearchPending = true;
    S.musicAddPendingQuery = query;
    S.musicAddSearchLimit = nextLimit;
    S.musicLibraryResults = [];
    if(S.page==='music' && S.musicAddMode) renderMusicPage(document.getElementById('main'));
    sendAction({{type:'music_search_library', query, limit: nextLimit}});
}}

function getChatCacheKey(){{
    return 'openclaw.ui.chatCache.v'+CHAT_CACHE_VERSION+'::'+location.origin+'::'+WS_PORT;
}}

function normalizeChatMessage(m){{
    if(!m || typeof m!=='object') return null;
    const msg=Object.assign({{}}, m);
    if(msg.id===undefined||msg.id===null) msg.id = 'msg-'+Math.random().toString(36).slice(2,10);
    if(msg.ts===undefined||msg.ts===null) msg.ts = Date.now()/1000;
    return msg;
}}

function normalizeChatThread(t){{
    if(!t || typeof t!=='object') return null;
    const thread=Object.assign({{}}, t);
    thread.id = String(thread.id || ('thread-'+Math.random().toString(36).slice(2,10)));
    thread.title = String(thread.title || 'Untitled');
    thread.messages = Array.isArray(thread.messages) ? thread.messages.map(normalizeChatMessage).filter(Boolean) : [];
    const now = Date.now()/1000;
    thread.created_ts = Number(thread.created_ts || thread.updated_ts || now) || now;
    thread.updated_ts = Number(thread.updated_ts || thread.created_ts || now) || now;
    return thread;
}}

function mergeChatThreads(serverThreads, cachedThreads){{
    const merged = new Map();
    (Array.isArray(cachedThreads)?cachedThreads:[]).map(normalizeChatThread).filter(Boolean).forEach(t=>merged.set(t.id, t));
    (Array.isArray(serverThreads)?serverThreads:[]).map(normalizeChatThread).filter(Boolean).forEach(t=>{{
        const existing = merged.get(t.id);
        if(!existing || Number(t.updated_ts||0) >= Number(existing.updated_ts||0)) merged.set(t.id, t);
    }});
    return [...merged.values()].sort((a,b)=>Number(b.updated_ts||0)-Number(a.updated_ts||0));
}}

function persistChatCache(){{
    try {{
        const payload = {{
            version: CHAT_CACHE_VERSION,
            saved_ts: Date.now()/1000,
            activeChatId: String(S.activeChatId||'active'),
            selectedChatId: String(S.selectedChatId||'active'),
            chat: (Array.isArray(S.chat)?S.chat:[]).map(normalizeChatMessage).filter(Boolean),
            chatThreads: (Array.isArray(S.chatThreads)?S.chatThreads:[]).map(normalizeChatThread).filter(Boolean),
        }};
        localStorage.setItem(getChatCacheKey(), JSON.stringify(payload));
    }} catch(_) {{}}
}}

function hydrateChatCache(){{
    try {{
        const raw = localStorage.getItem(getChatCacheKey());
        if(!raw) return;
        const data = JSON.parse(raw);
        if(!data || typeof data!=='object') return;
        if(Array.isArray(data.chat)) S.chat = data.chat.map(normalizeChatMessage).filter(Boolean);
        if(Array.isArray(data.chatThreads)) S.chatThreads = data.chatThreads.map(normalizeChatThread).filter(Boolean);
        if(data.activeChatId) S.activeChatId = String(data.activeChatId);
        if(data.selectedChatId) S.selectedChatId = String(data.selectedChatId);
    }} catch(_) {{}}
}}

function applyServerChatState(chat, chatThreads, activeChatId, replaceThreads=false){{
    if(Array.isArray(chat)) S.chat = chat.map(normalizeChatMessage).filter(Boolean);
    if(Array.isArray(chatThreads)){{
        S.chatThreads = replaceThreads
            ? chatThreads.map(normalizeChatThread).filter(Boolean).sort((a,b)=>Number(b.updated_ts||0)-Number(a.updated_ts||0))
            : mergeChatThreads(chatThreads, S.chatThreads);
    }}
    if(activeChatId) S.activeChatId = String(activeChatId);
    if(!S.selectedChatId) S.selectedChatId = 'active';
    const selected = String(S.selectedChatId||'active');
    if(selected!=='active' && !(S.chatThreads||[]).some(t=>String(t.id||'')===selected)) S.selectedChatId = 'active';
    persistChatCache();
}}

function loadUiPrefs(){{
    S.ttsMuted = readBoolPref(PREF_TTS_MUTED, true);
    S.browserAudioEnabled = readBoolPref(PREF_BROWSER_AUDIO, true);
    S.continuousMode = readBoolPref(PREF_CONTINUOUS, false);
    S.chatFollowLatest = readBoolPref(PREF_CHAT_FOLLOW, true);
}}

function updateChatFollowToggleState(){{
    const btn=document.getElementById('chatFollowToggle');
    if(!btn) return;
    const on=!!S.chatFollowLatest;
    btn.textContent=on?'Follow latest: On':'Follow latest: Off';
    btn.classList.toggle('bg-emerald-700', on);
    btn.classList.toggle('hover:bg-emerald-600', on);
    btn.classList.toggle('bg-gray-800', !on);
    btn.classList.toggle('hover:bg-gray-700', !on);
}}

function pushUiPrefsToServer(){{
    if(!S.ws || S.ws.readyState!==WebSocket.OPEN) return;
    if(!S.pendingSettingActions['tts_mute_set']) sendSettingAction('tts_mute_set', !!S.ttsMuted);
    if(!S.pendingSettingActions['browser_audio_set']) sendSettingAction('browser_audio_set', !!S.browserAudioEnabled);
    if(!S.pendingSettingActions['continuous_mode_set']) sendSettingAction('continuous_mode_set', !!S.continuousMode);
}}

function applyMicControlToggles(){{
        applyToggle(document.getElementById('ttsMuteToggle'), !!S.ttsMuted);
        applyToggle(document.getElementById('browserAudioToggle'), !!S.browserAudioEnabled);
        applyToggle(document.getElementById('continuousModeToggle'), !!S.continuousMode);
    const ttsBtn=document.getElementById('ttsMuteToggle');
    const browserBtn=document.getElementById('browserAudioToggle');
    const contBtn=document.getElementById('continuousModeToggle');
    const pend=S.pendingSettingActions||{{}};
    const ttsPending=!!pend['tts_mute_set'];
    const browserPending=!!pend['browser_audio_set'];
    const contPending=!!pend['continuous_mode_set'];
    [[ttsBtn,ttsPending],[browserBtn,browserPending],[contBtn,contPending]].forEach(([btn,pending])=>{{ if(!btn) return; btn.disabled=!!pending; btn.classList.toggle('opacity-60',!!pending); btn.classList.toggle('cursor-not-allowed',!!pending); }});
    const ttsErr=(S.settingActionErrors&&S.settingActionErrors['tts_mute_set'])?S.settingActionErrors['tts_mute_set'].msg:'';
    const browserErr=(S.settingActionErrors&&S.settingActionErrors['browser_audio_set'])?S.settingActionErrors['browser_audio_set'].msg:'';
    const contErr=(S.settingActionErrors&&S.settingActionErrors['continuous_mode_set'])?S.settingActionErrors['continuous_mode_set'].msg:'';
    const ttsLabel=document.getElementById('ttsMuteLabel');
    const browserLabel=document.getElementById('browserAudioLabel');
    const contLabel=document.getElementById('continuousModeLabel');
    if(ttsLabel) ttsLabel.textContent='Mute TTS output'+(ttsErr?' ⚠ '+ttsErr:'');
    if(browserLabel) browserLabel.textContent='Browser audio streaming'+(browserErr?' ⚠ '+browserErr:'');
    if(contLabel) contLabel.textContent='Continuous mode'+(contErr?' ⚠ '+contErr:'');
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

function startWsPingTimer(){{
    if(S.wsPingTimer) clearInterval(S.wsPingTimer);
    S.wsPingTimer=setInterval(()=>{{
        if(S.ws && S.ws.readyState === WebSocket.OPEN){{
            try {{ S.ws.send(JSON.stringify({{type:'ping'}})); }} catch(_) {{}}
        }}
    }}, 30000);
}}

function stopWsPingTimer(){{
    if(S.wsPingTimer){{ clearInterval(S.wsPingTimer); S.wsPingTimer=null; }}
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

function isChatAtBottom(){{
    const area=document.getElementById('chatArea');
    if(!area) return true;
    return (area.scrollTop + area.clientHeight) >= (area.scrollHeight - 8);
}}

function updateScrollDownButton(){{
    const wrap=document.getElementById('scrollDownWrap');
    if(!wrap) return;
    const area=document.getElementById('chatArea');
    if(!area || S.page!=='home'){{
        wrap.classList.add('hidden');
        wrap.classList.remove('flex');
        return;
    }}
    const overflow=area.scrollHeight > (area.clientHeight + 1);
    const atBottom=isChatAtBottom();
    const shouldShow=overflow && !atBottom;
    wrap.classList.toggle('hidden', !shouldShow);
    wrap.classList.toggle('flex', shouldShow);
}}

function requestScrollToBottomBurst(){{
    S.scrollToBottomPending=true;
    S.autoScrollUntilTs=Date.now()+12000;
}}

function getPage(){{ const h=location.hash.replace('#',''); return h==='/music'?'music':'home'; }}
function navigate(p){{ location.hash='#/'+p; }}
function updateNavActiveState(){{
    document.querySelectorAll('[data-nav]').forEach(el=>{{
        const isActive=(el.dataset.nav||'')===S.page;
        el.classList.toggle('bg-gray-700', isActive);
        el.classList.toggle('text-white', isActive);
        el.classList.toggle('text-gray-300', !isActive);
    }});
}}
window.addEventListener('hashchange',()=>{{ S.page=getPage(); renderPage(); updateNavActiveState(); closeMenu(); }});

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
        persistChatCache();
        renderPage();
        return;
    }}

    const followLatestBtn = e.target.closest('[data-action="chat-follow-toggle"]');
    if (followLatestBtn) {{
        S.chatFollowLatest = !S.chatFollowLatest;
        writeBoolPref(PREF_CHAT_FOLLOW, !!S.chatFollowLatest);
        updateChatFollowToggleState();
        if(S.chatFollowLatest) scrollChat();
        return;
    }}

    const scrollDownBtn = e.target.closest('[data-action="chat-scroll-down"]');
    if (scrollDownBtn) {{
        scrollChat();
        return;
    }}

    const timerBtn = e.target.closest('[data-action="timer-cancel"]');
    if (timerBtn) {{
        sendTimerAction('timer_cancel','timer_id', timerBtn.dataset.timerId);
        return;
    }}

    const alarmBtn = e.target.closest('[data-action="alarm-cancel"]');
    if (alarmBtn) {{
        sendTimerAction('alarm_cancel','alarm_id', alarmBtn.dataset.alarmId);
        return;
    }}

    const browserAudioToggle = e.target.closest('[data-action="toggle-browser-audio"]');
    if (browserAudioToggle) {{
        e.stopPropagation();
        if(S.pendingSettingActions['browser_audio_set']) return;
        sendSettingAction('browser_audio_set', !S.browserAudioEnabled);
        return;
    }}

    const ttsMuteToggle = e.target.closest('[data-action="toggle-tts-mute"]');
    if (ttsMuteToggle) {{
        e.stopPropagation();
        if(S.pendingSettingActions['tts_mute_set']) return;
        sendSettingAction('tts_mute_set', !S.ttsMuted);
        return;
    }}

    const continuousToggle = e.target.closest('[data-action="toggle-continuous-mode"]');
    if (continuousToggle) {{
        e.stopPropagation();
        if(S.pendingSettingActions['continuous_mode_set']) return;
        sendSettingAction('continuous_mode_set', !S.continuousMode);
        return;
    }}

    const queueCb = e.target.closest('[data-action="music-queue-select"]');
    if (queueCb) {{
        e.stopPropagation();
        const songId = String(queueCb.dataset.songId||'').trim();
        const checked = !!queueCb.checked;
        if (e.shiftKey && S.musicQueueLastCheckedId !== null) {{
            const boxes = [...document.querySelectorAll('[data-action="music-queue-select"]')];
            const ids = boxes.map(x=>String(x.dataset.songId||'').trim());
            const a = ids.indexOf(String(S.musicQueueLastCheckedId));
            const b = ids.indexOf(songId);
            if (a >= 0 && b >= 0) {{
                const lo = Math.min(a,b), hi = Math.max(a,b);
                for (let i=lo;i<=hi;i++) S.musicQueueSelectionByIds[ids[i]] = checked;
            }} else {{
                S.musicQueueSelectionByIds[songId] = checked;
            }}
        }} else {{
            S.musicQueueSelectionByIds[songId] = checked;
        }}
        S.musicQueueLastCheckedId = songId;
        if(!checked) delete S.musicQueueSelectionByIds[songId];
        renderMusicPage(document.getElementById('main'));
        return;
    }}

    const addCb = e.target.closest('[data-action="music-add-select"]');
    if (addCb) {{
        const file = String(addCb.dataset.file || '');
        const checked = !!addCb.checked;
        if (e.shiftKey && S.musicAddLastCheckedFile) {{
            const boxes = [...document.querySelectorAll('[data-action="music-add-select"]')];
            const ordered = boxes.map(x=>String(x.dataset.file||''));
            const a = ordered.indexOf(String(S.musicAddLastCheckedFile));
            const b = ordered.indexOf(file);
            if (a >= 0 && b >= 0) {{
                const lo = Math.min(a,b), hi = Math.max(a,b);
                for (let i=lo;i<=hi;i++) {{ const k = ordered[i]; if(k) S.musicAddSelection[k] = checked; }}
            }} else if (file) {{
                S.musicAddSelection[file] = checked;
            }}
        }} else if (file) {{
            S.musicAddSelection[file] = checked;
        }}
        S.musicAddLastCheckedFile = file;
        if(!checked && file) delete S.musicAddSelection[file];
        renderMusicPage(document.getElementById('main'));
        return;
    }}

    const selectAllBtn = e.target.closest('[data-action="music-select-all"]');
    if (selectAllBtn) {{
        const boxes=[...document.querySelectorAll('[data-action="music-queue-select"]')];
        boxes.forEach(cb=>{{
            const songId = String(cb.dataset.songId || '').trim();
            if(songId) S.musicQueueSelectionByIds[songId] = true;
        }});
        renderMusicPage(document.getElementById('main'));
        return;
    }}

    const selectNoneBtn = e.target.closest('[data-action="music-select-none"]');
    if (selectNoneBtn) {{
        S.musicQueueSelectionByIds={{}};
        S.musicQueueLastCheckedId=null;
        renderMusicPage(document.getElementById('main'));
        return;
    }}

    const addModeBtn = e.target.closest('[data-action="music-add-open"]');
    if (addModeBtn) {{
        S.musicAddMode = true;
        S.musicAddSelection = {{}};
        S.musicAddHasSearched = false;
        S.musicAddSearchPending = false;
        S.musicAddPendingQuery = '';
        S.musicAddSearchLimit = 200;
        sendAction({{type:'music_list_playlists'}});
        renderMusicPage(document.getElementById('main'));
        return;
    }}

    const addSearchBtn = e.target.closest('[data-action="music-add-search-submit"]');
    if (addSearchBtn && !addSearchBtn.disabled) {{
        submitMusicLibrarySearch();
        return;
    }}

    const addSearchMoreBtn = e.target.closest('[data-action="music-add-search-more"]');
    if (addSearchMoreBtn && !addSearchMoreBtn.disabled) {{
        submitMusicLibrarySearchWithLimit((Number(S.musicAddSearchLimit)||200) + 200);
        return;
    }}

    const addModeCancel = e.target.closest('[data-action="music-add-cancel"]');
    if (addModeCancel) {{
        S.musicAddMode = false;
        S.musicAddSearchPending = false;
        S.musicAddPendingQuery = '';
        renderMusicPage(document.getElementById('main'));
        return;
    }}

    const addSelectedBtn = e.target.closest('[data-action="music-add-selected"]');
    if (addSelectedBtn) {{
        const files = Object.keys(S.musicAddSelection).filter(k=>S.musicAddSelection[k]);
        if(files.length) sendMusicAction('music_add_files', {{files}});
        S.musicAddSelection = {{}};
        S.musicAddLastCheckedFile='';
        renderMusicPage(document.getElementById('main'));
        return;
    }}

    const addSelectAllBtn = e.target.closest('[data-action="music-add-select-all"]');
    if (addSelectAllBtn) {{
        (S.musicLibraryResults||[]).forEach(item=>{{
            const file=String((item&&item.file)||'').trim();
            if(file) S.musicAddSelection[file]=true;
        }});
        renderMusicPage(document.getElementById('main'));
        return;
    }}

    const addSelectNoneBtn = e.target.closest('[data-action="music-add-select-none"]');
    if (addSelectNoneBtn) {{
        S.musicAddSelection={{}};
        S.musicAddLastCheckedFile='';
        renderMusicPage(document.getElementById('main'));
        return;
    }}

    const removeSelectedBtn = e.target.closest('[data-action="music-remove-selected"]');
    if (removeSelectedBtn) {{
        const song_ids = Object.keys(S.musicQueueSelectionByIds).filter(k=>S.musicQueueSelectionByIds[k]);
        if(song_ids.length) sendMusicAction('music_remove_selected', {{positions: [], song_ids}});
        S.musicQueueSelectionByIds = {{}};
        renderMusicPage(document.getElementById('main'));
        return;
    }}

    const openSavePlaylistBtn = e.target.closest('[data-action="music-open-save-playlist"]');
    if (openSavePlaylistBtn) {{
        const loadedName = String((S.music && S.music.loaded_playlist) || '').trim();
        if (loadedName) {{
            sendMusicAction('music_save_playlist', {{name: loadedName}});
            return;
        }}
        S.musicPlaylistModalOpen = true;
        S.musicPlaylistModalMode = 'save';
        S.musicPlaylistModalName = '';
        renderMusicPage(document.getElementById('main'));
        return;
    }}

    const openCreateSelectedBtn = e.target.closest('[data-action="music-open-create-selected"]');
    if (openCreateSelectedBtn) {{
        const positions = (S.musicQueue||[])
            .filter(item=>!!S.musicQueueSelectionByIds[String(item.id||'').trim()])
            .map(item=>Number(item.pos))
            .filter(Number.isFinite);
        if(!positions.length) return;
        S.musicPlaylistModalOpen = true;
        S.musicPlaylistModalMode = 'selected';
        S.musicPlaylistModalName = '';
        renderMusicPage(document.getElementById('main'));
        return;
    }}

    const modalCancelBtn = e.target.closest('[data-action="music-modal-cancel"]');
    if (modalCancelBtn) {{
        S.musicPlaylistModalOpen = false;
        S.musicPlaylistModalMode = '';
        S.musicPlaylistModalName = '';
        renderMusicPage(document.getElementById('main'));
        return;
    }}

    const modalConfirmBtn = e.target.closest('[data-action="music-modal-confirm"]');
    if (modalConfirmBtn) {{
        const mode = String(S.musicPlaylistModalMode||'').trim();
        const name = String(S.musicPlaylistModalName||'').trim();
        if(mode==='save'){{
            if(!name) return;
            sendMusicAction('music_save_playlist', {{name}});
        }}else if(mode==='selected'){{
            if(!name) return;
            const positions = (S.musicQueue||[])
                .filter(item=>!!S.musicQueueSelectionByIds[String(item.id||'').trim()])
                .map(item=>Number(item.pos))
                .filter(Number.isFinite);
            if(positions.length) sendMusicAction('music_create_playlist', {{name, positions}});
        }}else if(mode==='delete'){{
            if(!name) return;
            sendMusicAction('music_delete_playlist', {{name}});
        }}
        S.musicPlaylistModalOpen = false;
        S.musicPlaylistModalMode = '';
        S.musicPlaylistModalName = '';
        renderMusicPage(document.getElementById('main'));
        return;
    }}

    const loadPlaylistBtn = e.target.closest('[data-action="music-load-playlist"]');
    if (loadPlaylistBtn) {{
        const name = String(loadPlaylistBtn.dataset.playlistName || '').trim();
        if(name){{
            S.music.loaded_playlist = name;
            sendMusicAction('music_load_playlist', {{name}});
        }}
        return;
    }}

    const openDeletePlaylistBtn = e.target.closest('[data-action="music-open-delete-playlist"]');
    if (openDeletePlaylistBtn) {{
        const name = String(openDeletePlaylistBtn.dataset.playlistName || '').trim();
        if(!name) return;
        S.musicPlaylistModalOpen = true;
        S.musicPlaylistModalMode = 'delete';
        S.musicPlaylistModalName = name;
        renderMusicPage(document.getElementById('main'));
        return;
    }}

    const refreshPlaylistsBtn = e.target.closest('[data-action="music-refresh-playlists"]');
    if (refreshPlaylistsBtn) {{
        sendAction({{type:'music_list_playlists'}});
        return;
    }}

    const musicRow = e.target.closest('[data-action="music-play-track"]');
    if (musicRow) {{
        if (e.target.closest('input[type="checkbox"]')) return;
        const pos = Number(musicRow.dataset.position);
        sendMusicAction('music_play_track', {{position: pos}});
        return;
    }}

    const musicToggle = e.target.closest('[data-action="music-toggle"]');
    if (musicToggle && !musicToggle.disabled) {{
        const isCurrentlyPlaying = normalizeMusicState(S.music.state) === 'play';
        sendMusicAction(isCurrentlyPlaying ? 'music_stop' : 'music_toggle');
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
    requestScrollToBottomBurst();
    updateChatComposerState();
    sendAction({{type:'chat_text', text, client_msg_id:clientMsgId}});
    input.value = '';
    if (S.selectedChatId !== 'active') {{
        S.selectedChatId = 'active';
        persistChatCache();
        renderPage();
    }}
}});

document.addEventListener('keydown', e => {{
    const t = e.target;
    if(!t || t.id!=='musicAddSearch') return;
    if(e.key!=='Enter') return;
    e.preventDefault();
    submitMusicLibrarySearch();
}});

document.addEventListener('input', e => {{
    const t = e.target;
    if(!t) return;
    if(t.id==='musicQueueSearch'){{
        const start = (typeof t.selectionStart === 'number') ? t.selectionStart : null;
        const end = (typeof t.selectionEnd === 'number') ? t.selectionEnd : null;
        S.musicQueueFilter = String(t.value||'');
        renderMusicPage(document.getElementById('main'));
        setTimeout(() => {{
            const el = document.getElementById('musicQueueSearch');
            if(!el) return;
            if(el !== document.activeElement) el.focus();
            if(start !== null && end !== null && typeof el.setSelectionRange === 'function') {{
                const maxLen = String(el.value||'').length;
                const nextStart = Math.max(0, Math.min(start, maxLen));
                const nextEnd = Math.max(0, Math.min(end, maxLen));
                try {{ el.setSelectionRange(nextStart, nextEnd); }} catch(_) {{}}
            }}
        }}, 0);
        return;
    }}
    if(t.id==='musicAddSearch'){{
        S.musicAddQuery = String(t.value||'');
        S.musicAddHasSearched = false;
        S.musicAddSearchPending = false;
        S.musicAddPendingQuery = '';
        const canSearch=canSearchMusicLibrary(S.musicAddQuery);
        const btn=document.getElementById('musicAddSearchSubmit');
        if(btn){{
            btn.disabled=!canSearch;
            btn.style.opacity=canSearch?'':'0.5';
            btn.style.cursor=canSearch?'':'not-allowed';
            btn.textContent='Search';
        }}
        const hint=document.getElementById('musicAddMinHint');
        if(hint) hint.classList.toggle('hidden', canSearch);
        return;
    }}
    if(t.id==='musicNewPlaylistName'){{
        S.musicNewPlaylistName = String(t.value||'');
        return;
    }}
    if(t.id==='musicPlaylistModalName'){{
        S.musicPlaylistModalName = String(t.value||'');
        return;
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
    btn.classList.remove('border-transparent');
        if(S.hotword_active) btn.classList.add('bg-green-900','border-green-500');
    else if(!S.micEnabled) btn.classList.add('bg-red-900','border-red-600');
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

document.getElementById('musicToggleBtn').addEventListener('click',()=>{{if(Object.keys(S.pendingMusicActions||{{}}).length>0) return; sendMusicAction(S.music.state==='play'?'music_stop':'music_toggle');}});
function normalizeMusicState(v){{
    const s=String(v||'').trim().toLowerCase();
    if(s==='play'||s==='playing') return 'play';
    if(s==='pause'||s==='paused') return 'pause';
    if(s==='stop'||s==='stopped'||s==='idle') return 'stop';
    return s||'stop';
}}
function applyMusicHeader(){{
    const m=S.music;
    // Normalize and cache state for consistency
    m.state=normalizeMusicState(m.state);
    const header=document.getElementById('musicHeader');
    const titleEl=document.getElementById('musicTitle');
    const artistEl=document.getElementById('musicArtist');
    const btn=document.getElementById('musicToggleBtn');
    if(!header||!titleEl||!artistEl||!btn) return;
    const pendingCount=Object.keys(S.pendingMusicActions||{{}}).length;
    const active=m.state==='play'||(m.state==='pause'&&((m.title&&String(m.title).trim())||Number(m.queue_length||0)>0));
    header.classList.toggle('hidden',!active);
    titleEl.textContent=(m.title&&String(m.title).trim())||'\u2014';
    artistEl.textContent=(m.artist&&String(m.artist).trim())||'\u2014';
    btn.disabled=pendingCount>0;
    btn.classList.toggle('opacity-60', pendingCount>0);
    btn.classList.toggle('cursor-not-allowed', pendingCount>0);
    btn.textContent=pendingCount>0?'\u2026':(m.state==='play'?'\u23f9':'\u25b6');
    btn.title=pendingCount>0?'Processing\u2026':(m.state==='play'?'Stop':'Play');
}}

function renderTimerBar(){{
  const bar=document.getElementById('timerBar');
    const visibleTimers=S.timers.filter(t=>{{
        const kind=String(t.kind||'timer').toLowerCase();
        const rem=Number(t.remaining_seconds);
        if(!Number.isFinite(rem)) return false;
        if(kind==='alarm' && rem<=0 && !t.ringing) return false;
        return true;
    }});
    if(!visibleTimers.length){{ bar.classList.add('hidden'); bar.innerHTML=''; return; }}
  bar.classList.remove('hidden');
    bar.innerHTML=visibleTimers.map(t=>{{
    const rem=Math.max(0,Math.round(t.remaining_seconds));
    const mm=String(Math.floor(rem/60)).padStart(2,'0'),ss=String(rem%60).padStart(2,'0');
        const kind=String(t.kind||'timer').toLowerCase();
        const isAlarm=kind==='alarm';
        const actionAttr=isAlarm?'alarm-cancel':'timer-cancel';
        const idAttr=isAlarm?' data-alarm-id="'+esc(t.id)+'"':' data-timer-id="'+esc(t.id)+'"';
                const pendingKey=(isAlarm?'alarm_cancel:':'timer_cancel:')+String(t.id||'');
                const isPending=!!(S.pendingTimerActions&&S.pendingTimerActions[pendingKey]);
                const timerErr=(S.timerActionErrors&&S.timerActionErrors[pendingKey])?S.timerActionErrors[pendingKey].msg:'';
        const icon=isAlarm?'\u23f0':'\u23f1';
        const baseCls=isAlarm
            ?'flex items-center gap-1 px-3 py-1 rounded-full bg-red-800 hover:bg-red-700 text-xs transition-colors'
            :'flex items-center gap-1 px-3 py-1 rounded-full bg-amber-700 hover:bg-amber-600 text-xs transition-colors';
        const label=isAlarm?(t.label||'Alarm'):(t.label||'Timer');
                const disabledAttr=isPending?' disabled style="opacity:.55;cursor:not-allowed"':'';
                const pendingTxt=isPending?' \u2026':'';
                const errTxt=timerErr?' ⚠':'';
                return '<button class="'+baseCls+'" data-action="'+actionAttr+'"'+idAttr+disabledAttr+' title="'+esc(timerErr||'Click to cancel')+'">'+icon+' '+esc(label)+' '+mm+':'+ss+pendingTxt+errTxt+'</button>';
  }}).join('');
}}

function renderPage(){{
  const main=document.getElementById('main');
    const dock=document.getElementById('chatComposerDock');
    if(S.page==='music'){{
        if(dock) dock.classList.add('hidden');
        renderMusicPage(main);
        sendAction({{type:'music_list_playlists'}});
    }} else {{
        if(dock) dock.classList.remove('hidden');
        if(main && main.dataset.page==='home'){{
            const selected = S.selectedChatId || 'active';
            renderThreadList(selected);
            renderChatMessages(selected);
            updateChatFollowToggleState();
        }} else {{
            renderHomePage(main);
        }}
        updateChatComposerState();
    }}
    applyMusicHeader();
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
                +'<div class="flex items-center gap-2">'
                    +'<button data-action="chat-sidebar-toggle" class="px-2.5 py-1.5 rounded-lg text-xs bg-gray-800 hover:bg-gray-700 transition-colors">'+sidebarToggleText+'</button>'
                    +'<button id="chatFollowToggle" data-action="chat-follow-toggle" class="px-2.5 py-1.5 rounded-lg text-xs transition-colors"></button>'
                +'</div>'
                +'<button data-action="chat-new" class="px-3 py-1.5 rounded-lg text-xs bg-blue-700 hover:bg-blue-600 transition-colors">New</button>'
            +'</div>'
            +'<div id="chatArea" class="flex-1 overflow-y-auto px-4 py-4 space-y-3 min-h-0"></div>'
        +'</div>'
    +'</div>';

    renderThreadList(selected);
    renderChatMessages(selected);
    const area=document.getElementById('chatArea');
    if(area){{
        area.addEventListener('scroll', ()=>{{
            if(!isChatAtBottom()) S.autoScrollUntilTs=0;
            updateScrollDownButton();
        }}, {{passive:true}});
    }}
    updateChatFollowToggleState();
    updateScrollDownButton();
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
    const prevScrollTop=area.scrollTop;
    const prevScrollHeight=area.scrollHeight;
    const nearBottom=(prevScrollHeight-area.clientHeight-prevScrollTop)<=80;
    const openDetailKeys=new Set();
    area.querySelectorAll('details[data-detail-key]').forEach((el)=>{{
        if(el.open){{
            const k=el.getAttribute('data-detail-key');
            if(k) openDetailKeys.add(k);
        }}
    }});
    area.innerHTML='';
    const msgs = getSelectedMessages(selectedId);
        const collated = collateChatMessages(msgs);
        collated.forEach(m=>area.appendChild(mkBubble(m)));
    area.querySelectorAll('details[data-detail-key]').forEach((el)=>{{
        const k=el.getAttribute('data-detail-key');
        if(k && openDetailKeys.has(k)) el.open=true;
    }});
    if(S.scrollToBottomPending){{
        scrollChat();
        S.scrollToBottomPending=false;
        updateScrollDownButton();
        return;
    }}
    const burstActive=Date.now()<Number(S.autoScrollUntilTs||0);
    if((S.chatFollowLatest && nearBottom) || burstActive){{
        scrollChat();
    }} else {{
        area.scrollTop=prevScrollTop;
    }}
    updateScrollDownButton();
}}
function scrollChat(){{ const a=document.getElementById('chatArea'); if(a){{ a.scrollTop=a.scrollHeight; updateScrollDownButton(); }} }}
function collateChatMessages(msgs){{
    const out=[];
    let activeBucket=null;

    const flushBucket=()=>{{
        if(!activeBucket) return;
        if(activeBucket.events.length>0){{
            out.push({{role:'context_group',request_id:activeBucket.reqId,events:activeBucket.events,steps:activeBucket.steps,interim:activeBucket.interim,hasFinal:activeBucket.finals.length>0}});
        }}
        const validStreams=activeBucket.streams.filter(s=>String(s.text||'').trim().length>0);
        if(validStreams.length>0 && activeBucket.finals.length===0){{
            out.push({{
                role:'assistant_stream_group',
                request_id:activeBucket.reqId,
                source:validStreams[0].source||'assistant',
                text:validStreams.map(s=>String(s.text||'').trim()).filter(Boolean).join(' '),
                segments:validStreams,
                latest:validStreams[validStreams.length-1],
            }});
        }}
        activeBucket.finals.forEach(f=>out.push(f));
        activeBucket=null;
    }};

    const ensureActiveBucket=(reqId)=>{{
        if(!activeBucket || activeBucket.reqId!==reqId){{
            flushBucket();
            activeBucket={{reqId,streams:[],steps:[],interim:[],events:[],finals:[]}};
        }}
        return activeBucket;
    }};

    (msgs||[]).forEach(m=>{{
        const role=(m&&m.role)||'';
        const reqId=(m&&m.request_id!==undefined&&m.request_id!==null)?String(m.request_id):null;
        if(role==='user'||role==='system'){{
            flushBucket();
            out.push(m);
            return;
        }}
        if(role==='step'){{
            const bucket=ensureActiveBucket(reqId);
            bucket.steps.push(m);
            bucket.events.push({{kind:'tool',payload:m}});
            return;
        }}
        if(role==='interim'){{
            const bucket=ensureActiveBucket(reqId);
            bucket.interim.push(m);
            bucket.events.push({{kind:'lifecycle',payload:m}});
            return;
        }}
        if(role==='assistant'){{
            const bucket=ensureActiveBucket(reqId);
            const segKind=String((m&&m.segment_kind)||'final').toLowerCase();
            if(segKind==='stream') bucket.streams.push(m);
            else bucket.finals.push(m);
            return;
        }}
        flushBucket();
        out.push(m);
    }});

    flushBucket();
    return out;
}}

const CHAT_MARKDOWN_ALLOWED_TAGS=['p','strong','em','h1','h2','h3','h4','h5','h6','ul','ol','li','code','pre','blockquote','a','hr','table','thead','tbody','tr','th','td','br','del','s'];
const CHAT_MARKDOWN_ALLOWED_ATTR=['href','title'];
const ASSISTANT_CHART_TAG_RE=/<(?:mermaidchart|pyramidchart)>([\s\S]*?)<\/(?:mermaidchart|pyramidchart)>/gi;
let mermaidInitialized=false;
let mermaidRenderSeq=0;

function renderMarkdownHtml(raw){{
    const txt=String(raw||'');
    if(!txt.trim()) return '';
    if(typeof marked==='undefined' || typeof DOMPurify==='undefined') return '';
    try{{
        marked.setOptions({{breaks:true,gfm:true}});
        return DOMPurify.sanitize(marked.parse(txt),{{ALLOWED_TAGS:CHAT_MARKDOWN_ALLOWED_TAGS,ALLOWED_ATTR:CHAT_MARKDOWN_ALLOWED_ATTR}});
    }}catch(_ ){{
        return '';
    }}
}}

function splitAssistantContent(raw){{
    const txt=String(raw||'');
    if(!txt) return [];
    ASSISTANT_CHART_TAG_RE.lastIndex=0;
    const parts=[];
    let lastIndex=0;
    let match;
    while((match=ASSISTANT_CHART_TAG_RE.exec(txt))!==null){{
        if(match.index>lastIndex){{
            const markdownPart=txt.slice(lastIndex, match.index);
            if(markdownPart.trim()) parts.push({{type:'markdown', text:markdownPart}});
        }}
        const chartBody=String(match[1]||'').trim();
        if(chartBody) parts.push({{type:'mermaid', text:chartBody}});
        lastIndex=match.index + match[0].length;
    }}
    if(lastIndex<txt.length){{
        const tail=txt.slice(lastIndex);
        if(tail.trim()) parts.push({{type:'markdown', text:tail}});
    }}
    if(!parts.length && looksLikeMermaidSource(txt)){{
        parts.push({{type:'mermaid', text:txt.trim()}});
    }}
    return parts;
}}

function looksLikeMermaidSource(raw){{
    const txt=String(raw||'').trim();
    if(!txt) return false;
    const lower=txt.toLowerCase();
    if(lower.includes('<mermaidchart>') || lower.includes('<pyramidchart>')) return true;
    if(lower.includes('%%{{init')) return true;
    const keywordHits=[
        /\bflowchart\b/i,
        /\bgraph\s+(td|lr|rl|bt)\b/i,
        /\bsequencediagram\b/i,
        /\bclassdiagram\b/i,
        /\bstatediagram(?:-v2)?\b/i,
        /\berdiagram\b/i,
        /\bjourney\b/i,
        /\bgantt\b/i,
        /\bpie\b/i,
        /\bxychart(?:-beta)?\b/i,
        /\bmindmap\b/i,
        /\btimeline\b/i,
        /\bquadrantchart\b/i,
        /\bbar\b/i,
    ].reduce((acc, re)=>acc+(re.test(txt)?1:0),0);
    const tokenHit=/(-->|-\.->|==>|:::|\bx-?axis\b|\by-?axis\b|\btitle\b)/i.test(txt);
    return keywordHits>=2 || (keywordHits>=1 && tokenHit);
}}

function ensureMermaidInitialized(){{
    if(mermaidInitialized || typeof mermaid==='undefined') return;
    mermaid.initialize({{
        startOnLoad:false,
        securityLevel:'strict',
        theme:'dark',
        fontFamily:'ui-sans-serif, system-ui, sans-serif',
    }});
    mermaidInitialized=true;
}}

async function renderMermaidBlock(target, chartText){{
    target.innerHTML='';
    const label=document.createElement('div');
    label.className='mermaidchart-label';
    label.textContent='Diagram';
    target.appendChild(label);

    const content=document.createElement('div');
    target.appendChild(content);

    if(typeof mermaid==='undefined'){{
        const pre=document.createElement('pre');
        pre.textContent=chartText;
        content.appendChild(pre);
        return;
    }}

    try{{
        ensureMermaidInitialized();
        const renderId='voice-mermaid-'+(++mermaidRenderSeq);
        const rendered=await mermaid.render(renderId, chartText);
        const svg=(rendered && typeof rendered==='object' && rendered.svg) ? rendered.svg : '';
        if(!svg) throw new Error('empty mermaid render');
        content.innerHTML=DOMPurify.sanitize(svg, {{USE_PROFILES:{{svg:true,svgFilters:true}}}});
        if(rendered && typeof rendered.bindFunctions==='function'){{
            rendered.bindFunctions(content);
        }}
    }}catch(err){{
        const pre=document.createElement('pre');
        pre.className='mermaidchart-error';
        pre.textContent='Unable to render diagram.\\n\\n'+chartText;
        content.appendChild(pre);
    }}
}}

function renderAssistantContent(target, raw){{
    const txt=String(raw||'');
    target.textContent='';
    if(!txt.trim()) return;

    const parts=splitAssistantContent(txt);
    if(!parts.length){{
        const html=renderMarkdownHtml(txt);
        if(html) target.innerHTML=html;
        else target.textContent=txt;
        return;
    }}

    target.classList.add('assistant-rich-content');
    const mermaidTasks=[];
    for(const part of parts){{
        if(part.type==='markdown'){{
            const block=document.createElement('div');
            block.className='md-content';
            const html=renderMarkdownHtml(part.text);
            if(html) block.innerHTML=html;
            else block.textContent=part.text;
            target.appendChild(block);
            continue;
        }}
        const block=document.createElement('div');
        block.className='mermaidchart-shell';
        target.appendChild(block);
        mermaidTasks.push(renderMermaidBlock(block, part.text));
    }}
    if(mermaidTasks.length){{
        Promise.allSettled(mermaidTasks).catch(()=>{{}});
    }}
}}

function mkBubble(m){{
  const d=document.createElement('div');
    const role = (m&&m.role)||'';
    d.className='chat-msg flex '+(role==='user'?'justify-end':'justify-start');
        d.setAttribute('data-role', role);

    if(role==='assistant_stream_group'){{
        const wrap=document.createElement('div');
        wrap.className='max-w-xs sm:max-w-sm lg:max-w-md';

        const b=document.createElement('div');
        b.className='px-4 py-2 rounded-2xl rounded-bl-md text-sm leading-relaxed bg-gray-700 text-gray-100 md-content';
        const _rawText=(m.text||'');
        renderAssistantContent(b, _rawText);
        wrap.appendChild(b);
        d.appendChild(wrap);
        return d;
    }}

    if(role==='context_group'){{
        const wrap=document.createElement('div');
        wrap.className='max-w-xs sm:max-w-sm lg:max-w-md space-y-1';
        const reqKey=String((m&&m.request_id)!==undefined && (m&&m.request_id)!==null ? m.request_id : 'na');

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

        const normToolName=(name)=>String(name||'').trim().toLowerCase();
        const isExecTool=(name)=>{{
            const n=normToolName(name);
            return n==='exec' || n.includes('run_in_terminal') || n.includes('run-terminal') || n.includes('terminal');
        }};
        const isWriteTool=(name)=>{{
            const n=normToolName(name);
            return n==='write' || n.includes('write_file') || n.includes('create_file') || n.includes('str_replace') || n.includes('insert');
        }};
        const isReadTool=(name)=>{{
            const n=normToolName(name);
            return n==='read' || n.includes('read_file');
        }};
        const isProcessTool=(name)=>{{
            const n=normToolName(name);
            return n==='process' || n.includes('process');
        }};
        const isWebSearchTool=(name)=>{{
            const n=normToolName(name);
            return n==='web_search' || n.includes('web_search') || n==='websearch' || n.includes('search_web');
        }};
        const isWebGetTool=(name)=>{{
            const n=normToolName(name);
            return n==='web_get' || n.includes('web_get') || n==='webget' || n.includes('fetch_web') || n.includes('fetch_webpage') || n==='web_fetch';
        }};
        const basename=(p)=>{{
            const s=String(p||'').trim();
            if(!s) return '';
            const parts=s.split(/[\\/]+/).filter(Boolean);
            return parts.length?parts[parts.length-1]:s;
        }};
        const pickText=(val)=>{{
            if(val===undefined||val===null) return '';
            if(typeof val==='string') return val;
            if(Array.isArray(val)) return val.map(pickText).filter(Boolean).join('\\n');
            if(typeof val==='object'){{
                const direct=val.content!==undefined?val.content:(val.text!==undefined?val.text:(val.result!==undefined?val.result:(val.output!==undefined?val.output:undefined)));
                if(direct!==undefined) return pickText(direct);
                return toPretty(val);
            }}
            return String(val);
        }};
        const renderMarkdown=(raw)=>{{
            return renderMarkdownHtml(raw);
        }};

        const clampPreviewLines=(raw, maxLines=2)=>{{
            const src=String(raw||'');
            if(!src) return '';
            const lines=src.split(/\\r?\\n/);
            if(lines.length<=maxLines) return src;
            return lines.slice(0, maxLines).join('\\n')+'…';
        }};

        const isTransientLifecycleError=(phase, errText)=>{{
            const p=String(phase||'').toLowerCase();
            if(p==='timeout') return false;
            const txt=String(errText||'').toLowerCase();
            if(!txt) return false;
            return txt.includes('connection error')
                || txt.includes('network error')
                || txt.includes('connection reset')
                || txt.includes('socket closed')
                || txt.includes('disconnected');
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
                const fp=req.filePath||req.file_path||req.path||req.old_path||req.new_path||req.uri;
                if(fp!==undefined) return 'file: '+String(fp);
            }}
            const fp=req.filePath||req.file_path||req.path||req.old_path||req.new_path||req.uri;
            if(fp!==undefined) return 'file: '+String(fp);
            const cmd=req.command||req.cmd||req.argv;
            if(cmd!==undefined) return 'command: '+toPretty(cmd);
            return toPretty(req);
        }};

        const extractResult=(parsed)=>{{
            if(!parsed||typeof parsed!=='object') return '';
            const outCandidates=[parsed.result, parsed.partialResult, parsed.output, parsed.stdout, parsed.stderr, parsed.message, parsed.text, parsed.content];
            for(const c of outCandidates){{
                const rendered=toPretty(c);
                if(rendered&&String(rendered).trim()) return rendered;
            }}
            return '';
        }};

        const extractResultObject=(parsed)=>{{
            if(!parsed||typeof parsed!=='object') return null;
            const outCandidates=[parsed.result, parsed.partialResult, parsed.output, parsed.stdout, parsed.stderr, parsed.message, parsed.text, parsed.content];
            for(const c of outCandidates){{
                if(c===undefined || c===null) continue;
                if(typeof c==='object') return c;
                if(typeof c==='string'){{
                    const t=String(c).trim();
                    if(!t) continue;
                    try{{
                        const j=JSON.parse(t);
                        if(j && typeof j==='object') return j;
                    }}catch(_ ){{}}
                }}
            }}
            return null;
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
            let hasLifecycleHardError=false;
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
                            urls:[],
                            query:'',
                            action:'',
                            fileContent:'',
                            metas:[],
                            isError:null,
                            errorText:'',
                            requestPreview:'',
                            payloads:[],
                            results:[],
                            webResults:'',
                            webContent:'',
                            timeout:false,
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
                        const action=req.action||req.type||req.operation;
                        if(action!==undefined && !g.action) g.action=String(action);
                        const query=req.query||req.q||req.search||req.term;
                        if(query!==undefined && !g.query) g.query=String(query);
                        const reqUrls=[];
                        if(req.url!==undefined) reqUrls.push(req.url);
                        if(req.uri!==undefined) reqUrls.push(req.uri);
                        if(req.urls!==undefined) reqUrls.push(req.urls);
                        reqUrls.flatMap((u)=>Array.isArray(u)?u:[u]).forEach((u)=>{{
                            const s=String(u||'').trim();
                            if(s && !g.urls.includes(s)) g.urls.push(s);
                        }});
                        const fp=req.filePath||req.file_path||req.path||req.old_path||req.new_path||req.uri;
                        if(fp!==undefined){{
                            const f=String(fp);
                            if(!g.files.includes(f)) g.files.push(f);
                        }}
                        if(isWriteTool(toolName) && !g.fileContent){{
                            const writeBody=req.content!==undefined?req.content:(req.text!==undefined?req.text:(req.file_text!==undefined?req.file_text:(req.new_str!==undefined?req.new_str:undefined)));
                            const bodyTxt=pickText(writeBody).trim();
                            if(bodyTxt) g.fileContent=bodyTxt;
                        }}
                    }}

                    if(parsed && typeof parsed==='object'){{
                        if(parsed.meta!==undefined){{
                            const metaTxt=toPretty(parsed.meta);
                            if(metaTxt && !g.metas.includes(metaTxt)) g.metas.push(metaTxt);
                        }}
                        if(parsed.isError!==undefined) g.isError=!!parsed.isError;
                        if(phase==='error') g.isError=true;
                        if(phase==='timeout'){{ g.isError=true; g.timeout=true; }}
                        if(!g.errorText){{
                            const errVal=parsed.error!==undefined?parsed.error:(parsed.stderr!==undefined?parsed.stderr:undefined);
                            const errTxt=pickText(errVal).trim();
                            if(errTxt) g.errorText=errTxt;
                        }}
                        if(/\\btimeout|timed out\\b/i.test(String(g.errorText||''))) g.timeout=true;

                        const resultObj=extractResultObject(parsed);
                        if(resultObj && typeof resultObj==='object'){{
                            const robj=resultObj;
                            if(!g.query){{
                                const q=robj.query!==undefined?robj.query:(robj.search!==undefined?robj.search:undefined);
                                if(q!==undefined) g.query=String(q);
                            }}
                            const outUrl=robj.url!==undefined?robj.url:(robj.uri!==undefined?robj.uri:undefined);
                            if(outUrl!==undefined){{
                                const s=String(outUrl||'').trim();
                                if(s && !g.urls.includes(s)) g.urls.push(s);
                            }}
                            if(Array.isArray(robj.urls)){{
                                robj.urls.forEach((u)=>{{
                                    const s=String(u||'').trim();
                                    if(s && !g.urls.includes(s)) g.urls.push(s);
                                }});
                            }}
                            if(isWebSearchTool(toolName) && !g.webResults && Array.isArray(robj.results)){{
                                g.webResults=toPretty(robj.results);
                            }}
                            if(isWebGetTool(toolName) && !g.webContent){{
                                const c=robj.content!==undefined?robj.content:(robj.text!==undefined?robj.text:undefined);
                                const cText=pickText(c).trim();
                                if(cText) g.webContent=cText;
                            }}
                        }}
                        if((isReadTool(toolName)||isWriteTool(toolName)) && !g.fileContent && parsed.outputText){{
                            const textContent=pickText(parsed.outputText).trim();
                            if(textContent) g.fileContent=textContent;
                        }}
                    }}

                    const resultText=extractResult(parsed) || '';
                    if(resultText && !g.results.includes(resultText)) g.results.push(resultText);
                    if(/\\btimeout|timed out\\b/i.test(String(resultText||''))) g.timeout=true;
                    if((isReadTool(toolName)||isWriteTool(toolName)) && !g.fileContent && resultText){{
                        const fallbackTxt=pickText(resultText).trim();
                        if(fallbackTxt) g.fileContent=fallbackTxt;
                    }}
                    if(isWebSearchTool(toolName) && !g.webResults && resultText){{
                        g.webResults=resultText;
                    }}
                    if(isWebGetTool(toolName) && !g.webContent && resultText){{
                        g.webContent=resultText;
                    }}
                    continue;
                }}

                const name=String(payload.text||payload.name||payload.phase||'lifecycle').toLowerCase();
                const phase=String(payload.phase||'').toLowerCase();
                if(name==='lifecycle' && phase==='start'){{ hasLifecycleStart=true; continue; }}
                if(name==='lifecycle' && phase==='end'){{ hasLifecycleEnd=true; continue; }}
                if(name==='lifecycle' && (phase==='error' || phase==='timeout')){{
                    const lifecycleErrText=String(payload.error||payload.details||'').trim();
                    if(!isTransientLifecycleError(phase, lifecycleErrText)) hasLifecycleHardError=true;
                }}

                const parsed=parseDetails(payload.details);
                let details='';
                if(parsed&&typeof parsed==='object'){{
                    const t=parsed.text!==undefined?parsed.text:(parsed.message!==undefined?parsed.message:parsed.result);
                    details=toPretty(t!==undefined?t:parsed);
                }} else {{
                    details=String(payload.details||'').trim();
                }}
                lifecycleItems.push({{name:String(payload.text||payload.name||payload.phase||'lifecycle'), details}});
                const parsedPhase=(parsed&&typeof parsed==='object'&&parsed.phase!==undefined)?String(parsed.phase).toLowerCase():'';
                const lifecycleErrText=(parsed&&typeof parsed==='object')
                    ? String(parsed.error!==undefined?parsed.error:(parsed.message!==undefined?parsed.message:details))
                    : details;
                const isLifecycleError=(name==='lifecycle' && (phase==='error' || parsedPhase==='error'));
                const lifecycleTimeout=(phase==='timeout' || parsedPhase==='timeout');
                if((isLifecycleError || lifecycleTimeout) && !isTransientLifecycleError(phase||parsedPhase, lifecycleErrText)){{
                    hasLifecycleHardError=true;
                }}
                if(!isLifecycleError) addReasoning((payload.text||payload.name||'event')+': '+details);
            }}

            const toolGroupList=[...toolGroups.values()];
            for(const g of toolGroupList){{
                for(const mtxt of g.metas) addReasoning(mtxt);
            }}

            const allToolsTerminal=(toolGroupList.length>0) && toolGroupList.every((g)=>
                g.phases.includes('result') || g.phases.includes('end') || g.phases.includes('error') || g.isError!==null
            );
            const hasToolError=toolGroupList.some((g)=>g.isError===true || g.phases.includes('error') || g.timeout || /\\btimeout|timed out\\b/i.test(String(g.errorText||'')));
            const hasLifecycleError=hasLifecycleHardError;
            const waiting=(hasLifecycleStart && !hasLifecycleEnd && !hasLifecycleError && !allToolsTerminal && !m.hasFinal);
            const waitingRow=waiting
                ? '<div class="px-2 py-1 border-b border-gray-800/60 text-[11px] text-yellow-300 flex items-center gap-2">'
                    +'<span class="inline-block w-3 h-3 border-2 border-yellow-300/80 border-t-transparent rounded-full animate-spin"></span>'
                    +'<span>waiting…</span>'
                  +'</div>'
                : '';
            const statusRow=(!waiting)
                ? ((hasToolError || hasLifecycleError)
                    ? '<div class="px-2 py-1 border-b border-gray-800/60 text-[11px] text-red-300 flex items-center gap-2"><span>✕</span><span>completed with errors</span></div>'
                    : ((hasLifecycleStart || hasLifecycleEnd || toolGroupList.length>0 || lifecycleItems.length>0)
                        ? '<div class="px-2 py-1 border-b border-gray-800/60 text-[11px] text-emerald-300 flex items-center gap-2"><span>✓</span><span>finished</span></div>'
                        : ''))
                : '';

            const toolRows=toolGroupList.map((g, gi)=>{{
                const failed=(g.isError===true || g.phases.includes('error') || g.timeout);
                const success=!failed && (g.isError===false || g.phases.includes('result') || g.phases.includes('end'));
                const icon=failed?'✕':(success?'✓':'•');
                const phaseLabel=g.phases.filter(Boolean).join(', ')||'update';
                const isExec=isExecTool(g.name);
                const isWrite=isWriteTool(g.name);
                const isRead=isReadTool(g.name);
                const isProcess=isProcessTool(g.name);
                const isWebSearch=isWebSearchTool(g.name);
                const isWebGet=isWebGetTool(g.name);

                const summaryParts=[];
                if(g.command) summaryParts.push('command: '+g.command);
                if(g.files.length) summaryParts.push('file: '+g.files.join(', '));
                if(!summaryParts.length && g.requestPreview) summaryParts.push(g.requestPreview);
                if(g.isError!==null) summaryParts.push('isError: '+String(g.isError));
                if(g.metas.length) summaryParts.push('meta: '+g.metas.join(' | '));
                const summary=summaryParts.join(' · ') || '(no request details)';

                const resultJoined=(g.results.length?g.results.join('\\n\\n---\\n\\n'):'') || '(no result)';
                const payloadJoined=(g.payloads.length?g.payloads.join('\\n\\n---\\n\\n'):'') || '(no payload)';
                const detailBase='req:'+reqKey+':tool:'+String(g.callId||g.name||'tool')+':'+String(gi);
                const payloadBlock='<details data-detail-key="'+esc(detailBase+':payload')+'" class="mt-1 rounded border border-gray-700/70 bg-gray-900/40">'
                    +'<summary class="px-1.5 py-1 cursor-pointer text-[10px] text-gray-300 hover:text-gray-100">payload</summary>'
                    +'<pre class="px-1.5 pb-1.5 whitespace-pre-wrap break-words text-[11px] text-gray-300">'+esc(payloadJoined)+'</pre>'
                    +'</details>';
                                const resultDetails=(resultJoined && resultJoined!=='(no result)')
                                        ? '<details data-detail-key="'+esc(detailBase+':result')+'" class="mt-1 rounded border border-gray-700/70 bg-gray-900/40">'
                                                +'<summary class="px-1.5 py-1 cursor-pointer text-[10px] text-gray-300 hover:text-gray-100">results</summary>'
                                                +'<pre class="px-1.5 pb-1.5 whitespace-pre-wrap break-words text-[11px] text-gray-300">'+esc(resultJoined)+'</pre>'
                                            +'</details>'
                                        : '';
                                const execCommand=String(g.command||g.requestPreview||'(no command)').replace(/^command:\s*/i,'');
                                const fileLabel=(g.files.length?String(g.files[0]):'(no file)');
                const contentLabel=(isWrite?'file content':'content');
                                const markdownLike=/\.(md|markdown|mdx)$/i.test(String(fileLabel||''));
                                const markdownHtml=((isWrite||isRead) && markdownLike) ? renderMarkdown(g.fileContent) : '';
                const contentBlock=((isWrite||isRead) && g.fileContent)
                                        ? '<details data-detail-key="'+esc(detailBase+':content')+'" class="mt-1 rounded border border-gray-700/70 bg-gray-900/40">'
                        +'<summary class="px-1.5 py-1 cursor-pointer text-[10px] text-gray-300 hover:text-gray-100">'+contentLabel+'</summary>'
                                                +(markdownHtml
                                                        ? '<div class="px-1.5 pb-1.5 text-[11px] text-gray-300 md-content">'+markdownHtml+'</div>'
                                                        : '<pre class="px-1.5 pb-1.5 whitespace-pre-wrap break-words text-[11px] text-gray-300">'+esc(g.fileContent)+'</pre>')
                      +'</details>'
                    : ((isWrite||isRead)
                                                ? '<details data-detail-key="'+esc(detailBase+':content')+'" class="mt-1 rounded border border-gray-700/70 bg-gray-900/40">'
                            +'<summary class="px-1.5 py-1 cursor-pointer text-[10px] text-gray-300 hover:text-gray-100">'+contentLabel+'</summary>'
                                                        +'<pre class="px-1.5 pb-1.5 whitespace-pre-wrap break-words text-[11px] text-gray-300">(no content returned)</pre>'
                          +'</details>'
                        : '');
                                const errorInline=(failed)
                                    ? '<div class="mt-1 text-[11px] text-red-300 whitespace-pre-wrap break-words">'+(g.timeout?'timeout':'error')+((g.errorText || (resultJoined && resultJoined!=='(no result)'))?(': '+esc(g.errorText || resultJoined)):'')+'</div>'
                    : '';

                let bodyHtml='<div class="text-[11px] text-gray-300 mt-0.5">'+esc(summary)+'</div>'
                    +resultDetails;
                if(isExec){{
                    bodyHtml='<div class="mt-0.5 text-[11px] text-gray-300 whitespace-pre-wrap break-words">'+esc(clampPreviewLines(execCommand, 2))+'</div>'
                        +resultDetails
                        +errorInline;
                }} else if(isWrite||isRead){{
                    bodyHtml='<div class="mt-0.5 text-[11px] text-gray-300">'+esc(fileLabel)+'</div>'
                        +contentBlock
                        +errorInline;
                }} else if(isProcess){{
                    const actionLabel=String(g.action||g.requestPreview||'(no action)');
                    bodyHtml='<div class="mt-0.5 text-[11px] text-gray-300 whitespace-pre-wrap break-words">'+esc(actionLabel)+'</div>'
                        +resultDetails
                        +errorInline;
                }} else if(isWebSearch){{
                    const queryLabel=String(g.query||g.requestPreview||'(no query)');
                    const webResultsBlock=(g.webResults && String(g.webResults).trim())
                        ? '<details data-detail-key="'+esc(detailBase+':webresults')+'" class="mt-1 rounded border border-gray-700/70 bg-gray-900/40">'
                            +'<summary class="px-1.5 py-1 cursor-pointer text-[10px] text-gray-300 hover:text-gray-100">results</summary>'
                            +'<pre class="px-1.5 pb-1.5 whitespace-pre-wrap break-words text-[11px] text-gray-300">'+esc(g.webResults)+'</pre>'
                          +'</details>'
                        : resultDetails;
                    bodyHtml='<div class="mt-0.5 text-[11px] text-gray-300 whitespace-pre-wrap break-words">'+esc(queryLabel)+'</div>'
                        +webResultsBlock
                        +errorInline;
                }} else if(isWebGet){{
                    const urlLabel=String((g.urls&&g.urls.length)?g.urls[0]:(g.requestPreview||'(no url)'));
                    const webContentBlock=(g.webContent && String(g.webContent).trim())
                        ? '<details data-detail-key="'+esc(detailBase+':webcontent')+'" class="mt-1 rounded border border-gray-700/70 bg-gray-900/40">'
                            +'<summary class="px-1.5 py-1 cursor-pointer text-[10px] text-gray-300 hover:text-gray-100">content</summary>'
                            +'<pre class="px-1.5 pb-1.5 whitespace-pre-wrap break-words text-[11px] text-gray-300">'+esc(g.webContent)+'</pre>'
                          +'</details>'
                        : resultDetails;
                    bodyHtml='<div class="mt-0.5 text-[11px] text-gray-300 whitespace-pre-wrap break-words">'+esc(urlLabel)+'</div>'
                        +webContentBlock
                        +errorInline;
                }}

                return '<div class="px-2 py-1 border-b border-gray-800/60 last:border-b-0">'
                    +'<div class="text-[11px] text-amber-300 flex items-center gap-1"><span class="w-3 text-center">'+icon+'</span><span class="font-mono">'+esc(g.name)+'</span><span class="text-[10px] text-gray-500">'+esc(phaseLabel)+'</span></div>'
                    +bodyHtml
                    +payloadBlock
                    +'</div>';
            }}).join('');

            const lifecycleRows=lifecycleItems.map((item, li)=>{{
                const detailsBlock='<details data-detail-key="'+esc('req:'+reqKey+':lifecycle:'+String(li))+ '" class="mt-1 rounded border border-gray-700/70 bg-gray-900/40">'
                    +'<summary class="px-1.5 py-1 cursor-pointer text-[10px] text-gray-300 hover:text-gray-100">payload</summary>'
                    +'<pre class="px-1.5 pb-1.5 whitespace-pre-wrap break-words text-[11px] text-gray-300">'+esc(item.details||'(no details)')+'</pre>'
                    +'</details>';
                return '<div class="px-2 py-1 border-b border-gray-800/60 last:border-b-0">'
                    +'<div class="text-[10px] text-sky-500 font-mono">'+esc(item.name)+'</div>'
                    +detailsBlock
                    +'</div>';
            }}).join('');

            const interimBlock=reasoningLines.length
                                ? '<details data-detail-key="'+esc('req:'+reqKey+':interim')+'" class="mt-2 rounded border border-gray-700/70 bg-gray-900/40">'
                    +'<summary class="px-2 py-1 cursor-pointer text-[10px] text-gray-300 hover:text-gray-100">Interim/Reasoning</summary>'
                    +'<pre class="px-2 pb-2 whitespace-pre-wrap break-words text-[11px] text-gray-300">'+esc(reasoningLines.join('\\n'))+'</pre>'
                  +'</details>'
                : '';

            const executionSequenceContent=statusRow+waitingRow+toolRows+lifecycleRows+interimBlock;
            if(executionSequenceContent){{
                const thinkingSummary=waiting
                    ? '<summary class="px-3 py-1.5 cursor-pointer text-gray-200 hover:text-gray-100 select-none flex items-center gap-2"><span class="inline-block w-3 h-3 border-2 border-yellow-300/80 border-t-transparent rounded-full animate-spin"></span><span>Thinking</span></summary>'
                    : '<summary class="px-3 py-1.5 cursor-pointer text-gray-200 hover:text-gray-100 select-none">Thinking</summary>';
                const timeline=document.createElement('div');
                timeline.innerHTML='<details data-detail-key="'+esc('req:'+reqKey+':thinking')+'" class="rounded-xl bg-gray-900/60 border border-gray-700 text-xs overflow-hidden">'
                    +thinkingSummary
                    +'<div class="px-2 pb-2 pt-1">'+executionSequenceContent+'</div>'
                    +'</details>';
                wrap.appendChild(timeline);
            }}
        }}

        d.appendChild(wrap);
        return d;
    }}

  const b=document.createElement('div');
    b.className='max-w-xs sm:max-w-sm lg:max-w-md px-4 py-2 rounded-2xl text-sm leading-relaxed '+
        (role==='user'?'bg-blue-700 text-white rounded-br-md':
         role==='system'?'bg-gray-700 text-gray-300 italic text-xs':
     'bg-gray-700 text-gray-100 rounded-bl-md');
    if(role==='assistant'){{
        renderAssistantContent(b, m.text||'');
    }}else{{
        b.textContent=m.text||'';
    }}
    d.appendChild(b); return d;
}}

function renderMusicPage(main){{
  main.dataset.page='music';
    const m=S.music, q=S.musicQueue||[];
    m.state=normalizeMusicState(m.state);
    const pendingMusicCount=Object.keys(S.pendingMusicActions||{{}}).length;
  const qFilter=String(S.musicQueueFilter||'').trim().toLowerCase();
  const filtered=q.filter(item=>{{
    if(!qFilter) return true;
    const hay=[item.title,item.artist,item.album,item.file].map(v=>String(v||'').toLowerCase()).join(' | ');
    return hay.includes(qFilter);
  }});
    const selectedCount=Object.keys(S.musicQueueSelectionByIds||{{}}).filter(k=>S.musicQueueSelectionByIds[k]).length;
  const playlistRows=(S.musicPlaylists||[]).map(name=>{{
    const n=String(name||'').trim();
    if(!n) return '';
    return '<div class="flex items-center gap-1">'
      +'<button data-action="music-load-playlist" data-playlist-name="'+esc(n)+'" class="flex-1 text-left px-2 py-1.5 rounded-lg bg-gray-800 hover:bg-gray-700 transition-colors text-sm truncate">'+esc(n)+'</button>'
      +'<button data-action="music-open-delete-playlist" data-playlist-name="'+esc(n)+'" class="w-8 h-8 rounded-lg bg-gray-800 hover:bg-red-800 transition-colors text-sm" title="Delete playlist">✕</button>'
    +'</div>';
  }}).join('');

  if(S.musicAddMode){{
        const canSearch=canSearchMusicLibrary(S.musicAddQuery);
        const searchPending=!!S.musicAddSearchPending;
        const addPending=Object.values(S.pendingMusicActions||{{}}).some(item=>String((item&&item.type)||'')==='music_add_files');
        const addRows=(S.musicLibraryResults||[]).map(item=>{{
      const file=String(item.file||'');
      const checked=!!S.musicAddSelection[file];
      return '<tr class="hover:bg-gray-800">'
        +'<td class="px-2 py-2 w-8"><input type="checkbox" data-action="music-add-select" data-file="'+esc(file)+'" '+(checked?'checked':'')+'></td>'
        +'<td class="px-2 py-2 text-sm truncate max-w-xs">'+esc(item.title||file.split('/').pop()||'\u2014')+'</td>'
        +'<td class="px-2 py-2 text-xs text-gray-400 truncate">'+esc(item.artist||'')+'</td>'
        +'<td class="px-2 py-2 text-xs text-gray-500 truncate">'+esc(item.album||'')+'</td>'
      +'</tr>';
    }}).join('');
    const addSelectedCount=Object.keys(S.musicAddSelection||{{}}).filter(k=>S.musicAddSelection[k]).length;
    const searchLimit=Math.max(1, Number(S.musicAddSearchLimit||200) || 200);
    const canLoadMore=!!(S.musicAddHasSearched && !searchPending && (S.musicLibraryResults||[]).length>=searchLimit);
    main.innerHTML='<div class="max-w-5xl mx-auto px-2 py-4 space-y-3">'
      +'<div class="flex items-center justify-between gap-2 flex-wrap px-2">'
        +'<h2 class="font-semibold text-lg">Add Songs</h2>'
        +'<div class="flex items-center gap-2">'
          +'<button data-action="music-add-cancel" class="px-3 py-1 rounded-lg text-sm bg-gray-700 hover:bg-gray-600 transition-colors">Back to Queue</button>'
                    +'<button data-action="music-add-selected" class="px-3 py-1 rounded-lg text-sm bg-blue-700 hover:bg-blue-600 transition-colors" '+((addSelectedCount && !addPending)? '' : 'disabled style="opacity:.5;cursor:not-allowed"')+'>'+(addPending?'Adding...':'Add Selected ('+addSelectedCount+')')+'</button>'
        +'</div>'
      +'</div>'
      +'<div class="px-2">'
                +'<div class="flex items-center gap-2">'
                    +'<input type="search" id="musicAddSearch" data-action="music-add-search" value="'+esc(S.musicAddQuery||'')+'" placeholder="Search library by title, artist, album" class="flex-1 rounded-lg bg-gray-800 border border-gray-700 px-3 py-2 text-sm text-gray-100 placeholder-gray-400 focus:outline-none focus:ring-2 focus:ring-blue-600" />'
                                        +'<button id="musicAddSearchSubmit" data-action="music-add-search-submit" class="px-3 py-2 rounded-lg text-sm bg-blue-700 hover:bg-blue-600 transition-colors" '+((canSearch && !searchPending) ? '' : 'disabled style="opacity:.5;cursor:not-allowed"')+'>'+(searchPending?'Searching…':'Search')+'</button>'
                +'</div>'
                                +(canSearch ? '' : '<p id="musicAddMinHint" class="text-xs text-gray-500 mt-1">Enter at least '+MUSIC_LIBRARY_SEARCH_MIN_LEN+' letters to search</p>')
      +'</div>'
    +(canLoadMore ? '<div class="px-2 -mt-1"><button data-action="music-add-search-more" class="px-3 py-1 rounded-lg text-xs bg-gray-700 hover:bg-gray-600 transition-colors">Load More</button></div>' : '')
      +(addRows
                ? '<div class="px-2 flex items-center justify-end gap-1 text-xs text-gray-400">'
                        +'<button data-action="music-add-select-all" class="px-2 py-1 rounded bg-gray-800 hover:bg-gray-700 transition-colors">Select All</button>'
                        +'<button data-action="music-add-select-none" class="px-2 py-1 rounded bg-gray-800 hover:bg-gray-700 transition-colors">Select None</button>'
                    +'</div>'
                    +'<div class="overflow-x-auto rounded-xl border border-gray-800"><table class="w-full text-left"><thead><tr class="text-xs text-gray-400 border-b border-gray-800"><th class="px-2 py-2">#</th><th class="px-2 py-2">Title</th><th class="px-2 py-2">Artist</th><th class="px-2 py-2">Album</th></tr></thead><tbody>'+addRows+'</tbody></table></div>'
                                : '<p class="text-gray-500 text-center py-10 text-sm">'+(searchPending ? '<span class="inline-flex items-center gap-2"><span class="inline-block w-3 h-3 border-2 border-gray-400 border-t-transparent rounded-full animate-spin"></span>Searching…</span>' : (canSearch && S.musicAddHasSearched ? 'No matches found' : 'Search to find songs to add'))+'</p>')
      +'</div>';
    return;
  }}

  const rows=filtered.map(item=>{{
    const active=item.pos===m.position;
    const songId=String(item.id||'').trim();
    const checked=!!S.musicQueueSelectionByIds[songId];
    return '<tr class="hover:bg-gray-800 '+(active?'bg-gray-800 font-semibold text-green-400':'')+'">'
      +'<td class="px-3 py-3 w-12"><input type="checkbox" data-action="music-queue-select" data-position="'+item.pos+'" data-song-id="'+esc(songId)+'" class="w-5 h-5 cursor-pointer" '+(checked?'checked':'')+'></td>'
      +'<td class="px-2 py-2 w-8 text-gray-500 text-xs">'+(item.pos+1)+'</td>'
      +'<td class="px-2 py-2 text-sm truncate max-w-xs cursor-pointer hover:text-blue-400" data-action="music-play-track" data-position="'+item.pos+'">'+esc(item.title||item.file||'\u2014')+'</td>'
      +'<td class="px-2 py-2 text-xs text-gray-400 truncate">'+esc(item.artist||'')+'</td>'
      +'<td class="px-2 py-2 text-xs text-gray-500 truncate">'+esc(item.album||'')+'</td>'
      +'<td class="px-2 py-2 text-xs text-gray-500 text-right pr-4">'+fmtDur(item.duration)+'</td>'
    +'</tr>';
  }}).join('');

  const modalTitle = S.musicPlaylistModalMode==='save'
        ? 'Save Playlist'
    : (S.musicPlaylistModalMode==='selected' ? 'Create Playlist from Selected' : 'Delete Playlist');
    const modalName=String(S.musicPlaylistModalName||'').trim();
    const existingPlaylists=(S.musicPlaylists||[]).map(x=>String(x||'').trim().toLowerCase()).filter(Boolean);
    const loadedPlaylist=String((S.music&&S.music.loaded_playlist)||'').trim().toLowerCase();
    const hasNameConflict=!!modalName && existingPlaylists.includes(modalName.toLowerCase()) && modalName.toLowerCase()!==loadedPlaylist;
  const modalBody = S.musicPlaylistModalMode==='delete'
    ? '<p class="text-sm text-gray-300">Delete playlist <span class="font-semibold">'+esc(S.musicPlaylistModalName||'')+'</span>?</p>'
        : '<div class="space-y-2">'
            +'<input id="musicPlaylistModalName" value="'+esc(S.musicPlaylistModalName||'')+'" placeholder="Playlist name" class="w-full rounded-lg bg-gray-800 border border-gray-700 px-3 py-2 text-sm text-gray-100 placeholder-gray-400 focus:outline-none focus:ring-2 focus:ring-blue-600" />'
            +(hasNameConflict ? '<p class="text-xs text-amber-300">⚠ Playlist exists. Saving will overwrite it.</p>' : '')
        +'</div>';
    const modalConfirmLabel = S.musicPlaylistModalMode==='delete' ? 'Delete' : (hasNameConflict ? 'Overwrite' : 'Save');

  main.innerHTML='<div class="max-w-6xl mx-auto px-2 py-4 space-y-3">'
    +'<div class="grid grid-cols-1 md:grid-cols-4 gap-3">'
      +'<div class="rounded-xl border border-gray-800 bg-gray-900/40 p-2 space-y-2 md:col-span-1">'
        +'<div class="flex items-center justify-between gap-2">'
          +'<div class="text-sm font-semibold">Playlists</div>'
          +'<button data-action="music-refresh-playlists" class="px-2 py-1 rounded-lg text-xs bg-gray-700 hover:bg-gray-600 transition-colors">Refresh Playlists</button>'
        +'</div>'
        +(playlistRows || '<p class="text-xs text-gray-500 px-1 py-2">No playlists available</p>')
      +'</div>'
      +'<div class="md:col-span-3 space-y-3">'
        +'<div class="flex items-center justify-between gap-2 flex-wrap px-2">'
          +'<h2 class="font-semibold text-lg">Queue <span class="text-gray-400 font-normal text-sm ml-1">'+m.queue_length+' tracks</span></h2>'
          +'<div class="flex items-center gap-2">'
            +'<button data-action="music-add-open" class="px-3 py-1 rounded-lg text-sm bg-blue-700 hover:bg-blue-600 transition-colors">Add Songs</button>'
                        +'<button data-action="music-open-save-playlist" class="px-3 py-1 rounded-lg text-sm bg-emerald-700 hover:bg-emerald-600 transition-colors">Save Playlist</button>'
                        +'<button data-action="music-toggle" class="px-4 py-2 rounded-lg text-base font-semibold bg-gray-700 hover:bg-gray-600 transition-colors" '+(pendingMusicCount? 'disabled style="opacity:.5;cursor:not-allowed"' : '')+'>'+(pendingMusicCount?'\u2026 Pending':(m.state==='play'?'\u23f9 Stop':'\u25b6 Play'))+'</button>'
          +'</div>'
        +'</div>'
        +'<div class="px-2">'
          +'<input type="search" id="musicQueueSearch" data-action="music-queue-search" value="'+esc(S.musicQueueFilter||'')+'" placeholder="Filter queue: title, artist, album" class="w-full rounded-lg bg-gray-800 border border-gray-700 px-3 py-2 text-sm text-gray-100 placeholder-gray-400 focus:outline-none focus:ring-2 focus:ring-blue-600" />'
        +'</div>'
        +(S.musicActionError? '<div class="px-2 text-xs text-red-300">⚠ '+esc(S.musicActionError)+'</div>' : '')
        +(rows
            ? '<div class="px-2 min-h-10 flex items-center justify-between gap-2">'
                +'<div class="flex items-center gap-2">'
                    +'<button data-action="music-remove-selected" class="px-3 py-1.5 rounded-lg text-sm bg-red-800 hover:bg-red-700 transition-colors" '+(selectedCount? '' : 'disabled style="opacity:.5;cursor:not-allowed"')+'>Remove Selected ('+selectedCount+')</button>'
                    +'<button data-action="music-open-create-selected" class="px-3 py-1.5 rounded-lg text-sm bg-emerald-700 hover:bg-emerald-600 transition-colors" '+(selectedCount? '' : 'disabled style="opacity:.5;cursor:not-allowed"')+'>Create Playlist from Selected</button>'
                +'</div>'
                +'<div class="flex items-center justify-end gap-1 text-xs text-gray-400">'
                    +'<button data-action="music-select-all" title="Select all" class="w-7 h-7 rounded border border-gray-700 hover:bg-gray-800">☑</button>'
                    +'<button data-action="music-select-none" title="Select none" class="w-7 h-7 rounded border border-gray-700 hover:bg-gray-800">☐</button>'
                +'</div>'
              +'</div>'
              +'<div class="overflow-x-auto rounded-xl border border-gray-800"><table class="w-full text-left"><thead><tr class="text-xs text-gray-400 border-b border-gray-800"><th class="px-2 py-2">Sel</th><th class="px-2 py-2">#</th><th class="px-2 py-2">Title</th><th class="px-2 py-2">Artist</th><th class="px-2 py-2">Album</th><th class="px-2 py-2 text-right pr-4">Dur</th></tr></thead><tbody>'+rows+'</tbody></table></div>'
            : '<p class="text-gray-500 text-center py-8 text-sm">No tracks match your filter</p>')
      +'</div>'
    +'</div>'
    +(S.musicPlaylistModalOpen
        ? '<div class="fixed inset-0 z-40 bg-black/60 flex items-center justify-center px-4">'
            +'<div class="w-full max-w-md rounded-xl border border-gray-700 bg-gray-900 p-4 space-y-3">'
              +'<div class="text-sm font-semibold">'+modalTitle+'</div>'
              +modalBody
              +'<div class="flex justify-end gap-2">'
                +'<button data-action="music-modal-cancel" class="px-3 py-1.5 rounded-lg text-sm bg-gray-700 hover:bg-gray-600 transition-colors">Cancel</button>'
                +'<button data-action="music-modal-confirm" class="px-3 py-1.5 rounded-lg text-sm '+(S.musicPlaylistModalMode==='delete'?'bg-red-700 hover:bg-red-600':'bg-blue-700 hover:bg-blue-600')+' transition-colors">'+modalConfirmLabel+'</button>'
              +'</div>'
            +'</div>'
          +'</div>'
        : '')
  +'</div>';
}}

function fmtDur(s){{ if(!s) return '\u2014'; const t=Math.round(Number(s)); return Math.floor(t/60)+':'+String(t%60).padStart(2,'0'); }}
function esc(s){{ return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); }}

function wsUrl(){{ return (location.protocol==='https:'?'wss':'ws')+'://'+location.hostname+':'+WS_PORT+'/ws'; }}
function sendAction(payload){{ if(S.ws&&S.ws.readyState===WebSocket.OPEN) S.ws.send(JSON.stringify(payload)); }}
function sendMusicAction(actionType, extraPayload={{}}){{
    if(!(S.ws&&S.ws.readyState===WebSocket.OPEN)){{
        recordInlineError('music','', 'Not connected; retry in a moment');
        if(S.page==='music') renderMusicPage(document.getElementById('main'));
        applyMusicHeader();
        return null;
    }}
    const actionId='m'+(S.nextMusicActionId++);
    const timeoutMs=(actionType==='music_load_playlist') ? MUSIC_LOAD_PLAYLIST_TIMEOUT_MS : PENDING_ACTION_TIMEOUT_MS;
    S.pendingMusicActions[actionId]={{type:actionType, ts:Date.now(), timeoutMs, payload:Object.assign({{}}, extraPayload||{{}})}};
    sendAction(Object.assign({{type:actionType, action_id:actionId}}, extraPayload||{{}}));
    if(S.page==='music') renderMusicPage(document.getElementById('main'));
    applyMusicHeader();
    return actionId;
}}
function sendTimerAction(actionType, idKey, idValue){{
    const actionId='t'+(S.nextTimerActionId++);
    const pendingKey=String(actionType||'')+':'+String(idValue||'');
    S.pendingTimerActions[pendingKey]={{type:actionType, action_id:actionId, ts:Date.now()}};
    const payload={{type:actionType, action_id:actionId}};
    payload[String(idKey||'id')]=idValue;
    sendAction(payload);
    renderTimerBar();
    return actionId;
}}
function sendSettingAction(actionType, enabled){{
    const actionId='s'+(S.nextSettingActionId++);
    S.pendingSettingActions[String(actionType)]={{action_id:actionId, enabled:!!enabled, ts:Date.now()}};
    sendAction({{type:String(actionType), action_id:actionId, enabled:!!enabled}});
    applyMicControlToggles();
    return actionId;
}}

function recordInlineError(kind, key, message){{
    const msg=String(message||'Action failed');
    const now=Date.now();
    if(kind==='music'){{
        S.musicActionError=msg;
        S.musicActionErrorTs=now;
        return;
    }}
    if(kind==='setting'){{
        S.settingActionErrors[String(key||'unknown')]={{msg, ts:now}};
        return;
    }}
    if(kind==='timer'){{
        S.timerActionErrors[String(key||'unknown')]={{msg, ts:now}};
    }}
}}

function expirePendingActions(){{
    const now=Date.now();
    let touchMusic=false, touchTimer=false, touchSettings=false;

    Object.keys(S.pendingMusicActions||{{}}).forEach((actionId)=>{{
        const item=S.pendingMusicActions[actionId];
        if(!item) return;
        const timeoutMs=Math.max(1000, Number(item.timeoutMs||PENDING_ACTION_TIMEOUT_MS));
        if((now-Number(item.ts||0))>timeoutMs){{
            delete S.pendingMusicActions[actionId];
            recordInlineError('music','', 'Music action timed out');
            touchMusic=true;
        }}
    }});

    Object.keys(S.pendingTimerActions||{{}}).forEach((k)=>{{
        const item=S.pendingTimerActions[k];
        if(!item) return;
        if((now-Number(item.ts||0))>PENDING_ACTION_TIMEOUT_MS){{
            delete S.pendingTimerActions[k];
            recordInlineError('timer', k, 'Timer/alarm action timed out');
            touchTimer=true;
        }}
    }});

    Object.keys(S.pendingSettingActions||{{}}).forEach((k)=>{{
        const item=S.pendingSettingActions[k];
        if(!item) return;
        if((now-Number(item.ts||0))>PENDING_ACTION_TIMEOUT_MS){{
            delete S.pendingSettingActions[k];
            recordInlineError('setting', k, 'Setting update timed out');
            touchSettings=true;
        }}
    }});

    if(S.musicActionErrorTs && (now-S.musicActionErrorTs)>INLINE_ERROR_TTL_MS){{
        S.musicActionError='';
        S.musicActionErrorTs=0;
        touchMusic=true;
    }}
    Object.keys(S.settingActionErrors||{{}}).forEach((k)=>{{
        const it=S.settingActionErrors[k];
        if(it && (now-Number(it.ts||0))>INLINE_ERROR_TTL_MS){{
            delete S.settingActionErrors[k];
            touchSettings=true;
        }}
    }});
    Object.keys(S.timerActionErrors||{{}}).forEach((k)=>{{
        const it=S.timerActionErrors[k];
        if(it && (now-Number(it.ts||0))>INLINE_ERROR_TTL_MS){{
            delete S.timerActionErrors[k];
            touchTimer=true;
        }}
    }});

    if(touchMusic){{ applyMusicHeader(); if(S.page==='music') renderMusicPage(document.getElementById('main')); }}
    if(touchTimer) renderTimerBar();
    if(touchSettings) applyMicControlToggles();
}}

function clearPendingActionsOnDisconnect(){{
    const hadMusic = Object.keys(S.pendingMusicActions||{{}}).length > 0;
    const hadTimer = Object.keys(S.pendingTimerActions||{{}}).length > 0;
    const hadSetting = Object.keys(S.pendingSettingActions||{{}}).length > 0;
    if(!hadMusic && !hadTimer && !hadSetting) return;

    let retryPlaylistName='';
    let retryPlaylistTs=0;
    Object.keys(S.pendingMusicActions||{{}}).forEach((actionId)=>{{
        const item=S.pendingMusicActions[actionId];
        if(!item || String(item.type||'')!=='music_load_playlist') return;
        const name=String(((item.payload||{{}}).name)||'').trim();
        if(!name) return;
        const ts=Number(item.ts||0);
        if(!retryPlaylistName || ts > retryPlaylistTs){{
            retryPlaylistName=name;
            retryPlaylistTs=ts;
        }}
    }});
    if(retryPlaylistName){{
        S.musicLoadRetryPending = retryPlaylistName;
        S.musicLoadRetryAttempted = false;
    }}

    S.pendingMusicActions = {{}};
    S.pendingTimerActions = {{}};
    S.pendingSettingActions = {{}};

    if(hadMusic){{
        recordInlineError('music','', retryPlaylistName ? 'Connection reset; retrying playlist load…' : 'Connection reset; please retry music action');
        applyMusicHeader();
        if(S.page==='music') renderMusicPage(document.getElementById('main'));
    }}
    if(hadTimer) renderTimerBar();
    if(hadSetting) applyMicControlToggles();
}}

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
    const retryMs=(err&&err.name==='NotFoundError'&&S.lastAudioInputCount===0)?10000:(err&&err.name==='InvalidStateError')?8000:2500;
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
    S.captureWorkletModuleReady=false;
}}

async function disconnectWs(manual=true){{
    if(manual) S.wsManualDisconnect=true;
    if(S.wsReconnectTimer){{ clearTimeout(S.wsReconnectTimer); S.wsReconnectTimer=null; }}
    stopWsPingTimer();
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
            startWsPingTimer();
            if(S.browserAudioEnabled) ensureBrowserCapture().catch((err)=>reportCaptureFailure(err,'connect'));
            S.ws.send(JSON.stringify({{type:'ui_ready'}}));
                pushUiPrefsToServer();
            if(S.musicLoadRetryPending && !S.musicLoadRetryAttempted){{
                const retryName=String(S.musicLoadRetryPending||'').trim();
                if(retryName){{
                    S.musicLoadRetryAttempted=true;
                    setTimeout(()=>{{
                        if(!S.ws || S.ws.readyState!==WebSocket.OPEN) return;
                        const actionId=sendMusicAction('music_load_playlist', {{name: retryName}});
                        if(actionId) S.musicLoadRetryPending=null;
                    }}, 120);
                }}
            }}
    }};
    S.ws.onclose=(evt)=>{{
        S.wsConnected=false;
        S.wsDebug.status='closed';
        S.wsDebug.lastCloseCode=(evt&&evt.code!==undefined)?evt.code:null;
        S.wsDebug.lastCloseReason=(evt&&evt.reason)?String(evt.reason):'';
        updateWsDebugBanner();
        updateMicInteractivity();
        stopWsPingTimer();
        clearPendingActionsOnDisconnect();
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
    if(msg.orchestrator){{
        const rev = Number(msg.orchestrator.status_rev||0);
        if(!Number.isNaN(rev) && rev>0) S.lastStatusRev = Math.max(S.lastStatusRev, rev);
        applyOrch(msg.orchestrator);
    }}
            if(msg.ui_control){{
                const uiRev = Number(msg.ui_control_rev||0);
                const staleUi = (!Number.isNaN(uiRev) && uiRev>0 && uiRev<S.lastUiControlRev);
                if(!staleUi){{
                if(!Number.isNaN(uiRev) && uiRev>0) S.lastUiControlRev = uiRev;
                if(msg.ui_control.mic_enabled!==undefined) S.micEnabled=!!msg.ui_control.mic_enabled;
                if(msg.ui_control.tts_muted!==undefined && !S.pendingSettingActions['tts_mute_set']) S.ttsMuted=!!msg.ui_control.tts_muted;
                if(msg.ui_control.browser_audio_enabled!==undefined && !S.pendingSettingActions['browser_audio_set']) S.browserAudioEnabled=!!msg.ui_control.browser_audio_enabled;
                if(msg.ui_control.continuous_mode!==undefined && !S.pendingSettingActions['continuous_mode_set']) S.continuousMode=!!msg.ui_control.continuous_mode;
                S.settingActionErrors={{}};
                applyMicState();
                applyMicControlToggles();
                }}
            }}
    if(msg.music) applyMusic(msg.music);
    if(Array.isArray(msg.music_queue)){{
        S.musicQueue=msg.music_queue;
        syncMusicFromQueue();
    }}
    if(msg.music_rev!==undefined) S.lastMusicRev=Math.max(S.lastMusicRev, Number(msg.music_rev)||0);
    if(Array.isArray(msg.timers)) applyTimers(msg.timers);
    if(msg.timers_rev!==undefined) S.lastTimersRev=Math.max(S.lastTimersRev, Number(msg.timers_rev)||0);
            applyServerChatState(msg.chat, msg.chat_threads, msg.active_chat_id);
            renderPage();
      break;
    case 'orchestrator_status':
        if(msg.status_rev!==undefined){{
            const rev=Number(msg.status_rev)||0;
            if(rev<=S.lastStatusRev) break;
            S.lastStatusRev=rev;
        }}
        applyOrch(msg);
        break;
    case 'status': if(msg.orchestrator) applyOrch(msg.orchestrator); break;
        case 'chat_append':
            if(msg.message){{
                const nextMsg = normalizeChatMessage(msg.message);
                if(nextMsg) S.chat.push(nextMsg);
                if(nextMsg && nextMsg.role==='user') requestScrollToBottomBurst();
                S.selectedChatId='active';
                persistChatCache();
                if(S.page==='home'){{
                    renderThreadList('active');
                    renderChatMessages('active');
                }}
            }}
            break;

        case 'chat_threads_update':
            applyServerChatState(undefined, msg.chat_threads, msg.active_chat_id);
            if(S.page==='home') renderPage();
            break;
        case 'chat_reset':
            S.chat=[];
            applyServerChatState([], msg.chat_threads, msg.active_chat_id, true);
            S.selectedChatId='active';
            persistChatCache();
            if(S.page==='home') renderPage();
            break;
        case 'chat_text_ack':
            if(msg.client_msg_id) S.pendingChatSends.delete(String(msg.client_msg_id));
            if(S.page==='home') updateChatComposerState();
            break;
        case 'navigate':
            if(msg.page==='music' || msg.page==='home'){{
                navigate(msg.page);
            }}
            break;
        case 'music_transport':
            if(msg.music_rev!==undefined){{
                const rev=Number(msg.music_rev)||0;
                if(rev<=S.lastMusicRev) break;
                S.lastMusicRev=rev;
            }}
            applyMusic(msg.music||msg);
            if(S.page==='music') renderMusicPage(document.getElementById('main'));
            applyMusicHeader();
            break;
        case 'music_queue':
            if(msg.music_rev!==undefined){{
                const rev=Number(msg.music_rev)||0;
                if(rev<=S.lastMusicRev) break;
                S.lastMusicRev=rev;
            }}
            if(msg.queue!==undefined){{
                S.musicQueue=msg.queue;
                syncMusicFromQueue();
            }}
            if(S.page==='music') renderMusicPage(document.getElementById('main'));
            applyMusicHeader();
            break;
        case 'music_state':
            if(msg.music_rev!==undefined){{
                const rev=Number(msg.music_rev)||0;
                if(rev<=S.lastMusicRev) break;
                S.lastMusicRev=rev;
            }}
            applyMusic(msg.music||msg);
            if(S.music && S.music.loaded_playlist){{
                const loadedNow=String(S.music.loaded_playlist||'').trim().toLowerCase();
                Object.keys(S.pendingMusicActions||{{}}).forEach((actionId)=>{{
                    const item=S.pendingMusicActions[actionId];
                    if(!item || String(item.type||'')!=='music_load_playlist') return;
                    const expected=String(((item.payload||{{}}).name)||'').trim().toLowerCase();
                    if(expected && loadedNow && expected===loadedNow) delete S.pendingMusicActions[actionId];
                }});
                if(S.musicLoadRetryPending){{
                    const expectedRetry=String(S.musicLoadRetryPending||'').trim().toLowerCase();
                    if(expectedRetry && loadedNow && expectedRetry===loadedNow){{
                        S.musicLoadRetryPending=null;
                        S.musicLoadRetryAttempted=false;
                    }}
                }}
            }}
            if(msg.queue!==undefined) S.musicQueue=msg.queue;
            else if(msg.music&&msg.music.queue!==undefined) S.musicQueue=msg.music.queue;
            syncMusicFromQueue();
            if(S.page==='music') renderMusicPage(document.getElementById('main'));
            applyMusicHeader();
            break;
        case 'timers_state':
            if(msg.timers_rev!==undefined){{
                const rev=Number(msg.timers_rev)||0;
                if(rev<=S.lastTimersRev) break;
                S.lastTimersRev=rev;
            }}
            if(Array.isArray(msg.timers)) applyTimers(msg.timers);
            break;
        case 'music_action_ack':
            if(msg.action_id) delete S.pendingMusicActions[String(msg.action_id)];
            if(String(msg.action||'')==='music_load_playlist'){{
                S.musicLoadRetryPending=null;
                S.musicLoadRetryAttempted=false;
            }}
            S.musicActionError='';
            S.musicActionErrorTs=0;
            if(S.page==='music') renderMusicPage(document.getElementById('main'));
            applyMusicHeader();
            break;
        case 'music_action_error':
            if(msg.action_id) delete S.pendingMusicActions[String(msg.action_id)];
            recordInlineError('music', '', String(msg.error||'Music action failed'));
            S.wsDebug.lastError='music action failed: '+String(msg.error||msg.action||'unknown');
            updateWsDebugBanner();
            if(S.page==='music') renderMusicPage(document.getElementById('main'));
            applyMusicHeader();
            break;
        case 'music_library_results':
            if(msg.query!==undefined){{
                const pendingQuery=String(S.musicAddPendingQuery||'').trim();
                const responseQuery=String(msg.query||'').trim();
                if(pendingQuery && responseQuery && responseQuery!==pendingQuery) break;
            }}
            S.musicAddSearchPending = false;
            S.musicAddPendingQuery = '';
            if(msg.limit!==undefined){{
                const responseLimit = Math.max(1, Number(msg.limit) || 200);
                S.musicAddSearchLimit = responseLimit;
            }}
            if(Array.isArray(msg.results)){{
                S.musicLibraryResults = msg.results;
                S.musicAddSelection = {{}};
                (msg.results||[]).forEach(item=>{{
                    const file=String((item&&item.file)||'').trim();
                    if(file) S.musicAddSelection[file] = true;
                }});
                S.musicAddLastCheckedFile='';
            }}
            if(S.page==='music' && S.musicAddMode) renderMusicPage(document.getElementById('main'));
            break;
        case 'music_playlists':
            if(Array.isArray(msg.playlists)) S.musicPlaylists = msg.playlists;
            if(S.page==='music') renderMusicPage(document.getElementById('main'));
            break;
        case 'timer_action_ack':
            if(msg.action){{
                const key=String(msg.action)+':'+String(msg.id||'');
                delete S.pendingTimerActions[key];
                delete S.timerActionErrors[key];
            }}
            renderTimerBar();
            break;
        case 'timer_action_error':
            if(msg.action){{
                const key=String(msg.action)+':'+String(msg.id||'');
                delete S.pendingTimerActions[key];
                recordInlineError('timer', key, String(msg.error||'Timer/alarm action failed'));
            }}
            S.wsDebug.lastError='timer/alarm action failed: '+String(msg.error||msg.action||'unknown');
            updateWsDebugBanner();
            renderTimerBar();
            break;
        case 'ui_control':
            if(msg.ui_control_rev!==undefined){{
                const uiRev=Number(msg.ui_control_rev)||0;
                if(uiRev<=S.lastUiControlRev) break;
                S.lastUiControlRev=uiRev;
            }}
            const prevBrowserAudio=!!S.browserAudioEnabled;
            if(msg.mic_enabled!==undefined) S.micEnabled=!!msg.mic_enabled;
            if(msg.tts_muted!==undefined) S.ttsMuted=!!msg.tts_muted;
            if(msg.browser_audio_enabled!==undefined) S.browserAudioEnabled=!!msg.browser_audio_enabled;
            if(msg.continuous_mode!==undefined) S.continuousMode=!!msg.continuous_mode;
            if(msg.page==='music' || msg.page==='home') navigate(msg.page);
            writeBoolPref(PREF_TTS_MUTED, !!S.ttsMuted);
            writeBoolPref(PREF_BROWSER_AUDIO, !!S.browserAudioEnabled);
            writeBoolPref(PREF_CONTINUOUS, !!S.continuousMode);
            if(prevBrowserAudio && !S.browserAudioEnabled){{
                try{{ if(S.processor) S.processor.disconnect(); }}catch(_ ){{}}
                try{{ if(S.audioCtx) S.audioCtx.close(); }}catch(_ ){{}}
                if(S.mediaStream) try{{ S.mediaStream.getTracks().forEach(t=>t.stop()); }}catch(_ ){{}}
                S.processor=null; S.audioCtx=null; S.mediaStream=null; S.captureWorkletModuleReady=false;
            }} else if(!prevBrowserAudio && S.browserAudioEnabled){{
                startBrowserCapture().catch(err=>reportCaptureFailure(err,'browserAudioToggle'));
            }}
            S.pendingSettingActions={{}};
            S.settingActionErrors={{}};
            applyMicState();
            applyMicControlToggles();
            break;
        case 'setting_action_ack':
            if(msg.action){{
                delete S.pendingSettingActions[String(msg.action)];
                delete S.settingActionErrors[String(msg.action)];
            }}
            applyMicControlToggles();
            break;
        case 'setting_action_error':
            if(msg.action){{
                delete S.pendingSettingActions[String(msg.action)];
                recordInlineError('setting', String(msg.action), String(msg.error||'Setting update failed'));
            }}
            S.wsDebug.lastError='setting action failed: '+String(msg.error||msg.action||'unknown');
            updateWsDebugBanner();
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
        const ctx = S.feedbackAudioCtx || new (window.AudioContext || window.webkitAudioContext)();
        S.feedbackAudioCtx = ctx;
        if (ctx.state === 'suspended') await ctx.resume();
        const buf = await ctx.decodeAudioData(bytes.buffer.slice(0));
        const src = ctx.createBufferSource();
        src.buffer = buf;
        const gainNode = ctx.createGain();
        gainNode.gain.value = Math.max(0, Math.min(4, Number(gain) || 1.0));
        src.connect(gainNode);
        gainNode.connect(ctx.destination);
        src.start();
    }} catch(e) {{
        console.debug('Feedback sound error:', e);
    }}
}}
function applyOrch(o){{
  if(o.voice_state!==undefined) S.voice_state=o.voice_state;
  if(o.wake_state!==undefined)  S.wake_state=o.wake_state;
  if(o.hotword_active!==undefined) S.hotword_active=!!o.hotword_active;
  if(o.tts_playing!==undefined) S.tts_playing=!!o.tts_playing;
  if(o.mic_rms!==undefined)     S.mic_rms=Number(o.mic_rms)||0;
  if(o.mic_enabled!==undefined) S.micEnabled=!!o.mic_enabled;
  applyMicState();
}}
function syncMusicFromQueue(){{
    if(!S.music || !Array.isArray(S.musicQueue) || !S.musicQueue.length) return;
    const pos = Number(S.music.position);
    if(!Number.isFinite(pos) || pos < 0) return;
    const current = S.musicQueue.find(item => Number(item && item.pos) === pos);
    if(!current || typeof current !== 'object') return;
    const title = String(current.title || current.file || '').trim();
    const artist = String(current.artist || '').trim();
    const album = String(current.album || '').trim();
    if(title) S.music.title = title;
    if(artist) S.music.artist = artist;
    if(album) S.music.album = album;
    if(current.file) S.music.file = current.file;
}}
function applyMusic(m){{
    const payload=(m&&typeof m==='object'&&m.music&&typeof m.music==='object')?m.music:m;
    if(!payload||typeof payload!=='object') return;
    Object.assign(S.music,payload);
    S.music.state=normalizeMusicState(S.music.state);
    syncMusicFromQueue();
    applyMusicHeader();
}}
function applyTimers(t){{
  const now=Date.now()/1000;
    S.timers=(Array.isArray(t)?t:[])
        .filter(timer=>{{
            if(!timer||typeof timer!=='object') return false;
            const kind=String(timer.kind||'timer').toLowerCase();
            const rem=Number(timer.remaining_seconds);
            if(!Number.isFinite(rem)) return false;
            if(kind==='alarm' && rem<=0 && !timer.ringing) return false;
            return true;
        }})
        .map(timer=>Object.assign({{}},timer,{{_clientAnchorTs:now, _clientAnchorRem:Number(timer.remaining_seconds)||0}}));
  renderTimerBar();
}}

async function ensureCaptureWorkletModule(ctx){{
        if (S.captureWorkletModuleReady) return true;
        if (!ctx || !ctx.audioWorklet || typeof AudioWorkletNode === 'undefined') return false;
        const source = `
class CaptureProcessor extends AudioWorkletProcessor {{
    process(inputs) {{
        const input = inputs && inputs[0] && inputs[0][0] ? inputs[0][0] : null;
        if (input) {{
            let sum = 0;
            for (let i = 0; i < input.length; i++) sum += input[i] * input[i];
            const rms = Math.sqrt(sum / Math.max(1, input.length));
            this.port.postMessage({{ rms, samples: input.slice(0) }});
        }}
        return true;
    }}
}}
registerProcessor('openclaw-capture-processor', CaptureProcessor);
`;
        const blob = new Blob([source], {{ type: 'application/javascript' }});
        const url = URL.createObjectURL(blob);
        try {{
                await ctx.audioWorklet.addModule(url);
                S.captureWorkletModuleReady = true;
                return true;
        }} finally {{
                URL.revokeObjectURL(url);
        }}
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
  S.processor=null; S.audioCtx=null; S.mediaStream=null; S.captureWorkletModuleReady=false;

  if(!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia){{
      throw new Error('Browser mediaDevices.getUserMedia is unavailable');
  }}

  const captureConstraints=[
      {{audio:true,video:false}},
      {{audio:{{echoCancellation:false,noiseSuppression:false,autoGainControl:false}},video:false}},
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
    const mute=S.audioCtx.createGain(); mute.gain.value=0;

    let proc=null;
    const workletReady = await ensureCaptureWorkletModule(S.audioCtx);
    if (workletReady) {{
            proc = new AudioWorkletNode(S.audioCtx, 'openclaw-capture-processor', {{
                    numberOfInputs: 1,
                    numberOfOutputs: 1,
                    outputChannelCount: [1],
                    channelCount: 1,
            }});
            proc.port.onmessage = (evt) => {{
                    const data = evt && evt.data ? evt.data : null;
                    if (!data || !data.samples) return;
                    if(!S.ws||S.ws.readyState!==WebSocket.OPEN) return;
                    if(!S.browserAudioEnabled) return;
                    const inp = data.samples;
                    const rms = Number(data.rms) || 0;
                    const now=performance.now();
                    if(now-S.lastLevel>=120){{ S.lastLevel=now; S.ws.send(JSON.stringify({{type:'browser_audio_level',rms,peak:rms}})); }}
                    const out=new Int16Array(inp.length);
                    for(let i=0;i<inp.length;i++){{const s=Math.max(-1,Math.min(1,inp[i]));out[i]=s<0?s*0x8000:s*0x7fff;}}
                    S.ws.send(out.buffer);
            }};
            src.connect(proc); proc.connect(mute); mute.connect(S.audioCtx.destination);
    }} else {{
            const scriptProc=S.audioCtx.createScriptProcessor(2048,1,1);
            src.connect(scriptProc); scriptProc.connect(mute); mute.connect(S.audioCtx.destination);
            scriptProc.onaudioprocess=evt=>{{
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
            proc = scriptProc;
    }}
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

loadUiPrefs();
hydrateChatCache();
S.page=getPage(); renderPage(); updateNavActiveState(); applyMicState(); applyMicControlToggles(); updateWsDebugBanner(); updateMicInteractivity(); connectWs();
setupServerRefreshWatcher();
setInterval(()=>{{ expirePendingActions(); if(!S.timers.length) return; const now=Date.now()/1000; S.timers.forEach(t=>{{ if(t._clientAnchorTs===undefined){{ t._clientAnchorTs=now; t._clientAnchorRem=t.remaining_seconds; }} t.remaining_seconds=Math.max(0, t._clientAnchorRem-(now-t._clientAnchorTs)); }}); renderTimerBar(); }},500);
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
        chat_persist_path: str = "",
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
        self._browser_pcm_packet_count: int = 0
        self._browser_pcm_packet_bytes: int = 0
        self._browser_level_packet_count: int = 0
        self._last_audio_packet_log_ts: float = 0.0
        self._last_hotword_ts: float | None = None

        self._orchestrator_status: dict[str, Any] = {
            "voice_state": "idle",
            "wake_state": "asleep",
            "speech_active": False,
            "tts_playing": False,
            "mic_rms": 0.0,
            "queue_depth": 0,
        }
        self._status_rev: int = 0

        self._chat_messages: list[dict[str, Any]] = []
        self._chat_seq: int = 0
        self._chat_threads: list[dict[str, Any]] = []
        self._active_chat_id: str = "active"
        self._chat_thread_limit = 100
        _default_persist = Path.home() / ".config" / "openclaw" / "chat_state.json"
        self._chat_persist_path = Path(chat_persist_path) if chat_persist_path else _default_persist
        self._load_chat_state()
        self._music_state: dict[str, Any] = {
            "state": "stop", "title": "", "artist": "", "album": "",
            "queue_length": 0, "elapsed": 0.0, "duration": 0.0, "position": -1,
        }
        self._music_queue: list[dict[str, Any]] = []
        self._music_rev: int = 0
        self._timers_state: list[dict[str, Any]] = []
        self._timers_rev: int = 0
        self._ui_control_state: dict[str, Any] = {
            "mic_enabled": not mic_starts_disabled,
            "tts_muted": False,
            "browser_audio_enabled": True,
            "continuous_mode": False,
        }
        self._ui_control_rev: int = 0

        self._on_mic_toggle: Callable[[str], Awaitable[None]] | None = None
        self._on_music_toggle: Callable[[str], Awaitable[None]] | None = None
        self._on_music_stop: Callable[[str], Awaitable[None]] | None = None
        self._on_music_play_track: Callable[[int, str], Awaitable[None]] | None = None
        self._on_music_remove_selected: Callable[[list[int], str, list[str] | None], Awaitable[None]] | None = None
        self._on_music_add_files: Callable[[list[str], str], Awaitable[None]] | None = None
        self._on_music_create_playlist: Callable[[str, list[int], str], Awaitable[None]] | None = None
        self._on_music_load_playlist: Callable[[str, str], Awaitable[None]] | None = None
        self._on_music_save_playlist: Callable[[str, str], Awaitable[None]] | None = None
        self._on_music_delete_playlist: Callable[[str, str], Awaitable[None]] | None = None
        self._on_music_search_library: Callable[[str, int, str], Awaitable[list[dict[str, Any]]]] | None = None
        self._on_music_list_playlists: Callable[[str], Awaitable[list[str]]] | None = None
        self._on_get_music_state: Callable[[], Awaitable[tuple[dict[str, Any], list[dict[str, Any]]]]] | None = None
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
        try:
            loop = asyncio.get_running_loop()
            if loop.is_running() and self._clients:
                asyncio.ensure_future(self.broadcast(self._build_status_payload()))
        except RuntimeError:
            pass

    def note_hotword_detected(self) -> None:
        self._last_hotword_ts = time.monotonic()

    def _load_chat_state(self) -> None:
        """Load persisted chat threads and active messages from disk (best-effort)."""
        try:
            if not self._chat_persist_path.exists():
                return
            raw = self._chat_persist_path.read_text(encoding="utf-8")
            data = json.loads(raw)
            threads = data.get("threads")
            if isinstance(threads, list):
                self._chat_threads = threads[: self._chat_thread_limit]
            active = data.get("active_messages")
            if isinstance(active, list):
                self._chat_messages = active[-self.chat_history_limit:]
            seq = data.get("chat_seq")
            if isinstance(seq, int):
                self._chat_seq = seq
            logger.info(
                "Loaded %d chat thread(s) and %d active message(s) from %s",
                len(self._chat_threads),
                len(self._chat_messages),
                self._chat_persist_path,
            )
        except Exception:
            logger.debug("Could not load chat state from disk (will start fresh)", exc_info=True)

    def _persist_chat_state(self) -> None:
        """Atomically write chat threads and active messages to disk (best-effort)."""
        try:
            self._chat_persist_path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "threads": self._chat_threads,
                "active_messages": self._chat_messages,
                "chat_seq": self._chat_seq,
                "saved_ts": time.time(),
            }
            tmp_fd, tmp_path = tempfile.mkstemp(
                dir=self._chat_persist_path.parent,
                prefix=".chat_state_",
                suffix=".tmp",
            )
            try:
                with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
                    json.dump(payload, f, ensure_ascii=False)
                os.replace(tmp_path, self._chat_persist_path)
            except Exception:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
                raise
        except Exception:
            logger.debug("Could not persist chat state to disk", exc_info=True)

    def update_chat_history(self, messages: list[dict[str, Any]]) -> None:
        self._chat_messages = list(messages[-self.chat_history_limit:])
        self._persist_chat_state()

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
        self._persist_chat_state()

    def start_new_chat(self) -> None:
        self._archive_active_chat_if_needed()
        self._chat_messages = []
        self._active_chat_id = "active"
        self._persist_chat_state()
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
        self._persist_chat_state()
        asyncio.create_task(self.broadcast({"type": "chat_append", "message": msg}))

    def update_music_transport(self, **state: Any) -> None:
        self._music_state.update(state)
        self._music_rev += 1
        payload: dict[str, Any] = {
            "type": "music_transport",
            "music_rev": self._music_rev,
            "music": dict(self._music_state),
        }
        asyncio.create_task(self.broadcast(payload))

    def update_music_queue(self, queue: list[dict[str, Any]]) -> None:
        self._music_queue = list(queue)
        self._music_rev += 1
        payload: dict[str, Any] = {
            "type": "music_queue",
            "music_rev": self._music_rev,
            "queue": list(self._music_queue),
        }
        asyncio.create_task(self.broadcast(payload))

    def update_music_state(self, queue: list[dict[str, Any]] | None = None, **state: Any) -> None:
        self._music_state.update(state)
        if queue is not None:
            self._music_queue = list(queue)
        self._music_rev += 1
        asyncio.create_task(
            self.broadcast(
                {
                    "type": "music_state",
                    "music_rev": self._music_rev,
                    "music": dict(self._music_state),
                    "queue": list(self._music_queue),
                }
            )
        )

    async def push_music_state_now(self, queue: list[dict[str, Any]] | None = None, **state: Any) -> None:
        """Like update_music_state but awaits the broadcast to guarantee clients receive state before any subsequent ack."""
        self._music_state.update(state)
        if queue is not None:
            self._music_queue = list(queue)
        self._music_rev += 1
        await self.broadcast(
            {
                "type": "music_state",
                "music_rev": self._music_rev,
                "music": dict(self._music_state),
                "queue": list(self._music_queue),
            }
        )

    def update_timers_state(self, timers: list[dict[str, Any]]) -> None:
        self._timers_state = list(timers)
        self._timers_rev += 1
        asyncio.create_task(self.broadcast({
            "type": "timers_state",
            "timers_rev": self._timers_rev,
            "timers": self._timers_state,
        }))

    def update_ui_control_state(self, **state: Any) -> None:
        self._ui_control_state.update(state)
        self._ui_control_rev += 1
        asyncio.create_task(self.broadcast({
            "type": "ui_control",
            "ui_control_rev": self._ui_control_rev,
            **self._ui_control_state,
        }))

    def navigate_ui_page(self, page: str) -> None:
        page_name = str(page or "").strip().lower()
        if page_name not in ("home", "music"):
            return
        asyncio.create_task(self.broadcast({
            "type": "navigate",
            "page": page_name,
        }))

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
        on_music_remove_selected: Callable[[list[int], str, list[str] | None], Awaitable[None]] | None = None,
        on_music_add_files: Callable[[list[str], str], Awaitable[None]] | None = None,
        on_music_create_playlist: Callable[[str, list[int], str], Awaitable[None]] | None = None,
        on_music_load_playlist: Callable[[str, str], Awaitable[None]] | None = None,
        on_music_save_playlist: Callable[[str, str], Awaitable[None]] | None = None,
        on_music_delete_playlist: Callable[[str, str], Awaitable[None]] | None = None,
        on_music_search_library: Callable[[str, int, str], Awaitable[list[dict[str, Any]]]] | None = None,
        on_music_list_playlists: Callable[[str], Awaitable[list[str]]] | None = None,
        on_get_music_state: Callable[[], Awaitable[tuple[dict[str, Any], list[dict[str, Any]]]]] | None = None,
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
        if on_music_remove_selected is not None:
            self._on_music_remove_selected = on_music_remove_selected
        if on_music_add_files is not None:
            self._on_music_add_files = on_music_add_files
        if on_music_create_playlist is not None:
            self._on_music_create_playlist = on_music_create_playlist
        if on_music_load_playlist is not None:
            self._on_music_load_playlist = on_music_load_playlist
        if on_music_save_playlist is not None:
            self._on_music_save_playlist = on_music_save_playlist
        if on_music_delete_playlist is not None:
            self._on_music_delete_playlist = on_music_delete_playlist
        if on_music_search_library is not None:
            self._on_music_search_library = on_music_search_library
        if on_music_list_playlists is not None:
            self._on_music_list_playlists = on_music_list_playlists
        if on_get_music_state is not None:
            self._on_get_music_state = on_get_music_state
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

    # ------------------------------------------------------------------
    # Broadcast helper
    # ------------------------------------------------------------------

    async def broadcast(self, payload: dict[str, Any]) -> None:
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
                self._browser_level_packet_count += 1
                now = time.monotonic()
                if now - self._last_audio_packet_log_ts >= 2.0:
                    logger.info(
                        "📦 Audio packet source summary: browser_audio_level=%d browser_pcm=%d (%d bytes queued=%d)",
                        self._browser_level_packet_count,
                        self._browser_pcm_packet_count,
                        self._browser_pcm_packet_bytes,
                        len(self._browser_pcm_frames),
                    )
                    self._browser_level_packet_count = 0
                    self._browser_pcm_packet_count = 0
                    self._browser_pcm_packet_bytes = 0
                    self._last_audio_packet_log_ts = now
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

        async def _send_music_action_ack(action: str, action_id: Any) -> None:
            if websocket is None or not action_id:
                return
            await websocket.send(
                json.dumps(
                    {
                        "type": "music_action_ack",
                        "action": action,
                        "action_id": str(action_id),
                    }
                )
            )

        async def _send_music_playlists_update() -> None:
            if websocket is None or self._on_music_list_playlists is None:
                return
            try:
                names = await self._on_music_list_playlists(client_id)
                await websocket.send(
                    json.dumps(
                        {
                            "type": "music_playlists",
                            "playlists": names or [],
                        }
                    )
                )
            except Exception:
                pass

        if msg_type == "music_toggle" and self._on_music_toggle:
            action_id = payload.get("action_id")
            try:
                await self._on_music_toggle(client_id)
                # Push authoritative state BEFORE ack so client never flashes old state
                if self._on_get_music_state:
                    try:
                        transport, queue = await self._on_get_music_state()
                        await self.push_music_state_now(queue=queue, **transport)
                    except Exception:
                        pass
                await _send_music_action_ack("music_toggle", action_id)
            except Exception as exc:
                logger.warning("music_toggle handler error: %s", exc)
                if websocket is not None and action_id:
                    await websocket.send(json.dumps({
                        "type": "music_action_error",
                        "action": "music_toggle",
                        "action_id": str(action_id),
                        "error": str(exc),
                    }))
            return

        if msg_type == "music_stop" and self._on_music_stop:
            action_id = payload.get("action_id")
            try:
                await self._on_music_stop(client_id)
                if self._on_get_music_state:
                    try:
                        transport, queue = await self._on_get_music_state()
                        await self.push_music_state_now(queue=queue, **transport)
                    except Exception:
                        pass
                await _send_music_action_ack("music_stop", action_id)
            except Exception as exc:
                logger.warning("music_stop handler error: %s", exc)
                if websocket is not None and action_id:
                    await websocket.send(json.dumps({
                        "type": "music_action_error",
                        "action": "music_stop",
                        "action_id": str(action_id),
                        "error": str(exc),
                    }))
            return

        if msg_type == "music_play_track" and self._on_music_play_track:
            action_id = payload.get("action_id")
            pos = payload.get("position")
            if pos is not None:
                try:
                    await self._on_music_play_track(int(pos), client_id)
                    if self._on_get_music_state:
                        try:
                            transport, queue = await self._on_get_music_state()
                            await self.push_music_state_now(queue=queue, **transport)
                        except Exception:
                            pass
                    await _send_music_action_ack("music_play_track", action_id)
                except Exception as exc:
                    logger.warning("music_play_track handler error: %s", exc)
                    if websocket is not None and action_id:
                        await websocket.send(json.dumps({
                            "type": "music_action_error",
                            "action": "music_play_track",
                            "action_id": str(action_id),
                            "error": str(exc),
                        }))
            return

        if msg_type == "music_remove_selected" and self._on_music_remove_selected:
            action_id = payload.get("action_id")
            positions = payload.get("positions")
            song_ids = payload.get("song_ids")
            try:
                pos_list = [int(p) for p in positions] if isinstance(positions, list) else []
                song_id_list = [str(s).strip() for s in song_ids] if isinstance(song_ids, list) else []
                await _send_music_action_ack("music_remove_selected", action_id)
                await self._on_music_remove_selected(pos_list, client_id, song_id_list or None)
            except Exception as exc:
                logger.warning("music_remove_selected handler error: %s", exc)
                if websocket is not None and action_id:
                    await websocket.send(json.dumps({
                        "type": "music_action_error",
                        "action": "music_remove_selected",
                        "action_id": str(action_id),
                        "error": str(exc),
                    }))
            return

        if msg_type == "music_add_files" and self._on_music_add_files:
            action_id = payload.get("action_id")
            files = payload.get("files")
            try:
                file_list = [str(f) for f in files] if isinstance(files, list) else []
                await self._on_music_add_files(file_list, client_id)
                await _send_music_action_ack("music_add_files", action_id)
            except Exception as exc:
                logger.warning("music_add_files handler error: %s", exc)
                if websocket is not None and action_id:
                    await websocket.send(json.dumps({
                        "type": "music_action_error",
                        "action": "music_add_files",
                        "action_id": str(action_id),
                        "error": str(exc),
                    }))
            return

        if msg_type == "music_create_playlist" and self._on_music_create_playlist:
            action_id = payload.get("action_id")
            name = str(payload.get("name", "")).strip()
            positions = payload.get("positions")
            try:
                pos_list = [int(p) for p in positions] if isinstance(positions, list) else []
                if name:
                    await _send_music_action_ack("music_create_playlist", action_id)
                    await self._on_music_create_playlist(name, pos_list, client_id)
                    await _send_music_playlists_update()
            except Exception as exc:
                logger.warning("music_create_playlist handler error: %s", exc)
                if websocket is not None and action_id:
                    await websocket.send(json.dumps({
                        "type": "music_action_error",
                        "action": "music_create_playlist",
                        "action_id": str(action_id),
                        "error": str(exc),
                    }))
            return

        if msg_type == "music_load_playlist" and self._on_music_load_playlist:
            action_id = payload.get("action_id")
            name = str(payload.get("name", "")).strip()
            try:
                if name:
                    await _send_music_action_ack("music_load_playlist", action_id)
                    await self._on_music_load_playlist(name, client_id)
                    await _send_music_playlists_update()
            except Exception as exc:
                logger.warning("music_load_playlist handler error: %s", exc)
                if websocket is not None and action_id:
                    await websocket.send(json.dumps({
                        "type": "music_action_error",
                        "action": "music_load_playlist",
                        "action_id": str(action_id),
                        "error": str(exc),
                    }))
            return

        if msg_type == "music_save_playlist" and self._on_music_save_playlist:
            action_id = payload.get("action_id")
            name = str(payload.get("name", "")).strip()
            try:
                if name:
                    await _send_music_action_ack("music_save_playlist", action_id)
                    await self._on_music_save_playlist(name, client_id)
                    await _send_music_playlists_update()
            except Exception as exc:
                logger.warning("music_save_playlist handler error: %s", exc)
                if websocket is not None and action_id:
                    await websocket.send(json.dumps({
                        "type": "music_action_error",
                        "action": "music_save_playlist",
                        "action_id": str(action_id),
                        "error": str(exc),
                    }))
            return

        if msg_type == "music_delete_playlist" and self._on_music_delete_playlist:
            action_id = payload.get("action_id")
            name = str(payload.get("name", "")).strip()
            try:
                if name:
                    await _send_music_action_ack("music_delete_playlist", action_id)
                    await self._on_music_delete_playlist(name, client_id)
                    await _send_music_playlists_update()
            except Exception as exc:
                logger.warning("music_delete_playlist handler error: %s", exc)
                if websocket is not None and action_id:
                    await websocket.send(json.dumps({
                        "type": "music_action_error",
                        "action": "music_delete_playlist",
                        "action_id": str(action_id),
                        "error": str(exc),
                    }))
            return

        if msg_type == "music_search_library" and self._on_music_search_library:
            query = str(payload.get("query", "")).strip()
            requested_limit = max(1, min(2000, int(payload.get("limit", 200) or 200)))
            try:
                rows = await self._on_music_search_library(query, requested_limit, client_id)
                if websocket is not None:
                    await websocket.send(json.dumps({
                        "type": "music_library_results",
                        "query": query,
                        "limit": requested_limit,
                        "results": rows or [],
                    }))
            except Exception as exc:
                logger.warning("music_search_library handler error: %s", exc)
            return

        if msg_type == "music_list_playlists" and self._on_music_list_playlists:
            try:
                names = await self._on_music_list_playlists(client_id)
                if websocket is not None:
                    await websocket.send(json.dumps({
                        "type": "music_playlists",
                        "playlists": names or [],
                    }))
            except Exception as exc:
                logger.warning("music_list_playlists handler error: %s", exc)
            return

        if msg_type == "timer_cancel" and self._on_timer_cancel:
            action_id = payload.get("action_id")
            timer_id = payload.get("timer_id", "")
            if timer_id:
                try:
                    await self._on_timer_cancel(str(timer_id), client_id)
                    if websocket is not None and action_id:
                        await websocket.send(json.dumps({
                            "type": "timer_action_ack",
                            "action": "timer_cancel",
                            "action_id": str(action_id),
                            "id": str(timer_id),
                        }))
                except Exception as exc:
                    logger.warning("timer_cancel handler error: %s", exc)
                    if websocket is not None and action_id:
                        await websocket.send(json.dumps({
                            "type": "timer_action_error",
                            "action": "timer_cancel",
                            "action_id": str(action_id),
                            "id": str(timer_id),
                            "error": str(exc),
                        }))
            return

        if msg_type == "alarm_cancel" and self._on_alarm_cancel:
            action_id = payload.get("action_id")
            alarm_id = payload.get("alarm_id", "")
            if alarm_id:
                try:
                    await self._on_alarm_cancel(str(alarm_id), client_id)
                    if websocket is not None and action_id:
                        await websocket.send(json.dumps({
                            "type": "timer_action_ack",
                            "action": "alarm_cancel",
                            "action_id": str(action_id),
                            "id": str(alarm_id),
                        }))
                except Exception as exc:
                    logger.warning("alarm_cancel handler error: %s", exc)
                    if websocket is not None and action_id:
                        await websocket.send(json.dumps({
                            "type": "timer_action_error",
                            "action": "alarm_cancel",
                            "action_id": str(action_id),
                            "id": str(alarm_id),
                            "error": str(exc),
                        }))
            return

        if msg_type == "tts_mute_set" and self._on_tts_mute_set:
            action_id = payload.get("action_id")
            enabled = bool(payload.get("enabled", False))
            try:
                await self._on_tts_mute_set(enabled, client_id)
                if websocket is not None and action_id:
                    await websocket.send(json.dumps({
                        "type": "setting_action_ack",
                        "action": "tts_mute_set",
                        "action_id": str(action_id),
                    }))
            except Exception as exc:
                logger.warning("tts_mute_set handler error: %s", exc)
                if websocket is not None and action_id:
                    await websocket.send(json.dumps({
                        "type": "setting_action_error",
                        "action": "tts_mute_set",
                        "action_id": str(action_id),
                        "error": str(exc),
                    }))
            return

        if msg_type == "browser_audio_set" and self._on_browser_audio_set:
            action_id = payload.get("action_id")
            enabled = bool(payload.get("enabled", True))
            try:
                await self._on_browser_audio_set(enabled, client_id)
                if websocket is not None and action_id:
                    await websocket.send(json.dumps({
                        "type": "setting_action_ack",
                        "action": "browser_audio_set",
                        "action_id": str(action_id),
                    }))
            except Exception as exc:
                logger.warning("browser_audio_set handler error: %s", exc)
                if websocket is not None and action_id:
                    await websocket.send(json.dumps({
                        "type": "setting_action_error",
                        "action": "browser_audio_set",
                        "action_id": str(action_id),
                        "error": str(exc),
                    }))
            return

        if msg_type == "continuous_mode_set" and self._on_continuous_mode_set:
            action_id = payload.get("action_id")
            enabled = bool(payload.get("enabled", False))
            try:
                await self._on_continuous_mode_set(enabled, client_id)
                if websocket is not None and action_id:
                    await websocket.send(json.dumps({
                        "type": "setting_action_ack",
                        "action": "continuous_mode_set",
                        "action_id": str(action_id),
                    }))
            except Exception as exc:
                logger.warning("continuous_mode_set handler error: %s", exc)
                if websocket is not None and action_id:
                    await websocket.send(json.dumps({
                        "type": "setting_action_error",
                        "action": "continuous_mode_set",
                        "action_id": str(action_id),
                        "error": str(exc),
                    }))
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
        self._browser_pcm_packet_count += 1
        self._browser_pcm_packet_bytes += len(pcm_bytes)

        now = time.monotonic()
        if now - self._last_audio_packet_log_ts >= 2.0:
            logger.info(
                "📦 Audio packet source summary: browser_pcm=%d (%d bytes, rms=%.4f peak=%.4f queued=%d) browser_audio_level=%d",
                self._browser_pcm_packet_count,
                self._browser_pcm_packet_bytes,
                self._latest_browser_audio["rms"],
                self._latest_browser_audio["peak"],
                len(self._browser_pcm_frames),
                self._browser_level_packet_count,
            )
            self._browser_level_packet_count = 0
            self._browser_pcm_packet_count = 0
            self._browser_pcm_packet_bytes = 0
            self._last_audio_packet_log_ts = now

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
        self._status_rev += 1
        orch = dict(self._orchestrator_status)
        orch["hotword_active"] = hotword_active
        orch["mic_enabled"] = self._ui_control_state.get("mic_enabled", False)
        orch["status_rev"] = self._status_rev
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
        orch["status_rev"] = self._status_rev
        return {
            "type": "state_snapshot",
            "orchestrator": orch,
            "ui_control": dict(self._ui_control_state),
            "ui_control_rev": self._ui_control_rev,
            "music": dict(self._music_state),
            "music_queue": list(self._music_queue),
            "music_rev": self._music_rev,
            "timers": list(self._timers_state),
            "timers_rev": self._timers_rev,
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
        ssl_context = self._ensure_ssl_context()
        start_http_servers(self, html, ssl_context)
