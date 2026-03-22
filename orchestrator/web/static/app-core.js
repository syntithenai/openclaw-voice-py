const RUNTIME = window.__OPENCLAW_RUNTIME__ || {};
const WS_PORT = Number(RUNTIME.wsPort || 0);
const MIC_STARTS_DISABLED = !!RUNTIME.micStartsDisabled;
const AUDIO_AUTHORITY = String(RUNTIME.audioAuthority || 'native');
const SERVER_INSTANCE_ID = String(RUNTIME.serverInstanceId || '');
const AUTH_MODE_BOOTSTRAP = String(RUNTIME.authMode || 'disabled').toLowerCase();
const AUTHENTICATED_BOOTSTRAP = !!RUNTIME.authenticated;
const AUTH_USER_BOOTSTRAP = (RUNTIME.authUser && typeof RUNTIME.authUser === 'object') ? RUNTIME.authUser : null;
const PREF_TTS_MUTED = 'openclaw.ui.ttsMuted';
const PREF_BROWSER_AUDIO = 'openclaw.ui.browserAudioEnabled';
const PREF_CONTINUOUS = 'openclaw.ui.continuousMode';
const PREF_MUSIC_QUEUE_FILTER = 'openclaw.ui.musicQueueFilter';
const PREF_MUSIC_PLAYLIST_FILTER = 'openclaw.ui.musicPlaylistFilter';
const CHAT_CACHE_VERSION = 1;
const PENDING_ACTION_TIMEOUT_MS = 8000;
const INLINE_ERROR_TTL_MS = 7000;
const MUSIC_LIBRARY_SEARCH_MIN_LEN = 3;
const WS_RECONNECT_MS = 1500;

const S = {
  ws:null, wsConnected:false,
  micEnabled:!MIC_STARTS_DISABLED,
  voice_state:'idle', wake_state:'asleep', tts_playing:false, mic_rms:0,
    chat:[], music:{state:'stop',title:'',artist:'',queue_length:0,elapsed:0,duration:0,position:-1,loaded_playlist:''},
    chatThreads:[], activeChatId:'active', selectedChatId:'active', chatSidebarOpen:true,
                chatThreadFilter:'',
        chatDeleteModalOpen:false, chatDeleteTargetId:'', chatDeleteTargetTitle:'',
        chatFollowLatest:true,
    musicQueue:[], musicPlaylists:[], musicLibraryResults:[],
        musicQueueFilter:'', musicPlaylistFilter:'', musicQueueSelectionByIds:{}, musicQueueLastCheckedId:null,
        musicAddMode:false, musicAddQuery:'', musicAddSelection:{}, musicAddLastCheckedFile:'', musicAddHasSearched:false,
        musicAddSearchPending:false, musicAddPendingQuery:'',
        musicNewPlaylistName:'',
        musicPlaylistModalOpen:false, musicPlaylistModalMode:'', musicPlaylistModalName:'', musicPlaylistModalOriginalName:'',
        recordings:[], recordingsDetail:null, recordingsDetailLoading:false,
        recordingsSelectionByIds:{}, recordingsLastCheckedId:null,
        recordingsActionError:'', recordingsActionErrorTs:0, lastRecordingsRev:0,
        recordingsDeletePending:false, recorderStartPending:false, recorderStopPending:false,
        recorderActive:false,
        timers:[], page:'home',
    audioCtx:null, mediaStream:null, processor:null, lastLevel:0,
    feedbackAudioCtx:null,
    captureWorkletModuleReady:false,
    ttsMuted:true, browserAudioEnabled:true, continuousMode:false,
    pendingChatSends:new Set(), nextClientMsgId:1,
    nextMusicActionId:1, pendingMusicActions:{},
    nextTimerActionId:1, pendingTimerActions:{},
    nextRecordingsActionId:1,
    nextSettingActionId:1, pendingSettingActions:{},
    settingActionErrors:{}, timerActionErrors:{},
    musicActionError:'', musicActionErrorTs:0,
    optimisticTimers:{},
    _lastOptimisticIntentKey:'',
    _lastOptimisticIntentTs:0,
    lastStatusRev:0, lastMusicRev:0, lastTimersRev:0, lastUiControlRev:0,
    wsDebug:{ status:'init', lastCloseCode:null, lastCloseReason:'', lastError:'' },
    wsManualDisconnect:false, wsReconnectTimer:null,
    wsPingTimer:null,
    captureRetryTimer:null,
    lastAudioInputCount:null,
    scrollToBottomPending:false,
    autoScrollUntilTs:0,
    authMode: AUTH_MODE_BOOTSTRAP,
    isAuthenticated: AUTHENTICATED_BOOTSTRAP,
    authUser: AUTH_USER_BOOTSTRAP,
};

function authRequiresLogin(){
    return String(S.authMode||'disabled')==='required';
}

function wsAuthAllowed(){
    return !(authRequiresLogin() && !S.isAuthenticated);
}

function buildAuthLoginUrl(){
    const next = String(location.pathname||'/') + String(location.search||'') + String(location.hash||'');
    return '/auth/google/login?next=' + encodeURIComponent(next || '/');
}

function renderAuthButton(){
    const btn = document.getElementById('loginBtn');
    if(!btn) return;

    if(String(S.authMode||'disabled')==='disabled'){
        btn.classList.add('hidden');
        return;
    }

    btn.classList.remove('hidden');
    if(S.isAuthenticated){
        const displayName = String((S.authUser&&S.authUser.given_name) || (S.authUser&&S.authUser.name) || (S.authUser&&S.authUser.email) || 'Account');
        const compact = displayName.length > 18 ? (displayName.slice(0, 17) + '…') : displayName;
        btn.textContent = compact;
        btn.setAttribute('title', 'Signed in as ' + String((S.authUser&&S.authUser.email)||displayName) + '. Click to sign out');
        btn.dataset.action = 'auth-logout';
        btn.classList.remove('bg-gray-800','hover:bg-gray-700','border-gray-700');
        btn.classList.add('bg-emerald-800','hover:bg-emerald-700','border-emerald-600');
    } else {
        btn.textContent = 'Sign in';
        btn.setAttribute('title', 'Sign in with Google');
        btn.dataset.action = 'auth-login';
        btn.classList.remove('bg-emerald-800','hover:bg-emerald-700','border-emerald-600');
        btn.classList.add('bg-gray-800','hover:bg-gray-700','border-gray-700');
    }
}

async function refreshAuthSession(opts={}){
    const shouldRender = opts.render!==false;
    const shouldAdjustWs = opts.adjustWs!==false;
    try{
        const resp = await fetch('/auth/session', { method:'GET', credentials:'same-origin', cache:'no-store' });
        if(resp.ok){
            const data = await resp.json();
            S.authMode = String((data&&data.mode)||S.authMode||'disabled').toLowerCase();
            S.isAuthenticated = !!(data&&data.authenticated);
            S.authUser = (data&&data.user&&typeof data.user==='object') ? data.user : null;
        }
    }catch(_ ){}

    renderAuthButton();

    if(shouldAdjustWs){
        if(!wsAuthAllowed()){
            if(typeof disconnectWs==='function'){
                try{ await disconnectWs(true); }catch(_ ){}
            }
        } else if(typeof connectWs==='function'){
            S.wsManualDisconnect=false;
            connectWs();
        }
    }

    if(shouldRender && typeof renderPage==='function') renderPage();
    if(typeof updateMicInteractivity==='function') updateMicInteractivity();
}

function triggerGoogleLogin(){
    window.location.assign(buildAuthLoginUrl());
}

async function triggerGoogleLogout(){
    try{
        await fetch('/auth/logout', { method:'POST', credentials:'same-origin' });
    }catch(_ ){}
    await refreshAuthSession({ render:true, adjustWs:true });
}

const SIMPLE_NUMBER_WORDS = {
    zero:0, one:1, two:2, three:3, four:4, five:5, six:6, seven:7, eight:8, nine:9,
    ten:10, eleven:11, twelve:12, thirteen:13, fourteen:14, fifteen:15, sixteen:16,
    seventeen:17, eighteen:18, nineteen:19, twenty:20, thirty:30, forty:40, fifty:50, sixty:60,
};

function parseSimpleNumberToken(raw){
    const s=String(raw||'').trim().toLowerCase().replace(/[^a-z0-9\-\s]/g,'');
    if(!s) return null;
    if(/^\d+(?:\.\d+)?$/.test(s)) return Number(s);
    if(s==='half' || s==='a half') return 0.5;
    if(s==='couple' || s==='a couple') return 2;
    const halfMatch=s.match(/^(.+?)\s+and\s+a\s+half$/);
    if(halfMatch){
        const base=parseSimpleNumberToken(halfMatch[1]);
        if(Number.isFinite(base)) return Number(base)+0.5;
    }
    if(SIMPLE_NUMBER_WORDS[s]!==undefined) return SIMPLE_NUMBER_WORDS[s];
    if(s.includes('-')){
        const parts=s.split('-').map(p=>p.trim()).filter(Boolean);
        if(parts.length===2 && SIMPLE_NUMBER_WORDS[parts[0]]!==undefined && SIMPLE_NUMBER_WORDS[parts[1]]!==undefined){
            return SIMPLE_NUMBER_WORDS[parts[0]] + SIMPLE_NUMBER_WORDS[parts[1]];
        }
    }
    if(s.includes(' ')){
        const parts=s.split(/\s+/).filter(Boolean);
        if(parts.length===2 && SIMPLE_NUMBER_WORDS[parts[0]]!==undefined && SIMPLE_NUMBER_WORDS[parts[1]]!==undefined){
            return SIMPLE_NUMBER_WORDS[parts[0]] + SIMPLE_NUMBER_WORDS[parts[1]];
        }
    }
    return null;
}

function parseSimpleDurationSeconds(text){
    const raw=String(text||'').toLowerCase();
    const compact=raw.replace(/[,!?\.]/g,' ');
    const patterns=[
        /\b(?:for|in)\s*([a-z0-9\-\s]{1,36}?)\s*(seconds?|secs?|minutes?|mins?|hours?|hrs?)\b/i,
        /\b([a-z0-9\-\s]{1,36}?)\s*(seconds?|secs?|minutes?|mins?|hours?|hrs?)\b/i,
    ];
    for(const rx of patterns){
        const m=compact.match(rx);
        if(!m) continue;
        const count=parseSimpleNumberToken(m[1]);
        if(!Number.isFinite(count) || count<=0) continue;
        const unit=String(m[2]||'').toLowerCase();
        let mult=1;
        if(unit.startsWith('min')) mult=60;
        else if(unit.startsWith('hour') || unit.startsWith('hr')) mult=3600;
        const total=Math.round(Number(count)*mult);
        if(Number.isFinite(total) && total>0) return total;
    }
    return null;
}

function extractScheduleLabel(normalized, kind){
    const named=normalized.match(/\b(?:called|named)\s+([a-z][a-z0-9\s]{1,28})\b/i);
    if(named && named[1]){
        return String(named[1]).trim().replace(/\s+/g,' ').replace(/\b(timer|alarm)\b/gi,'').trim();
    }
    const kindWord=kind==='alarm'?'alarm':'timer';
    const prefixed=normalized.match(new RegExp('\\bset\\s+(?:an?\\s+)?([a-z][a-z0-9\\s]{1,28})\\s+'+kindWord+'\\b','i'));
    if(prefixed && prefixed[1]){
        const raw=String(prefixed[1]).replace(/\b(for|in)\b.*$/i,'').trim();
        const cleaned=raw.replace(/\s+/g,' ').trim();
        if(cleaned && cleaned!=='a' && cleaned!=='an' && cleaned!=='the') return cleaned;
    }
    return '';
}

function inferOptimisticScheduleFromText(text){
    const raw=String(text||'').trim();
    if(!raw) return null;
    const normalized=raw.toLowerCase();
    if(!/\b(set|create|start|wake\s+me|wake\s+us|remind\s+me|alarm\s+me)\b/.test(normalized)) return null;
    const wakeStyle=/\bwake\s+(me|us)\b/.test(normalized);
    const remindStyle=/\bremind\s+me\b/.test(normalized);
    const isAlarm=/\balarm\b/.test(normalized) || wakeStyle;
    const isTimer=/\btimer\b/.test(normalized);
    if(!isAlarm && !isTimer && !remindStyle) return null;
    const durationSeconds=parseSimpleDurationSeconds(normalized);
    if(!durationSeconds) return null;
    const kind=isAlarm?'alarm':'timer';
    const label=extractScheduleLabel(normalized, kind);
    return {
        kind,
        durationSeconds,
        label,
        dedupeKey:kind+':'+String(durationSeconds)+':'+String(label||''),
    };
}

function queueOptimisticTimerFromText(text, sourceTag='user'){
    const parsed=inferOptimisticScheduleFromText(text);
    if(!parsed) return false;
    const now=Date.now();
    const key=String(parsed.dedupeKey||'');
    if(key && S._lastOptimisticIntentKey===key && (now-Number(S._lastOptimisticIntentTs||0))<2500){
        return false;
    }
    S._lastOptimisticIntentKey=key;
    S._lastOptimisticIntentTs=now;

    const id='optimistic-'+now+'-'+Math.random().toString(36).slice(2,7);
    const label=(parsed.label&&String(parsed.label).trim()) || (parsed.kind==='alarm'?'Alarm':'Timer');
    const pendingServerAck=parsed.kind==='alarm';
    const optimisticEntry={
        id,
        kind:parsed.kind,
        label,
        remaining_seconds:Number(parsed.durationSeconds)||0,
        ringing:false,
        _optimistic:true,
        _pendingServerAck:pendingServerAck,
        _clientAnchorTs:pendingServerAck?null:(now/1000),
        _clientAnchorRem:Number(parsed.durationSeconds)||0,
    };
    S.optimisticTimers[id]={
        id,
        kind:parsed.kind,
        createdAtMs:now,
        durationSeconds:parsed.durationSeconds,
        source:String(sourceTag||'user'),
        label,
        pendingServerAck,
    };

    const currentTimers=Array.isArray(S.timers)?S.timers:[];
    S.timers=currentTimers
        .filter(t=>!(t&&t._optimistic&&String(t.id||'')===id))
        .concat([optimisticEntry])
        .sort((a,b)=>{
            const ak=String((a&&a.kind)||'timer').toLowerCase()==='alarm'?0:1;
            const bk=String((b&&b.kind)||'timer').toLowerCase()==='alarm'?0:1;
            if(ak!==bk) return ak-bk;
            return (Number((a&&a.remaining_seconds)||0))-(Number((b&&b.remaining_seconds)||0));
        });
    return true;
}

function applyToggle(btn, enabled){
        if(!btn) return;
        btn.classList.toggle('bg-emerald-700', !!enabled);
        btn.classList.toggle('border-emerald-500', !!enabled);
        btn.classList.toggle('bg-gray-700', !enabled);
        btn.classList.toggle('border-gray-600', !enabled);
        const knob=btn.querySelector('span');
        if(knob) knob.style.transform = enabled ? 'translateX(18px)' : 'translateX(0px)';
        btn.setAttribute('aria-checked', enabled ? 'true' : 'false');
}

function readBoolPref(key, fallback){
    try {
        const raw = localStorage.getItem(key);
        if (raw === null || raw === undefined || raw === '') return !!fallback;
        return String(raw).toLowerCase() === 'true';
    } catch(_) {
        return !!fallback;
    }
}

function writeBoolPref(key, value){
    try { localStorage.setItem(key, value ? 'true' : 'false'); } catch(_) {}
}

function readStringPref(key, fallback){
    try {
        const raw = localStorage.getItem(key);
        return (raw === null || raw === undefined) ? (fallback||'') : String(raw);
    } catch(_) { return fallback||''; }
}

function writeStringPref(key, value){
    try { localStorage.setItem(key, String(value||'')); } catch(_) {}
}

function canSearchMusicLibrary(query){
    return String(query||'').trim().length >= MUSIC_LIBRARY_SEARCH_MIN_LEN;
}

function submitMusicLibrarySearch(){
    const query = String(S.musicAddQuery||'').trim();
    if(!canSearchMusicLibrary(query)) return;
    S.musicAddHasSearched = true;
    S.musicAddSearchPending = true;
    S.musicAddPendingQuery = query;
    S.musicLibraryResults = [];
    if(S.page==='music' && S.musicAddMode) renderMusicPage(document.getElementById('main'));
    sendAction({type:'music_search_library', query});
}

function getChatCacheKey(){
    return 'openclaw.ui.chatCache.v'+CHAT_CACHE_VERSION+'::'+location.origin+'::'+WS_PORT;
}

function normalizeChatMessage(m){
    if(!m || typeof m!=='object') return null;
    const msg=Object.assign({}, m);
    if(msg.id===undefined||msg.id===null) msg.id = 'msg-'+Math.random().toString(36).slice(2,10);
    if(msg.ts===undefined||msg.ts===null) msg.ts = Date.now()/1000;
    return msg;
}

function normalizeChatThread(t){
    if(!t || typeof t!=='object') return null;
    const thread=Object.assign({}, t);
    thread.id = String(thread.id || ('thread-'+Math.random().toString(36).slice(2,10)));
    thread.title = String(thread.title || 'Untitled');
    thread.messages = Array.isArray(thread.messages) ? thread.messages.map(normalizeChatMessage).filter(Boolean) : [];
    const now = Date.now()/1000;
    thread.created_ts = Number(thread.created_ts || thread.updated_ts || now) || now;
    thread.updated_ts = Number(thread.updated_ts || thread.created_ts || now) || now;
    return thread;
}

function mergeChatThreads(serverThreads, cachedThreads){
    const merged = new Map();
    (Array.isArray(cachedThreads)?cachedThreads:[]).map(normalizeChatThread).filter(Boolean).forEach(t=>merged.set(t.id, t));
    (Array.isArray(serverThreads)?serverThreads:[]).map(normalizeChatThread).filter(Boolean).forEach(t=>{
        const existing = merged.get(t.id);
        if(!existing || Number(t.updated_ts||0) >= Number(existing.updated_ts||0)) merged.set(t.id, t);
    });
    return [...merged.values()].sort((a,b)=>Number(b.updated_ts||0)-Number(a.updated_ts||0));
}

function persistChatCache(){
    try {
        const payload = {
            version: CHAT_CACHE_VERSION,
            saved_ts: Date.now()/1000,
            activeChatId: String(S.activeChatId||'active'),
            selectedChatId: String(S.selectedChatId||'active'),
            chat: (Array.isArray(S.chat)?S.chat:[]).map(normalizeChatMessage).filter(Boolean),
            chatThreads: (Array.isArray(S.chatThreads)?S.chatThreads:[]).map(normalizeChatThread).filter(Boolean),
        };
        localStorage.setItem(getChatCacheKey(), JSON.stringify(payload));
    } catch(_) {}
}

function hydrateChatCache(){
    try {
        const raw = localStorage.getItem(getChatCacheKey());
        if(!raw) return;
        const data = JSON.parse(raw);
        if(!data || typeof data!=='object') return;
        if(Array.isArray(data.chat)) S.chat = data.chat.map(normalizeChatMessage).filter(Boolean);
        if(Array.isArray(data.chatThreads)) S.chatThreads = data.chatThreads.map(normalizeChatThread).filter(Boolean);
        if(data.activeChatId) S.activeChatId = String(data.activeChatId);
        if(data.selectedChatId) S.selectedChatId = String(data.selectedChatId);
    } catch(_) {}
}

function applyServerChatState(chat, chatThreads, activeChatId){
    if(Array.isArray(chat)) S.chat = chat.map(normalizeChatMessage).filter(Boolean);
    if(Array.isArray(chatThreads)){
        S.chatThreads = chatThreads
            .map(normalizeChatThread)
            .filter(Boolean)
            .sort((a,b)=>Number(b.updated_ts||0)-Number(a.updated_ts||0));
    }
    if(activeChatId) S.activeChatId = String(activeChatId);
    if(!S.selectedChatId) S.selectedChatId = 'active';
    const selected = String(S.selectedChatId||'active');
    if(selected!=='active' && !(S.chatThreads||[]).some(t=>String(t.id||'')===selected)) S.selectedChatId = 'active';
    persistChatCache();
}

function loadUiPrefs(){
    S.ttsMuted = readBoolPref(PREF_TTS_MUTED, true);
    S.browserAudioEnabled = readBoolPref(PREF_BROWSER_AUDIO, true);
    S.continuousMode = readBoolPref(PREF_CONTINUOUS, false);
    S.musicQueueFilter = readStringPref(PREF_MUSIC_QUEUE_FILTER, '');
    S.musicPlaylistFilter = readStringPref(PREF_MUSIC_PLAYLIST_FILTER, '');
}

function pushUiPrefsToServer(){
    if(!S.ws || S.ws.readyState!==WebSocket.OPEN) return;
    if(!S.pendingSettingActions['tts_mute_set']) sendSettingAction('tts_mute_set', !!S.ttsMuted);
    if(!S.pendingSettingActions['browser_audio_set']) sendSettingAction('browser_audio_set', !!S.browserAudioEnabled);
    if(!S.pendingSettingActions['continuous_mode_set']) sendSettingAction('continuous_mode_set', !!S.continuousMode);
}

function applyMicControlToggles(){
        applyToggle(document.getElementById('ttsMuteToggle'), !!S.ttsMuted);
        applyToggle(document.getElementById('browserAudioToggle'), !!S.browserAudioEnabled);
        applyToggle(document.getElementById('continuousModeToggle'), !!S.continuousMode);
    const ttsBtn=document.getElementById('ttsMuteToggle');
    const browserBtn=document.getElementById('browserAudioToggle');
    const contBtn=document.getElementById('continuousModeToggle');
    const pend=S.pendingSettingActions||{};
    const ttsPending=!!pend['tts_mute_set'];
    const browserPending=!!pend['browser_audio_set'];
    const contPending=!!pend['continuous_mode_set'];
    [[ttsBtn,ttsPending],[browserBtn,browserPending],[contBtn,contPending]].forEach(([btn,pending])=>{ if(!btn) return; btn.disabled=!!pending; btn.classList.toggle('opacity-60',!!pending); btn.classList.toggle('cursor-not-allowed',!!pending); });
    const ttsErr=(S.settingActionErrors&&S.settingActionErrors['tts_mute_set'])?S.settingActionErrors['tts_mute_set'].msg:'';
    const browserErr=(S.settingActionErrors&&S.settingActionErrors['browser_audio_set'])?S.settingActionErrors['browser_audio_set'].msg:'';
    const contErr=(S.settingActionErrors&&S.settingActionErrors['continuous_mode_set'])?S.settingActionErrors['continuous_mode_set'].msg:'';
    const ttsLabel=document.getElementById('ttsMuteLabel');
    const browserLabel=document.getElementById('browserAudioLabel');
    const contLabel=document.getElementById('continuousModeLabel');
    if(ttsLabel) ttsLabel.textContent='Mute TTS output'+(ttsErr?' ⚠ '+ttsErr:'');
    if(browserLabel) browserLabel.textContent='Browser audio streaming'+(browserErr?' ⚠ '+browserErr:'');
    if(contLabel) contLabel.textContent='Continuous mode'+(contErr?' ⚠ '+contErr:'');
}

function formatMusicTime(seconds){
    const safe=Math.max(0, Math.floor(Number(seconds)||0));
    const mm=Math.floor(safe/60);
    const ss=String(safe%60).padStart(2,'0');
    return mm+':'+ss;
}

function getEffectiveMusicElapsed(){
    const base=Math.max(0, Number(S.music && S.music.elapsed) || 0);
    const duration=Math.max(0, Number(S.music && S.music.duration) || 0);
    const state=String((S.music&&S.music.state)||'').toLowerCase();
    if(state!=='play' || !S.music || !S.music._clientElapsedAnchorTs) return Math.min(base, duration||base);
    const delta=Math.max(0, (Date.now()-Number(S.music._clientElapsedAnchorTs||Date.now()))/1000);
    const predicted=base+delta;
    if(duration>0) return Math.min(duration, predicted);
    return predicted;
}

function onTopMusicProgressClick(event){
    if(!S.music) return;
    const duration=Math.max(0, Number(S.music.duration)||0);
    if(duration<=0) return;
    if(!(S.ws&&S.ws.readyState===WebSocket.OPEN)) return;
    const bar=document.getElementById('wsDebugBanner');
    if(!bar) return;
    const rect=bar.getBoundingClientRect();
    if(!rect || rect.width<=0) return;
    const pointerX=Number(event&&event.clientX);
    const fallbackX=rect.width*Math.max(0, Math.min(1, getEffectiveMusicElapsed()/duration));
    const rawX=Number.isFinite(pointerX) ? (pointerX-rect.left) : fallbackX;
    const x=Math.min(rect.width, Math.max(0, rawX));
    const ratio=x/rect.width;
    const target=Math.max(0, Math.min(duration, duration*ratio));
    // Optimistically update client-side elapsed time so UI stays at the target position
    if(S.music){
        S.music.elapsed = target;
        // Reset the client‑side anchor timestamp used by getEffectiveMusicElapsed()
        S.music._clientElapsedAnchorTs = Date.now();
    }
    sendMusicAction('music_seek', {seconds: target});
}

function applyTopMusicProgress(){
    const bar=document.getElementById('wsDebugBanner');
    const fill=document.getElementById('wsProgressFill');
    const text=document.getElementById('wsDebugText');
    if(!bar || !fill || !text) return;
    const duration=Math.max(0, Number(S.music && S.music.duration)||0);
    const state=String((S.music&&S.music.state)||'').toLowerCase();
    const active=(state==='play'||state==='pause') && duration>0;
    if(!active){
        bar.classList.add('hidden');
        fill.style.width='0%';
        text.textContent='0:00 / 0:00';
        return;
    }
    bar.classList.remove('hidden');
    const elapsed=getEffectiveMusicElapsed();
    const ratio=Math.max(0, Math.min(1, duration>0?(elapsed/duration):0));
    fill.style.width=(ratio*100).toFixed(2)+'%';
    text.textContent=formatMusicTime(elapsed)+' / '+formatMusicTime(duration);
    bar.title='Click to seek';
}

function updateWsDebugBanner(){
    applyTopMusicProgress();
}

function updateMicInteractivity(){
    const btn=document.getElementById('micBtn');
    const menuBtn=document.getElementById('micMenuBtn');
    if(!btn) return;
    const connected=!!S.wsConnected;
    btn.disabled=!connected;
    btn.classList.toggle('opacity-60', !connected);
    btn.classList.toggle('cursor-not-allowed', !connected);
    if(menuBtn){
      menuBtn.disabled=!connected;
      menuBtn.classList.toggle('opacity-60', !connected);
      menuBtn.classList.toggle('cursor-not-allowed', !connected);
    }
    btn.classList.toggle('bg-gray-700', !connected);
    btn.classList.toggle('border-gray-500', !connected);
    if(!connected){
        btn.title='Microphone disabled: WebSocket not connected';
    } else {
        btn.title='Microphone';
    }
}

function startWsPingTimer(){
    if(S.wsPingTimer) clearInterval(S.wsPingTimer);
    S.wsPingTimer=setInterval(()=>{
        if(S.ws && S.ws.readyState === WebSocket.OPEN){
            try { S.ws.send(JSON.stringify({type:'ping'})); } catch(_) {}
            if(S.page==='music' && typeof sendAction==='function'){
                sendAction({type:'music_list_playlists'});
            }
        }
    }, 30000);
}

function stopWsPingTimer(){
    if(S.wsPingTimer){ clearInterval(S.wsPingTimer); S.wsPingTimer=null; }
}

function updateChatComposerState(){
    const input=document.getElementById('chatInput');
    const sendBtn=document.getElementById('chatSendBtn');
    const isPending=S.pendingChatSends.size>0;
    if(input){
        input.disabled=isPending;
        input.placeholder=isPending?'Sending...':'Type a message';
    }
    if(sendBtn){
        sendBtn.disabled=isPending;
        sendBtn.classList.toggle('opacity-60',isPending);
        sendBtn.classList.toggle('cursor-not-allowed',isPending);
        sendBtn.textContent=isPending?'Sending...':'Send';
    }
}

function isChatAtBottom(){
    const area=document.getElementById('chatArea');
    if(!area) return true;
    return (area.scrollTop + area.clientHeight) >= (area.scrollHeight - 8);
}

function updateScrollDownButton(){
    const wrap=document.getElementById('scrollDownWrap');
    if(!wrap) return;
    const area=document.getElementById('chatArea');
    if(!area || S.page!=='home'){
        wrap.classList.add('hidden');
        wrap.classList.remove('flex');
        return;
    }
    const overflow=area.scrollHeight > (area.clientHeight + 1);
    const atBottom=isChatAtBottom();
    const shouldShow=overflow && !atBottom;
    wrap.classList.toggle('hidden', !shouldShow);
    wrap.classList.toggle('flex', shouldShow);
}

function getScrollUpArea(){
    if(S.page==='home') return document.getElementById('chatArea');
    if(S.page==='music') return document.getElementById('main');
    if(S.page==='recordings') return document.getElementById('main');
    return null;
}

function updateScrollUpButton(){
    const wrap=document.getElementById('scrollUpWrap');
    if(!wrap) return;
    const area=getScrollUpArea();
    if(!area){
        wrap.classList.add('hidden');
        wrap.classList.remove('flex');
        return;
    }
    const overflow=area.scrollHeight > (area.clientHeight + 1);
    const partiallyScrolled=area.scrollTop > 8;
    const shouldShow=overflow && partiallyScrolled;
    wrap.classList.toggle('hidden', !shouldShow);
    wrap.classList.toggle('flex', shouldShow);
}

function scrollCurrentViewUp(){
    const area=getScrollUpArea();
    if(!area) return;
    area.scrollTop=0;
    updateScrollUpButton();
    updateScrollDownButton();
}

function requestScrollToBottomBurst(){
    S.scrollToBottomPending=true;
    S.autoScrollUntilTs=Date.now()+12000;
}

function getPage(){
    const h=location.hash.replace('#','');
    if(h==='/music') return 'music';
    if(h==='/recordings') return 'recordings';
    return 'home';
}
function navigate(p){ location.hash='#/'+p; }
function updateNavActiveState(){
    document.querySelectorAll('[data-nav]').forEach(el=>{
        const isActive=(el.dataset.nav||'')===S.page;
        el.classList.toggle('bg-gray-700', isActive);
        el.classList.toggle('text-white', isActive);
        el.classList.toggle('text-gray-300', !isActive);
    });
}
window.addEventListener('hashchange',()=>{
    S.page=getPage();
    renderPage();
    updateNavActiveState();
    closeMenu();
    if(S.page==='music' && typeof sendAction==='function'){
        sendAction({type:'music_list_playlists'});
    }
});

function closeMenu(){ document.getElementById('menuDropdown').classList.add('hidden'); }
function closeMicControlMenu(){ const m=document.getElementById('micControlDropdown'); if(m) m.classList.add('hidden'); }
document.getElementById('menuBtn').addEventListener('click',e=>{ e.stopPropagation(); document.getElementById('menuDropdown').classList.toggle('hidden'); });
document.getElementById('micMenuBtn').addEventListener('click',e=>{ e.stopPropagation(); const m=document.getElementById('micControlDropdown'); if(m) m.classList.toggle('hidden'); });

const MESSAGE_TOAST_DURATION_MS = 2000;
let S_msgToastTimer=null;

function clearMessageToasts(container, { animate = false } = {}) {
    if (!container) return;
    const toasts = container.querySelectorAll('.msg-toast');
    toasts.forEach((toastEl) => {
        if (animate) {
            toastEl.classList.add('exit');
            setTimeout(() => toastEl.remove(), 300);
            return;
        }
        toastEl.remove();
    });
}

function showMessageToast(message, role) {
    if (S.page === 'home') return;
    if (role !== 'user' && role !== 'assistant') return;
    
    const container = document.getElementById('msgToastContainer');
    if (!container) return;

    const text = String(message || '');
    if (!text.trim()) return;
    
    // Clear existing toast and timer if any
    if (S_msgToastTimer) {
        clearTimeout(S_msgToastTimer);
        S_msgToastTimer = null;
    }
    clearMessageToasts(container);
    
    const toastEl = document.createElement('div');
    toastEl.className = 'msg-toast px-3 py-2 rounded-lg text-sm bg-gray-800 border border-gray-700 shadow-lg mb-2';
    
    if (role === 'user') {
        toastEl.classList.add('border-blue-600', 'text-blue-100');
        toastEl.innerHTML = `<strong class="text-blue-300">You:</strong> ${escapeHtml(text.substring(0, 100))}${text.length > 100 ? '...' : ''}`;
    } else {
        toastEl.classList.add('border-green-600', 'text-green-100');
        toastEl.innerHTML = `<strong class="text-green-300">Assistant:</strong> ${escapeHtml(text.substring(0, 100))}${text.length > 100 ? '...' : ''}`;
    }
    
    container.appendChild(toastEl);
    
    // Auto-hide after 2 seconds
    S_msgToastTimer = setTimeout(() => {
        if (!toastEl.isConnected) {
            S_msgToastTimer = null;
            return;
        }
        clearMessageToasts(container, { animate: true });
        S_msgToastTimer = null;
    }, MESSAGE_TOAST_DURATION_MS);
}

function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}
