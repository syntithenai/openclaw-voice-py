function upsertTaskById(list, byId, task){
    if(!task || typeof task!=='object') return;
    const id=String(task.task_id||task.run_id||task.id||'').trim();
    if(!id) return;
    const next=Object.assign({}, byId[id]||{}, task, {task_id:id});
    byId[id]=next;
    const idx=list.findIndex((entry)=>String((entry&&entry.task_id)||'')===id);
    if(idx>=0) list[idx]=next;
    else list.push(next);
}

function appendSandboxTaskLog(msg){
    const taskId=String((msg&&msg.task_id)||'').trim();
    if(!taskId) return;
    if(!S.sandboxTaskLogEntriesById[taskId]) S.sandboxTaskLogEntriesById[taskId]=[];
    const entry={
        seq:Number(msg.seq||0),
        stream:String(msg.stream||'stdout'),
        lines:Array.isArray(msg.lines)?msg.lines:[String(msg.lines||'')],
    };
    const bucket=S.sandboxTaskLogEntriesById[taskId];
    const seqKey=Number(entry.seq||0);
    if(seqKey>0){
        const dupIdx=bucket.findIndex((item)=>Number((item&&item.seq)||0)===seqKey && String((item&&item.stream)||'')===entry.stream);
        if(dupIdx>=0) bucket[dupIdx]=entry;
        else bucket.push(entry);
        bucket.sort((a,b)=>Number((a&&a.seq)||0)-Number((b&&b.seq)||0));
    }else{
        bucket.push(entry);
    }
    if(S.sandboxTaskLogEntriesById[taskId].length>400) S.sandboxTaskLogEntriesById[taskId]=S.sandboxTaskLogEntriesById[taskId].slice(-400);
}

function appendSubagentThinking(msg){
    const runId=String((msg&&msg.run_id)||'').trim();
    if(!runId) return;
    if(!S.subagentThinkingEntriesById[runId]) S.subagentThinkingEntriesById[runId]=[];
    const entry={seq:Number(msg.seq||0), text_delta:String(msg.text_delta||'')};
    const bucket=S.subagentThinkingEntriesById[runId];
    const seqKey=Number(entry.seq||0);
    if(seqKey>0){
        const dupIdx=bucket.findIndex((item)=>Number((item&&item.seq)||0)===seqKey);
        if(dupIdx>=0) bucket[dupIdx]=entry;
        else bucket.push(entry);
        bucket.sort((a,b)=>Number((a&&a.seq)||0)-Number((b&&b.seq)||0));
    }else{
        bucket.push(entry);
    }
    if(S.subagentThinkingEntriesById[runId].length>400) S.subagentThinkingEntriesById[runId]=S.subagentThinkingEntriesById[runId].slice(-400);
}

function acceptChatFrame(msg){
    if(!msg || typeof msg!=='object') return true;
    const rid = String(msg.request_id||'').trim();
    if(!rid) return true;
    const seq = Number(msg.stream_seq||0);
    const generation = Number(msg.request_generation||0);
    if(!S.chatStreamCursorByRequest || typeof S.chatStreamCursorByRequest!=='object') S.chatStreamCursorByRequest = {};
    const prev = S.chatStreamCursorByRequest[rid] || {generation:0, seq:0};
    const next = {generation:Number(prev.generation||0), seq:Number(prev.seq||0)};

    if(Number.isFinite(generation) && generation > 0){
        if(next.generation > 0 && generation < next.generation) return false;
        if(generation > next.generation){
            next.generation = generation;
            next.seq = 0;
        }
    }

    if(Number.isFinite(seq) && seq > 0){
        if(next.seq > 0 && seq > (next.seq + 1)){
            const pending = !!(S.chatReconcilePendingByRequest && S.chatReconcilePendingByRequest[rid]);
            if(!pending && typeof sendAction==='function'){
                S.chatReconcilePendingByRequest[rid] = true;
                sendAction({type:'chat_request_reconcile', request_id:rid, last_seq:next.seq});
            }
            return false;
        }
        if(seq <= next.seq) return false;
        next.seq = seq;
    }
    S.chatStreamCursorByRequest[rid] = next;
    return true;
}

function applyRecoveredChatMessages(messages){
    if(!Array.isArray(messages)) return;
    messages.forEach((raw)=>{
        const msg = normalizeChatMessage(raw);
        if(!msg || !acceptChatFrame(msg)) return;
        const idx = S.chat.findIndex((m)=>String((m&&m.id)||'')===String(msg.id||''));
        if(idx>=0) S.chat[idx]=msg;
        else S.chat.push(msg);
    });
    persistChatCache();
    if(S.page==='home') renderChatMessages('active');
}

function requestSelectedChatThreadLoad(force){
    const tid=String(S.selectedChatId||'active').trim();
    if(!tid || tid==='active') return false;
    const thread=(S.chatThreads||[]).find((item)=>String((item&&item.id)||'').trim()===tid);
    if(!thread) return false;
    if(!force){
        const activeTid=String(S.activeChatThreadId||'').trim();
        const hasMessages=Array.isArray(thread.messages) && thread.messages.length>0;
        if(activeTid===tid && hasMessages) return false;
        if(String(S.chatThreadLoadPendingId||'').trim()===tid) return false;
    }
    S.chatThreadLoadPendingId=tid;
    return sendAction({type:'chat_select', thread_id: tid});
}

function handleMsg(msg){
  // Debug logging for music actions
  if(msg.type && msg.type.startsWith('music_')) console.log(`📥 Received message type="${msg.type}"`, {queue_len: Array.isArray(msg.music_queue) ? msg.music_queue.length : 'N/A', rev: msg.music_rev, action_ack: msg.action_ack});
  
  // Timing instrumentation for large playlists
  const isLargePlaylist = Array.isArray(msg.music_queue) && msg.music_queue.length > 50;
  if (isLargePlaylist) console.time('🎵 music_queue render');
  
  switch(msg.type){
        case 'hello':
                if(typeof syncBrowserAudioLeaseState==='function') syncBrowserAudioLeaseState();
                break;
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
    if(Array.isArray(msg.recordings)){
        S.recordings = msg.recordings;
        const alive = new Set((S.recordings||[]).map(item=>String((item&&item.id)||'')));
        Object.keys(S.recordingsSelectionByIds||{}).forEach((id)=>{ if(!alive.has(id)) delete S.recordingsSelectionByIds[id]; });
        if(S.recordingsDetail && !alive.has(String((S.recordingsDetail&&S.recordingsDetail.id)||''))){
            S.recordingsDetail = null;
            S.recordingsDetailLoading = false;
        }
    }
    if(msg.recordings_rev!==undefined) S.lastRecordingsRev=Math.max(S.lastRecordingsRev, Number(msg.recordings_rev)||0);
    if(Array.isArray(msg.timers)) applyTimers(msg.timers);
    if(msg.timers_rev!==undefined) S.lastTimersRev=Math.max(S.lastTimersRev, Number(msg.timers_rev)||0);
    if(Array.isArray(msg.sandbox_tasks)){
        S.sandboxTasks=[];
        S.sandboxTaskById={};
        msg.sandbox_tasks.forEach((task)=>upsertTaskById(S.sandboxTasks, S.sandboxTaskById, task));
    }
    if(Array.isArray(msg.subagent_tasks)){
        S.subagentTasks=[];
        S.subagentTaskById={};
        msg.subagent_tasks.forEach((task)=>upsertTaskById(S.subagentTasks, S.subagentTaskById, task));
    }
            applyServerChatState(msg.chat, msg.chat_threads, msg.active_chat_id, msg.active_chat_thread_id);
            if(!S.chatThreadBootstrapRequested){
                S.chatThreadBootstrapRequested=true;
                requestSelectedChatThreadLoad(true);
            }
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
                applyServerChatState(undefined, msg.chat_threads, msg.active_chat_id, msg.active_chat_thread_id);
                const nextMsg = normalizeChatMessage(msg.message);
                if(nextMsg && !acceptChatFrame(nextMsg)) break;
                let suppressReloadUserEcho=false;
                const reloadRun=S.chatReloadInFlight;
                if(nextMsg && nextMsg.role==='user' && reloadRun && !reloadRun.userEchoSuppressed){
                    const incomingText=String(nextMsg.text||'').trim();
                    const reloadText=String(reloadRun.text||'').trim();
                    if(incomingText && incomingText===reloadText){
                        suppressReloadUserEcho=true;
                        reloadRun.userEchoSuppressed=true;
                    }
                }
                if(nextMsg && !suppressReloadUserEcho) S.chat.push(nextMsg);
                if(nextMsg && nextMsg.role==='assistant'){
                    S.chatReloadInFlight=null;
                }
                if(nextMsg && nextMsg.role==='user' && !suppressReloadUserEcho) requestScrollToBottomBurst();
                if(nextMsg && nextMsg.role==='user' && !suppressReloadUserEcho && queueOptimisticTimerFromText(String(nextMsg.text||''), 'chat_append')){
                    renderTimerBar();
                }
                persistChatCache();
                // Show toast notification on non-home pages
                if(nextMsg && !suppressReloadUserEcho){
                    const toastText = nextMsg.role === 'assistant'
                        ? String(nextMsg.tts_text || nextMsg.text || '')
                        : String(nextMsg.text || '');
                    showMessageToast(toastText, nextMsg.role);
                }
                if(S.page==='home'){
                    renderThreadList('active');
                    const segKind=String((nextMsg&&nextMsg.segment_kind)||'final').toLowerCase();
                    const isAssistantStream=!!nextMsg && nextMsg.role==='assistant' && segKind==='stream';
                    const isRawGateway=!!nextMsg && nextMsg.role==='raw_gateway';
                    const isTransientContext=!!nextMsg && (nextMsg.role==='step' || nextMsg.role==='interim' || nextMsg.role==='raw_gateway');
                    const patchedByRole=!!nextMsg && !isAssistantStream && !isTransientContext
                        && typeof upsertActiveChatBubbleInPlace==='function'
                        && upsertActiveChatBubbleInPlace(nextMsg, 'append');
                    if(isAssistantStream && typeof scheduleAssistantStreamBubbleUpdate==='function'){
                        scheduleAssistantStreamBubbleUpdate('active', nextMsg);
                    }else if(isRawGateway && typeof scheduleGatewayDebugBubbleUpdate==='function'){
                        scheduleGatewayDebugBubbleUpdate(nextMsg.request_id, 'active');
                    }else if(isTransientContext && typeof isAssistantStreamBubbleActive==='function' && isAssistantStreamBubbleActive()){
                        // Defer expensive grouped-context rerenders while stream bubble is active.
                    }else if(typeof scheduleChatMessagesRender==='function'){
                        // Also re-render when a final assistant message is patched in-place so the
                        // context_group thinking spinner is updated (hasFinal→true clears waiting).
                        const isFinalAssistant=!!nextMsg && nextMsg.role==='assistant' && !isAssistantStream;
                        if(!patchedByRole || isFinalAssistant) scheduleChatMessagesRender('active');
                    }else{
                        const isFinalAssistant=!!nextMsg && nextMsg.role==='assistant' && !isAssistantStream;
                        if(!patchedByRole || isFinalAssistant) renderChatMessages('active');
                    }
                }
            }
            if(typeof processQueuedChatDispatch==='function') processQueuedChatDispatch();
            break;
        case 'sandbox_exec_update':
            if(msg.task){
                upsertTaskById(S.sandboxTasks, S.sandboxTaskById, msg.task);
                renderTimerBar();
                if(S.page==='home'){
                    if(typeof scheduleChatMessagesRender==='function') scheduleChatMessagesRender(S.selectedChatId||'active');
                    else renderChatMessages(S.selectedChatId||'active');
                }
            }
            break;
        case 'sandbox_exec_log_append':
            appendSandboxTaskLog(msg);
            if(S.sandboxTaskPanelOpen && String(S.sandboxTaskPanelId||'')===String(msg.task_id||'')) renderTimerBar();
            if(S.page==='home'){
                if(typeof scheduleChatMessagesRender==='function') scheduleChatMessagesRender(S.selectedChatId||'active');
                else renderChatMessages(S.selectedChatId||'active');
            }
            break;
        case 'sandbox_task_logs':
            if(msg.task_id){
                S.sandboxTaskLogEntriesById[String(msg.task_id)] = Array.isArray(msg.entries)?msg.entries:[];
                if(S.sandboxTaskPanelOpen && String(S.sandboxTaskPanelId||'')===String(msg.task_id||'')) renderTimerBar();
                if(S.page==='home'){
                    if(typeof scheduleChatMessagesRender==='function') scheduleChatMessagesRender(S.selectedChatId||'active');
                    else renderChatMessages(S.selectedChatId||'active');
                }
            }
            break;
        case 'subagent_task_update':
            if(msg.task){
                upsertTaskById(S.subagentTasks, S.subagentTaskById, msg.task);
                renderTimerBar();
                if(S.page==='home'){
                    if(typeof scheduleChatMessagesRender==='function') scheduleChatMessagesRender(S.selectedChatId||'active');
                    else renderChatMessages(S.selectedChatId||'active');
                }
            }
            break;
        case 'subagent_thinking_append':
            appendSubagentThinking(msg);
            if(S.subagentTaskPanelOpen && String(S.subagentTaskPanelId||'')===String(msg.run_id||'')) renderTimerBar();
            if(S.page==='home'){
                if(typeof scheduleChatMessagesRender==='function') scheduleChatMessagesRender(S.selectedChatId||'active');
                else renderChatMessages(S.selectedChatId||'active');
            }
            break;
        case 'subagent_task_thinking':
            if(msg.run_id){
                S.subagentThinkingEntriesById[String(msg.run_id)] = Array.isArray(msg.entries)?msg.entries:[];
                if(S.subagentTaskPanelOpen && String(S.subagentTaskPanelId||'')===String(msg.run_id||'')) renderTimerBar();
                if(S.page==='home'){
                    if(typeof scheduleChatMessagesRender==='function') scheduleChatMessagesRender(S.selectedChatId||'active');
                    else renderChatMessages(S.selectedChatId||'active');
                }
            }
            break;
        case 'subagent_task_terminal':
            if(msg.run_id){
                const runId=String(msg.run_id);
                const task=S.subagentTaskById[runId]||{run_id:runId,task_id:runId};
                upsertTaskById(S.subagentTasks, S.subagentTaskById, Object.assign({}, task, {status:String(msg.status||'completed'), summary:String(msg.summary||''), error_summary:String(msg.error||'')}));
                renderTimerBar();
                if(S.page==='home'){
                    if(typeof scheduleChatMessagesRender==='function') scheduleChatMessagesRender(S.selectedChatId||'active');
                    else renderChatMessages(S.selectedChatId||'active');
                }
            }
            break;

        case 'chat_update':
            if(msg.message){
                applyServerChatState(undefined, msg.chat_threads, msg.active_chat_id, msg.active_chat_thread_id);
                const updatedMsg = normalizeChatMessage(msg.message);
                if(updatedMsg && !acceptChatFrame(updatedMsg)) break;
                if(updatedMsg && updatedMsg.id){
                    // Find and replace message with same ID
                    const idx = S.chat.findIndex(m => m.id === updatedMsg.id);
                    if(idx >= 0){
                        S.chat[idx] = updatedMsg;
                        // Show toast notification on non-home pages
                        const toastText = updatedMsg.role === 'assistant'
                            ? String(updatedMsg.tts_text || updatedMsg.text || '')
                            : String(updatedMsg.text || '');
                        showMessageToast(toastText, updatedMsg.role);
                        if(S.page==='home'){
                            const segKind=String((updatedMsg.segment_kind||'final')).toLowerCase();
                            const isAssistantStream=updatedMsg.role==='assistant' && segKind==='stream';
                            const isRawGateway=updatedMsg.role==='raw_gateway';
                            const isTransientContext=(updatedMsg.role==='step' || updatedMsg.role==='interim' || updatedMsg.role==='raw_gateway');
                            const patchedByRole=!isAssistantStream && !isTransientContext
                                && typeof upsertActiveChatBubbleInPlace==='function'
                                && upsertActiveChatBubbleInPlace(updatedMsg, 'update');
                            if(isAssistantStream && typeof scheduleAssistantStreamBubbleUpdate==='function'){
                                scheduleAssistantStreamBubbleUpdate('active', updatedMsg);
                            }else if(isRawGateway && typeof scheduleGatewayDebugBubbleUpdate==='function'){
                                scheduleGatewayDebugBubbleUpdate(updatedMsg.request_id, 'active');
                            }else if(isTransientContext && typeof isAssistantStreamBubbleActive==='function' && isAssistantStreamBubbleActive()){
                                // Defer expensive grouped-context rerenders while stream bubble is active.
                            }else if(typeof scheduleChatMessagesRender==='function'){
                                const isFinalAssistant=updatedMsg.role==='assistant' && !isAssistantStream;
                                if(!patchedByRole || isFinalAssistant) scheduleChatMessagesRender('active');
                            }else{
                                const isFinalAssistant=updatedMsg.role==='assistant' && !isAssistantStream;
                                if(!patchedByRole || isFinalAssistant) renderChatMessages('active');
                            }
                        }
                    }
                }
                persistChatCache();
            }
            if(typeof processQueuedChatDispatch==='function') processQueuedChatDispatch();
            break;

        case 'chat_threads_update':
            applyServerChatState(undefined, msg.chat_threads, msg.active_chat_id, msg.active_chat_thread_id);
            requestSelectedChatThreadLoad(false);
            if(S.page==='home') renderPage();
            break;
        case 'chat_reset':
            S.chat=[];
            applyServerChatState(
                Array.isArray(msg.chat) ? msg.chat : [],
                msg.chat_threads,
                msg.active_chat_id,
                msg.active_chat_thread_id,
            );
            {
                const pendingTid=String(S.chatThreadLoadPendingId||'').trim();
                const activeTid=String(S.activeChatThreadId||'').trim();
                if(pendingTid && activeTid && pendingTid===activeTid) S.chatThreadLoadPendingId='';
            }
            persistChatCache();
            if(S.page==='home') renderPage();
            break;
        case 'chat_text_ack':
            if(msg.client_msg_id) S.pendingChatSends.delete(String(msg.client_msg_id));
            if(msg.ok===false) S.chatReloadInFlight=null;
            if(msg.ok===false) S.chatStopPending=false;
            if(S.page==='home') updateChatComposerState();
            if(typeof processQueuedChatDispatch==='function') processQueuedChatDispatch();
            break;
        case 'chat_stop_ack':
            if(S.chatStopTimer){
                clearTimeout(S.chatStopTimer);
                S.chatStopTimer = null;
            }
            S.chatStopPending = false;
            S.chatQueuedItems = [];
            if(S.chatActiveRequestId){
                const rid = String(S.chatActiveRequestId||'').trim();
                if(rid){
                    if(!S.chatTerminalStateByRequest || typeof S.chatTerminalStateByRequest!=='object') S.chatTerminalStateByRequest = {};
                    S.chatTerminalStateByRequest[rid] = {state:'superseded', reason:'Stopped by user', ts:Date.now(), source:'chat_stop_ack'};
                }
            }
            updateChatComposerState();
            break;
        case 'chat_stop_error':
            if(S.chatStopTimer){
                clearTimeout(S.chatStopTimer);
                S.chatStopTimer = null;
            }
            S.chatStopPending = false;
            if(S.chatActiveRequestId){
                const rid = String(S.chatActiveRequestId||'').trim();
                if(rid && S.chatTerminalStateByRequest && typeof S.chatTerminalStateByRequest==='object') delete S.chatTerminalStateByRequest[rid];
            }
            recordInlineError('setting', 'chat_stop', String(msg.error||'Failed to stop chat'));
            updateChatComposerState();
            break;
        case 'chat_queue_update':
            if(Array.isArray(msg.items)) S.chatQueuedItems = msg.items.slice();
            updateChatComposerState();
            break;
        case 'chat_steer_ack':
            if(msg.action_id && S.pendingChatSteerNowByAction) delete S.pendingChatSteerNowByAction[String(msg.action_id)];
            updateChatComposerState();
            break;
        case 'chat_steer_error': {
            const actionId = String(msg.action_id||'').trim();
            const pending = actionId && S.pendingChatSteerNowByAction ? S.pendingChatSteerNowByAction[actionId] : null;
            if(actionId && S.pendingChatSteerNowByAction) delete S.pendingChatSteerNowByAction[actionId];
            if(pending && String(pending.text||'').trim()){
                if(!Array.isArray(S.chatQueuedItems)) S.chatQueuedItems=[];
                S.chatQueuedItems.unshift({
                    id: String(pending.queueId||('q' + (S.chatQueueSeq++))),
                    text: String(pending.text||'').trim(),
                    mode: 'steer',
                    createdTs: Number(pending.ts||Date.now()),
                });
            }
            recordInlineError('setting', 'chat_steer_now', String(msg.error||'Failed to steer now'));
            updateChatComposerState();
            break;
        }
        case 'chat_run_state': {
            const state = String(msg.state||'').trim().toLowerCase();
            const rid = String(msg.request_id||'').trim();
            const reason = String(msg.reason||'').trim();
            if(rid) S.chatActiveRequestId = rid;
            if(rid && state==='streaming' && S.chatTerminalStateByRequest && typeof S.chatTerminalStateByRequest==='object'){
                delete S.chatTerminalStateByRequest[rid];
            }
            if(state){
                if(state==='in_progress') S.chatRunState='streaming';
                else S.chatRunState=state;
            }
            if(state==='streaming' || state==='in_progress' || state==='waiting_tool'){
                if(!Number(S.chatLastActivityTs||0)) S.chatLastActivityTs = Date.now();
            }
            if(reason) S.chatStatusText = reason;
            if(state==='completed' || state==='failed' || state==='superseded' || state==='cancelled'){
                S.chatStopPending = false;
                if(rid){
                    if(!S.chatTerminalStateByRequest || typeof S.chatTerminalStateByRequest!=='object') S.chatTerminalStateByRequest = {};
                    S.chatTerminalStateByRequest[rid] = {state, reason, ts:Date.now(), source:'chat_run_state'};
                }
            }
            if(S.page==='home') updateChatComposerState();
            break;
        }
        case 'chat_reconcile_snapshot':
            if(msg.request_id && S.chatReconcilePendingByRequest) delete S.chatReconcilePendingByRequest[String(msg.request_id)];
            applyRecoveredChatMessages(msg.messages);
            break;
        case 'chat_stream_replay':
            applyRecoveredChatMessages(msg.messages);
            break;
        case 'navigate':
            if(msg.page==='music' || msg.page==='home' || msg.page==='recordings'){
                navigate(msg.page);
            }
            break;
        case 'recordings_state':
            if(msg.recordings_rev!==undefined){
                const rev=Number(msg.recordings_rev)||0;
                if(rev<=S.lastRecordingsRev) break;
                S.lastRecordingsRev=rev;
            }
            if(Array.isArray(msg.recordings)){
                S.recordings = msg.recordings;
                const alive = new Set((S.recordings||[]).map(item=>String((item&&item.id)||'')));
                Object.keys(S.recordingsSelectionByIds||{}).forEach((id)=>{ if(!alive.has(id)) delete S.recordingsSelectionByIds[id]; });
                if(S.recordingsDetail && !alive.has(String((S.recordingsDetail&&S.recordingsDetail.id)||''))){
                    S.recordingsDetail = null;
                    S.recordingsDetailLoading = false;
                }
            }
            if(S.page==='recordings') renderRecordingsPage(document.getElementById('main'));
            break;
        case 'recording_detail':
            S.recordingsDetailLoading = false;
            if(msg.error){
                S.recordingsActionError = String(msg.error||'Failed to load recording');
            } else {
                S.recordingsActionError = '';
                S.recordingsDetail = msg.recording || null;
            }
            if(S.page==='recordings') renderRecordingsPage(document.getElementById('main'));
            break;
        case 'recordings_action_ack':
            S.recordingsDeletePending = false;
            S.recordingsActionError = '';
            if(S.page==='recordings') renderRecordingsPage(document.getElementById('main'));
            break;
        case 'recordings_action_error':
            S.recordingsDeletePending = false;
            S.recordingsActionError = String(msg.error||'Recordings action failed');
            if(S.page==='recordings') renderRecordingsPage(document.getElementById('main'));
            break;
        case 'recorder_start_ack':
            S.recorderStartPending = false;
            S.recordingsActionError = '';
            if(S.page==='recordings') renderRecordingsPage(document.getElementById('main'));
            break;
        case 'recorder_start_error':
            S.recorderStartPending = false;
            S.recordingsActionError = String(msg.error||'Failed to start recording');
            if(S.page==='recordings') renderRecordingsPage(document.getElementById('main'));
            break;
        case 'recorder_stop_ack':
            S.recorderStopPending = false;
            S.recordingsActionError = '';
            if(S.page==='recordings') renderRecordingsPage(document.getElementById('main'));
            break;
        case 'recorder_stop_error':
            S.recorderStopPending = false;
            S.recordingsActionError = String(msg.error||'Failed to stop recording');
            if(S.page==='recordings') renderRecordingsPage(document.getElementById('main'));
            break;
        case 'music_transport':
            if(msg.music_rev!==undefined){
                const rev=Number(msg.music_rev)||0;
                if(rev<=S.lastMusicRev) break;
                S.lastMusicRev=rev;
            }
            if(S._musicStateRetryTimer){
                clearTimeout(S._musicStateRetryTimer);
                S._musicStateRetryTimer = null;
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
                reconcilePendingMusicLoads();
            }
            if(S.page==='music') {
                const t0 = performance.now();
                renderMusicPage(document.getElementById('main'));
                const elapsed = performance.now() - t0;
                if(msg.queue && msg.queue.length > 50){
                    console.timeEnd('🎵 music_queue render');
                    console.log(`  → renderMusicPage: ${elapsed.toFixed(1)}ms for ${msg.queue.length} items`);
                }
            }
            applyMusicHeader();
            break;
        case 'music_state':
            if(msg.music_rev!==undefined){
                const rev=Number(msg.music_rev)||0;
                if(rev<=S.lastMusicRev) break;
                S.lastMusicRev=rev;
            }
            if(S._musicStateRetryTimer){
                clearTimeout(S._musicStateRetryTimer);
                S._musicStateRetryTimer = null;
            }
            applyMusic(msg.music||msg);
            if(msg.queue!==undefined) S.musicQueue=msg.queue;
            else if(msg.music&&msg.music.queue!==undefined) S.musicQueue=msg.music.queue;
            syncMusicFromQueue();
            reconcilePendingMusicLoads();
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
            if(isMusicLoadActionType(msg.action)){
                // Poll for state updates after a playlist load. The server will push a
                // music_queue broadcast once the queue is ready, but we also poll as
                // belt-and-suspenders in case the broadcast is missed.
                requestMusicStateRetry('post_load_ack', 8, 2000);
            }
            if(['music_save_playlist','music_create_playlist','music_delete_playlist','music_rename_playlist','music_save_queue_then_clear_queue','music_save_queue_then_load_playlist'].includes(String(msg.action||''))){
                sendAction({type:'music_list_playlists'});
            }
            if(S.page==='music') renderMusicPage(document.getElementById('main'));
            applyMusicHeader();
            break;
        case 'music_action_pending':
            if(msg.action_id){
                const pendingItem={
                    type:String(msg.action||''),
                    ts:Date.now(),
                };
                if(msg.name!==undefined) pendingItem.name=String(msg.name||'');
                S.pendingMusicActions[String(msg.action_id)]=pendingItem;
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
        case 'music_genre_cloud':
            S.musicGenreCloudPending = false;
            if(Array.isArray(msg.genres)){
                S.musicGenreCloud = msg.genres;
            }
            if(S.page==='music' && S.musicAddMode) renderMusicPage(document.getElementById('main'));
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
  const prevWake = S.wake_state;
  const prevMicEnabled = S.micEnabled;
  if(o.voice_state!==undefined) S.voice_state=o.voice_state;
  if(o.wake_state!==undefined)  S.wake_state=o.wake_state;
  if(o.hotword_active!==undefined) S.hotword_active=!!o.hotword_active;
  if(o.tts_playing!==undefined) S.tts_playing=!!o.tts_playing;
  if(o.mic_rms!==undefined)     S.mic_rms=Number(o.mic_rms)||0;
  if(o.mic_enabled!==undefined) S.micEnabled=!!o.mic_enabled;
  if(o.recorder_active!==undefined) {
    const wasActive = S.recorderActive;
    S.recorderActive=!!o.recorder_active;
    if(S.recorderActive && !wasActive) { S.recorderStartPending=false; }
    if(!S.recorderActive && wasActive) { S.recorderStopPending=false; }
    if(S.recorderActive !== wasActive && S.page==='recordings') renderRecordingsPage(document.getElementById('main'));
  }
  if(S.wake_state !== prevWake || S.micEnabled !== prevMicEnabled){
    console.log('[orch] wake_state:', prevWake, '→', S.wake_state, '| micEnabled:', prevMicEnabled, '→', S.micEnabled, '| voice='+S.voice_state);
  }
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
    if(current.file){
        const hasBrowserOverride = String(S.music.file||'').includes('/.openclaw-transcoded/');
        if(!(S.browserAudioEnabled && hasBrowserOverride)) S.music.file = current.file;
    }
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
    reconcilePendingMusicLoads();
    syncMusicFromQueue();
    syncBrowserMusicPlayback();
    applyTopMusicProgress();
    applyMusicHeader();
    applyMusicQueueHighlight();
}

function _ensureBrowserMusicAudio(){
    if(S._browserMusicAudio) return S._browserMusicAudio;
    const audio = new Audio();
    audio.preload = 'metadata';
    audio.crossOrigin = 'anonymous';
    S._browserMusicAudio = audio;
    return audio;
}

function _mediaUrlForFile(filePath){
    const raw = String(filePath||'').trim();
    if(!raw) return '';
    const parts = raw.split('/').map(p=>encodeURIComponent(p));
    return '/files/media/' + parts.join('/');
}

function syncBrowserMusicPlayback(){
    try{
        const browserEnabled = !!S.browserAudioEnabled;
        const music = S.music || {};
        const state = normalizeMusicState(music.state);
        const filePath = String(music.file||'').trim();
        const audio = _ensureBrowserMusicAudio();

        if(!browserEnabled){
            if(!audio.paused) audio.pause();
            return;
        }

        if(!filePath){
            if(!audio.paused) audio.pause();
            return;
        }

        const src = _mediaUrlForFile(filePath);
        if(src && audio.dataset.currentSrc !== src){
            audio.src = src;
            audio.dataset.currentSrc = src;
        }

        if(state === 'play'){
            const playPromise = audio.play();
            if(playPromise && typeof playPromise.catch === 'function') playPromise.catch(()=>{});
        } else if(state === 'pause'){
            if(!audio.paused) audio.pause();
        } else {
            if(!audio.paused) audio.pause();
            try { audio.currentTime = 0; } catch {}
        }
    }catch {}
}

function reconcilePendingMusicLoads(){
    const pending=S.pendingMusicActions||{};
    const currentLoaded=String((S.music&&S.music.loaded_playlist)||'').trim().toLowerCase();
    const queueLen=getMusicQueueLength();
    let changed=false;
    Object.keys(pending).forEach((actionId)=>{
        const item=pending[actionId];
        if(!item || !isMusicLoadActionType(item.type)) return;
        const requested=String(item.name||'').trim().toLowerCase();
        if(requested && currentLoaded && requested===currentLoaded){
            delete pending[actionId];
            changed=true;
            return;
        }
        if(!requested && currentLoaded && queueLen>0){
            delete pending[actionId];
            changed=true;
        }
    });
    if(changed){
        S.musicActionError='';
        S.musicActionErrorTs=0;
    }
    return changed;
}
function applyTimers(t){
  const now=Date.now()/1000;
    const serverTimers=(Array.isArray(t)?t:[])
        .filter(timer=>{
            if(!timer||typeof timer!=='object') return false;
            const kind=String(timer.kind||'timer').toLowerCase();
            const rem=Number(timer.remaining_seconds);
            if(!Number.isFinite(rem)) return false;
            return true;
        })
        .map(timer=>Object.assign({},timer,{_clientAnchorTs:now, _clientAnchorRem:Number(timer.remaining_seconds)||0}));

    const optimistic=Object.assign({}, S.optimisticTimers||{});
    const optimisticList=[];
    Object.keys(optimistic).forEach((id)=>{
        const it=optimistic[id];
        if(!it) return;
        const pendingServerAck=!!it.pendingServerAck;
        const ageSec=Math.max(0, (Date.now()-Number(it.createdAtMs||Date.now()))/1000);
        const expectedRem=pendingServerAck
            ? Math.max(0, Number(it.durationSeconds)||0)
            : Math.max(0, Number(it.durationSeconds||0)-ageSec);
        if((!pendingServerAck && expectedRem<=0) || ageSec>20){
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
            _pendingServerAck:pendingServerAck,
            _clientAnchorTs:pendingServerAck?null:now,
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
        if (S.captureWorkletModuleReady && S.captureWorkletCtx === ctx) return true;
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
                S.captureWorkletCtx = ctx;
                return true;
        } finally {
                URL.revokeObjectURL(url);
        }
    }

    async function startBrowserCapture(){
  if(!S.browserAudioEnabled){
      if(typeof stopBrowserCapture==='function') await stopBrowserCapture();
      return;
  }
  if(S.captureStartInProgress) return;
  S.captureStartInProgress = true;
  try {
  if(typeof syncBrowserAudioLeaseState==='function') syncBrowserAudioLeaseState();
  if(typeof tryAcquireBrowserAudioLease==='function' && !tryAcquireBrowserAudioLease()){
      if(typeof stopBrowserCapture==='function') await stopBrowserCapture({ releaseLease: false });
      clearCaptureRetry();
      if(typeof applyMicControlToggles==='function') applyMicControlToggles();
      return;
  }
  const hasLiveTrack = !!(S.mediaStream && S.mediaStream.getAudioTracks().some(t=>t.readyState==='live'));
  if (hasLiveTrack && S.processor) {
      if (S.audioCtx && S.audioCtx.state === 'suspended') await S.audioCtx.resume();
      clearCaptureRetry();
      if(typeof applyMicControlToggles==='function') applyMicControlToggles();
      return;
  }

  // Cleanup stale graph if present, then rebuild.
  try{ if(S.processor) S.processor.disconnect(); }catch(_ ){}
  try{ if(S.audioCtx) await S.audioCtx.close(); }catch(_ ){}
  if(S.mediaStream){
      try{ S.mediaStream.getTracks().forEach(t=>t.stop()); }catch(_ ){}
  }
  S.processor=null; S.audioCtx=null; S.mediaStream=null; S.captureWorkletModuleReady=false; S.captureWorkletCtx=null;

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
                    if(typeof currentTabOwnsBrowserAudio==='function' && !currentTabOwnsBrowserAudio()) return;
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
                if(typeof currentTabOwnsBrowserAudio==='function' && !currentTabOwnsBrowserAudio()) return;
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
        if(typeof applyMicControlToggles==='function') applyMicControlToggles();
  } finally {
    S.captureStartInProgress = false;
  }
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
renderAuthButton();
initGoogleSignIn();
S.page=getPage(); renderPage(); updateNavActiveState(); applyMicState(); applyMicControlToggles(); updateWsDebugBanner(); updateMicInteractivity();
window.addEventListener('resize', ()=>{ updateScrollUpButton(); updateScrollDownButton(); }, {passive:true});
if(typeof applyFilesRouteFromHash === 'function') applyFilesRouteFromHash();
if(wsAuthAllowed()) connectWs();
refreshAuthSession({render:false, adjustWs:false}).catch(()=>{});
setupServerRefreshWatcher();
setInterval(()=>{ expirePendingActions(); applyTopMusicProgress(); if(!S.timers.length) return; const now=Date.now()/1000; S.timers.forEach(t=>{ if(t&&t._pendingServerAck) return; if(t._clientAnchorTs===undefined||t._clientAnchorTs===null){ t._clientAnchorTs=now; t._clientAnchorRem=t.remaining_seconds; } t.remaining_seconds=Math.max(0, t._clientAnchorRem-(now-t._clientAnchorTs)); }); renderTimerBar(); },500);
startBrowserCapture().catch((err)=>{
    reportCaptureFailure(err,'startup');
});
