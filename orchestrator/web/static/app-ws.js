function handleMsg(msg){
  // Debug logging for music actions
  if(msg.type && msg.type.startsWith('music_')) console.log(`📥 Received message type="${msg.type}"`, {queue_len: Array.isArray(msg.music_queue) ? msg.music_queue.length : 'N/A', rev: msg.music_rev, action_ack: msg.action_ack});
  
  // Timing instrumentation for large playlists
  const isLargePlaylist = Array.isArray(msg.music_queue) && msg.music_queue.length > 50;
  if (isLargePlaylist) console.time('🎵 music_queue render');
  
  switch(msg.type){
    case 'hello': break;
    case 'state_snapshot':
    if(msg.orchestrator){
        const rev = Number(msg.orchestrator.status_rev||0);
        if(!Number.isNaN(rev) && rev>0) S.lastStatusRev = Math.max(S.lastStatusRev, rev);
        applyOrch(msg.orchestrator);
    }
            if(msg.ui_control){
                const uiRev = Number(msg.ui_control_rev||0);
                const staleUi = (!Number.isNaN(uiRev) && uiRev>0 && uiRev<S.lastUiControlRev);
                if(!staleUi){
                if(!Number.isNaN(uiRev) && uiRev>0) S.lastUiControlRev = uiRev;
                if(msg.ui_control.mic_enabled!==undefined) S.micEnabled=!!msg.ui_control.mic_enabled;
                if(msg.ui_control.tts_muted!==undefined && !S.pendingSettingActions['tts_mute_set']) S.ttsMuted=!!msg.ui_control.tts_muted;
                if(msg.ui_control.browser_audio_enabled!==undefined && !S.pendingSettingActions['browser_audio_set']) S.browserAudioEnabled=!!msg.ui_control.browser_audio_enabled;
                if(msg.ui_control.continuous_mode!==undefined && !S.pendingSettingActions['continuous_mode_set']) S.continuousMode=!!msg.ui_control.continuous_mode;
                S.settingActionErrors={};
                applyMicState();
                applyMicControlToggles();
                }
            }
    if(msg.music) applyMusic(msg.music);
    if(Array.isArray(msg.music_queue)){
        S.musicQueue=msg.music_queue;
        syncMusicFromQueue();
        if(msg.music_queue.length > 50) console.time('🎵 state_snapshot page render');
    }
    if(Array.isArray(msg.music_playlists)) S.musicPlaylists = msg.music_playlists;
    if(msg.music_rev!==undefined) S.lastMusicRev=Math.max(S.lastMusicRev, Number(msg.music_rev)||0);
    if(Array.isArray(msg.timers)) applyTimers(msg.timers);
    if(msg.timers_rev!==undefined) S.lastTimersRev=Math.max(S.lastTimersRev, Number(msg.timers_rev)||0);
            applyServerChatState(msg.chat, msg.chat_threads, msg.active_chat_id);
            if(S.page==='music' && (!Array.isArray(S.musicPlaylists) || S.musicPlaylists.length===0)){
                sendAction({type:'music_list_playlists'});
            }
            if(S.page==='music' && S.musicQueue && S.musicQueue.length > 50) {
                const t0 = performance.now();
                renderPage();
                const elapsed = performance.now() - t0;
                console.timeEnd('🎵 state_snapshot page render');
                console.log(`  → renderPage: ${elapsed.toFixed(1)}ms for ${S.musicQueue.length} queue items`);
            } else {
                renderPage();
            }
      break;
    case 'orchestrator_status':
        if(msg.status_rev!==undefined){
            const rev=Number(msg.status_rev)||0;
            if(rev<=S.lastStatusRev) break;
            S.lastStatusRev=rev;
        }
        applyOrch(msg);
        break;
    case 'status': if(msg.orchestrator) applyOrch(msg.orchestrator); break;
        case 'chat_append':
            if(msg.message){
                applyServerChatState(undefined, msg.chat_threads, msg.active_chat_id);
                const nextMsg = normalizeChatMessage(msg.message);
                if(nextMsg) S.chat.push(nextMsg);
                if(nextMsg && nextMsg.role==='user') requestScrollToBottomBurst();
                if(nextMsg && nextMsg.role==='user' && queueOptimisticTimerFromText(String(nextMsg.text||''), 'chat_append')){
                    renderTimerBar();
                }
                S.selectedChatId='active';
                persistChatCache();
                if(S.page==='home'){
                    renderThreadList('active');
                    renderChatMessages('active');
                }
            }
            break;

        case 'chat_threads_update':
            applyServerChatState(undefined, msg.chat_threads, msg.active_chat_id);
            if(S.page==='home') renderPage();
            break;
        case 'chat_reset':
            S.chat=[];
            applyServerChatState([], msg.chat_threads, msg.active_chat_id);
            S.selectedChatId='active';
            persistChatCache();
            if(S.page==='home') renderPage();
            break;
        case 'chat_text_ack':
            if(msg.client_msg_id) S.pendingChatSends.delete(String(msg.client_msg_id));
            if(S.page==='home') updateChatComposerState();
            break;
        case 'navigate':
            if(msg.page==='music' || msg.page==='home'){
                navigate(msg.page);
            }
            break;
        case 'music_transport':
            if(msg.music_rev!==undefined){
                const rev=Number(msg.music_rev)||0;
                if(rev<=S.lastMusicRev) break;
                S.lastMusicRev=rev;
            }
            applyMusic(msg.music||msg);
            // Transport updates can arrive frequently (elapsed/position changes).
            // Avoid full page re-render on each tick, especially with large queues.
            applyMusicHeader();
            break;
        case 'music_queue':
            if(msg.music_rev!==undefined){
                const rev=Number(msg.music_rev)||0;
                if(rev<=S.lastMusicRev) break;
                S.lastMusicRev=rev;
            }
            if(S._musicStateRetryTimer){
                clearTimeout(S._musicStateRetryTimer);
                S._musicStateRetryTimer = null;
            }
            if(msg.queue!==undefined){
                S.musicQueue=msg.queue;
                syncMusicFromQueue();
            }
            if(S.page==='music') {
                const t0 = performance.now();
                renderMusicPage(document.getElementById('main'));
                const elapsed = performance.now() - t0;
                if(msg.queue && msg.queue.length > 50) console.timeEnd('🎵 music_queue render'), console.log(`  → renderMusicPage: ${elapsed.toFixed(1)}ms for ${msg.queue.length} items`);
            }
            applyMusicHeader();
            break;
        case 'music_state':
            if(msg.music_rev!==undefined){
                const rev=Number(msg.music_rev)||0;
                if(rev<=S.lastMusicRev) break;
                S.lastMusicRev=rev;
            }
            applyMusic(msg.music||msg);
            if(msg.queue!==undefined) S.musicQueue=msg.queue;
            else if(msg.music&&msg.music.queue!==undefined) S.musicQueue=msg.music.queue;
            syncMusicFromQueue();
            if(S.page==='music') renderMusicPage(document.getElementById('main'));
            applyMusicHeader();
            break;
        case 'timers_state':
            if(msg.timers_rev!==undefined){
                const rev=Number(msg.timers_rev)||0;
                if(rev<=S.lastTimersRev) break;
                S.lastTimersRev=rev;
            }
            if(Array.isArray(msg.timers)) applyTimers(msg.timers);
            break;
        case 'music_action_ack':
            if(msg.action_id) delete S.pendingMusicActions[String(msg.action_id)];
            S.musicActionError='';
            S.musicActionErrorTs=0;
            if(String(msg.action||'')==='music_load_playlist'){
                requestMusicStateRetry('music_load_playlist ack', 8, 600);
            }
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
            if(msg.query!==undefined){
                const pendingQuery=String(S.musicAddPendingQuery||'').trim();
                const responseQuery=String(msg.query||'').trim();
                if(pendingQuery && responseQuery && responseQuery!==pendingQuery) break;
            }
            S.musicAddSearchPending = false;
            S.musicAddPendingQuery = '';
            if(msg.error){
                recordInlineError('music', 'library_search', String(msg.error||'Music search failed'));
            }
            if(Array.isArray(msg.results)){
                S.musicLibraryResults = msg.results;
                S.musicAddSelection = {};
                S.musicAddLastCheckedFile='';
            }
            if(S.page==='music' && S.musicAddMode) renderMusicPage(document.getElementById('main'));
            break;
        case 'music_playlists':
            if(Array.isArray(msg.playlists)) S.musicPlaylists = msg.playlists;
            if(S.page==='music') renderMusicPage(document.getElementById('main'));
            break;
        case 'timer_action_ack':
            if(msg.action){
                const key=String(msg.action)+':'+String(msg.id||'');
                delete S.pendingTimerActions[key];
                delete S.timerActionErrors[key];
            }
            renderTimerBar();
            break;
        case 'timer_action_error':
            if(msg.action){
                const key=String(msg.action)+':'+String(msg.id||'');
                delete S.pendingTimerActions[key];
                recordInlineError('timer', key, String(msg.error||'Timer/alarm action failed'));
            }
            S.wsDebug.lastError='timer/alarm action failed: '+String(msg.error||msg.action||'unknown');
            updateWsDebugBanner();
            renderTimerBar();
            break;
        case 'ui_control':
            if(msg.ui_control_rev!==undefined){
                const uiRev=Number(msg.ui_control_rev)||0;
                if(uiRev<=S.lastUiControlRev) break;
                S.lastUiControlRev=uiRev;
            }
            const prevBrowserAudio=!!S.browserAudioEnabled;
            if(msg.mic_enabled!==undefined) S.micEnabled=!!msg.mic_enabled;
            if(msg.tts_muted!==undefined) S.ttsMuted=!!msg.tts_muted;
            if(msg.browser_audio_enabled!==undefined) S.browserAudioEnabled=!!msg.browser_audio_enabled;
            if(msg.continuous_mode!==undefined) S.continuousMode=!!msg.continuous_mode;
            if(msg.page==='music' || msg.page==='home') navigate(msg.page);
            writeBoolPref(PREF_TTS_MUTED, !!S.ttsMuted);
            writeBoolPref(PREF_BROWSER_AUDIO, !!S.browserAudioEnabled);
            writeBoolPref(PREF_CONTINUOUS, !!S.continuousMode);
            if(prevBrowserAudio && !S.browserAudioEnabled){
                try{ if(S.processor) S.processor.disconnect(); }catch(_ ){}
                try{ if(S.audioCtx) S.audioCtx.close(); }catch(_ ){}
                if(S.mediaStream) try{ S.mediaStream.getTracks().forEach(t=>t.stop()); }catch(_ ){}
                S.processor=null; S.audioCtx=null; S.mediaStream=null; S.captureWorkletModuleReady=false;
            } else if(!prevBrowserAudio && S.browserAudioEnabled){
                startBrowserCapture().catch(err=>reportCaptureFailure(err,'browserAudioToggle'));
            }
            S.pendingSettingActions={};
            S.settingActionErrors={};
            applyMicState();
            applyMicControlToggles();
            break;
        case 'setting_action_ack':
            if(msg.action){
                delete S.pendingSettingActions[String(msg.action)];
                delete S.settingActionErrors[String(msg.action)];
            }
            applyMicControlToggles();
            break;
        case 'setting_action_error':
            if(msg.action){
                delete S.pendingSettingActions[String(msg.action)];
                recordInlineError('setting', String(msg.action), String(msg.error||'Setting update failed'));
            }
            S.wsDebug.lastError='setting action failed: '+String(msg.error||msg.action||'unknown');
            updateWsDebugBanner();
            applyMicControlToggles();
            break;
            case 'feedback_sound':
                playFeedbackSound(msg.audio_b64, msg.gain||1.0);
                break;
  }
}

function requestMusicStateRetry(reason, attempts=6, delayMs=500){
    let remaining = Math.max(1, Number(attempts)||1);
    const delay = Math.max(100, Number(delayMs)||500);
    if(S._musicStateRetryTimer){
        clearTimeout(S._musicStateRetryTimer);
        S._musicStateRetryTimer = null;
    }
    const tick = ()=>{
        if(remaining<=0) return;
        remaining -= 1;
        sendAction({type:'music_get_state'});
        if(remaining>0){
            S._musicStateRetryTimer = setTimeout(tick, delay);
        }
    };
    console.log(`🔄 Requesting music_get_state retries (${reason}, attempts=${attempts}, delay=${delay}ms)`);
    tick();
}

async function playFeedbackSound(b64, gain) {
    try {
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
    } catch(e) {
        console.debug('Feedback sound error:', e);
    }
}
function applyOrch(o){
  if(o.voice_state!==undefined) S.voice_state=o.voice_state;
  if(o.wake_state!==undefined)  S.wake_state=o.wake_state;
  if(o.hotword_active!==undefined) S.hotword_active=!!o.hotword_active;
  if(o.tts_playing!==undefined) S.tts_playing=!!o.tts_playing;
  if(o.mic_rms!==undefined)     S.mic_rms=Number(o.mic_rms)||0;
  if(o.mic_enabled!==undefined) S.micEnabled=!!o.mic_enabled;
  applyMicState();
}
function syncMusicFromQueue(){
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
}
function applyMusicQueueHighlight(){
    if(S.page!=='music') return;
    const pos = Number(S.music && S.music.position);
    if(!Number.isFinite(pos) || pos < 0) return;
    const rows = document.querySelectorAll('.music-queue-table-container tbody tr[data-queue-pos]');
    if(!rows || !rows.length) return;

    rows.forEach((row) => {
        const rowPos = Number(row.getAttribute('data-queue-pos'));
        const isActive = Number.isFinite(rowPos) && rowPos === pos;
        row.classList.toggle('bg-gray-800', isActive);
        row.classList.toggle('font-semibold', isActive);
        row.classList.toggle('text-green-400', isActive);
    });
}
function applyMusic(m){
    const payload=(m&&typeof m==='object'&&m.music&&typeof m.music==='object')?m.music:m;
    if(!payload||typeof payload!=='object') return;
    Object.assign(S.music,payload);
    S.music._clientElapsedAnchorTs=Date.now();
    S.music.state=normalizeMusicState(S.music.state);
    syncMusicFromQueue();
    applyTopMusicProgress();
    applyMusicHeader();
    applyMusicQueueHighlight();
}
function applyTimers(t){
  const now=Date.now()/1000;
    const serverTimers=(Array.isArray(t)?t:[])
        .filter(timer=>{
            if(!timer||typeof timer!=='object') return false;
            const kind=String(timer.kind||'timer').toLowerCase();
            const rem=Number(timer.remaining_seconds);
            if(!Number.isFinite(rem)) return false;
            if(kind==='timer' && rem<=0) return false;
            return true;
        })
        .map(timer=>Object.assign({},timer,{_clientAnchorTs:now, _clientAnchorRem:Number(timer.remaining_seconds)||0}));

    const optimistic=Object.assign({}, S.optimisticTimers||{});
    const optimisticList=[];
    Object.keys(optimistic).forEach((id)=>{
        const it=optimistic[id];
        if(!it) return;
        const ageSec=Math.max(0, (Date.now()-Number(it.createdAtMs||Date.now()))/1000);
        const expectedRem=Math.max(0, Number(it.durationSeconds||0)-ageSec);
        if(expectedRem<=0 || ageSec>20){
            delete S.optimisticTimers[id];
            return;
        }
        const matched=serverTimers.some((s)=>{
            const sk=String(s.kind||'timer').toLowerCase();
            if(sk!==String(it.kind||'timer')) return false;
            const srem=Number(s.remaining_seconds)||0;
            return Math.abs(srem-expectedRem)<=8;
        });
        if(matched){
            delete S.optimisticTimers[id];
            return;
        }
        optimisticList.push({
            id,
            kind:String(it.kind||'timer'),
            label:String(it.label|| (String(it.kind||'timer')==='alarm'?'Alarm':'Timer')),
            remaining_seconds:expectedRem,
            ringing:false,
            _optimistic:true,
            _clientAnchorTs:now,
            _clientAnchorRem:expectedRem,
        });
    });

    S.timers=serverTimers.concat(optimisticList)
        .sort((a,b)=>{
            const ak=String(a.kind||'timer').toLowerCase()==='alarm'?0:1;
            const bk=String(b.kind||'timer').toLowerCase()==='alarm'?0:1;
            if(ak!==bk) return ak-bk;
            return (Number(a.remaining_seconds)||0)-(Number(b.remaining_seconds)||0);
        });
  renderTimerBar();
}

async function ensureCaptureWorkletModule(ctx){
        if (S.captureWorkletModuleReady) return true;
        if (!ctx || !ctx.audioWorklet || typeof AudioWorkletNode === 'undefined') return false;
        const source = `
class CaptureProcessor extends AudioWorkletProcessor {
    process(inputs) {
        const input = inputs && inputs[0] && inputs[0][0] ? inputs[0][0] : null;
        if (input) {
            let sum = 0;
            for (let i = 0; i < input.length; i++) sum += input[i] * input[i];
            const rms = Math.sqrt(sum / Math.max(1, input.length));
            this.port.postMessage({ rms, samples: input.slice(0) });
        }
        return true;
    }
}
registerProcessor('openclaw-capture-processor', CaptureProcessor);
`;
        const blob = new Blob([source], { type: 'application/javascript' });
        const url = URL.createObjectURL(blob);
        try {
                await ctx.audioWorklet.addModule(url);
                S.captureWorkletModuleReady = true;
                return true;
        } finally {
                URL.revokeObjectURL(url);
        }
    }

    async function startBrowserCapture(){
  if(!S.browserAudioEnabled) return;
  const hasLiveTrack = !!(S.mediaStream && S.mediaStream.getAudioTracks().some(t=>t.readyState==='live'));
  if (hasLiveTrack && S.processor) {
      if (S.audioCtx && S.audioCtx.state === 'suspended') await S.audioCtx.resume();
      clearCaptureRetry();
      return;
  }

  // Cleanup stale graph if present, then rebuild.
  try{ if(S.processor) S.processor.disconnect(); }catch(_ ){}
  try{ if(S.audioCtx) await S.audioCtx.close(); }catch(_ ){}
  if(S.mediaStream){
      try{ S.mediaStream.getTracks().forEach(t=>t.stop()); }catch(_ ){}
  }
  S.processor=null; S.audioCtx=null; S.mediaStream=null; S.captureWorkletModuleReady=false;

  if(!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia){
      throw new Error('Browser mediaDevices.getUserMedia is unavailable');
  }

  const captureConstraints=[
      {audio:true,video:false},
      {audio:{echoCancellation:false,noiseSuppression:false,autoGainControl:false},video:false},
  ];

  let lastErr=null;
  for(const constraints of captureConstraints){
      try{
          S.mediaStream=await navigator.mediaDevices.getUserMedia(constraints);
          break;
      }catch(err){
          lastErr=err;
      }
  }
  if(!S.mediaStream) throw (lastErr||new Error('getUserMedia failed'));

  S.audioCtx=new(window.AudioContext||window.webkitAudioContext)();
    if (S.audioCtx.state === 'suspended') await S.audioCtx.resume();
  const src=S.audioCtx.createMediaStreamSource(S.mediaStream);
    const mute=S.audioCtx.createGain(); mute.gain.value=0;

    let proc=null;
    const workletReady = await ensureCaptureWorkletModule(S.audioCtx);
    if (workletReady) {
            proc = new AudioWorkletNode(S.audioCtx, 'openclaw-capture-processor', {
                    numberOfInputs: 1,
                    numberOfOutputs: 1,
                    outputChannelCount: [1],
                    channelCount: 1,
            });
            proc.port.onmessage = (evt) => {
                    const data = evt && evt.data ? evt.data : null;
                    if (!data || !data.samples) return;
                    if(!S.ws||S.ws.readyState!==WebSocket.OPEN) return;
                    if(!S.browserAudioEnabled) return;
                    const inp = data.samples;
                    const rms = Number(data.rms) || 0;
                    const now=performance.now();
                    if(now-S.lastLevel>=120){ S.lastLevel=now; S.ws.send(JSON.stringify({type:'browser_audio_level',rms,peak:rms})); }
                    const out=new Int16Array(inp.length);
                    for(let i=0;i<inp.length;i++){const s=Math.max(-1,Math.min(1,inp[i]));out[i]=s<0?s*0x8000:s*0x7fff;}
                    S.ws.send(out.buffer);
            };
            src.connect(proc); proc.connect(mute); mute.connect(S.audioCtx.destination);
    } else {
            const scriptProc=S.audioCtx.createScriptProcessor(2048,1,1);
            src.connect(scriptProc); scriptProc.connect(mute); mute.connect(S.audioCtx.destination);
            scriptProc.onaudioprocess=evt=>{
                const inp=evt.inputBuffer.getChannelData(0);
                let ss=0; for(let i=0;i<inp.length;i++) ss+=inp[i]*inp[i];
                const rms=Math.sqrt(ss/Math.max(1,inp.length));
                if(!S.ws||S.ws.readyState!==WebSocket.OPEN) return;
                if(!S.browserAudioEnabled) return;
                const now=performance.now();
                if(now-S.lastLevel>=120){ S.lastLevel=now; S.ws.send(JSON.stringify({type:'browser_audio_level',rms,peak:rms})); }
                const out=new Int16Array(inp.length);
                for(let i=0;i<inp.length;i++){const s=Math.max(-1,Math.min(1,inp[i]));out[i]=s<0?s*0x8000:s*0x7fff;}
                S.ws.send(out.buffer);
            };
            proc = scriptProc;
    }
    S.processor=proc;
    clearCaptureRetry();
}

async function ensureBrowserCapture(){
    await startBrowserCapture();
}

document.addEventListener('visibilitychange',()=>{
    if(document.visibilityState==='visible' && !S.wsManualDisconnect){
        ensureBrowserCapture().catch(()=>{});
    }
});

if(navigator.mediaDevices && typeof navigator.mediaDevices.addEventListener==='function'){
    navigator.mediaDevices.addEventListener('devicechange',()=>{
        if(!S.wsManualDisconnect && S.wsConnected){
            ensureBrowserCapture().catch((err)=>reportCaptureFailure(err,'devicechange'));
        }
    });
}

function setupServerRefreshWatcher(){
    let inFlight=false;
    setInterval(async ()=>{
        if(inFlight) return;
        inFlight=true;
        try{
            const resp=await fetch('/health?ts='+Date.now(), { cache:'no-store' });
            if(!resp.ok) return;
            const data=await resp.json();
            const remoteId=String((data&&data.instance_id)||'');
            if(remoteId && SERVER_INSTANCE_ID && remoteId!==SERVER_INSTANCE_ID){
                location.reload();
            }
        }catch(_ ){
            // Ignore transient network/server hiccups; watcher is best-effort.
        }finally{
            inFlight=false;
        }
    }, 2000);
}

loadUiPrefs();
hydrateChatCache();
S.page=getPage(); renderPage(); updateNavActiveState(); applyMicState(); applyMicControlToggles(); updateWsDebugBanner(); updateMicInteractivity(); connectWs();
setupServerRefreshWatcher();
setInterval(()=>{ expirePendingActions(); applyTopMusicProgress(); if(!S.timers.length) return; const now=Date.now()/1000; S.timers.forEach(t=>{ if(t._clientAnchorTs===undefined){ t._clientAnchorTs=now; t._clientAnchorRem=t.remaining_seconds; } t.remaining_seconds=Math.max(0, t._clientAnchorRem-(now-t._clientAnchorTs)); }); renderTimerBar(); },500);
startBrowserCapture().catch((err)=>{
    reportCaptureFailure(err,'startup');
});
