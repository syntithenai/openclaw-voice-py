document.addEventListener('click', e=>{
    closeMenu();
    const target = (e.target && typeof e.target.closest==='function') ? e.target : (e.target && e.target.parentElement ? e.target.parentElement : null);
    if(!target || !target.closest('#micControlWrap')) closeMicControlMenu();
});
document.addEventListener('keydown', e=>{ if(e.key==='Escape'){ closeMenu(); closeMicControlMenu(); } });
document.querySelectorAll('[data-nav]').forEach(el=>el.addEventListener('click',e=>{ e.preventDefault(); navigate(el.dataset.nav); }));
document.addEventListener('click', e => {
    const target = (e.target && typeof e.target.closest==='function') ? e.target : (e.target && e.target.parentElement ? e.target.parentElement : null);
    if(!target) return;

    const newChatBtn = target.closest('[data-action="chat-new"]');
    if (newChatBtn) {
        sendAction({type:'chat_new'});
        S.selectedChatId = 'active';
        renderPage();
        return;
    }

    const toggleSidebarBtn = target.closest('[data-action="chat-sidebar-toggle"]');
    if (toggleSidebarBtn) {
        S.chatSidebarOpen = !S.chatSidebarOpen;
        renderPage();
        return;
    }

    const deleteThreadOpenBtn = target.closest('[data-action="chat-delete-open"]');
    if (deleteThreadOpenBtn) {
        e.stopPropagation();
        const tid = String(deleteThreadOpenBtn.dataset.threadId || '').trim();
        if(!tid || tid==='active') return;
        const title = String(deleteThreadOpenBtn.dataset.threadTitle || '').trim() || 'Untitled';
        S.chatDeleteModalOpen = true;
        S.chatDeleteTargetId = tid;
        S.chatDeleteTargetTitle = title;
        renderPage();
        return;
    }

    const deleteThreadCancelBtn = target.closest('[data-action="chat-delete-cancel"]');
    if (deleteThreadCancelBtn) {
        S.chatDeleteModalOpen = false;
        S.chatDeleteTargetId = '';
        S.chatDeleteTargetTitle = '';
        renderPage();
        return;
    }

    const deleteThreadConfirmBtn = target.closest('[data-action="chat-delete-confirm"]');
    if (deleteThreadConfirmBtn) {
        const tid = String(S.chatDeleteTargetId || '').trim();
        if(tid && tid!=='active'){
            S.chatThreads = (S.chatThreads||[]).filter(t=>String((t&&t.id)||'')!==tid);
            if(String(S.selectedChatId||'active')===tid) S.selectedChatId='active';
            persistChatCache();
            sendAction({type:'chat_delete', thread_id: tid});
        }
        S.chatDeleteModalOpen = false;
        S.chatDeleteTargetId = '';
        S.chatDeleteTargetTitle = '';
        renderPage();
        return;
    }

    const selectThreadBtn = target.closest('[data-action="chat-select"]');
    if (selectThreadBtn) {
        const tid = selectThreadBtn.dataset.threadId || 'active';
        S.selectedChatId = tid;
        persistChatCache();
        renderPage();
        return;
    }

    const scrollDownBtn = target.closest('[data-action="chat-scroll-down"]');
    if (scrollDownBtn) {
        scrollChat();
        return;
    }

    const scrollUpBtn = target.closest('[data-action="page-scroll-up"]');
    if (scrollUpBtn) {
        scrollCurrentViewUp();
        return;
    }

    const timerBtn = target.closest('[data-action="timer-cancel"]');
    if (timerBtn) {
        sendTimerAction('timer_cancel','timer_id', timerBtn.dataset.timerId);
        return;
    }

    const alarmBtn = target.closest('[data-action="alarm-cancel"]');
    if (alarmBtn) {
        sendTimerAction('alarm_cancel','alarm_id', alarmBtn.dataset.alarmId);
        return;
    }

    const optimisticTimerBtn = target.closest('[data-action="optimistic-timer-cancel"]');
    if (optimisticTimerBtn) {
        const optimisticId = String(optimisticTimerBtn.dataset.optimisticId || '').trim();
        if(optimisticId && S.optimisticTimers && S.optimisticTimers[optimisticId]){
            delete S.optimisticTimers[optimisticId];
            S.timers = (Array.isArray(S.timers)?S.timers:[]).filter(t=>String((t&&t.id)||'')!==optimisticId);
            renderTimerBar();
        }
        return;
    }

    const browserAudioToggle = target.closest('[data-action="toggle-browser-audio"]');
    if (browserAudioToggle) {
        e.stopPropagation();
        if(S.pendingSettingActions['browser_audio_set']) return;
        sendSettingAction('browser_audio_set', !S.browserAudioEnabled);
        return;
    }

    const ttsMuteToggle = target.closest('[data-action="toggle-tts-mute"]');
    if (ttsMuteToggle) {
        e.stopPropagation();
        if(S.pendingSettingActions['tts_mute_set']) return;
        sendSettingAction('tts_mute_set', !S.ttsMuted);
        return;
    }

    const continuousToggle = target.closest('[data-action="toggle-continuous-mode"]');
    if (continuousToggle) {
        e.stopPropagation();
        if(S.pendingSettingActions['continuous_mode_set']) return;
        sendSettingAction('continuous_mode_set', !S.continuousMode);
        return;
    }

    const queueCb = target.closest('[data-action="music-queue-select"]');
    if (queueCb) {
        e.stopPropagation();
        const songId = String(queueCb.dataset.songId||'').trim();
        const checked = !!queueCb.checked;
        if (e.shiftKey && S.musicQueueLastCheckedId !== null) {
            const boxes = [...document.querySelectorAll('[data-action="music-queue-select"]')];
            const ids = boxes.map(x=>String(x.dataset.songId||'').trim());
            const a = ids.indexOf(String(S.musicQueueLastCheckedId));
            const b = ids.indexOf(songId);
            if (a >= 0 && b >= 0) {
                const lo = Math.min(a,b), hi = Math.max(a,b);
                for (let i=lo;i<=hi;i++) S.musicQueueSelectionByIds[ids[i]] = checked;
            } else {
                S.musicQueueSelectionByIds[songId] = checked;
            }
        } else {
            S.musicQueueSelectionByIds[songId] = checked;
        }
        S.musicQueueLastCheckedId = songId;
        if(!checked) delete S.musicQueueSelectionByIds[songId];
        renderMusicPage(document.getElementById('main'));
        return;
    }

    const addCb = target.closest('[data-action="music-add-select"]');
    if (addCb) {
        const file = String(addCb.dataset.file || '');
        const checked = !!addCb.checked;
        if (e.shiftKey && S.musicAddLastCheckedFile) {
            const boxes = [...document.querySelectorAll('[data-action="music-add-select"]')];
            const ordered = boxes.map(x=>String(x.dataset.file||''));
            const a = ordered.indexOf(String(S.musicAddLastCheckedFile));
            const b = ordered.indexOf(file);
            if (a >= 0 && b >= 0) {
                const lo = Math.min(a,b), hi = Math.max(a,b);
                for (let i=lo;i<=hi;i++) { const k = ordered[i]; if(k) S.musicAddSelection[k] = checked; }
            } else if (file) {
                S.musicAddSelection[file] = checked;
            }
        } else if (file) {
            S.musicAddSelection[file] = checked;
        }
        S.musicAddLastCheckedFile = file;
        if(!checked && file) delete S.musicAddSelection[file];
        renderMusicPage(document.getElementById('main'));
        return;
    }

    const addQuickAddBtn = target.closest('[data-action="music-add-quick-add"]');
    if (addQuickAddBtn) {
        const file = String(addQuickAddBtn.dataset.file || '').trim();
        if(file) sendMusicAction('music_add_files', {files:[file]});
        return;
    }

    const selectAllBtn = target.closest('[data-action="music-select-all"]');
    if (selectAllBtn) {
        const boxes=[...document.querySelectorAll('[data-action="music-queue-select"]')];
        boxes.forEach(cb=>{
            const songId = String(cb.dataset.songId || '').trim();
            if(songId) S.musicQueueSelectionByIds[songId] = true;
        });
        renderMusicPage(document.getElementById('main'));
        return;
    }

    const selectNoneBtn = target.closest('[data-action="music-select-none"]');
    if (selectNoneBtn) {
        S.musicQueueSelectionByIds={};
        S.musicQueueLastCheckedId=null;
        renderMusicPage(document.getElementById('main'));
        return;
    }

    const addModeBtn = target.closest('[data-action="music-add-open"]');
    if (addModeBtn) {
        S.musicAddMode = true;
        S.musicAddSelection = {};
        S.musicAddHasSearched = false;
        S.musicAddSearchPending = false;
        S.musicAddPendingQuery = '';
        sendAction({type:'music_list_playlists'});
        renderMusicPage(document.getElementById('main'));
        return;
    }

    const addSearchBtn = target.closest('[data-action="music-add-search-submit"]');
    if (addSearchBtn && !addSearchBtn.disabled) {
        submitMusicLibrarySearch();
        return;
    }

    const addModeCancel = target.closest('[data-action="music-add-cancel"]');
    if (addModeCancel) {
        S.musicAddMode = false;
        S.musicAddSearchPending = false;
        S.musicAddPendingQuery = '';
        renderMusicPage(document.getElementById('main'));
        return;
    }

    const addSelectedBtn = target.closest('[data-action="music-add-selected"]');
    if (addSelectedBtn) {
        const files = Object.keys(S.musicAddSelection).filter(k=>S.musicAddSelection[k]);
        if(files.length) sendMusicAction('music_add_files', {files});
        S.musicAddSelection = {};
        S.musicAddLastCheckedFile='';
        renderMusicPage(document.getElementById('main'));
        return;
    }

    const addSelectAllBtn = target.closest('[data-action="music-add-select-all"]');
    if (addSelectAllBtn) {
        (S.musicLibraryResults||[]).forEach(item=>{
            const file=String((item&&item.file)||'').trim();
            if(file) S.musicAddSelection[file]=true;
        });
        renderMusicPage(document.getElementById('main'));
        return;
    }

    const addSelectNoneBtn = target.closest('[data-action="music-add-select-none"]');
    if (addSelectNoneBtn) {
        S.musicAddSelection={};
        S.musicAddLastCheckedFile='';
        renderMusicPage(document.getElementById('main'));
        return;
    }

    const removeSelectedBtn = target.closest('[data-action="music-remove-selected"]');
    if (removeSelectedBtn) {
        const song_ids = Object.keys(S.musicQueueSelectionByIds).filter(k=>S.musicQueueSelectionByIds[k]);
        if(song_ids.length) sendMusicAction('music_remove_selected', {positions: [], song_ids});
        S.musicQueueSelectionByIds = {};
        renderMusicPage(document.getElementById('main'));
        return;
    }

    const openSavePlaylistBtn = target.closest('[data-action="music-open-save-playlist"]');
    if (openSavePlaylistBtn) {
        const loadedName = String((S.music && S.music.loaded_playlist) || '').trim();
        if (loadedName) {
            sendMusicAction('music_save_playlist', {name: loadedName});
            return;
        }
        S.musicPlaylistModalOpen = true;
        S.musicPlaylistModalMode = 'save';
        S.musicPlaylistModalName = '';
        renderMusicPage(document.getElementById('main'));
        return;
    }

    const openCreateSelectedBtn = target.closest('[data-action="music-open-create-selected"]');
    if (openCreateSelectedBtn) {
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
    }

    const modalCancelBtn = target.closest('[data-action="music-modal-cancel"]');
    if (modalCancelBtn) {
        S.musicPlaylistModalOpen = false;
        S.musicPlaylistModalMode = '';
        S.musicPlaylistModalName = '';
        renderMusicPage(document.getElementById('main'));
        return;
    }

    const modalConfirmBtn = target.closest('[data-action="music-modal-confirm"]');
    if (modalConfirmBtn) {
        const mode = String(S.musicPlaylistModalMode||'').trim();
        const name = String(S.musicPlaylistModalName||'').trim();
        if(mode==='save'){
            if(!name) return;
            sendMusicAction('music_save_playlist', {name});
        }else if(mode==='selected'){
            if(!name) return;
            const positions = (S.musicQueue||[])
                .filter(item=>!!S.musicQueueSelectionByIds[String(item.id||'').trim()])
                .map(item=>Number(item.pos))
                .filter(Number.isFinite);
            if(positions.length) sendMusicAction('music_create_playlist', {name, positions});
        }else if(mode==='delete'){
            if(!name) return;
            sendMusicAction('music_delete_playlist', {name});
        }
        S.musicPlaylistModalOpen = false;
        S.musicPlaylistModalMode = '';
        S.musicPlaylistModalName = '';
        renderMusicPage(document.getElementById('main'));
        return;
    }

    const loadPlaylistBtn = target.closest('[data-action="music-load-playlist"]');
    if (loadPlaylistBtn) {
        const name = String(loadPlaylistBtn.dataset.playlistName || '').trim();
        if(name){
            console.log(`🎵 Loading playlist: "${name}"`);
            S.music.loaded_playlist = name;
            sendMusicAction('music_load_playlist', {name});
            console.log(`✓ Sent music_load_playlist action for "${name}"`);
        }
        return;
    }

    const openDeletePlaylistBtn = target.closest('[data-action="music-open-delete-playlist"]');
    if (openDeletePlaylistBtn) {
        const name = String(openDeletePlaylistBtn.dataset.playlistName || '').trim();
        if(!name) return;
        S.musicPlaylistModalOpen = true;
        S.musicPlaylistModalMode = 'delete';
        S.musicPlaylistModalName = name;
        renderMusicPage(document.getElementById('main'));
        return;
    }

    const refreshPlaylistsBtn = target.closest('[data-action="music-refresh-playlists"]');
    if (refreshPlaylistsBtn) {
        sendAction({type:'music_list_playlists'});
        return;
    }

    const clearQueueBtn = target.closest('[data-action="music-clear-queue"]');
    if (clearQueueBtn) {
        sendMusicAction('music_clear_queue', {});
        return;
    }

    const musicRow = target.closest('[data-action="music-play-track"]');
    if (musicRow) {
        if (e.target.closest('input[type="checkbox"]')) return;
        const pos = Number(musicRow.dataset.position);
        sendMusicAction('music_play_track', {position: pos});
        return;
    }

    const musicToggle = target.closest('[data-action="music-toggle"]');
    if (musicToggle && !musicToggle.disabled) {
        const isCurrentlyPlaying = normalizeMusicState(S.music.state) === 'play';
        sendMusicAction(isCurrentlyPlaying ? 'music_stop' : 'music_toggle');
    }
});

document.addEventListener('submit', e => {
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
    if(queueOptimisticTimerFromText(text, 'chat_submit')) renderTimerBar();
    sendAction({type:'chat_text', text, client_msg_id:clientMsgId});
    input.value = '';
    if (S.selectedChatId !== 'active') {
        S.selectedChatId = 'active';
        persistChatCache();
        renderPage();
    }
});

document.addEventListener('keydown', e => {
    const t = e.target;
    if(!t || t.id!=='musicAddSearch') return;
    if(e.key!=='Enter') return;
    e.preventDefault();
    submitMusicLibrarySearch();
});

function handleTextInputChange(t){
    if(!t) return;
    if(t.id==='musicQueueSearch'){
        S.musicQueueFilter = String(t.value||'');
        writeStringPref(PREF_MUSIC_QUEUE_FILTER, S.musicQueueFilter);
        renderMusicPage(document.getElementById('main'));
        return;
    }
    if(t.id==='musicPlaylistSearch'){
        S.musicPlaylistFilter = String(t.value||'');
        writeStringPref(PREF_MUSIC_PLAYLIST_FILTER, S.musicPlaylistFilter);
        renderMusicPage(document.getElementById('main'));
        return;
    }
    if(t.id==='chatThreadSearch'){
        S.chatThreadFilter = String(t.value||'');
        renderThreadList(S.selectedChatId || 'active');
        return;
    }
    if(t.id==='musicAddSearch'){
        S.musicAddQuery = String(t.value||'');
        S.musicAddHasSearched = false;
        S.musicAddSearchPending = false;
        S.musicAddPendingQuery = '';
        const canSearch=canSearchMusicLibrary(S.musicAddQuery);
        const btn=document.getElementById('musicAddSearchSubmit');
        if(btn){
            btn.disabled=!canSearch;
            btn.style.opacity=canSearch?'':'0.5';
            btn.style.cursor=canSearch?'':'not-allowed';
            btn.textContent='Search';
        }
        const hint=document.getElementById('musicAddMinHint');
        if(hint) hint.classList.toggle('hidden', canSearch);
        return;
    }
    if(t.id==='musicNewPlaylistName'){
        S.musicNewPlaylistName = String(t.value||'');
        return;
    }
    if(t.id==='musicPlaylistModalName'){
        S.musicPlaylistModalName = String(t.value||'');
        return;
    }
}

document.addEventListener('input', e => {
    handleTextInputChange(e.target);
});

document.addEventListener('search', e => {
    handleTextInputChange(e.target);
});

function applyMicState(){
  const btn=document.getElementById('micBtn');
  const rms=S.mic_rms||0;
    if(!S.wsConnected){
        btn.style.borderWidth='4px';
        btn.classList.remove('bg-red-900','border-red-600','bg-green-900','border-green-500','bg-gray-700','border-gray-500');
        btn.classList.add('bg-gray-700','border-gray-500');
        return;
    }
  const bw=S.micEnabled?Math.round(2+Math.min(8,Math.pow(rms,0.55)*40)):4;
  btn.style.borderWidth=bw+'px';
  btn.classList.remove('bg-red-900','border-red-600','bg-green-900','border-green-500','bg-gray-700','border-gray-500');
    btn.classList.remove('border-transparent');
        if(S.recorderActive) btn.classList.add('bg-green-900','border-green-500');
    else if(S.hotword_active) btn.classList.add('bg-green-900','border-green-500');
    else if(!S.micEnabled) btn.classList.add('bg-red-900','border-red-600');
    else if(S.wake_state==='awake') btn.classList.add('bg-green-900','border-green-500');
  else btn.classList.add('bg-red-900','border-red-600');
  const col = S.recorderActive ? 'green(recorder)' : (S.hotword_active ? 'green(hotword)' : (!S.micEnabled ? 'red(mic_off)' : (S.wake_state==='awake' ? 'green(awake)' : 'red(asleep)')));
  if(!applyMicState._lastCol || applyMicState._lastCol !== col){
    console.log('[mic_btn]', col, '| wake_state='+S.wake_state, 'micEnabled='+S.micEnabled, 'hotword='+S.hotword_active, 'voice='+S.voice_state);
    applyMicState._lastCol = col;
  }
}
document.getElementById('micBtn').addEventListener('click',()=>{
    if(S.browserAudioEnabled){
        ensureBrowserCapture().catch((err)=>{
            S.wsDebug.status='capture_error';
            S.wsDebug.lastError=(err&&err.message)?String(err.message):'browser mic capture failed';
            updateWsDebugBanner();
        });
    }
    if(!S.wsConnected || !S.ws || S.ws.readyState!==WebSocket.OPEN){
        S.wsDebug.status='closed';
        S.wsDebug.lastError='mic click ignored: websocket disconnected';
        updateWsDebugBanner();
        return;
    }
  sendAction({type:'mic_toggle'});
  if(!S.recorderActive){
    if(!S.micEnabled){ S.micEnabled=true; S.wake_state='awake'; }
    else if(S.wake_state==='awake'){ S.wake_state='asleep'; }
    else{ S.wake_state='awake'; }
  }
  applyMicState();
});

document.getElementById('musicToggleBtn').addEventListener('click',()=>{if(Object.keys(S.pendingMusicActions||{}).length>0) return; sendMusicAction(S.music.state==='play'?'music_stop':'music_toggle');});
function normalizeMusicState(v){
    const s=String(v||'').trim().toLowerCase();
    if(s==='play'||s==='playing') return 'play';
    if(s==='pause'||s==='paused') return 'pause';
    if(s==='stop'||s==='stopped'||s==='idle') return 'stop';
    return s||'stop';
}
function applyMusicHeader(){
    const m=S.music;
    // Normalize and cache state for consistency
    m.state=normalizeMusicState(m.state);
    const header=document.getElementById('musicHeader');
    const titleEl=document.getElementById('musicTitle');
    const artistEl=document.getElementById('musicArtist');
    const btn=document.getElementById('musicToggleBtn');
    if(!header||!titleEl||!artistEl||!btn) return;
    const pendingCount=Object.keys(S.pendingMusicActions||{}).length;
    const active=m.state==='play'||(m.state==='pause'&&((m.title&&String(m.title).trim())||Number(m.queue_length||0)>0));
    header.classList.toggle('hidden',!active);
    titleEl.textContent=(m.title&&String(m.title).trim())||'—';
    artistEl.textContent=(m.artist&&String(m.artist).trim())||'—';
    btn.disabled=pendingCount>0;
    btn.classList.toggle('opacity-60', pendingCount>0);
    btn.classList.toggle('cursor-not-allowed', pendingCount>0);
    btn.textContent=pendingCount>0?'…':(m.state==='play'?'⏹':'▶');
    btn.title=pendingCount>0?'Processing…':(m.state==='play'?'Stop':'Play');
    applyTopMusicProgress();
}

function renderTimerBar(){
  const bar=document.getElementById('timerBar');
    const visibleTimers=S.timers.filter(t=>{
        const kind=String(t.kind||'timer').toLowerCase();
        const rem=Number(t.remaining_seconds);
        if(!Number.isFinite(rem)) return false;
                if(kind==='timer' && rem<=0) return false;
        return true;
    });
    if(!visibleTimers.length){ bar.classList.add('hidden'); bar.innerHTML=''; return; }
  bar.classList.remove('hidden');
    bar.innerHTML=visibleTimers.map(t=>{
    const rem=Math.max(0,Math.round(t.remaining_seconds));
    const mm=String(Math.floor(rem/60)).padStart(2,'0'),ss=String(rem%60).padStart(2,'0');
        const kind=String(t.kind||'timer').toLowerCase();
        const isAlarm=kind==='alarm';
        const isOptimistic=!!t._optimistic;
        const actionAttr=isAlarm?'alarm-cancel':'timer-cancel';
        const idAttr=isAlarm?' data-alarm-id="'+esc(t.id)+'"':' data-timer-id="'+esc(t.id)+'"';
                const pendingKey=(isAlarm?'alarm_cancel:':'timer_cancel:')+String(t.id||'');
                const isPending=!!(S.pendingTimerActions&&S.pendingTimerActions[pendingKey]);
                const timerErr=(S.timerActionErrors&&S.timerActionErrors[pendingKey])?S.timerActionErrors[pendingKey].msg:'';
        const icon=isAlarm?'⏰':'⏱';
        const isRingingAlarm=isAlarm && !!t.ringing;
        const baseCls=isAlarm
            ?('flex items-center gap-1 px-3 py-1 rounded-full text-xs transition-colors '+(isRingingAlarm?'bg-red-600 hover:bg-red-500 animate-pulse':'bg-red-800 hover:bg-red-700'))
            :'flex items-center gap-1 px-3 py-1 rounded-full bg-amber-700 hover:bg-amber-600 text-xs transition-colors';
        const label=isAlarm?(t.label||'Alarm'):(t.label||'Timer');
            const disabledAttr=(isPending||isOptimistic)?' disabled style="opacity:.55;cursor:not-allowed"':'';
            const pendingTxt=(isPending||isOptimistic)?' …':'';
                const errTxt=timerErr?' ⚠':'';
                                const titleTxt=isOptimistic?'Click to cancel pending '+(isAlarm?'alarm':'timer'):(timerErr||'Click to cancel');
                                const actionChunk=isOptimistic
                                        ?(' data-action="optimistic-timer-cancel" data-optimistic-id="'+esc(t.id)+'"')
                                        :(' data-action="'+actionAttr+'"'+idAttr);
                                const effectiveDisabledAttr=(isPending && !isOptimistic)?' disabled style="opacity:.55;cursor:not-allowed"':'';
                                                                const ringingTxt=isRingingAlarm?' 🔔':'';
                                                                return '<button type="button" class="'+baseCls+'"'+actionChunk+effectiveDisabledAttr+' title="'+esc(titleTxt)+'">'+icon+' '+esc(label)+ringingTxt+' '+mm+':'+ss+pendingTxt+errTxt+'</button>';
  }).join('');
}

function renderPage(){
  const main=document.getElementById('main');
    const dock=document.getElementById('chatComposerDock');
        if(main) main.onscroll=null;
    if(dock) dock.classList.remove('hidden');
    if(S.page==='music'){
        renderMusicPage(main);
        sendAction({type:'music_list_playlists'});
    } else {
        renderHomePage(main);
    }
    updateChatComposerState();
    renderTimerBar();
    applyMusicHeader();
    updateScrollUpButton();
}

function renderHomePage(main){
    const sidebarClass = S.chatSidebarOpen ? 'w-72 border-r border-gray-800' : 'w-0 border-r-0';
    const sidebarInnerClass = S.chatSidebarOpen ? 'opacity-100' : 'opacity-0 pointer-events-none';
    const sidebarToggleText = S.chatSidebarOpen ? '&lt;&lt;' : '&gt;&gt;';
    const sidebarToggleTitle = S.chatSidebarOpen ? 'Hide chats' : 'Show chats';
    const selected = S.selectedChatId || 'active';

    main.dataset.page='home';
    const chatDeleteModal = S.chatDeleteModalOpen
        ? '<div class="fixed inset-0 z-20 bg-black/60 flex items-center justify-center px-4">'
            +'<div class="w-full max-w-md rounded-xl border border-gray-700 bg-gray-900 p-4 space-y-3">'
                +'<div class="text-sm font-semibold">Delete chat history</div>'
                +'<p class="text-sm text-gray-300">Do you really want to delete chat history - <span class="font-semibold">'+esc(S.chatDeleteTargetTitle||'Untitled')+'</span></p>'
                +'<div class="flex justify-end gap-2">'
                    +'<button data-action="chat-delete-cancel" class="px-3 py-1.5 rounded-lg text-sm bg-gray-700 hover:bg-gray-600 transition-colors">Cancel</button>'
                    +'<button data-action="chat-delete-confirm" class="px-3 py-1.5 rounded-lg text-sm bg-red-700 hover:bg-red-600 transition-colors">Delete</button>'
                +'</div>'
            +'</div>'
        +'</div>'
        : '';

    main.innerHTML='<div class="w-full h-full flex min-h-0">'
        +'<aside id="chatSidebar" class="'+sidebarClass+' transition-all duration-200 overflow-hidden">'
            +'<div class="'+sidebarInnerClass+' h-full flex flex-col">'
                +'<div class="px-0 py-3 text-xs uppercase tracking-wide text-gray-400 border-b border-gray-800">Previous chats</div>'
                +'<div class="px-2 py-2 border-b border-gray-800">'
                    +'<input id="chatThreadSearch" type="search" value="'+esc(S.chatThreadFilter||'')+'" placeholder="Search chats" class="w-full rounded-lg bg-gray-800 border border-gray-700 px-3 py-2 text-sm text-gray-100 placeholder-gray-400 focus:outline-none focus:ring-2 focus:ring-blue-600" />'
                +'</div>'
                +'<div id="chatThreadList" class="flex-1 overflow-y-auto p-0 space-y-0"></div>'
            +'</div>'
        +'</aside>'
        +'<div class="flex-1 min-w-0 flex flex-col h-full">'
            +'<div class="px-3 py-2 border-b border-gray-800 flex items-center justify-between gap-2">'
                +'<div class="flex items-center gap-2">'
                    +'<button data-action="chat-sidebar-toggle" title="'+sidebarToggleTitle+'" aria-label="'+sidebarToggleTitle+'" class="px-2.5 py-1.5 rounded-lg text-xs bg-gray-800 hover:bg-gray-700 transition-colors">'+sidebarToggleText+'</button>'
                +'</div>'
                +'<button data-action="chat-new" class="px-3 py-1.5 rounded-lg text-xs bg-blue-700 hover:bg-blue-600 transition-colors">New</button>'
            +'</div>'
            +'<div id="chatArea" class="flex-1 overflow-y-auto px-4 py-4 space-y-3 min-h-0"></div>'
        +'</div>'
    +'</div>'
    +chatDeleteModal;

    renderThreadList(selected);
    renderChatMessages(selected);
    const area=document.getElementById('chatArea');
    if(area){
        area.addEventListener('scroll', ()=>{
            if(!isChatAtBottom()) S.autoScrollUntilTs=0;
            updateScrollDownButton();
            updateScrollUpButton();
        }, {passive:true});
    }
    if(main) main.onscroll=null;
    updateScrollDownButton();
    updateScrollUpButton();
}
function getSelectedMessages(selectedId){
    if(!selectedId || selectedId==='active') return S.chat;
    const t=(S.chatThreads||[]).find(x=>x.id===selectedId);
    return (t&&Array.isArray(t.messages)) ? t.messages : [];
}
function renderThreadList(selectedId){
    const list=document.getElementById('chatThreadList');
    if(!list) return;
    const query = String(S.chatThreadFilter||'').trim().toLowerCase();
    const threads = (S.chatThreads||[]).filter(t=>{
        if(String(t.id||'')==='active') return false;
        if(!query) return true;
        return String((t.title||'')).toLowerCase().includes(query);
    });
    const items = [];
    threads.forEach(t=>{
        const title = esc((t.title||'Untitled').trim()||'Untitled');
        const activeCls = (t.id===selectedId) ? 'bg-blue-800 text-white' : 'bg-gray-800 text-gray-200 hover:bg-gray-700';
        items.push('<button data-action="chat-select" data-thread-id="'+esc(t.id)+'" class="w-full text-left px-3 py-2 rounded-none transition-colors border-b border-gray-800 '+activeCls+'"><div class="flex items-center gap-2"><div class="text-sm truncate flex-1">'+title+'</div><span data-action="chat-delete-open" data-thread-id="'+esc(t.id)+'" data-thread-title="'+title+'" class="shrink-0 w-6 h-6 inline-flex items-center justify-center rounded text-sm bg-gray-700 hover:bg-red-700 transition-colors" title="Delete chat history" aria-label="Delete chat history">✕</span></div></button>');
    });
    if(!items.length){
        items.push('<div class="px-3 py-2 text-sm text-gray-500 border-b border-gray-800">No chats found</div>');
    }
    list.innerHTML = items.join('');
}
function renderChatMessages(selectedId){
    const area=document.getElementById('chatArea');
    if(!area) return;
    const prevScrollTop=area.scrollTop;
    const prevScrollHeight=area.scrollHeight;
    const nearBottom=(prevScrollHeight-area.clientHeight-prevScrollTop)<=80;
    const openDetailKeys=new Set();
    area.querySelectorAll('details[data-detail-key]').forEach((el)=>{
        if(el.open){
            const k=el.getAttribute('data-detail-key');
            if(k) openDetailKeys.add(k);
        }
    });
    area.innerHTML='';
    const msgs = getSelectedMessages(selectedId);
        const collated = collateChatMessages(msgs);
        collated.forEach(m=>area.appendChild(mkBubble(m)));
    area.querySelectorAll('details[data-detail-key]').forEach((el)=>{
        const k=el.getAttribute('data-detail-key');
        if(k && openDetailKeys.has(k)) el.open=true;
    });
    if(S.scrollToBottomPending){
        scrollChat();
        S.scrollToBottomPending=false;
        updateScrollDownButton();
        updateScrollUpButton();
        return;
    }
    const burstActive=Date.now()<Number(S.autoScrollUntilTs||0);
    if((S.chatFollowLatest && nearBottom) || burstActive){
        scrollChat();
    } else {
        area.scrollTop=prevScrollTop;
    }
    updateScrollDownButton();
    updateScrollUpButton();
}
function scrollChat(){ const a=document.getElementById('chatArea'); if(a){ a.scrollTop=a.scrollHeight; updateScrollDownButton(); } }
function collateChatMessages(msgs){
    const out=[];
    let activeBucket=null;

    const flushBucket=()=>{
        if(!activeBucket) return;
        if(activeBucket.events.length>0){
            out.push({role:'context_group',request_id:activeBucket.reqId,events:activeBucket.events,steps:activeBucket.steps,interim:activeBucket.interim,hasFinal:activeBucket.finals.length>0});
        }
        const validStreams=activeBucket.streams.filter(s=>String(s.text||'').trim().length>0);
        if(validStreams.length>0 && activeBucket.finals.length===0){
            out.push({
                role:'assistant_stream_group',
                request_id:activeBucket.reqId,
                source:validStreams[0].source||'assistant',
                text:validStreams.map(s=>String(s.text||'').trim()).filter(Boolean).join(' '),
                segments:validStreams,
                latest:validStreams[validStreams.length-1],
            });
        }
        activeBucket.finals.forEach(f=>out.push(f));
        activeBucket=null;
    };

    const ensureActiveBucket=(reqId)=>{
        if(!activeBucket || activeBucket.reqId!==reqId){
            flushBucket();
            activeBucket={reqId,streams:[],steps:[],interim:[],events:[],finals:[]};
        }
        return activeBucket;
    };

    (msgs||[]).forEach(m=>{
        const role=(m&&m.role)||'';
        const reqId=(m&&m.request_id!==undefined&&m.request_id!==null)?String(m.request_id):null;
        if(role==='user'||role==='system'){
            flushBucket();
            out.push(m);
            return;
        }
        if(role==='step'){
            const bucket=ensureActiveBucket(reqId);
            bucket.steps.push(m);
            bucket.events.push({kind:'tool',payload:m});
            return;
        }
        if(role==='interim'){
            const bucket=ensureActiveBucket(reqId);
            bucket.interim.push(m);
            bucket.events.push({kind:'lifecycle',payload:m});
            return;
        }
        if(role==='assistant'){
            const bucket=ensureActiveBucket(reqId);
            const segKind=String((m&&m.segment_kind)||'final').toLowerCase();
            if(segKind==='stream') bucket.streams.push(m);
            else bucket.finals.push(m);
            return;
        }
        flushBucket();
        out.push(m);
    });

    flushBucket();
    return out;
}

const CHAT_MARKDOWN_ALLOWED_TAGS=['p','strong','em','h1','h2','h3','h4','h5','h6','ul','ol','li','code','pre','blockquote','a','hr','table','thead','tbody','tr','th','td','br','del','s'];
const CHAT_MARKDOWN_ALLOWED_ATTR=['href','title'];
const ASSISTANT_CHART_TAG_RE=/<(?:mermaidchart|pyramidchart)>([\s\S]*?)<\/(?:mermaidchart|pyramidchart)>/gi;
let mermaidInitialized=false;
let mermaidRenderSeq=0;

function renderMarkdownHtml(raw){
    const txt=String(raw||'');
    if(!txt.trim()) return '';
    if(typeof marked==='undefined' || typeof DOMPurify==='undefined') return '';
    try{
        marked.setOptions({breaks:true,gfm:true});
        return DOMPurify.sanitize(marked.parse(txt),{ALLOWED_TAGS:CHAT_MARKDOWN_ALLOWED_TAGS,ALLOWED_ATTR:CHAT_MARKDOWN_ALLOWED_ATTR});
    }catch(_ ){
        return '';
    }
}

function splitAssistantContent(raw){
    const txt=String(raw||'');
    if(!txt) return [];
    ASSISTANT_CHART_TAG_RE.lastIndex=0;
    const parts=[];
    let lastIndex=0;
    let match;
    while((match=ASSISTANT_CHART_TAG_RE.exec(txt))!==null){
        if(match.index>lastIndex){
            const markdownPart=txt.slice(lastIndex, match.index);
            if(markdownPart.trim()) parts.push({type:'markdown', text:markdownPart});
        }
        const chartBody=String(match[1]||'').trim();
        if(chartBody) parts.push({type:'mermaid', text:chartBody});
        lastIndex=match.index + match[0].length;
    }
    if(lastIndex<txt.length){
        const tail=txt.slice(lastIndex);
        if(tail.trim()) parts.push({type:'markdown', text:tail});
    }
    if(!parts.length && looksLikeMermaidSource(txt)){
        parts.push({type:'mermaid', text:txt.trim()});
    }
    return parts;
}

function looksLikeMermaidSource(raw){
    const txt=String(raw||'').trim();
    if(!txt) return false;
    const lower=txt.toLowerCase();
    if(lower.includes('<mermaidchart>') || lower.includes('<pyramidchart>')) return true;
    if(lower.includes('%%{init')) return true;
    const keywordHits=[
        /flowchart/i,
        /graph\s+(td|lr|rl|bt)/i,
        /sequencediagram/i,
        /classdiagram/i,
        /statediagram(?:-v2)?/i,
        /erdiagram/i,
        /journey/i,
        /gantt/i,
        /pie/i,
        /xychart(?:-beta)?/i,
        /mindmap/i,
        /timeline/i,
        /quadrantchart/i,
        /bar/i,
    ].reduce((acc, re)=>acc+(re.test(txt)?1:0),0);
    const tokenHit=/(-->|-\.->|==>|:::|x-?axis|y-?axis|title)/i.test(txt);
    return keywordHits>=2 || (keywordHits>=1 && tokenHit);
}

function ensureMermaidInitialized(){
    if(mermaidInitialized || typeof mermaid==='undefined') return;
    mermaid.initialize({
        startOnLoad:false,
        securityLevel:'strict',
        theme:'dark',
        fontFamily:'ui-sans-serif, system-ui, sans-serif',
    });
    mermaidInitialized=true;
}

async function renderMermaidBlock(target, chartText){
    target.innerHTML='';
    const label=document.createElement('div');
    label.className='mermaidchart-label';
    label.textContent='Diagram';
    target.appendChild(label);

    const content=document.createElement('div');
    target.appendChild(content);

    if(typeof mermaid==='undefined'){
        const pre=document.createElement('pre');
        pre.textContent=chartText;
        content.appendChild(pre);
        return;
    }

    try{
        ensureMermaidInitialized();
        const renderId='voice-mermaid-'+(++mermaidRenderSeq);
        const rendered=await mermaid.render(renderId, chartText);
        const svg=(rendered && typeof rendered==='object' && rendered.svg) ? rendered.svg : '';
        if(!svg) throw new Error('empty mermaid render');
        content.innerHTML=DOMPurify.sanitize(svg, {USE_PROFILES:{svg:true,svgFilters:true}});
        if(rendered && typeof rendered.bindFunctions==='function'){
            rendered.bindFunctions(content);
        }
    }catch(err){
        const pre=document.createElement('pre');
        pre.className='mermaidchart-error';
        pre.textContent='Unable to render diagram.\n\n'+chartText;
        content.appendChild(pre);
    }
}

function renderAssistantContent(target, raw){
    const txt=String(raw||'');
    target.textContent='';
    if(!txt.trim()) return;

    const parts=splitAssistantContent(txt);
    if(!parts.length){
        const html=renderMarkdownHtml(txt);
        if(html) target.innerHTML=html;
        else target.textContent=txt;
        return;
    }

    target.classList.add('assistant-rich-content');
    const mermaidTasks=[];
    for(const part of parts){
        if(part.type==='markdown'){
            const block=document.createElement('div');
            block.className='md-content';
            const html=renderMarkdownHtml(part.text);
            if(html) block.innerHTML=html;
            else block.textContent=part.text;
            target.appendChild(block);
            continue;
        }
        const block=document.createElement('div');
        block.className='mermaidchart-shell';
        target.appendChild(block);
        mermaidTasks.push(renderMermaidBlock(block, part.text));
    }
    if(mermaidTasks.length){
        Promise.allSettled(mermaidTasks).catch(()=>{});
    }
}

function mkBubble(m){
  const d=document.createElement('div');
    const role = (m&&m.role)||'';
    d.className='chat-msg flex '+(role==='user'?'justify-end':'justify-start');
        d.setAttribute('data-role', role);

    if(role==='assistant_stream_group'){
        const wrap=document.createElement('div');
        wrap.className='max-w-xs sm:max-w-sm lg:max-w-md';

        const b=document.createElement('div');
        b.className='px-4 py-2 rounded-2xl rounded-bl-md text-sm leading-relaxed bg-gray-700 text-gray-100 md-content';
        const _rawText=(m.text||'');
        renderAssistantContent(b, _rawText);
        wrap.appendChild(b);
        d.appendChild(wrap);
        return d;
    }

    if(role==='context_group'){
        const wrap=document.createElement('div');
        wrap.className='max-w-xs sm:max-w-sm lg:max-w-md space-y-1';
        const reqKey=String((m&&m.request_id)!==undefined && (m&&m.request_id)!==null ? m.request_id : 'na');

        const parseDetails=(raw)=>{
            const txt=String(raw||'').trim();
            if(!txt) return null;
            try{ return JSON.parse(txt); }catch(_){ return null; }
        };

        const toPretty=(val)=>{
            if(val===undefined||val===null) return '';
            if(typeof val==='string') return val;
            try{ return JSON.stringify(val, null, 2); }catch(_){ return String(val); }
        };

        const normToolName=(name)=>String(name||'').trim().toLowerCase();
        const isExecTool=(name)=>{
            const n=normToolName(name);
            return n==='exec' || n.includes('run_in_terminal') || n.includes('run-terminal') || n.includes('terminal');
        };
        const isWriteTool=(name)=>{
            const n=normToolName(name);
            return n==='write' || n.includes('write_file') || n.includes('create_file') || n.includes('str_replace') || n.includes('insert');
        };
        const isReadTool=(name)=>{
            const n=normToolName(name);
            return n==='read' || n.includes('read_file');
        };
        const isProcessTool=(name)=>{
            const n=normToolName(name);
            return n==='process' || n.includes('process');
        };
        const isWebSearchTool=(name)=>{
            const n=normToolName(name);
            return n==='web_search' || n.includes('web_search') || n==='websearch' || n.includes('search_web');
        };
        const isWebGetTool=(name)=>{
            const n=normToolName(name);
            return n==='web_get' || n.includes('web_get') || n==='webget' || n.includes('fetch_web') || n.includes('fetch_webpage') || n==='web_fetch';
        };
        const basename=(p)=>{
            const s=String(p||'').trim();
            if(!s) return '';
            const parts=s.split(/[\/]+/).filter(Boolean);
            return parts.length?parts[parts.length-1]:s;
        };
        const pickText=(val)=>{
            if(val===undefined||val===null) return '';
            if(typeof val==='string') return val;
            if(Array.isArray(val)) return val.map(pickText).filter(Boolean).join('\n');
            if(typeof val==='object'){
                const direct=val.content!==undefined?val.content:(val.text!==undefined?val.text:(val.result!==undefined?val.result:(val.output!==undefined?val.output:undefined)));
                if(direct!==undefined) return pickText(direct);
                return toPretty(val);
            }
            return String(val);
        };
        const renderMarkdown=(raw)=>{
            return renderMarkdownHtml(raw);
        };

        const clampPreviewLines=(raw, maxLines=2)=>{
            const src=String(raw||'');
            if(!src) return '';
            const lines=src.split(/\r?\n/);
            if(lines.length<=maxLines) return src;
            return lines.slice(0, maxLines).join('\n')+'…';
        };

        const isTransientLifecycleError=(phase, errText)=>{
            const p=String(phase||'').toLowerCase();
            if(p==='timeout') return false;
            const txt=String(errText||'').toLowerCase();
            if(!txt) return false;
            return txt.includes('connection error')
                || txt.includes('network error')
                || txt.includes('connection reset')
                || txt.includes('socket closed')
                || txt.includes('disconnected');
        };

        const summarizeRequest=(toolName, parsed)=>{
            if(!parsed||typeof parsed!=='object') return '';
            const req=parsed.args!==undefined?parsed.args:(parsed.arguments!==undefined?parsed.arguments:parsed.input);
            if(req===undefined||req===null) return '';
            if(typeof req==='string') return req;
            const lname=String(toolName||'').toLowerCase();
            if(lname.includes('exec')){
                const cmd=req.command||req.cmd||req.argv||req.script;
                if(cmd!==undefined) return 'command: '+toPretty(cmd);
            }
            if(lname.includes('read')||lname.includes('write')||lname.includes('file')){
                const fp=req.filePath||req.file_path||req.path||req.old_path||req.new_path||req.uri;
                if(fp!==undefined) return 'file: '+String(fp);
            }
            const fp=req.filePath||req.file_path||req.path||req.old_path||req.new_path||req.uri;
            if(fp!==undefined) return 'file: '+String(fp);
            const cmd=req.command||req.cmd||req.argv;
            if(cmd!==undefined) return 'command: '+toPretty(cmd);
            return toPretty(req);
        };

        const extractResult=(parsed)=>{
            if(!parsed||typeof parsed!=='object') return '';
            const outCandidates=[parsed.result, parsed.partialResult, parsed.output, parsed.stdout, parsed.stderr, parsed.message, parsed.text, parsed.content];
            for(const c of outCandidates){
                const rendered=toPretty(c);
                if(rendered&&String(rendered).trim()) return rendered;
            }
            return '';
        };

        const extractResultObject=(parsed)=>{
            if(!parsed||typeof parsed!=='object') return null;
            const outCandidates=[parsed.result, parsed.partialResult, parsed.output, parsed.stdout, parsed.stderr, parsed.message, parsed.text, parsed.content];
            for(const c of outCandidates){
                if(c===undefined || c===null) continue;
                if(typeof c==='object') return c;
                if(typeof c==='string'){
                    const t=String(c).trim();
                    if(!t) continue;
                    try{
                        const j=JSON.parse(t);
                        if(j && typeof j==='object') return j;
                    }catch(_ ){}
                }
            }
            return null;
        };

        const events=(m.events||[]);
        if(events.length>0){
            const toolGroups=new Map();
            const lifecycleItems=[];
            const reasoningLines=[];
            const seenReasoning=new Set();
            const addReasoning=(line)=>{
                const s=String(line||'').trim();
                if(!s) return;
                if(seenReasoning.has(s)) return;
                seenReasoning.add(s);
                reasoningLines.push(s);
            };

            let hasLifecycleStart=false;
            let hasLifecycleEnd=false;
            let hasLifecycleHardError=false;
            let anonIdx=0;

            for(const ev of events){
                const kind=(ev&&ev.kind)||'lifecycle';
                const payload=(ev&&ev.payload)||{};

                if(kind==='tool'){
                    const toolName=String(payload.name||payload.text||'tool').trim()||'tool';
                    const phase=String(payload.phase||'update').toLowerCase();
                    const callId=String(payload.tool_call_id||payload.toolCallId||'').trim();
                    const key=callId||('anon:'+toolName+':'+(++anonIdx));
                    if(!toolGroups.has(key)){
                        toolGroups.set(key,{
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
                        });
                    }
                    const g=toolGroups.get(key);
                    if(!g.phases.includes(phase)) g.phases.push(phase);

                    const parsed=parseDetails(payload.details);
                    const rawPayload=String(payload.details||'').trim() || (parsed?toPretty(parsed):'');
                    if(rawPayload && !g.payloads.includes(rawPayload)) g.payloads.push(rawPayload);

                    const req=parsed&&typeof parsed==='object'
                        ? (parsed.args!==undefined?parsed.args:(parsed.arguments!==undefined?parsed.arguments:parsed.input))
                        : undefined;
                    if(!g.requestPreview) g.requestPreview=summarizeRequest(toolName, parsed);

                    if(req && typeof req==='object'){
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
                        reqUrls.flatMap((u)=>Array.isArray(u)?u:[u]).forEach((u)=>{
                            const s=String(u||'').trim();
                            if(s && !g.urls.includes(s)) g.urls.push(s);
                        });
                        const fp=req.filePath||req.file_path||req.path||req.old_path||req.new_path||req.uri;
                        if(fp!==undefined){
                            const f=String(fp);
                            if(!g.files.includes(f)) g.files.push(f);
                        }
                        if(isWriteTool(toolName) && !g.fileContent){
                            const writeBody=req.content!==undefined?req.content:(req.text!==undefined?req.text:(req.file_text!==undefined?req.file_text:(req.new_str!==undefined?req.new_str:undefined)));
                            const bodyTxt=pickText(writeBody).trim();
                            if(bodyTxt) g.fileContent=bodyTxt;
                        }
                    }

                    if(parsed && typeof parsed==='object'){
                        if(parsed.meta!==undefined){
                            const metaTxt=toPretty(parsed.meta);
                            if(metaTxt && !g.metas.includes(metaTxt)) g.metas.push(metaTxt);
                        }
                        if(parsed.isError!==undefined) g.isError=!!parsed.isError;
                        if(phase==='error') g.isError=true;
                        if(phase==='timeout'){ g.isError=true; g.timeout=true; }
                        if(!g.errorText){
                            const errVal=parsed.error!==undefined?parsed.error:(parsed.stderr!==undefined?parsed.stderr:undefined);
                            const errTxt=pickText(errVal).trim();
                            if(errTxt) g.errorText=errTxt;
                        }
                        if(/\btimeout|timed out\b/i.test(String(g.errorText||''))) g.timeout=true;

                        const resultObj=extractResultObject(parsed);
                        if(resultObj && typeof resultObj==='object'){
                            const robj=resultObj;
                            if(!g.query){
                                const q=robj.query!==undefined?robj.query:(robj.search!==undefined?robj.search:undefined);
                                if(q!==undefined) g.query=String(q);
                            }
                            const outUrl=robj.url!==undefined?robj.url:(robj.uri!==undefined?robj.uri:undefined);
                            if(outUrl!==undefined){
                                const s=String(outUrl||'').trim();
                                if(s && !g.urls.includes(s)) g.urls.push(s);
                            }
                            if(Array.isArray(robj.urls)){
                                robj.urls.forEach((u)=>{
                                    const s=String(u||'').trim();
                                    if(s && !g.urls.includes(s)) g.urls.push(s);
                                });
                            }
                            if(isWebSearchTool(toolName) && !g.webResults && Array.isArray(robj.results)){
                                g.webResults=toPretty(robj.results);
                            }
                            if(isWebGetTool(toolName) && !g.webContent){
                                const c=robj.content!==undefined?robj.content:(robj.text!==undefined?robj.text:undefined);
                                const cText=pickText(c).trim();
                                if(cText) g.webContent=cText;
                            }
                        }
                        if((isReadTool(toolName)||isWriteTool(toolName)) && !g.fileContent && parsed.outputText){
                            const textContent=pickText(parsed.outputText).trim();
                            if(textContent) g.fileContent=textContent;
                        }
                    }

                    const resultText=extractResult(parsed) || '';
                    if(resultText && !g.results.includes(resultText)) g.results.push(resultText);
                    if(/\btimeout|timed out\b/i.test(String(resultText||''))) g.timeout=true;
                    if((isReadTool(toolName)||isWriteTool(toolName)) && !g.fileContent && resultText){
                        const fallbackTxt=pickText(resultText).trim();
                        if(fallbackTxt) g.fileContent=fallbackTxt;
                    }
                    if(isWebSearchTool(toolName) && !g.webResults && resultText){
                        g.webResults=resultText;
                    }
                    if(isWebGetTool(toolName) && !g.webContent && resultText){
                        g.webContent=resultText;
                    }
                    continue;
                }

                const name=String(payload.text||payload.name||payload.phase||'lifecycle').toLowerCase();
                const phase=String(payload.phase||'').toLowerCase();
                if(name==='lifecycle' && phase==='start'){ hasLifecycleStart=true; continue; }
                if(name==='lifecycle' && phase==='end'){ hasLifecycleEnd=true; continue; }
                if(name==='lifecycle' && (phase==='error' || phase==='timeout')){
                    const lifecycleErrText=String(payload.error||payload.details||'').trim();
                    if(!isTransientLifecycleError(phase, lifecycleErrText)) hasLifecycleHardError=true;
                }

                const parsed=parseDetails(payload.details);
                let details='';
                if(parsed&&typeof parsed==='object'){
                    const t=parsed.text!==undefined?parsed.text:(parsed.message!==undefined?parsed.message:parsed.result);
                    details=toPretty(t!==undefined?t:parsed);
                } else {
                    details=String(payload.details||'').trim();
                }
                lifecycleItems.push({name:String(payload.text||payload.name||payload.phase||'lifecycle'), details});
                const parsedPhase=(parsed&&typeof parsed==='object'&&parsed.phase!==undefined)?String(parsed.phase).toLowerCase():'';
                const lifecycleErrText=(parsed&&typeof parsed==='object')
                    ? String(parsed.error!==undefined?parsed.error:(parsed.message!==undefined?parsed.message:details))
                    : details;
                const isLifecycleError=(name==='lifecycle' && (phase==='error' || parsedPhase==='error'));
                const lifecycleTimeout=(phase==='timeout' || parsedPhase==='timeout');
                if((isLifecycleError || lifecycleTimeout) && !isTransientLifecycleError(phase||parsedPhase, lifecycleErrText)){
                    hasLifecycleHardError=true;
                }
                if(!isLifecycleError) addReasoning((payload.text||payload.name||'event')+': '+details);
            }

            const toolGroupList=[...toolGroups.values()];
            for(const g of toolGroupList){
                for(const mtxt of g.metas) addReasoning(mtxt);
            }

            const allToolsTerminal=(toolGroupList.length>0) && toolGroupList.every((g)=>
                g.phases.includes('result') || g.phases.includes('end') || g.phases.includes('error') || g.isError!==null
            );
            const hasToolError=toolGroupList.some((g)=>g.isError===true || g.phases.includes('error') || g.timeout || /\btimeout|timed out\b/i.test(String(g.errorText||'')));
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

            const toolRows=toolGroupList.map((g, gi)=>{
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

                const resultJoined=(g.results.length?g.results.join('\n\n---\n\n'):'') || '(no result)';
                const payloadJoined=(g.payloads.length?g.payloads.join('\n\n---\n\n'):'') || '(no payload)';
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
                if(isExec){
                    bodyHtml='<div class="mt-0.5 text-[11px] text-gray-300 whitespace-pre-wrap break-words">'+esc(clampPreviewLines(execCommand, 2))+'</div>'
                        +resultDetails
                        +errorInline;
                } else if(isWrite||isRead){
                    bodyHtml='<div class="mt-0.5 text-[11px] text-gray-300">'+esc(fileLabel)+'</div>'
                        +contentBlock
                        +errorInline;
                } else if(isProcess){
                    const actionLabel=String(g.action||g.requestPreview||'(no action)');
                    bodyHtml='<div class="mt-0.5 text-[11px] text-gray-300 whitespace-pre-wrap break-words">'+esc(actionLabel)+'</div>'
                        +resultDetails
                        +errorInline;
                } else if(isWebSearch){
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
                } else if(isWebGet){
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
                }

                return '<div class="px-2 py-1 border-b border-gray-800/60 last:border-b-0">'
                    +'<div class="text-[11px] text-amber-300 flex items-center gap-1"><span class="w-3 text-center">'+icon+'</span><span class="font-mono">'+esc(g.name)+'</span><span class="text-[10px] text-gray-500">'+esc(phaseLabel)+'</span></div>'
                    +bodyHtml
                    +payloadBlock
                    +'</div>';
            }).join('');

            const lifecycleRows=lifecycleItems.map((item, li)=>{
                const detailsBlock='<details data-detail-key="'+esc('req:'+reqKey+':lifecycle:'+String(li))+ '" class="mt-1 rounded border border-gray-700/70 bg-gray-900/40">'
                    +'<summary class="px-1.5 py-1 cursor-pointer text-[10px] text-gray-300 hover:text-gray-100">payload</summary>'
                    +'<pre class="px-1.5 pb-1.5 whitespace-pre-wrap break-words text-[11px] text-gray-300">'+esc(item.details||'(no details)')+'</pre>'
                    +'</details>';
                return '<div class="px-2 py-1 border-b border-gray-800/60 last:border-b-0">'
                    +'<div class="text-[10px] text-sky-500 font-mono">'+esc(item.name)+'</div>'
                    +detailsBlock
                    +'</div>';
            }).join('');

            const interimBlock=reasoningLines.length
                                ? '<details data-detail-key="'+esc('req:'+reqKey+':interim')+'" class="mt-2 rounded border border-gray-700/70 bg-gray-900/40">'
                    +'<summary class="px-2 py-1 cursor-pointer text-[10px] text-gray-300 hover:text-gray-100">Interim/Reasoning</summary>'
                    +'<pre class="px-2 pb-2 whitespace-pre-wrap break-words text-[11px] text-gray-300">'+esc(reasoningLines.join('\n'))+'</pre>'
                  +'</details>'
                : '';

            const executionSequenceContent=statusRow+waitingRow+toolRows+lifecycleRows+interimBlock;
            if(executionSequenceContent){
                const thinkingSummary=waiting
                    ? '<summary class="px-3 py-1.5 cursor-pointer text-gray-200 hover:text-gray-100 select-none flex items-center gap-2"><span class="inline-block w-3 h-3 border-2 border-yellow-300/80 border-t-transparent rounded-full animate-spin"></span><span>Thinking</span></summary>'
                    : '<summary class="px-3 py-1.5 cursor-pointer text-gray-200 hover:text-gray-100 select-none">Thinking</summary>';
                const timeline=document.createElement('div');
                timeline.innerHTML='<details data-detail-key="'+esc('req:'+reqKey+':thinking')+'" class="rounded-xl bg-gray-900/60 border border-gray-700 text-xs overflow-hidden">'
                    +thinkingSummary
                    +'<div class="px-2 pb-2 pt-1">'+executionSequenceContent+'</div>'
                    +'</details>';
                wrap.appendChild(timeline);
            }
        }

        d.appendChild(wrap);
        return d;
    }

  const b=document.createElement('div');
    b.className='max-w-xs sm:max-w-sm lg:max-w-md px-4 py-2 rounded-2xl text-sm leading-relaxed '+
        (role==='user'?'bg-blue-700 text-white rounded-br-md':
         role==='system'?'bg-gray-700 text-gray-300 italic text-xs':
     'bg-gray-700 text-gray-100 rounded-bl-md');
    if(role==='assistant'){
        renderAssistantContent(b, m.text||'');
    }else{
        b.textContent=m.text||'';
    }
    d.appendChild(b); return d;
}

