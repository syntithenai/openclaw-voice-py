document.addEventListener('click', e=>{
    closeMenu();
    const target = (e.target && typeof e.target.closest==='function') ? e.target : (e.target && e.target.parentElement ? e.target.parentElement : null);
    if(!target || !target.closest('#micControlWrap')) closeMicControlMenu();
});
document.addEventListener('keydown', e=>{ if(e.key==='Escape'){ closeMenu(); closeMicControlMenu(); } });
document.querySelectorAll('[data-nav]').forEach(el=>el.addEventListener('click',e=>{
    e.preventDefault();
    const nav = String(el.dataset.nav || '').trim();
    if(nav === 'music'){
        S.musicAddMode = false;
        S.musicAddSearchPending = false;
        S.musicAddPendingQuery = '';
    }
    if(nav && nav === S.page){
        if(nav === 'music') renderMusicPage(document.getElementById('main'));
        closeMenu();
        updateNavActiveState();
        return;
    }
    navigate(nav);
}));

function normalizeFilesPath(value){
    const raw = String(value||'').trim().replace(/\\/g, '/');
    if(!raw) return '';

    const stripWorkspacePrefix = (path)=>{
        const markers=[
            '/workspace-voice/',
            '/openclaw-voice/',
            '/openclaw/',
        ];
        for(const marker of markers){
            const idx=path.toLowerCase().lastIndexOf(marker.toLowerCase());
            if(idx>=0){
                const suffix=path.slice(idx + marker.length).replace(/^\/+/, '');
                if(suffix) return '/'+suffix;
            }
        }

        // Generic container/workspace roots (e.g. /home/node/.openclaw/workspace-*/...)
        const generic=path.match(/\/workspace-[^/]+\/(.+)$/i);
        if(generic && generic[1]) return '/'+String(generic[1]).replace(/^\/+/, '');

        return path;
    };

    const normalizedRaw=stripWorkspacePrefix(raw);
    if(normalizedRaw.startsWith('/')) return normalizedRaw;
    return '/' + normalizedRaw.replace(/^\/+/, '');
}

function buildFilesRouteHref(filePath){
    const normalized = normalizeFilesPath(filePath);
    if(!normalized) return '';
    return '/#/files?path=' + encodeURIComponent(normalized);
}

function getChatReloadSelectedThreadId(){
    return String(S.selectedChatId || 'active').trim() || 'active';
}

function clearChatReloadTarget(){
    S.chatReloadTargetId = '';
    S.chatReloadTargetThreadId = '';
}

function clearChatReloadInFlight(){
    S.chatReloadInFlight = null;
}

function getChatMessageById(selectedId, messageId){
    const messages = getSelectedMessages(selectedId);
    const targetId = String(messageId || '').trim();
    if(!targetId) return null;
    return messages.find((message)=>String((message&&message.id)||'').trim()===targetId) || null;
}

function updateCachedThreadMessages(threadId, messages){
    const tid = String(threadId || '').trim();
    if(!tid || tid==='active') return;
    const nextMessages = Array.isArray(messages) ? messages.map(normalizeChatMessage).filter(Boolean) : [];
    const now = Date.now() / 1000;
    S.chatThreads = (S.chatThreads || []).map((thread)=>{
        if(String((thread&&thread.id)||'').trim()!==tid) return thread;
        return Object.assign({}, thread, {
            messages: nextMessages,
            updated_ts: now,
        });
    });
}

function truncateChatLocallyAfterMessage(selectedId, messageId){
    const selected = String(selectedId || 'active').trim() || 'active';
    const targetId = String(messageId || '').trim();
    if(!targetId) return null;
    const messages = getSelectedMessages(selected);
    const matchIndex = messages.findIndex((message)=>String((message&&message.id)||'').trim()===targetId);
    if(matchIndex<0) return null;
    const targetMessage = getChatMessageById(selected, targetId);
    if(!targetMessage || String(targetMessage.role || '').toLowerCase()!=='user') return null;
    const truncated = messages.slice(0, matchIndex + 1).map(normalizeChatMessage).filter(Boolean);
    if(selected==='active'){
        S.chat = truncated;
        const activeThreadId = String(S.activeChatThreadId || '').trim();
        if(activeThreadId) updateCachedThreadMessages(activeThreadId, truncated);
    }else{
        updateCachedThreadMessages(selected, truncated);
    }
    persistChatCache();
    return targetMessage;
}

document.addEventListener('click', e => {
    const target = (e.target && typeof e.target.closest==='function') ? e.target : (e.target && e.target.parentElement ? e.target.parentElement : null);
    if(!target) return;

    if (S.page === 'files' && typeof handleFileManagerClick === 'function') {
        if (handleFileManagerClick(target, e)) {
            return;
        }
    }

    const openInFilesBtn = target.closest('[data-action="open-in-files"]');
    if (openInFilesBtn) {
        e.preventDefault();
        const fp = normalizeFilesPath(openInFilesBtn.dataset.filePath || '');
        const href = buildFilesRouteHref(fp);
        if (href) {
            const nextHash = href.slice(2);
            if (location.hash !== '#' + nextHash) {
                location.hash = '#' + nextHash;
            } else if (typeof window.fmOpenFile === 'function') {
                window.fmOpenFile(fp, { keepHash: true });
            }
        }
        return;
    }

    const gatewayDebugCopyBtn = target.closest('[data-action="gateway-debug-copy"]');
    if (gatewayDebugCopyBtn) {
        e.preventDefault();
        e.stopPropagation();
        const details = gatewayDebugCopyBtn.closest('details');
        const pre = details ? (details.querySelector('[data-debug-pre]') || details.querySelector('pre')) : null;
        const text = String(pre && pre.textContent || '').trim();
        if(!text) return;
        navigator.clipboard.writeText(text).then(()=>{
            const prev = gatewayDebugCopyBtn.textContent;
            gatewayDebugCopyBtn.textContent = 'Copied!';
            setTimeout(()=>{ gatewayDebugCopyBtn.textContent = prev; }, 1200);
        }).catch(()=>{
            const prev = gatewayDebugCopyBtn.textContent;
            gatewayDebugCopyBtn.textContent = 'Failed';
            setTimeout(()=>{ gatewayDebugCopyBtn.textContent = prev; }, 1200);
        });
        return;
    }

    const authLoginBtn = target.closest('[data-action="auth-login"]');
    if (authLoginBtn) {
        triggerGoogleLogin();
        return;
    }

    const authLogoutBtn = target.closest('[data-action="auth-logout"]');
    if (authLogoutBtn) {
        void triggerGoogleLogout();
        return;
    }

    const reloadResponseBtn = target.closest('[data-action="chat-reload-response"]');
    if (reloadResponseBtn) {
        e.preventDefault();
        e.stopPropagation();
        if (S.pendingChatSends.size > 0) return;
        const messageId = String(reloadResponseBtn.dataset.messageId || '').trim();
        const threadId = String(reloadResponseBtn.dataset.threadId || getChatReloadSelectedThreadId()).trim() || 'active';
        const targetMessage = truncateChatLocallyAfterMessage(threadId, messageId);
        const text = String((targetMessage&&targetMessage.text) || '').trim();
        if(!text) return;
        clearChatReloadTarget();
        S.chatReloadInFlight = {
            threadId,
            sourceMessageId: messageId,
            text,
            userEchoSuppressed: false,
        };
        const clientMsgId='c'+(S.nextClientMsgId++);
        S.pendingChatSends.add(clientMsgId);
        requestScrollToBottomBurst();
        updateChatComposerState();
        if(queueOptimisticTimerFromText(text, 'chat_submit')) renderTimerBar();
        if(S.page==='home') renderChatMessages(threadId);
        sendAction({
            type:'chat_reload',
            message_id: messageId,
            client_msg_id: clientMsgId,
            thread_id: threadId,
        });
        return;
    }

    const reloadSelectBtn = target.closest('[data-action="chat-select-reload"]');
    if (reloadSelectBtn) {
        e.preventDefault();
        const messageId = String(reloadSelectBtn.dataset.messageId || '').trim();
        const threadId = getChatReloadSelectedThreadId();
        if(!messageId) return;
        if(S.chatReloadTargetId===messageId && S.chatReloadTargetThreadId===threadId) clearChatReloadTarget();
        else {
            S.chatReloadTargetId = messageId;
            S.chatReloadTargetThreadId = threadId;
        }
        if(S.page==='home') renderChatMessages(threadId);
        return;
    }

    const newChatBtn = target.closest('[data-action="chat-new"]');
    if (newChatBtn) {
        clearChatReloadTarget();
        clearChatReloadInFlight();
        sendAction({type:'chat_new'});
        S.selectedChatId = 'active';
        renderPage();
        return;
    }

    const toggleSidebarBtn = target.closest('[data-action="chat-sidebar-toggle"]');
    if (toggleSidebarBtn) {
        S.chatSidebarOpen = !S.chatSidebarOpen;
        S.chatSidebarTouched = true;
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

    const clearAllOpenBtn = target.closest('[data-action="chat-clear-all-open"]');
    if (clearAllOpenBtn) {
        if(!Array.isArray(S.chatThreads) || !S.chatThreads.length) return;
        S.chatClearAllModalOpen = true;
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

    const clearAllCancelBtn = target.closest('[data-action="chat-clear-all-cancel"]');
    if (clearAllCancelBtn) {
        S.chatClearAllModalOpen = false;
        renderPage();
        return;
    }

    const deleteThreadConfirmBtn = target.closest('[data-action="chat-delete-confirm"]');
    if (deleteThreadConfirmBtn) {
        const tid = String(S.chatDeleteTargetId || '').trim();
        if(tid && tid!=='active'){
            S.chatThreads = (S.chatThreads||[]).filter(t=>String((t&&t.id)||'')!==tid);
            if(String(S.selectedChatId||'active')===tid) S.selectedChatId='active';
            if(S.chatReloadTargetThreadId===tid) clearChatReloadTarget();
            persistChatCache();
            sendAction({type:'chat_delete', thread_id: tid});
        }
        S.chatDeleteModalOpen = false;
        S.chatDeleteTargetId = '';
        S.chatDeleteTargetTitle = '';
        renderPage();
        return;
    }

    const clearAllConfirmBtn = target.closest('[data-action="chat-clear-all-confirm"]');
    if (clearAllConfirmBtn) {
        S.chatThreads = [];
        S.selectedChatId = 'active';
        clearChatReloadTarget();
        clearChatReloadInFlight();
        S.chatClearAllModalOpen = false;
        persistChatCache();
        sendAction({type:'chat_clear_all'});
        renderPage();
        return;
    }

    const selectThreadBtn = target.closest('[data-action="chat-select"]');
    if (selectThreadBtn) {
        const tid = selectThreadBtn.dataset.threadId || 'active';
        clearChatReloadTarget();
        clearChatReloadInFlight();
        S.selectedChatId = tid;
        if(isMobileChatViewport()) S.chatSidebarOpen=false;
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
        if(file){
            const nextPos = getMusicQueueLength();
            sendMusicAction('music_add_files', {files:[file]});
            sendMusicAction('music_play_track', {position: nextPos});
        }
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
        S.musicGenreCloudPending = true;
        sendAction({type:'music_list_playlists'});
        sendAction({type:'music_list_genres', limit: 100});
        renderMusicPage(document.getElementById('main'));
        return;
    }

    const addGenreTagBtn = target.closest('[data-action="music-add-genre-search"]');
    if (addGenreTagBtn && !addGenreTagBtn.disabled) {
        const genre = String(addGenreTagBtn.dataset.genre || '').trim();
        if (!genre) return;
        S.musicAddQuery = genre;
        S.musicAddHasSearched = false;
        S.musicAddSearchPending = false;
        S.musicAddPendingQuery = '';
        renderMusicPage(document.getElementById('main'));
        submitMusicLibrarySearch();
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
        openMusicPlaylistModal('save');
        return;
    }

    const openCreateSelectedBtn = target.closest('[data-action="music-open-create-selected"]');
    if (openCreateSelectedBtn) {
        const positions = (S.musicQueue||[])
            .filter(item=>!!S.musicQueueSelectionByIds[String(item.id||'').trim()])
            .map(item=>Number(item.pos))
            .filter(Number.isFinite);
        if(!positions.length) return;
        openMusicPlaylistModal('selected');
        return;
    }

    const modalCancelBtn = target.closest('[data-action="music-modal-cancel"]');
    if (modalCancelBtn) {
        closeMusicPlaylistModal();
        renderMusicPage(document.getElementById('main'));
        return;
    }

    const modalSecondaryBtn = target.closest('[data-action="music-modal-secondary"]');
    if (modalSecondaryBtn) {
        const actionType = String(S.musicPlaylistModalAction||'').trim();
        const actionName = String(S.musicPlaylistModalActionName||'').trim();
        closeMusicPlaylistModal();
        performQueuedMusicAction(actionType, actionName);
        renderMusicPage(document.getElementById('main'));
        return;
    }

    const modalConfirmBtn = target.closest('[data-action="music-modal-confirm"]');
    if (modalConfirmBtn) {
        console.log('🎵 [modal-confirm] Button clicked, mode=%s', S.musicPlaylistModalMode);
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
        }else if(mode==='edit-title'){
            if(!name) return;
            const origName = String(S.musicPlaylistModalOriginalName||'').trim();
            if(!origName || origName === name) {
                // No change, just close
                closeMusicPlaylistModal();
                renderMusicPage(document.getElementById('main'));
                return;
            }
            sendMusicAction('music_rename_playlist', {old_name: origName, new_name: name});
        }else if(mode==='save-before-load'){
            const targetName = String(S.musicPlaylistModalActionName||'').trim();
            if(!name || !targetName) return;
            S.music.loaded_playlist = targetName;
            sendMusicAction('music_save_queue_then_load_playlist', {save_name: name, name: targetName});
        }else if(mode==='save-before-clear'){
            if(!name) return;
            sendMusicAction('music_save_queue_then_clear_queue', {save_name: name});
        }
        closeMusicPlaylistModal();
        renderMusicPage(document.getElementById('main'));
        return;
    }

    const loadPlaylistBtn = target.closest('[data-action="music-load-playlist"]');
    if (loadPlaylistBtn) {
        const name = String(loadPlaylistBtn.dataset.playlistName || '').trim();
        if(name){
            if (hasUnsavedMusicQueue()) {
                openUnsavedQueueConfirm('music_load_playlist', name);
            } else {
                performQueuedMusicAction('music_load_playlist', name);
            }
        }
        return;
    }

    const openDeletePlaylistBtn = target.closest('[data-action="music-open-delete-playlist"]');
    if (openDeletePlaylistBtn) {
        const name = String(openDeletePlaylistBtn.dataset.playlistName || '').trim();
        if(!name) return;
        openMusicPlaylistModal('delete', {name});
        return;
    }

    const openEditPlaylistBtn = target.closest('[data-action="music-open-edit-playlist"]');
    if (openEditPlaylistBtn) {
        const name = String(openEditPlaylistBtn.dataset.playlistName || '').trim();
        if(!name) return;
        openMusicPlaylistModal('edit-title', {name, originalName: name});
        return;
    }

    const clearQueueBtn = target.closest('[data-action="music-clear-queue"]');
    if (clearQueueBtn) {
        if (hasUnsavedMusicQueue()) {
            openUnsavedQueueConfirm('music_clear_queue');
        } else {
            performQueuedMusicAction('music_clear_queue');
        }
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
        return;
    }

    const recordingBackBtn = target.closest('[data-action="recording-back-list"]');
    if (recordingBackBtn) {
        S.recordingsDetail = null;
        S.recordingsDetailLoading = false;
        renderRecordingsPage(document.getElementById('main'));
        return;
    }

    const recordingOpenBtn = target.closest('[data-action="recording-open-detail"]');
    if (recordingOpenBtn) {
        const recordingId = String(recordingOpenBtn.dataset.recordingId || '').trim();
        if(!recordingId) return;
        S.recordingsActionError='';
        S.recordingsDetail = null;
        S.recordingsDetailLoading = true;
        sendAction({type:'recording_get', recording_id: recordingId});
        renderRecordingsPage(document.getElementById('main'));
        return;
    }

    const recordingCb = target.closest('[data-action="recording-select"]');
    if (recordingCb) {
        const recordingId = String(recordingCb.dataset.recordingId || '').trim();
        if(!recordingId) return;
        const checked = !!recordingCb.checked;
        if (e.shiftKey && S.recordingsLastCheckedId) {
            const boxes=[...document.querySelectorAll('[data-action="recording-select"]')];
            const ids=boxes.map(x=>String(x.dataset.recordingId||'').trim());
            const a=ids.indexOf(String(S.recordingsLastCheckedId));
            const b=ids.indexOf(recordingId);
            if(a>=0 && b>=0){
                const lo=Math.min(a,b), hi=Math.max(a,b);
                for(let i=lo;i<=hi;i++){ const id=ids[i]; if(id) S.recordingsSelectionByIds[id]=checked; }
            } else {
                S.recordingsSelectionByIds[recordingId]=checked;
            }
        } else {
            S.recordingsSelectionByIds[recordingId]=checked;
        }
        S.recordingsLastCheckedId=recordingId;
        if(!checked) delete S.recordingsSelectionByIds[recordingId];
        renderRecordingsPage(document.getElementById('main'));
        return;
    }

    const recordingsDeleteBtn = target.closest('[data-action="recordings-delete-selected"]');
    if (recordingsDeleteBtn) {
        if(S.recordingsDeletePending) return;
        const recordingIds = Object.keys(S.recordingsSelectionByIds||{}).filter(k=>S.recordingsSelectionByIds[k]);
        if(!recordingIds.length) return;
        const actionId='r'+(S.nextRecordingsActionId++);
        S.recordingsActionError='';
        S.recordingsDeletePending = true;
        sendAction({type:'recordings_delete_selected', action_id: actionId, recording_ids: recordingIds});
        renderRecordingsPage(document.getElementById('main'));
        return;
    }

    const recordingsStartBtn = target.closest('[data-action="recordings-start-recording"]');
    if (recordingsStartBtn) {
        if(S.recorderStartPending) return;
        S.recordingsActionError='';
        S.recorderStartPending = true;
        sendAction({type:'recorder_start'});
        renderRecordingsPage(document.getElementById('main'));
        return;
    }

    const recordingsStopBtn = target.closest('[data-action="recordings-stop-recording"]');
    if (recordingsStopBtn) {
        if(S.recorderStopPending) return;
        S.recordingsActionError='';
        S.recorderStopPending = true;
        sendAction({type:'recorder_stop'});
        renderRecordingsPage(document.getElementById('main'));
        return;
    }

    const copyBlockBtn = target.closest('[data-action="recording-copy-block"]');
    if (copyBlockBtn) {
        const targetId = String(copyBlockBtn.dataset.copyTarget || '').trim();
        if(!targetId) return;
        const node = document.getElementById(targetId);
        if(!node) return;
        const text = String(node.textContent || '');
        if(!text) return;
        navigator.clipboard.writeText(text).catch(()=>{});
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
    clearChatReloadTarget();
    clearChatReloadInFlight();
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
    if(!t) return;
    if (S.page === 'files' && typeof handleFileManagerKeydown === 'function') {
        if (handleFileManagerKeydown(e)) return;
    }
    if(t.id==='musicAddSearch'){
        if(e.key!=='Enter') return;
        e.preventDefault();
        submitMusicLibrarySearch();
        return;
    }
    if(t.id==='musicPlaylistModalName'){
        if(e.key!=='Enter') return;
        e.preventDefault();
        const confirmBtn = document.getElementById('musicPlaylistModalConfirmBtn');
        if(confirmBtn) confirmBtn.click();
    }
});

function hasMusicPlaylistNameConflict(name){
    const modalName = String(name||'').trim().toLowerCase();
    if(!modalName) return false;
    const existingPlaylists = (S.musicPlaylists||[])
        .map(x=>String(x||'').trim().toLowerCase())
        .filter(Boolean);
    const mode = String(S.musicPlaylistModalMode||'').trim();
    const loadedPlaylist = String((S.music&&S.music.loaded_playlist)||'').trim().toLowerCase();
    const origName = String(S.musicPlaylistModalOriginalName||'').trim().toLowerCase();
    const ignoreName = mode==='edit-title' ? origName : loadedPlaylist;
    return existingPlaylists.includes(modalName) && modalName!==ignoreName;
}

function updateMusicPlaylistModalValidationUi(){
    const warningEl = document.getElementById('musicPlaylistModalWarning');
    const confirmBtn = document.getElementById('musicPlaylistModalConfirmBtn');
    const mode = String(S.musicPlaylistModalMode||'').trim();
    const name = String(S.musicPlaylistModalName||'').trim();
    const hasConflict = hasMusicPlaylistNameConflict(name);

    if(warningEl){
        if(hasConflict){
            warningEl.textContent = mode==='edit-title'
                ? '⚠ A playlist with this name already exists.'
                : '⚠ Playlist exists. Saving will overwrite it.';
            warningEl.classList.remove('hidden');
        }else{
            warningEl.textContent = '';
            warningEl.classList.add('hidden');
        }
    }

    if(confirmBtn){
        if(mode==='save-before-load'){
            confirmBtn.textContent = hasConflict ? 'Overwrite and Load' : 'Save and Load';
        }else if(mode==='save-before-clear'){
            confirmBtn.textContent = hasConflict ? 'Overwrite and Clear' : 'Save and Clear';
        }else if(mode!=='delete' && mode!=='edit-title'){
            confirmBtn.textContent = hasConflict ? 'Overwrite' : 'Save';
        }
    }
}

function closeMusicPlaylistModal(){
    S.musicPlaylistModalOpen = false;
    S.musicPlaylistModalMode = '';
    S.musicPlaylistModalName = '';
    S.musicPlaylistModalOriginalName = '';
    S.musicPlaylistModalAction = '';
    S.musicPlaylistModalActionName = '';
}

function focusMusicPlaylistModalNameInput(){
    setTimeout(() => {
        const inp = document.getElementById('musicPlaylistModalName');
        if (inp) {
            inp.focus();
            inp.select();
        }
    }, 50);
}

function openMusicPlaylistModal(mode, opts={}){
    S.musicPlaylistModalOpen = true;
    S.musicPlaylistModalMode = String(mode||'').trim();
    S.musicPlaylistModalName = String(opts.name||'');
    S.musicPlaylistModalOriginalName = String(opts.originalName||'');
    S.musicPlaylistModalAction = String(opts.action||'');
    S.musicPlaylistModalActionName = String(opts.actionName||'');
    renderMusicPage(document.getElementById('main'));
    if(String(mode||'').trim() !== 'delete') focusMusicPlaylistModalNameInput();
}

function openUnsavedQueueConfirm(actionType, actionName=''){
    const mode = String(actionType||'').trim()==='music_load_playlist' ? 'save-before-load' : 'save-before-clear';
    openMusicPlaylistModal(mode, { action: actionType, actionName });
}

function performQueuedMusicAction(actionType, actionName=''){
    const action = String(actionType||'').trim();
    const name = String(actionName||'').trim();
    if(action === 'music_load_playlist'){
        if(!name) return;
        console.log(`🎵 Loading playlist: "${name}"`);
        // Reset playlist filter after a load so the full playlist list is visible.
        S.musicPlaylistFilter = '';
        writeStringPref(PREF_MUSIC_PLAYLIST_FILTER, S.musicPlaylistFilter);
        S.music.loaded_playlist = name;
        sendMusicAction('music_load_playlist', {name});
        if(S.page==='music') renderMusicPage(document.getElementById('main'));
        console.log(`✓ Sent music_load_playlist action for "${name}"`);
        return;
    }
    if(action === 'music_clear_queue'){
        sendMusicAction('music_clear_queue', {});
    }
}

function renderMusicPagePreservingInput(inputId){
    const input = document.getElementById(inputId);
    const focused = !!input && document.activeElement === input;
    const selStart = input ? input.selectionStart : null;
    const selEnd = input ? input.selectionEnd : null;
    renderMusicPage(document.getElementById('main'));
    if(!focused) return;
    const inputAfterRender = document.getElementById(inputId);
    if(!inputAfterRender) return;
    inputAfterRender.focus();
    if(selStart !== null) inputAfterRender.setSelectionRange(selStart, selEnd);
}

function handleTextInputChange(t){
    if(!t) return;
    if(t.id==='musicQueueSearch'){
        S.musicQueueFilter = String(t.value||'');
        writeStringPref(PREF_MUSIC_QUEUE_FILTER, S.musicQueueFilter);
        renderMusicPagePreservingInput('musicQueueSearch');
        return;
    }
    if(t.id==='musicPlaylistSearch'){
        S.musicPlaylistFilter = String(t.value||'');
        writeStringPref(PREF_MUSIC_PLAYLIST_FILTER, S.musicPlaylistFilter);
        renderMusicPagePreservingInput('musicPlaylistSearch');
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
        S.musicLibraryResults = [];
        S.musicAddSelection = {};
        S.musicAddLastCheckedFile = '';
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
        renderMusicPagePreservingInput('musicAddSearch');
        return;
    }
    if(t.id==='musicNewPlaylistName'){
        S.musicNewPlaylistName = String(t.value||'');
        return;
    }
    if(t.id==='musicPlaylistModalName'){
        S.musicPlaylistModalName = String(t.value||'');
        updateMusicPlaylistModalValidationUi();
        return;
    }
}

document.addEventListener('input', e => {
    if (S.page === 'files' && typeof handleFileManagerInput === 'function') {
        if (handleFileManagerInput(e.target)) return;
    }
    handleTextInputChange(e.target);
});

document.addEventListener('search', e => {
    if (S.page === 'files' && typeof handleFileManagerInput === 'function') {
        if (handleFileManagerInput(e.target)) return;
    }
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
    const vuActive = !!(S.micEnabled || S.recorderActive);
    const bw=vuActive?Math.round(2+Math.min(8,Math.pow(rms,0.55)*40)):4;
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
    const pendingLoad=pendingCount>0&&Object.values(S.pendingMusicActions||{}).some(a=>a&&isMusicLoadActionType(a&&a.type));
    const active=m.state==='play'||(m.state==='pause'&&((m.title&&String(m.title).trim())||Number(m.queue_length||0)>0))||(m.state==='stop'&&Number(m.position||0)>=0)||pendingLoad;
    header.classList.toggle('hidden',!active);
    titleEl.textContent=(m.title&&String(m.title).trim())||'—';
    artistEl.textContent=(m.artist&&String(m.artist).trim())||'—';
    btn.disabled=pendingCount>0;
    btn.classList.toggle('opacity-60', pendingCount>0);
    btn.classList.toggle('cursor-not-allowed', pendingCount>0);
    btn.textContent=pendingCount>0?'…':(m.state==='play'?'⏹':'▶');
    btn.title=pendingCount>0?'Processing…':(m.state==='play'?'Stop':'Play');

    // Keep the queue page transport button in sync without forcing a full music page re-render.
    const queueBtn=document.querySelector('[data-action="music-toggle"]');
    if(queueBtn){
        const queueDisabled=pendingCount>0;
        queueBtn.disabled=queueDisabled;
        queueBtn.style.opacity=queueDisabled?'0.5':'';
        queueBtn.style.cursor=queueDisabled?'not-allowed':'';
        queueBtn.textContent=queueDisabled?'… Pending':(m.state==='play'?'⏹ Stop':'▶ Play');
    }

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

    renderAuthButton();

    if(authRequiresLogin() && !S.isAuthenticated){
        if(dock) dock.classList.add('hidden');
        if(main){
            main.dataset.page='auth-required';
            main.innerHTML=''
                +'<div class="w-full h-full flex items-start justify-center px-4 py-10">'
                    +'<div class="max-w-xl w-full rounded-xl border border-red-700 bg-red-950/40 p-5 text-red-100">'
                        +'<div class="text-lg font-semibold mb-2">Authentication required</div>'
                        +'<p class="text-sm text-red-200">Google sign-in is required before this web UI can be used.</p>'
                        +'<p class="text-xs text-red-300 mt-2">Use the sign-in button below or in the header.</p>'
                        +'<div class="mt-4">'
                            +'<button data-action="auth-login" class="px-3 py-2 rounded-lg text-sm font-medium bg-red-700 hover:bg-red-600 transition-colors">Sign in with Google</button>'
                        +'</div>'
                    +'</div>'
                +'</div>';
        }
        applyMusicHeader();
        updateScrollUpButton();
        return;
    }

    if(dock) dock.classList.remove('hidden');
    if(S.page==='music'){
        renderMusicPage(main);
        sendAction({type:'music_list_playlists'});
    } else if(S.page==='recordings'){
        renderRecordingsPage(main);
        if(!Array.isArray(S.recordings) || S.recordings.length===0){
            sendAction({type:'recordings_list'});
        }
    } else if (S.page==='files') {
        if (typeof renderFileManagerPage === 'function') {
            renderFileManagerPage(main);
        }
        if (typeof ensureFileManagerReady === 'function') {
            ensureFileManagerReady();
        }
    } else {
        renderHomePage(main);
    }
    updateChatComposerState();
    renderTimerBar();
    applyMusicHeader();
    updateScrollUpButton();
}

function renderHomePage(main){
    if(!S.chatSidebarTouched && isMobileChatViewport()) S.chatSidebarOpen=false;
    const sidebarClass = S.chatSidebarOpen ? 'w-72 border-r border-gray-800' : 'w-0 border-r-0';
    const sidebarInnerClass = S.chatSidebarOpen ? 'opacity-100' : 'opacity-0 pointer-events-none';
    const sidebarToggleIcon = '<svg aria-hidden="true" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 3v5h5"/><path d="M3 8a9 9 0 1 1-2 5"/><path d="M12 8v4l3 2"/></svg>';
    const sidebarToggleTitle = S.chatSidebarOpen ? 'Hide chats' : 'Show chats';
    const selected = S.selectedChatId || 'active';
    const hasChatThreads = Array.isArray(S.chatThreads) && S.chatThreads.length > 0;

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
    const chatClearAllModal = S.chatClearAllModalOpen
        ? '<div class="fixed inset-0 z-20 bg-black/60 flex items-center justify-center px-4">'
            +'<div class="w-full max-w-md rounded-xl border border-gray-700 bg-gray-900 p-4 space-y-3">'
                +'<div class="text-sm font-semibold">Clear all chat histories</div>'
                +'<p class="text-sm text-gray-300">Do you really want to clear all previous chat histories?</p>'
                +'<p class="text-xs text-gray-400">Your current chat stays open.</p>'
                +'<div class="flex justify-end gap-2">'
                    +'<button data-action="chat-clear-all-cancel" class="px-3 py-1.5 rounded-lg text-sm bg-gray-700 hover:bg-gray-600 transition-colors">Cancel</button>'
                    +'<button data-action="chat-clear-all-confirm" class="px-3 py-1.5 rounded-lg text-sm bg-red-700 hover:bg-red-600 transition-colors">Clear</button>'
                +'</div>'
            +'</div>'
        +'</div>'
        : '';

    main.innerHTML='<div class="w-full h-full flex min-h-0">'
        +'<aside id="chatSidebar" class="'+sidebarClass+' transition-all duration-200 overflow-hidden">'
            +'<div class="'+sidebarInnerClass+' h-full flex flex-col">'
                +'<div class="px-2 py-3 flex items-center gap-2 border-b border-gray-800">'
                    +'<div class="text-xs uppercase tracking-wide text-gray-400">Previous chats</div>'
                    +'<button data-action="chat-clear-all-open"'+(hasChatThreads?'':' disabled')+' class="ml-auto px-2.5 py-1 rounded-md text-[11px] font-medium uppercase tracking-wide transition-colors '+(hasChatThreads?'text-gray-300 bg-gray-800 hover:bg-red-700 hover:text-white':'text-gray-500 bg-gray-900 cursor-not-allowed')+'">Clear</button>'
                +'</div>'
                +'<div class="px-2 py-2 border-b border-gray-800">'
                    +'<input id="chatThreadSearch" type="search" value="'+esc(S.chatThreadFilter||'')+'" placeholder="Search chats" class="w-full rounded-lg bg-gray-800 border border-gray-700 px-3 py-2 text-sm text-gray-100 placeholder-gray-400 focus:outline-none focus:ring-2 focus:ring-blue-600" />'
                +'</div>'
                +'<div id="chatThreadList" class="flex-1 overflow-y-auto p-0 space-y-0"></div>'
            +'</div>'
        +'</aside>'
        +'<div class="flex-1 min-w-0 flex flex-col h-full">'
            +'<div class="px-3 py-2 border-b border-gray-800 flex items-center justify-between gap-2">'
                +'<div class="flex items-center gap-2">'
                    +'<button data-action="chat-sidebar-toggle" title="'+sidebarToggleTitle+'" aria-label="'+sidebarToggleTitle+'" class="inline-flex items-center justify-center w-8 h-8 rounded-lg bg-gray-800 hover:bg-gray-700 transition-colors">'+sidebarToggleIcon+'</button>'
                +'</div>'
                +'<button data-action="chat-new" class="px-3 py-1.5 rounded-lg text-xs bg-blue-700 hover:bg-blue-600 transition-colors">New</button>'
            +'</div>'
            +'<div id="chatArea" class="flex-1 overflow-y-auto px-4 py-4 space-y-3 min-h-0"></div>'
        +'</div>'
    +'</div>'
    +chatDeleteModal
    +chatClearAllModal;

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

const ASSISTANT_STREAM_PATCH_MIN_INTERVAL_MS=120;
let assistantStreamPatchTimerId=0;
let assistantStreamPatchSelectedId='active';
let assistantStreamPatchLastRunTs=0;
const assistantStreamPatchPendingByRequestId=new Map();

const CHAT_RENDER_MIN_INTERVAL_MS=140;
let chatRenderTimerId=0;
let chatRenderPendingSelectedId='active';
let chatRenderLastRunTs=0;

function scheduleChatMessagesRender(selectedId){
    chatRenderPendingSelectedId=selectedId||'active';
    if(chatRenderTimerId) return;
    const now=Date.now();
    const delay=Math.max(0, CHAT_RENDER_MIN_INTERVAL_MS-(now-chatRenderLastRunTs));
    chatRenderTimerId=setTimeout(()=>{
        chatRenderTimerId=0;
        chatRenderLastRunTs=Date.now();
        renderChatMessages(chatRenderPendingSelectedId);
    }, delay);
}

function isAssistantStreamBubbleActive(){
    const area=document.getElementById('chatArea');
    if(!area) return false;
    const streams=area.querySelectorAll('.chat-msg[data-role="assistant_stream_group"] details');
    for(const details of streams){
        if(details && details.open) return true;
    }
    return false;
}

function removeAssistantStreamRows(area, requestId){
    if(!area) return;
    const reqId=String(requestId||'');
    if(!reqId) return;
    const streamRows=area.querySelectorAll('.chat-msg[data-role="assistant_stream_group"][data-request-id="'+cssEsc(reqId)+'"]');
    streamRows.forEach((row)=>row.remove());
}

function upsertActiveChatBubbleInPlace(message, mode){
    const area=document.getElementById('chatArea');
    if(!area) return false;
    const msg=message && typeof message==='object' ? message : null;
    if(!msg) return false;
    const msgId=(msg.id!==undefined && msg.id!==null) ? String(msg.id) : '';
    const role=String(msg.role||'').toLowerCase();
    if(!role) return false;

    const prevScrollTop=area.scrollTop;
    const prevScrollHeight=area.scrollHeight;
    const nearBottom=(prevScrollHeight-area.clientHeight-prevScrollTop)<=80;

    if(mode==='update' && msgId){
        const existing=area.querySelector('.chat-msg[data-msg-id="'+cssEsc(msgId)+'"]');
        if(existing){
            existing.replaceWith(mkBubble(msg));
            if(role==='assistant') removeAssistantStreamRows(area, msg.request_id);
            if((S.chatFollowLatest && nearBottom) || Date.now()<Number(S.autoScrollUntilTs||0)) scrollChat();
            else area.scrollTop=prevScrollTop;
            updateScrollDownButton();
            updateScrollUpButton();
            return true;
        }
    }

    if(mode==='append'){
        if(msgId){
            const existing=area.querySelector('.chat-msg[data-msg-id="'+cssEsc(msgId)+'"]');
            if(existing){
                existing.replaceWith(mkBubble(msg));
            }else{
                area.appendChild(mkBubble(msg));
            }
        }else{
            area.appendChild(mkBubble(msg));
        }

        // Final assistant message replaces the transient stream row in the incremental path.
        if(role==='assistant'){
            removeAssistantStreamRows(area, msg.request_id);
        }

        if((S.chatFollowLatest && nearBottom) || Date.now()<Number(S.autoScrollUntilTs||0)) scrollChat();
        else area.scrollTop=prevScrollTop;
        updateScrollDownButton();
        updateScrollUpButton();
        return true;
    }

    return false;
}

function cssEsc(value){
    const raw=String(value||'');
    if(typeof CSS!=='undefined' && CSS && typeof CSS.escape==='function') return CSS.escape(raw);
    return raw.replace(/([\\"'\[\]#.:>+~*=()\s])/g, '\\$1');
}

function scheduleAssistantStreamBubbleUpdate(selectedId, streamMessage){
    assistantStreamPatchSelectedId=selectedId||'active';
    if(streamMessage && typeof streamMessage==='object'){
        const requestId=(streamMessage.request_id!==undefined&&streamMessage.request_id!==null)?String(streamMessage.request_id):'';
        assistantStreamPatchPendingByRequestId.set(requestId, {
            request_id:requestId,
            text:String(streamMessage.text||''),
        });
    }
    if(assistantStreamPatchTimerId) return;
    const now=Date.now();
    const delay=Math.max(0, ASSISTANT_STREAM_PATCH_MIN_INTERVAL_MS-(now-assistantStreamPatchLastRunTs));
    assistantStreamPatchTimerId=setTimeout(()=>{
        assistantStreamPatchTimerId=0;
        assistantStreamPatchLastRunTs=Date.now();
        const pendingMessages=Array.from(assistantStreamPatchPendingByRequestId.values());
        assistantStreamPatchPendingByRequestId.clear();
        let patchedAny=false;
        for(const pendingMessage of pendingMessages){
            if(updateAssistantStreamBubbleInPlace(assistantStreamPatchSelectedId, pendingMessage)) patchedAny=true;
        }
        if(!patchedAny) renderChatMessages(assistantStreamPatchSelectedId);
    }, delay);
}

function updateAssistantStreamBubbleInPlace(selectedId, streamMessage){
    const area=document.getElementById('chatArea');
    if(!area) return false;

    if(streamMessage && typeof streamMessage==='object'){
        const requestedId=String(streamMessage.request_id||'');
        const text=String(streamMessage.text||'');
        const streamNodes=Array.from(area.querySelectorAll('[data-stream-content="true"]'));
        if(streamNodes.length){
            let bubble=null;
            if(requestedId){
                bubble=streamNodes.find((node)=>String(node.getAttribute('data-stream-request-id')||'')===requestedId) || null;
                if(!bubble) return false;
            }else if(!bubble){
                bubble=streamNodes[streamNodes.length-1];
            }
            if(bubble){
                bubble.textContent=text;
                updateScrollDownButton();
                updateScrollUpButton();
                return true;
            }
        }
    }

    const collated=collateChatMessages(getSelectedMessages(selectedId));
    let latestStream=null;
    for(let i=collated.length-1;i>=0;i--){
        const msg=collated[i];
        if(msg && msg.role==='assistant_stream_group'){
            latestStream=msg;
            break;
        }
        if(msg && msg.role==='assistant') break;
    }
    if(!latestStream) return false;

    const streamRows=area.querySelectorAll('.chat-msg[data-role="assistant_stream_group"]');
    if(!streamRows.length) return false;
    const row=streamRows[streamRows.length-1];

    let bubble=row.querySelector('[data-stream-content="true"]');
    if(!bubble){
        const blocks=row.querySelectorAll('div');
        bubble=blocks.length ? blocks[blocks.length-1] : null;
    }
    if(!bubble) return false;

    bubble.textContent=String((latestStream&&latestStream.text)||'');
    updateScrollDownButton();
    updateScrollUpButton();
    return true;
}

const pendingGatewayDebugReqIds=new Set();
let gatewayDebugRafId=0;

function mergeRawGatewayFrames(rawMessages){
    return (Array.isArray(rawMessages)?rawMessages:[]).map((msg, idx)=>{
        const rawText=String((msg&&msg.text)||'');
        if(!rawText.trim()) return {index:idx, rawText:''};
        try{
            return JSON.parse(rawText);
        }catch(_){
            return {index:idx, rawText};
        }
    });
}

function extractLifecyclePhaseFlagsFromRawFrames(rawFrames){
    let hasStart=false;
    let hasEnd=false;
    let lastPhase='';

    const ingestPhase=(phaseValue)=>{
        const phase=String(phaseValue||'').toLowerCase();
        if(phase==='start' || phase==='end' || phase==='result') lastPhase=phase;
        if(phase==='start') hasStart=true;
        if(phase==='end' || phase==='result') hasEnd=true;
    };

    for(const frame of (Array.isArray(rawFrames)?rawFrames:[])){
        if(!frame || typeof frame!=='object') continue;

        const frameType=String(frame.type||'').toLowerCase();
        const frameEvent=String(frame.event||'').toLowerCase();
        if(frameType==='event' && frameEvent==='agent'){
            const payload=(frame.payload&&typeof frame.payload==='object') ? frame.payload : {};
            const stream=String(payload.stream||'').toLowerCase();
            if(stream==='lifecycle'){
                const data=(payload.data&&typeof payload.data==='object') ? payload.data : {};
                ingestPhase(data.phase!==undefined ? data.phase : payload.phase);
            }
            continue;
        }

        const name=String(frame.name||'').toLowerCase();
        if(name==='lifecycle'){
            ingestPhase(frame.phase);
        }
    }

    return {hasStart, hasEnd, lastPhase};
}

function collectRawGatewayMessagesForRequest(selectedId, requestId){
    const reqKey=String(requestId===undefined || requestId===null ? 'na' : requestId);
    return getSelectedMessages(selectedId).filter((m)=>{
        if(!m || m.role!=='raw_gateway') return false;
        const msgReq=String((m&&m.request_id)!==undefined && (m&&m.request_id)!==null ? m.request_id : 'na');
        return msgReq===reqKey;
    });
}

function findDetailByKey(area, detailKey){
    const nodes=area.querySelectorAll('details[data-detail-key]');
    for(const el of nodes){
        if(el.getAttribute('data-detail-key')===detailKey) return el;
    }
    return null;
}

function updateGatewayDebugBubbleInPlace(requestId, selectedId){
    const area=document.getElementById('chatArea');
    if(!area) return false;
    const reqKey=String(requestId===undefined || requestId===null ? 'na' : requestId);
    const detailKey='req:'+reqKey+':raw-group';
    const detailEl=findDetailByKey(area, detailKey);
    if(!detailEl) return false;

    const rawMessages=collectRawGatewayMessagesForRequest(selectedId, requestId);
    const mergedFrames=mergeRawGatewayFrames(rawMessages);
    const mergedJson=JSON.stringify(mergedFrames, null, 2) || '[]';

    const summaryLabel=detailEl.querySelector('[data-debug-label]');
    if(summaryLabel) summaryLabel.textContent='Debug: raw gateway JSON ('+String(rawMessages.length)+')';

    const pre=detailEl.querySelector('[data-debug-pre]') || detailEl.querySelector('pre');
    if(pre) pre.textContent=mergedJson;

    return true;
}

function scheduleGatewayDebugBubbleUpdate(requestId, selectedId){
    pendingGatewayDebugReqIds.add(String(requestId===undefined || requestId===null ? 'na' : requestId));
    if(gatewayDebugRafId) return;
    gatewayDebugRafId=requestAnimationFrame(()=>{
        gatewayDebugRafId=0;
        const ids=Array.from(pendingGatewayDebugReqIds);
        pendingGatewayDebugReqIds.clear();
        let missing=false;
        ids.forEach((id)=>{
            const ok=updateGatewayDebugBubbleInPlace(id, selectedId);
            if(!ok) missing=true;
        });
        if(missing){
            renderChatMessages(selectedId);
        } else {
            updateScrollDownButton();
            updateScrollUpButton();
        }
    });
}

function scrollChat(){ const a=document.getElementById('chatArea'); if(a){ a.scrollTop=a.scrollHeight; updateScrollDownButton(); } }

function extractResultObject(parsed){
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
}

function extractReferencedPathsFromEvents(events, opts){
    const includeRead=opts&&opts.includeRead!==undefined ? !!opts.includeRead : true;
    const includeWrite=opts&&opts.includeWrite!==undefined ? !!opts.includeWrite : true;
    const paths=[];
    const addPath=(value)=>{
        const text=String(value||'').trim();
        if(!text) return;
        if(!paths.includes(text)) paths.push(text);
    };
    const collectFromPayload=(obj)=>{
        if(!obj || typeof obj!=='object') return;
        const req=obj.args!==undefined ? obj.args : (obj.arguments!==undefined ? obj.arguments : obj.input);
        if(req && typeof req==='object'){
            [req.filePath, req.file_path, req.path, req.old_path, req.new_path, req.uri].forEach(addPath);
            if(Array.isArray(req.filePaths)) req.filePaths.forEach(addPath);
            if(Array.isArray(req.paths)) req.paths.forEach(addPath);
        }
        const resultObj=extractResultObject(obj);
        if(resultObj && typeof resultObj==='object'){
            [resultObj.filePath, resultObj.file_path, resultObj.path, resultObj.old_path, resultObj.new_path, resultObj.uri, resultObj.file].forEach(addPath);
            if(Array.isArray(resultObj.filePaths)) resultObj.filePaths.forEach(addPath);
            if(Array.isArray(resultObj.paths)) resultObj.paths.forEach(addPath);
            if(Array.isArray(resultObj.files)) resultObj.files.forEach(addPath);
        }
    };

    for(const ev of (events||[])){
        if(!ev || ev.kind!=='tool') continue;
        const p=ev.payload||{};
        const n=String(p.name||p.text||'').toLowerCase();
        const isRead=n==='read' || n.includes('read_file');
        const isWrite=n==='write' || n.includes('write_file') || n.includes('create_file') || n.includes('str_replace') || n.includes('insert') || n.includes('delete');
        if((isRead && !includeRead) || (isWrite && !includeWrite)) continue;
        if(!isRead && !isWrite) continue;
        const phase=String(p.phase||'').toLowerCase();
        if(phase!=='start' && phase!=='update' && phase!=='result' && phase!=='end') continue;
        try{
            const det=JSON.parse(String(p.details||'').trim());
            if(det && typeof det==='object') collectFromPayload(det);
        }catch{}
    }
    return paths;
}

function extractMarkdownLinkedPathsFromText(text){
    const paths=[];
    const seen=new Set();
    const add=(val)=>{
        const v=String(val||'').trim();
        if(!v || seen.has(v)) return;
        if(!v.includes('/')) return;
        seen.add(v);
        paths.push(v);
    };
    const raw=String(text||'');
    const mdLinkRe=/\[[^\]]*\]\(([^)\s#?]+)\)/g;
    let m;
    while((m=mdLinkRe.exec(raw))!==null){
        const url=String(m[1]||'').trim();
        if(!url || /^https?:\/\//i.test(url)) continue;
        if(!url.includes('/')) continue;
        if(/\.[a-zA-Z0-9]{1,10}$/.test(url) || url.startsWith('/')) add(url);
    }
    return paths;
}

function normalizePathForMatch(path){
    return String(path||'').trim().replace(/^file:\/\//i, '').replace(/\\/g, '/').replace(/^\.\//, '');
}

function pathsReferToSameFile(a, b){
    const left=normalizePathForMatch(a);
    const right=normalizePathForMatch(b);
    if(!left || !right) return false;
    if(left===right) return true;
    return left.endsWith('/'+right) || right.endsWith('/'+left);
}

function intersectPathsByReference(basePaths, candidatePaths){
    const out=[];
    const seen=new Set();
    for(const base of (basePaths||[])){
        if(!base) continue;
        const match=(candidatePaths||[]).some((cand)=>pathsReferToSameFile(base, cand));
        if(!match) continue;
        const key=normalizePathForMatch(base);
        if(seen.has(key)) continue;
        seen.add(key);
        out.push(base);
    }
    return out;
}

function collateChatMessages(msgs){
    const out=[];
    let activeBucket=null;

    const flushBucket=()=>{
        if(!activeBucket) return;
        if(activeBucket.events.length>0 || activeBucket.rawMsgs.length>0){
            out.push({
                role:'context_group',
                request_id:activeBucket.reqId,
                events:activeBucket.events,
                steps:activeBucket.steps,
                interim:activeBucket.interim,
                raw_messages:activeBucket.rawMsgs,
                hasFinal:activeBucket.finals.length>0,
            });
        }
        const validStreams=activeBucket.streams.filter(s=>String(s.text||'').length>0);
        const combinedStreamText=validStreams.map(s=>String(s.text||'')).join('');
        const writeReferencedFiles=extractReferencedPathsFromEvents(activeBucket.events, {includeRead:false, includeWrite:true});
        const referencedFiles=[];
        // Extract only markdown-linked file paths from response text.
        const responseCandidateTexts=[combinedStreamText, ...activeBucket.finals.map(f=>String(f.text||'')+' '+String(f.full_text||''))];
        const linkedPaths=[];
        for(const t of responseCandidateTexts){
            for(const p of extractMarkdownLinkedPathsFromText(t)){
                if(!linkedPaths.includes(p)) linkedPaths.push(p);
            }
        }
        const matchedWriteFiles=intersectPathsByReference(writeReferencedFiles, linkedPaths);
        const fallbackWriteFiles=matchedWriteFiles.length ? matchedWriteFiles : writeReferencedFiles;
        for(const p of fallbackWriteFiles){
            if(!referencedFiles.includes(p)) referencedFiles.push(p);
        }
        const extra={referenced_files:referencedFiles, written_files:referencedFiles};
        if(validStreams.length>0 && activeBucket.finals.length===0){
            out.push({
                role:'assistant_stream_group',
                request_id:activeBucket.reqId,
                source:validStreams[0].source||'assistant',
                text:combinedStreamText,
                segments:validStreams,
                latest:validStreams[validStreams.length-1],
                hasFinal:false,
                ...extra,
            });
        }
        activeBucket.finals.forEach((f, index)=>{
            if(validStreams.length>0 && index===activeBucket.finals.length-1){
                out.push({...f, ...extra, stream_text:combinedStreamText, stream_segments:validStreams});
                return;
            }
            out.push({...f, ...extra});
        });
        activeBucket=null;
    };

    const ensureActiveBucket=(reqId)=>{
        if(!activeBucket || activeBucket.reqId!==reqId){
            flushBucket();
            activeBucket={reqId,rawMsgs:[],streams:[],steps:[],interim:[],events:[],finals:[]};
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
        if(role==='raw_gateway'){
            const bucket=ensureActiveBucket(reqId);
            bucket.rawMsgs.push(m);
            return;
        }
        if(role==='assistant'){
            const bucket=ensureActiveBucket(reqId);
            const segKind=String((m&&m.segment_kind)||'final').toLowerCase();
            if(segKind==='stream') bucket.streams.push(m);
            else bucket.finals.push(m);
            return;
        }
        // Handle bare tool/lifecycle events (with name/phase but no role field)
        if(!role && (m.name || m.phase)){
            const bucket=ensureActiveBucket(reqId);
            const toolName=String(m.name||'tool').toLowerCase();
            const isLifecycle=toolName==='lifecycle' || m.phase==='start' && !m.toolCallId;
            bucket.steps.push(m);
            if(isLifecycle) bucket.events.push({kind:'lifecycle',payload:m});
            else bucket.events.push({kind:'tool',payload:m});
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
const OUTER_MARKDOWN_FENCE_RE=/^\s*```(?:markdown|md)\s*\r?\n([\s\S]*?)\r?\n```\s*$/i;
const OUTER_ANY_FENCE_RE=/^\s*```[^\n]*(?:\r?\n)?([\s\S]*?)(?:\r?\n)?```\s*$/i;
let mermaidInitialized=false;
let mermaidRenderSeq=0;

function unwrapOuterMarkdownFence(raw){
    const txt=String(raw||'');
    // Try markdown-specific fences first, then any fence (including plain ```)
    let m=OUTER_MARKDOWN_FENCE_RE.exec(txt);
    if(m) return String(m[1]||'');
    
    m=OUTER_ANY_FENCE_RE.exec(txt);
    if(m) return String(m[1]||'');
    
    return txt;
}

function decodeLikelyEscapedText(raw){
    const txt=String(raw||'');
    if(!txt) return '';
    // Some providers return markdown with literal escape sequences (\\n, \\t)
    // instead of real line breaks, which collapses formatting into one line.
    if(txt.includes('\\r\\n') || txt.includes('\\n') || txt.includes('\\t')){
        return txt
            .replace(/\\r\\n/g, '\n')
            .replace(/\\n/g, '\n')
            .replace(/\\t/g, '\t');
    }
    return txt;
}

function restoreLikelyFlattenedMarkdown(raw){
    let txt=String(raw||'');
    if(!txt) return '';

    const hasMarkdownSignals=(/(^|\s)#{1,6}\s?/.test(txt) || /\[[^\]]+\]\([^)]+\)/.test(txt) || /```/.test(txt) || /(^|\s)\d+\.\s/.test(txt));
    if(!hasMarkdownSignals) return txt;

    txt=txt.replace(/\r\n/g, '\n');

    // Normalize headings with missing space after hashes: ##Title -> ## Title
    txt=txt.replace(/(^|\n)(#{1,6})([^\s#])/g, '$1$2 $3');

    // Insert line breaks before heading/list markers that were flattened into prose.
    txt=txt
        .replace(/[ \t]+(#{1,6}\s)/g, '\n$1')
        .replace(/[ \t]+(>\s+)/g, '\n$1')
        .replace(/[ \t]+([-*+]\s?)(?=\S)/g, '\n$1')
        .replace(/[ \t]+(\d+\.\s+)/g, '\n$1')
        .replace(/[ \t]+(```)/g, '\n$1');

    // Ordered-list repair for compact forms: item1. First item2. Second
    txt=txt.replace(/([a-zA-Z])(?=(\d+\.\s+[A-Z]))/g, '$1\n');

    // Ensure list markers include a space after marker.
    txt=txt
        .replace(/(^|\n)([-*+])(?=\S)/g, '$1$2 ')
        .replace(/(^|\n)(\d+\.)(?=\S)/g, '$1$2 ');

    // Ensure fenced code blocks have a line break after opening fence.
    txt=txt.replace(/```([a-zA-Z0-9_-]+)?\s+([^\n])/g, (_m, lang, first)=>`\n\`\`\`${lang||''}\n${first}`);

    // Remove obvious heading-label artifacts that leak from explanatory prose.
    txt=txt.replace(/(^|\n)-H([1-6])-\s*/g, '$1');

    txt=txt.replace(/\n{3,}/g, '\n\n');
    return txt.trim();
}

function looksLikeMarkdownDocument(raw){
    const txt=String(raw||'').trim();
    if(!txt) return false;
    const checks=[
        /^#{1,6}\s+/m,
        /^\s*[-*+]\s+/m,
        /^\s*\d+\.\s+/m,
        /^\s*>\s+/m,
        /\[[^\]]+\]\([^)]+\)/m,
        /`[^`]+`/m,
        /^\s*\|.+\|\s*$/m,
    ];
    return checks.some((re)=>re.test(txt));
}

function normalizeAssistantMarkdownSource(raw){
    let txt=decodeLikelyEscapedText(raw);
    txt=restoreLikelyFlattenedMarkdown(txt);
    txt=unwrapOuterMarkdownFence(txt);
    const anyFence=OUTER_ANY_FENCE_RE.exec(txt);
    if(anyFence){
        const inner=String(anyFence[1]||'');
        if(looksLikeMarkdownDocument(inner)) txt=inner;
    }
    return txt;
}

function _escapeHtml(raw){
    return String(raw||'')
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
}

function _inlineMarkdownToHtml(raw){
    let s=_escapeHtml(raw);
    s=s.replace(/`([^`]+)`/g, '<code>$1</code>');
    s=s.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
    s=s.replace(/\*([^*]+)\*/g, '<em>$1</em>');
    s=s.replace(/\[([^\]]+)\]\(([^)]+)\)/g, '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>');
    return s;
}

function _fallbackMarkdownToHtml(raw){
    const lines=String(raw||'').replace(/\r\n/g,'\n').split('\n');
    const out=[];
    let inUl=false;
    let inOl=false;

    const closeLists=()=>{
        if(inUl){ out.push('</ul>'); inUl=false; }
        if(inOl){ out.push('</ol>'); inOl=false; }
    };

    for(const line of lines){
        const txt=String(line||'');
        const h=txt.match(/^\s*(#{1,6})\s+(.+)$/);
        if(h){
            closeLists();
            const level=Math.min(6, h[1].length);
            out.push(`<h${level}>${_inlineMarkdownToHtml(h[2])}</h${level}>`);
            continue;
        }

        const ul=txt.match(/^\s*[-*+]\s+(.+)$/);
        if(ul){
            if(inOl){ out.push('</ol>'); inOl=false; }
            if(!inUl){ out.push('<ul>'); inUl=true; }
            out.push(`<li>${_inlineMarkdownToHtml(ul[1])}</li>`);
            continue;
        }

        const ol=txt.match(/^\s*\d+\.\s+(.+)$/);
        if(ol){
            if(inUl){ out.push('</ul>'); inUl=false; }
            if(!inOl){ out.push('<ol>'); inOl=true; }
            out.push(`<li>${_inlineMarkdownToHtml(ol[1])}</li>`);
            continue;
        }

        if(!txt.trim()){
            closeLists();
            continue;
        }

        closeLists();
        out.push(`<p>${_inlineMarkdownToHtml(txt)}</p>`);
    }

    closeLists();
    return out.join('');
}

function _forceMarkdownLinksNewWindow(html){
    const src=String(html||'');
    if(!src.trim()) return '';
    if(typeof document==='undefined') return src;
    const tpl=document.createElement('template');
    tpl.innerHTML=src;
    tpl.content.querySelectorAll('a[href]').forEach((a)=>{
        a.setAttribute('target', '_blank');
        a.setAttribute('rel', 'noopener noreferrer');
    });
    return tpl.innerHTML;
}

function renderMarkdownHtml(raw){
    const txt=String(raw||'');
    if(!txt.trim()) return '';

    const markedApi=(typeof marked!=='undefined' && marked && typeof marked.parse==='function')
        ? marked
        : ((typeof window!=='undefined' && window.marked && typeof window.marked.parse==='function') ? window.marked : null);
    const domPurifyApi=(typeof DOMPurify!=='undefined' && DOMPurify && typeof DOMPurify.sanitize==='function')
        ? DOMPurify
        : ((typeof window!=='undefined' && window.DOMPurify && typeof window.DOMPurify.sanitize==='function') ? window.DOMPurify : null);

    try{
        if(markedApi){
            markedApi.setOptions({breaks:true,gfm:true});
            const parsed=markedApi.parse(txt);
            if(domPurifyApi){
                const safe=domPurifyApi.sanitize(parsed,{ALLOWED_TAGS:CHAT_MARKDOWN_ALLOWED_TAGS,ALLOWED_ATTR:CHAT_MARKDOWN_ALLOWED_ATTR});
                return _forceMarkdownLinksNewWindow(safe);
            }
            return _forceMarkdownLinksNewWindow(parsed);
        }

        const fallback=_fallbackMarkdownToHtml(txt);
        if(domPurifyApi){
            const safe=domPurifyApi.sanitize(fallback,{ALLOWED_TAGS:CHAT_MARKDOWN_ALLOWED_TAGS,ALLOWED_ATTR:CHAT_MARKDOWN_ALLOWED_ATTR});
            return _forceMarkdownLinksNewWindow(safe);
        }
        return _forceMarkdownLinksNewWindow(fallback);
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
    const txt=normalizeAssistantMarkdownSource(raw);
    target.textContent='';
    if(!txt.trim()) return;

    const parts=splitAssistantContent(txt);
    if(!parts.length){
        const html=renderMarkdownHtml(txt);
        if(html){
            target.classList.add('md-content');
            target.innerHTML=html;
        }
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

function makeResponseCopyButton(getText){
    const btn=document.createElement('button');
    btn.type='button';
    btn.className='absolute px-2 py-0.5 rounded text-[10px] bg-gray-900/70 hover:bg-gray-800 text-gray-200 border border-gray-600 transition-colors';
    btn.style.top='6px';
    btn.style.right='6px';
    btn.style.left='auto';
    btn.style.zIndex='2';
    btn.textContent='Copy';
    btn.title='Copy response';
    btn.addEventListener('click', async (e)=>{
        e.preventDefault();
        e.stopPropagation();
        const text=normalizeCopiedAssistantText(getText?getText():'');
        if(!text) return;
        try{
            await navigator.clipboard.writeText(text);
            const prev=btn.textContent;
            btn.textContent='Copied';
            setTimeout(()=>{ btn.textContent=prev; }, 1200);
        }catch(_){
            const prev=btn.textContent;
            btn.textContent='Failed';
            setTimeout(()=>{ btn.textContent=prev; }, 1200);
        }
    });
    return btn;
}

function makeUserMessageCopyButton(text){
    const btn=document.createElement('button');
    btn.type='button';
    btn.className='inline-flex items-center justify-center w-6 h-6 rounded text-[11px] bg-gray-700 hover:bg-gray-600 text-gray-100 border border-gray-500 transition-colors';
    btn.textContent='⧉';
    btn.title='Copy this message to clipboard';
    btn.setAttribute('data-action','copy-user-message');
    btn.addEventListener('click', async(e) => {
        e.stopPropagation();
        try {
            await navigator.clipboard.writeText(text);
            const originalText=btn.textContent;
            btn.textContent='✓';
            btn.disabled=true;
            setTimeout(() => {
                btn.textContent=originalText;
                btn.disabled=false;
            }, 2000);
        } catch(err) {
            console.error('Failed to copy:', err);
            btn.textContent='!';
            setTimeout(() => { btn.textContent='⧉'; }, 1500);
        }
    });
    return btn;
}

function normalizeCopiedAssistantText(raw){
    const txt=String(raw||'');
    if(!txt.trim()) return '';

    // If the entire message is wrapped in a markdown fence, copy only the inner content.
    const m=OUTER_MARKDOWN_FENCE_RE.exec(txt);
    if(m) return String(m[1]||'').trim();

    return txt.trim();
}

function attachPlainTriangleToDetails(detailsEl, summaryEl){
    if(!detailsEl || !summaryEl) return;
    if(summaryEl.querySelector('[data-expander-icon="true"]')) return;

    summaryEl.classList.add('list-none');
    summaryEl.style.listStyle='none';

    const icon=document.createElement('span');
    icon.setAttribute('data-expander-icon','true');
    icon.className='inline-block text-white select-none flex-shrink-0';
    icon.style.width='0.9rem';
    icon.style.lineHeight='1';
    icon.style.marginRight='0.15rem';
    icon.textContent=detailsEl.open ? '▼' : '▶';

    summaryEl.insertBefore(icon, summaryEl.firstChild);

    const sync=()=>{
        icon.textContent=detailsEl.open ? '▼' : '▶';
    };
    detailsEl.addEventListener('toggle', sync);
}

function mkBubble(m){
  const d=document.createElement('div');
    const role = (m&&m.role)||'';
    d.className='chat-msg flex '+(role==='user'?'justify-end':'justify-start');
        d.setAttribute('data-role', role);
        const msgId=(m&&m.id!==undefined&&m.id!==null)?String(m.id):'';
        if(msgId) d.setAttribute('data-msg-id', msgId);
        const reqId=(m&&m.request_id!==undefined&&m&&m.request_id!==null)?String(m.request_id):'';
        if(reqId) d.setAttribute('data-request-id', reqId);

    if(role==='gateway_debug_group'){
        const wrap=document.createElement('div');
        wrap.className='max-w-xs sm:max-w-sm lg:max-w-md space-y-1';
        const reqKey=String((m&&m.request_id)!==undefined && (m&&m.request_id)!==null ? m.request_id : 'na');
        const rawMessages=Array.isArray(m.raw_messages) ? m.raw_messages : [];
        const mergedFrames=mergeRawGatewayFrames(rawMessages);
        const mergedJson=JSON.stringify(mergedFrames, null, 2);
        const details=document.createElement('details');
        details.setAttribute('data-detail-key', esc('req:'+reqKey+':raw-group'));
        details.className='rounded-xl bg-gray-900/60 border border-fuchsia-700/60 text-xs overflow-hidden';
        const summary=document.createElement('summary');
        summary.className='px-3 py-1.5 cursor-pointer text-fuchsia-200 hover:text-fuchsia-100 select-none flex items-center';
        const summaryLabel=document.createElement('span');
        summaryLabel.className='flex-1';
        summaryLabel.textContent='Debug: raw gateway JSON ('+String(rawMessages.length)+')';
        const copyBtn=document.createElement('button');
        copyBtn.type='button';
        copyBtn.textContent='Copy';
        copyBtn.className='px-1.5 py-0.5 rounded text-[10px] bg-fuchsia-900/60 hover:bg-fuchsia-800/80 text-fuchsia-200 border border-fuchsia-700/50 transition-colors';
        copyBtn.style.marginLeft='1.5rem';
        copyBtn.addEventListener('click', (e)=>{
            e.preventDefault();
            e.stopPropagation();
            navigator.clipboard.writeText(mergedJson||'[]').then(()=>{
                const orig=copyBtn.textContent;
                copyBtn.textContent='Copied!';
                setTimeout(()=>{ copyBtn.textContent=orig; }, 1500);
            }).catch(()=>{});
        });
        summary.appendChild(summaryLabel);
        summary.appendChild(copyBtn);
        const body=document.createElement('div');
        body.className='px-2 pb-2 pt-1';
        const pre=document.createElement('pre');
        pre.className='whitespace-pre-wrap break-words text-[11px] text-gray-300';
        pre.textContent=mergedJson||'[]';
        body.appendChild(pre);
        details.appendChild(summary);
        details.appendChild(body);
        wrap.appendChild(details);
        d.appendChild(wrap);
        return d;
    }

    if(role==='assistant_stream_group'){
        const wrap=document.createElement('div');
        wrap.className='max-w-xs sm:max-w-sm lg:max-w-md relative';

        const reqKey=String((m&&m.request_id)!==undefined && (m&&m.request_id)!==null ? m.request_id : 'na');
        const streamComplete=!!(m&&m.hasFinal);
        const details=document.createElement('details');
        details.className='rounded-2xl rounded-bl-md bg-gray-700 text-gray-100 overflow-hidden';
        details.open=!streamComplete;

        const summary=document.createElement('summary');
        summary.className='px-4 py-2 cursor-pointer text-sm text-gray-100 hover:text-white select-none';
        summary.textContent=streamComplete ? 'Streamed response' : 'Streaming response';

        const b=document.createElement('div');
        b.className='px-4 pb-3 text-sm leading-relaxed text-gray-100 whitespace-pre-wrap break-words';
        b.setAttribute('data-stream-content','true');
        b.setAttribute('data-stream-request-id', reqKey);
        const _rawText=(m.text||'');
        b.textContent=_rawText;

        details.appendChild(summary);
        details.appendChild(b);

        const isQuickAnswer=String((m&&m.source)||'')==='quick_answer';
        if(!isQuickAnswer){
            details.classList.add('pr-12');
            wrap.appendChild(makeResponseCopyButton(()=>String((m&&m.text)||'')));
        }

        wrap.appendChild(details);
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

        const events=(m.events||[]);
        const rawMessages=Array.isArray(m.raw_messages) ? m.raw_messages : [];
        if(events.length>0 || rawMessages.length>0){
            const toolGroups=new Map();
            const lifecycleItems=[];

            let hasLifecycleStart=false;
            let hasLifecycleEnd=false;
            let hasLifecycleHardError=false;
            let lastLifecyclePhase='';
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
                if(name==='lifecycle' && (phase==='start' || phase==='end' || phase==='result')){
                    lastLifecyclePhase=phase;
                }
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
            }

            const toolGroupList=[...toolGroups.values()];
            const rawLifecycleFlags=extractLifecyclePhaseFlagsFromRawFrames(mergeRawGatewayFrames(rawMessages));
            if(!hasLifecycleStart && rawLifecycleFlags.hasStart) hasLifecycleStart=true;
            if(!hasLifecycleEnd && rawLifecycleFlags.hasEnd) hasLifecycleEnd=true;
            if(rawLifecycleFlags.lastPhase) lastLifecyclePhase=rawLifecycleFlags.lastPhase;

            const allToolsTerminal=(toolGroupList.length>0) && toolGroupList.every((g)=>
                g.phases.includes('result') || g.phases.includes('end') || g.phases.includes('error') || g.isError!==null
            );
            const failedGroups=toolGroupList.filter((g)=>
                g.isError===true
                || g.phases.includes('error')
                || g.timeout
                || /\btimeout|timed out\b/i.test(String(g.errorText||''))
            );
            const hasToolError=failedGroups.length>0;
            const hasLifecycleError=hasLifecycleHardError;
            // Treat lifecycle end/result and terminal tool phases as completion,
            // even when no final assistant message is emitted.
            const hasCompletionSignal = !!(
                hasLifecycleEnd
                || lastLifecyclePhase === 'end'
                || lastLifecyclePhase === 'result'
                || allToolsTerminal
            );
            const waiting=(hasLifecycleStart && !hasLifecycleError && !hasToolError && !m.hasFinal && !hasCompletionSignal);
            const waitingRow=waiting
                ? '<div class="px-2 py-1 border-b border-gray-800/60 text-[11px] text-yellow-300 flex items-center gap-2">'
                    +'<span class="inline-block w-3 h-3 border-2 border-yellow-300/80 border-t-transparent rounded-full animate-spin"></span>'
                    +'<span>waiting…</span>'
                  +'</div>'
                : '';
            const failedToolNames=failedGroups.map((g)=>String(g.name||'tool')).filter(Boolean);
            const uniqueFailedToolNames=[...new Set(failedToolNames)];
            const failedToolPreview=uniqueFailedToolNames.slice(0, 3).join(', ');
            const failedToolExtra=uniqueFailedToolNames.length>3 ? ' +' + String(uniqueFailedToolNames.length-3) + ' more' : '';
            const failureRow=hasToolError
                ? '<div class="px-2 py-1 border-b border-red-700/50 bg-red-950/30 text-[11px] text-red-200 flex items-center gap-2">'
                    +'<span>!</span><span>failed actions: '+String(failedGroups.length)+(failedToolPreview?(' ('+esc(failedToolPreview)+failedToolExtra+')'):'')+'</span>'
                  +'</div>'
                : '';
            const statusRow=(!waiting)
                ? ((hasToolError || hasLifecycleError)
                    ? '<div class="px-2 py-1 border-b border-gray-800/60 text-[11px] text-red-300 flex items-center gap-2"><span>✕</span><span>completed with errors</span></div>'
                    : ((hasLifecycleStart || hasLifecycleEnd || toolGroupList.length>0 || lifecycleItems.length>0 || allToolsTerminal)
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

                const rowTone=failed
                    ? 'text-red-300'
                    : (success ? 'text-emerald-300' : 'text-amber-300');

                return '<div class="px-2 py-1 border-b border-gray-800/60 last:border-b-0">'
                    +'<div class="text-[11px] '+rowTone+' flex items-center gap-1"><span class="w-3 text-center">'+icon+'</span><span class="font-mono">'+esc(g.name)+'</span><span class="text-[10px] text-gray-500">'+esc(phaseLabel)+'</span></div>'
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

            const mergedFrames=mergeRawGatewayFrames(rawMessages);
            const mergedJson=JSON.stringify(mergedFrames, null, 2) || '[]';
            const debugBlock=rawMessages.length
                ? '<details data-detail-key="'+esc('req:'+reqKey+':raw-group')+'" class="mt-2 rounded border border-fuchsia-700/60 bg-gray-900/40">'
                    +'<summary class="px-2 py-1 cursor-pointer text-[10px] text-fuchsia-200 hover:text-fuchsia-100 flex items-center">'
                        +'<span data-debug-label class="flex-1">Debug: raw gateway JSON ('+String(rawMessages.length)+')</span>'
                        +'<button type="button" data-action="gateway-debug-copy" class="px-1.5 py-0.5 rounded text-[10px] bg-fuchsia-900/60 hover:bg-fuchsia-800/80 text-fuchsia-200 border border-fuchsia-700/50 transition-colors" style="margin-left:1.5rem">Copy</button>'
                    +'</summary>'
                    +'<pre data-debug-pre class="px-2 pb-2 whitespace-pre-wrap break-words text-[11px] text-gray-300">'+esc(mergedJson)+'</pre>'
                  +'</details>'
                : '';

            const executionSequenceContent=failureRow+statusRow+waitingRow+toolRows+lifecycleRows+debugBlock;
            if(executionSequenceContent){
                const thinkingSummary=waiting
                    ? '<summary class="px-3 py-1.5 cursor-pointer text-gray-200 hover:text-gray-100 select-none flex items-center gap-2"><span class="inline-block w-3 h-3 border-2 border-yellow-300/80 border-t-transparent rounded-full animate-spin"></span><span>'+(hasToolError?'Thinking (with errors)':'Thinking')+'</span></summary>'
                    : '<summary class="px-3 py-1.5 cursor-pointer text-gray-200 hover:text-gray-100 select-none">Thinking</summary>';
                const timeline=document.createElement('div');
                timeline.innerHTML='<details data-detail-key="'+esc('req:'+reqKey+':thinking')+'" class="rounded-xl bg-gray-900/60 border border-gray-700 text-xs overflow-hidden">'
                    +thinkingSummary
                    +'<div class="px-2 pb-2 pt-1">'+executionSequenceContent+'</div>'
                    +'</details>';
                const thinkingDetails=timeline.querySelector('details[data-detail-key="'+esc('req:'+reqKey+':thinking')+'"]');
                if(thinkingDetails){
                    attachPlainTriangleToDetails(thinkingDetails, thinkingDetails.querySelector('summary'));
                }
                const debugDetails=timeline.querySelector('details[data-detail-key="'+esc('req:'+reqKey+':raw-group')+'"]');
                if(debugDetails){
                    attachPlainTriangleToDetails(debugDetails, debugDetails.querySelector('summary'));
                }
                wrap.appendChild(timeline);
            }
        }

        d.appendChild(wrap);
        return d;
    }

  const b=document.createElement('div');
    const isQuickAnswer=(m&&m.source)==='quick_answer';
    b.className='max-w-xs sm:max-w-sm lg:max-w-md px-4 py-2 rounded-2xl text-sm leading-relaxed '+
        (role==='user'?'bg-blue-700 text-white rounded-br-md':
         role==='system'?'bg-gray-700 text-gray-300 italic text-xs':
     (isQuickAnswer?'bg-gray-600 border-2':'bg-gray-700')+' text-gray-100 rounded-bl-md');
    if(isQuickAnswer) b.style.borderColor='#15803d';
    if(role==='assistant'){
        if(!isQuickAnswer) b.classList.add('relative','pr-12');
        const reqKey=String((m&&m.request_id)!==undefined && (m&&m.request_id)!==null ? m.request_id : 'na');
        const summaryText=String((m&&m.text)||'');
        const fullText=String((m&&m.full_text)||'').trim();
        const hasExpandableFullText=!!fullText && fullText!==summaryText.trim();

        const summaryBody=document.createElement('div');
        renderAssistantContent(summaryBody, summaryText);

        if(hasExpandableFullText){
            const expandDetails=document.createElement('details');
            expandDetails.setAttribute('data-detail-key', esc('req:'+reqKey+':full-response'));
            const expandSummaryEl=document.createElement('summary');
            expandSummaryEl.className='list-none flex items-start gap-1.5 cursor-pointer';
            expandSummaryEl.appendChild(summaryBody);
            const fullBody=document.createElement('div');
            fullBody.className='mt-2 pt-2 border-t border-gray-600/50 text-sm leading-relaxed text-gray-200';
            renderAssistantContent(fullBody, fullText);
            expandDetails.appendChild(expandSummaryEl);
            expandDetails.appendChild(fullBody);
            attachPlainTriangleToDetails(expandDetails, expandSummaryEl);
            b.appendChild(expandDetails);
        } else {
            b.appendChild(summaryBody);
        }

const referencedFiles=(Array.isArray(m.referenced_files)?m.referenced_files:m.written_files)?.filter(fp=>String(fp||'').trim()) || [];
        if(referencedFiles.length){
            const filesDiv=document.createElement('div');
            filesDiv.className='mt-2 flex flex-wrap gap-1';
            for(const fp of referencedFiles){
                const normalizedFp = normalizeFilesPath(fp);
                if(!normalizedFp) continue;
                // Check if file exists before showing chip
                const href = buildFilesRouteHref(normalizedFp);
                if(!href) continue;  // Skip if path normalization failed
                const fname=normalizedFp.split('/').filter(Boolean).pop()||normalizedFp;
                const link=document.createElement('a');
                link.className='inline-flex items-center gap-1 px-2 py-0.5 rounded text-xs bg-gray-800 border border-gray-600 hover:bg-gray-700 text-blue-300 hover:text-blue-200 transition-colors';
                link.setAttribute('data-action','open-in-files');
                link.setAttribute('data-file-path',normalizedFp);
                link.href=href;
                link.title=normalizedFp;
                link.textContent='\uD83D\uDCC4 '+fname;
                filesDiv.appendChild(link);
            }
            if(filesDiv.children.length>0) b.appendChild(filesDiv);
        }

        if(!isQuickAnswer){
            b.appendChild(makeResponseCopyButton(()=>String((m&&m.text)||'')));
        }
    }else if(role==='user'){
        const textWrap=document.createElement('div');
        textWrap.textContent=m.text||'';
        b.appendChild(textWrap);
        const actions=document.createElement('div');
        actions.className='mt-2 flex justify-end';
        actions.appendChild(makeUserMessageCopyButton(m.text||''));
        b.appendChild(actions);
    }else{
        b.textContent=m.text||'';
    }
    d.appendChild(b); return d;
}

