// Virtual scroll manager for large queues
const VirtualScroll = (() => {
  const ROW_HEIGHT = 44; // px: py-3 padding + text
  const BUFFER_ROWS = 5;
  return {
    renderVisibleRows(container, allRows, startIdx=0) {
      const viewportHeight = container.clientHeight - 200; // Account for header/footer
      const visibleCount = Math.ceil(viewportHeight / ROW_HEIGHT);
      const bufferStart = Math.max(0, startIdx - BUFFER_ROWS);
      const bufferEnd = Math.min(allRows.length, startIdx + visibleCount + BUFFER_ROWS);
      
      const spacerTop = bufferStart * ROW_HEIGHT;
      const spacerBottom = (allRows.length - bufferEnd) * ROW_HEIGHT;
      const visibleRowsHtml = allRows.slice(bufferStart, bufferEnd).join('');
      
      return `<tr style="height:${spacerTop}px"><td colspan="6"></td></tr>${visibleRowsHtml}<tr style="height:${spacerBottom}px"><td colspan="6"></td></tr>`;
    },
    getScrollIndex(container, allRows) {
      if (!container.parentElement) return 0;
      const tableContainer = container.closest('.music-queue-table-container');
      if (!tableContainer) return 0;
      const scrollTop = tableContainer.scrollTop;
      return Math.max(0, Math.floor(scrollTop / ROW_HEIGHT));
    }
  };
})();

function renderMusicPage(main){
  main.dataset.page='music';
    const m=S.music, q=S.musicQueue||[];
    m.state=normalizeMusicState(m.state);
    const pendingMusicCount=Object.keys(S.pendingMusicActions||{}).length;
  const qFilter=String(S.musicQueueFilter||'').trim().toLowerCase();
  const playlistFilter=String(S.musicPlaylistFilter||'').trim().toLowerCase();
  const filtered=q.filter(item=>{
    if(!qFilter) return true;
    const hay=[item.title,item.artist,item.album,item.file].map(v=>String(v||'').toLowerCase()).join(' | ');
    return hay.includes(qFilter);
  });
    const selectedCount=Object.keys(S.musicQueueSelectionByIds||{}).filter(k=>S.musicQueueSelectionByIds[k]).length;
  const filteredPlaylists=(S.musicPlaylists||[]).filter(name=>{
    const n=String(name||'').trim();
    if(!n) return false;
    if(!playlistFilter) return true;
    return n.toLowerCase().includes(playlistFilter);
  });
  const playlistRows=filteredPlaylists.map(name=>{
    const n=String(name||'').trim();
    if(!n) return '';
    return '<div class="flex w-full items-center justify-start gap-2 px-3 py-2 border-b border-gray-800 text-left">'
      +'<button data-action="music-load-playlist" data-playlist-name="'+esc(n)+'" class="block flex-1 w-full text-left text-sm text-gray-200 hover:text-white truncate">'+esc(n)+'</button>'
      +'<button data-action="music-open-delete-playlist" data-playlist-name="'+esc(n)+'" class="shrink-0 w-6 h-6 inline-flex items-center justify-center rounded text-sm bg-gray-700 hover:bg-red-700 transition-colors" title="Delete playlist" aria-label="Delete playlist">✕</button>'
    +'</div>';
  }).join('');

  if(S.musicAddMode){
        const canSearch=canSearchMusicLibrary(S.musicAddQuery);
        const searchPending=!!S.musicAddSearchPending;
        const addPending=Object.values(S.pendingMusicActions||{}).some(item=>String((item&&item.type)||'')==='music_add_files');
    const addRows=(S.musicLibraryResults||[]).map(item=>{
      const file=String(item.file||'');
      const checked=!!S.musicAddSelection[file];
      const title=esc(item.title||file.split('/').pop()||'—');
      const titleCell = file
        ? '<button type="button" data-action="music-add-quick-add" data-file="'+esc(file)+'" class="block w-full truncate text-left text-sm text-gray-100 hover:text-blue-400">'+title+'</button>'
        : '<span class="block w-full truncate text-left text-sm text-gray-100">'+title+'</span>';
      return '<tr class="hover:bg-gray-800">'
        +'<td class="px-2 py-2 w-8"><input type="checkbox" data-action="music-add-select" data-file="'+esc(file)+'" '+(checked?'checked':'')+'></td>'
        +'<td class="px-2 py-2 max-w-0">'+titleCell+'</td>'
        +'<td class="px-2 py-2 max-w-0 text-xs text-gray-400 truncate">'+esc(item.artist||'')+'</td>'
        +'<td class="px-2 py-2 max-w-0 text-xs text-gray-500 truncate">'+esc(item.album||'')+'</td>'
      +'</tr>';
    }).join('');
    const addSelectedCount=Object.keys(S.musicAddSelection||{}).filter(k=>S.musicAddSelection[k]).length;
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
                  +'<input id="musicAddSearch" type="search" data-action="music-add-search" value="'+esc(S.musicAddQuery||'')+'" placeholder="Search library by title, artist, album" class="flex-1 rounded-lg bg-gray-800 border border-gray-700 px-3 py-2 text-sm text-gray-100 placeholder-gray-400 focus:outline-none focus:ring-2 focus:ring-blue-600" />'
                                        +'<button id="musicAddSearchSubmit" data-action="music-add-search-submit" class="px-3 py-2 rounded-lg text-sm bg-blue-700 hover:bg-blue-600 transition-colors" '+((canSearch && !searchPending) ? '' : 'disabled style="opacity:.5;cursor:not-allowed"')+'>'+(searchPending?'Searching…':'Search')+'</button>'
                +'</div>'
                                +(canSearch ? '' : '<p id="musicAddMinHint" class="text-xs text-gray-500 mt-1">Enter at least '+MUSIC_LIBRARY_SEARCH_MIN_LEN+' letters to search</p>')
      +'</div>'
      +(addRows
                ? '<div class="px-2 flex items-center justify-end gap-1 text-xs text-gray-400">'
                        +'<button data-action="music-add-select-all" class="px-2 py-1 rounded bg-gray-800 hover:bg-gray-700 transition-colors">Select All</button>'
                        +'<button data-action="music-add-select-none" class="px-2 py-1 rounded bg-gray-800 hover:bg-gray-700 transition-colors">Select None</button>'
                    +'</div>'
                    +'<div class="overflow-x-auto rounded-xl border border-gray-800"><table class="w-full text-left table-fixed"><thead><tr class="text-xs text-gray-400 border-b border-gray-800"><th class="px-2 py-2 w-8">#</th><th class="px-2 py-2 w-1/2">Title</th><th class="px-2 py-2 w-1/4">Artist</th><th class="px-2 py-2 w-1/4">Album</th></tr></thead><tbody>'+addRows+'</tbody></table></div>'
                                : '<p class="text-gray-500 text-center py-10 text-sm">'+(searchPending ? '<span class="inline-flex items-center gap-2"><span class="inline-block w-3 h-3 border-2 border-gray-400 border-t-transparent rounded-full animate-spin"></span>Searching…</span>' : (canSearch && S.musicAddHasSearched ? 'No matches found' : 'Search to find songs to add'))+'</p>')
      +'</div>';
        main.onscroll=()=>{ updateScrollUpButton(); };
        updateScrollUpButton();
    return;
  }

  // Lazily create row HTML only when needed (critical for large queues)
  const createRowHtml = (item) => {
    const active=item.pos===m.position;
    const songId=String(item.id||'').trim();
    const checked=!!S.musicQueueSelectionByIds[songId];
    return '<tr class="hover:bg-gray-800 '+(active?'bg-gray-800 font-semibold text-green-400':'')+'">'
      +'<td class="px-3 py-3 w-12"><input type="checkbox" data-action="music-queue-select" data-position="'+item.pos+'" data-song-id="'+esc(songId)+'" class="w-5 h-5 cursor-pointer" '+(checked?'checked':'')+'></td>'
      +'<td class="px-2 py-2 w-8 text-gray-500 text-xs">'+(item.pos+1)+'</td>'
      +'<td class="px-2 py-2 text-sm truncate max-w-xs cursor-pointer hover:text-blue-400" data-action="music-play-track" data-position="'+item.pos+'">'+esc(item.title||item.file||'—')+'</td>'
      +'<td class="px-2 py-2 text-xs text-gray-400 truncate">'+esc(item.artist||'')+'</td>'
      +'<td class="px-2 py-2 text-xs text-gray-500 truncate">'+esc(item.album||'')+'</td>'
      +'<td class="px-2 py-2 text-xs text-gray-500 text-right pr-4">'+fmtDur(item.duration)+'</td>'
    +'</tr>';
  };

  // For large queues, render only visible rows initially
  let rows;
  if (filtered.length > 50) {
    // Create only visible rows (plus buffer) for initial render
    const VISIBLE_ROWS = 15;
    const BUFFER_ROWS = 5;
    const startIdx = 0;
    const endIdx = Math.min(filtered.length, startIdx + VISIBLE_ROWS + BUFFER_ROWS * 2);
    const visibleItems = filtered.slice(startIdx, endIdx);
    rows = visibleItems.map(createRowHtml).join('');
    // Add spacers for scrolled-out rows
    const spacerBottomCount = Math.max(0, filtered.length - endIdx);
    if (spacerBottomCount > 0) {
      rows += '<tr style="height:' + (spacerBottomCount * 44) + 'px"><td colspan="6"></td></tr>';
    }
  } else {
    // Small queues: render all rows immediately
    rows = filtered.map(createRowHtml).join('');
  }

  const modalTitle = S.musicPlaylistModalMode==='save'
        ? 'Save Playlist'
    : (S.musicPlaylistModalMode==='selected' ? 'Create Playlist from Selected' : 'Delete Playlist');
    const modalName=String(S.musicPlaylistModalName||'').trim();
    const existingPlaylists=(S.musicPlaylists||[]).map(x=>String(x||'').trim().toLowerCase()).filter(Boolean);
    const loadedPlaylistName=String((S.music&&S.music.loaded_playlist)||'').trim();
    const loadedPlaylist=loadedPlaylistName.toLowerCase();
    const queueLabel=loadedPlaylistName ? ('Playlist '+loadedPlaylistName) : 'Queue';
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
      +'<div class="rounded-xl border border-gray-800 bg-gray-900/40 md:col-span-1 overflow-hidden">'
        +'<div class="px-3 py-2 flex items-center justify-between gap-2 border-b border-gray-800">'
          +'<div class="text-sm font-semibold text-left">Playlists</div>'
          +'<button data-action="music-refresh-playlists" class="px-2 py-1 rounded-lg text-xs bg-gray-700 hover:bg-gray-600 transition-colors">Refresh Playlists</button>'
        +'</div>'
        +'<div class="px-2 py-2 border-b border-gray-800">'
          +'<input id="musicPlaylistSearch" type="search" value="'+esc(S.musicPlaylistFilter||'')+'" placeholder="Search playlists" class="w-full rounded-lg bg-gray-800 border border-gray-700 px-3 py-2 text-sm text-gray-100 placeholder-gray-400 focus:outline-none focus:ring-2 focus:ring-blue-600" />'
        +'</div>'
        +'<div class="max-h-72 overflow-y-auto p-0 text-left">'
          +(playlistRows || '<p class="text-xs text-gray-500 px-3 py-3 text-left">No playlists found</p>')
        +'</div>'
      +'</div>'
      +'<div class="md:col-span-3 space-y-3">'
        +'<div class="flex items-center justify-between gap-2 flex-wrap px-2">'
          +'<h2 class="font-semibold text-lg">'+esc(queueLabel)+' <span class="text-gray-400 font-normal text-sm ml-1">'+m.queue_length+' tracks</span></h2>'
          +'<div class="flex items-center gap-2">'
            +'<button data-action="music-clear-queue" class="px-3 py-1 rounded-lg text-sm bg-red-800 hover:bg-red-700 transition-colors" title="Clear entire queue">Clear Queue</button>'
            +'<button data-action="music-add-open" class="px-3 py-1 rounded-lg text-sm bg-blue-700 hover:bg-blue-600 transition-colors">Add Songs</button>'
                        +'<button data-action="music-open-save-playlist" class="px-3 py-1 rounded-lg text-sm bg-emerald-700 hover:bg-emerald-600 transition-colors">Save Playlist</button>'
                        +'<button data-action="music-toggle" class="px-4 py-2 rounded-lg text-base font-semibold bg-gray-700 hover:bg-gray-600 transition-colors" '+(pendingMusicCount? 'disabled style="opacity:.5;cursor:not-allowed"' : '')+'>'+(pendingMusicCount?'… Pending':(m.state==='play'?'⏹ Stop':'▶ Play'))+'</button>'
          +'</div>'
        +'</div>'
        +'<div class="px-2">'
          +'<input id="musicQueueSearch" type="search" data-action="music-queue-search" value="'+esc(S.musicQueueFilter||'')+'" placeholder="Filter queue: title, artist, album" class="w-full rounded-lg bg-gray-800 border border-gray-700 px-3 py-2 text-sm text-gray-100 placeholder-gray-400 focus:outline-none focus:ring-2 focus:ring-blue-600" />'
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
              +'<div class="music-queue-table-container rounded-xl border border-gray-800" style="overflow-y:auto;overflow-x:auto;max-height:600px"><table class="w-full text-left"><thead style="position:sticky;top:0;z-index:10;background:rgb(17,24,39)"><tr class="text-xs text-gray-400 border-b border-gray-800"><th class="px-2 py-2">Sel</th><th class="px-2 py-2">#</th><th class="px-2 py-2">Title</th><th class="px-2 py-2">Artist</th><th class="px-2 py-2">Album</th><th class="px-2 py-2 text-right pr-4">Dur</th></tr></thead><tbody>'+rows+'</tbody></table></div>'
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
  
  // Set up virtual scroll for large queues (progressive rendering as user scrolls)
  setTimeout(() => {
    const tableContainer = document.querySelector('.music-queue-table-container');
    const tbody = document.querySelector('.music-queue-table-container tbody');
    if (tableContainer && tbody && filtered.length > 50) {
      S._lastQueueScrollIdx = 0;
      const ROW_HEIGHT = 44;
      const VISIBLE_ROWS = 15;
      const BUFFER_ROWS = 5;
      
      tableContainer.addEventListener('scroll', () => {
        const scrollTop = tableContainer.scrollTop;
        const currentIdx = Math.max(0, Math.floor(scrollTop / ROW_HEIGHT));
        
        if (currentIdx !== S._lastQueueScrollIdx) {
          S._lastQueueScrollIdx = currentIdx;
          
          // Render visible rows + buffer for current scroll position
          const startIdx = Math.max(0, currentIdx - BUFFER_ROWS);
          const endIdx = Math.min(filtered.length, currentIdx + VISIBLE_ROWS + BUFFER_ROWS);
          const visibleItems = filtered.slice(startIdx, endIdx);
          
          // Build row HTML for visible range
          const spacerTopHeight = startIdx * ROW_HEIGHT;
          let newHtml = '';
          if (spacerTopHeight > 0) {
            newHtml += '<tr style="height:' + spacerTopHeight + 'px"><td colspan="6"></td></tr>';
          }
          newHtml += visibleItems.map(createRowHtml).join('');
          
          const spacerBottomCount = Math.max(0, filtered.length - endIdx);
          if (spacerBottomCount > 0) {
            newHtml += '<tr style="height:' + (spacerBottomCount * ROW_HEIGHT) + 'px"><td colspan="6"></td></tr>';
          }
          
          tbody.innerHTML = newHtml;
        }
      }, { passive: true});
    }
  }, 0);
  
    main.onscroll=()=>{ updateScrollUpButton(); };
    updateScrollUpButton();
}

function fmtDur(s){ if(!s) return '—'; const t=Math.round(Number(s)); return Math.floor(t/60)+':'+String(t%60).padStart(2,'0'); }
function esc(s){ return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); }

function wsUrl(){ return (location.protocol==='https:'?'wss':'ws')+'://'+location.hostname+':'+WS_PORT+'/ws'; }
function sendAction(payload){ 
  if(S.ws&&S.ws.readyState===WebSocket.OPEN) {
    if(payload.type && payload.type.startsWith('music_')) console.log(`📤 Sending action:`, payload);
    S.ws.send(JSON.stringify(payload)); 
  } else {
    console.warn('⚠️ WebSocket not ready (state=' + (S.ws ? S.ws.readyState : 'null') + '). Dropped:', payload);
  }
}
function sendMusicAction(actionType, extraPayload={}){
    const actionId='m'+(S.nextMusicActionId++);
    S.pendingMusicActions[actionId]={type:actionType, ts:Date.now()};
    sendAction(Object.assign({type:actionType, action_id:actionId}, extraPayload||{}));
    if(S.page==='music') renderMusicPage(document.getElementById('main'));
    applyMusicHeader();
    return actionId;
}
function sendTimerAction(actionType, idKey, idValue){
    const actionId='t'+(S.nextTimerActionId++);
    const pendingKey=String(actionType||'')+':'+String(idValue||'');
    S.pendingTimerActions[pendingKey]={type:actionType, action_id:actionId, ts:Date.now()};
    const payload={type:actionType, action_id:actionId};
    payload[String(idKey||'id')]=idValue;
    sendAction(payload);
    renderTimerBar();
    return actionId;
}
function sendSettingAction(actionType, enabled){
    const actionId='s'+(S.nextSettingActionId++);
    S.pendingSettingActions[String(actionType)]={action_id:actionId, enabled:!!enabled, ts:Date.now()};
    sendAction({type:String(actionType), action_id:actionId, enabled:!!enabled});
    applyMicControlToggles();
    return actionId;
}

function recordInlineError(kind, key, message){
    const msg=String(message||'Action failed');
    const now=Date.now();
    if(kind==='music'){
        S.musicActionError=msg;
        S.musicActionErrorTs=now;
        return;
    }
    if(kind==='setting'){
        S.settingActionErrors[String(key||'unknown')]={msg, ts:now};
        return;
    }
    if(kind==='timer'){
        S.timerActionErrors[String(key||'unknown')]={msg, ts:now};
    }
}

function expirePendingActions(){
    const now=Date.now();
    let touchMusic=false, touchTimer=false, touchSettings=false;

    Object.keys(S.pendingMusicActions||{}).forEach((actionId)=>{
        const item=S.pendingMusicActions[actionId];
        if(!item) return;
        if((now-Number(item.ts||0))>PENDING_ACTION_TIMEOUT_MS){
            delete S.pendingMusicActions[actionId];
            recordInlineError('music','', 'Music action timed out');
            touchMusic=true;
        }
    });

    Object.keys(S.pendingTimerActions||{}).forEach((k)=>{
        const item=S.pendingTimerActions[k];
        if(!item) return;
        if((now-Number(item.ts||0))>PENDING_ACTION_TIMEOUT_MS){
            delete S.pendingTimerActions[k];
            recordInlineError('timer', k, 'Timer/alarm action timed out');
            touchTimer=true;
        }
    });

    Object.keys(S.pendingSettingActions||{}).forEach((k)=>{
        const item=S.pendingSettingActions[k];
        if(!item) return;
        if((now-Number(item.ts||0))>PENDING_ACTION_TIMEOUT_MS){
            delete S.pendingSettingActions[k];
            recordInlineError('setting', k, 'Setting update timed out');
            touchSettings=true;
        }
    });

    if(S.musicActionErrorTs && (now-S.musicActionErrorTs)>INLINE_ERROR_TTL_MS){
        S.musicActionError='';
        S.musicActionErrorTs=0;
        touchMusic=true;
    }
    Object.keys(S.settingActionErrors||{}).forEach((k)=>{
        const it=S.settingActionErrors[k];
        if(it && (now-Number(it.ts||0))>INLINE_ERROR_TTL_MS){
            delete S.settingActionErrors[k];
            touchSettings=true;
        }
    });
    Object.keys(S.timerActionErrors||{}).forEach((k)=>{
        const it=S.timerActionErrors[k];
        if(it && (now-Number(it.ts||0))>INLINE_ERROR_TTL_MS){
            delete S.timerActionErrors[k];
            touchTimer=true;
        }
    });

    if(touchMusic){ applyMusicHeader(); if(S.page==='music') renderMusicPage(document.getElementById('main')); }
    if(touchTimer) renderTimerBar();
    if(touchSettings) applyMicControlToggles();
}

function formatCaptureError(err){
    const name=(err&&err.name)?String(err.name):'';
    const msg=(err&&err.message)?String(err.message):String(err||'capture failed');
    if(name==='NotFoundError') return 'No microphone device found (retrying)';
    if(name==='NotAllowedError') return 'Microphone permission denied';
    if(name==='NotReadableError') return 'Microphone is busy/unavailable';
    if(name==='OverconstrainedError') return 'Requested audio constraints not supported';
    return msg;
}

function clearCaptureRetry(){
    if(S.captureRetryTimer){
        clearTimeout(S.captureRetryTimer);
        S.captureRetryTimer=null;
    }
}

function scheduleCaptureRetry(delayMs=2500){
    if(S.captureRetryTimer || S.wsManualDisconnect || !S.wsConnected) return;
    S.captureRetryTimer=setTimeout(()=>{
        S.captureRetryTimer=null;
        ensureBrowserCapture().catch((err)=>reportCaptureFailure(err,'retry'));
    }, Math.max(500, Number(delayMs)||2500));
}

function reportCaptureFailure(err, phase='capture'){
    S.wsDebug.status='capture_error';
    S.wsDebug.lastError=formatCaptureError(err);
    updateWsDebugBanner();
    try{ console.error('Browser capture '+phase+' failed:', err); }catch(_ ){}
    try{ sendCaptureDiagnostics(err, phase); }catch(_ ){}
    const retryMs=(err&&err.name==='NotFoundError'&&S.lastAudioInputCount===0)?10000:(err&&err.name==='InvalidStateError')?8000:2500;
    scheduleCaptureRetry(retryMs);
}

async function sendCaptureDiagnostics(err, phase='capture'){
    const payload={
        type:'browser_capture_error',
        phase:String(phase||'capture'),
        name:(err&&err.name)?String(err.name):'',
        message:(err&&err.message)?String(err.message):String(err||''),
        secure_context:!!window.isSecureContext,
        has_media_devices:!!(navigator.mediaDevices&&navigator.mediaDevices.getUserMedia),
        user_agent:String(navigator.userAgent||''),
    };

    try{
        if(navigator.mediaDevices&&navigator.mediaDevices.enumerateDevices){
            const devices=await navigator.mediaDevices.enumerateDevices();
            const audioInputs=(devices||[]).filter(d=>d&&d.kind==='audioinput');
            payload.audio_input_count=audioInputs.length;
            payload.audio_input_labels=audioInputs.map(d=>String(d.label||'')).filter(Boolean).slice(0,6);
            S.lastAudioInputCount=audioInputs.length;
            if(audioInputs.length===0){
                S.wsDebug.lastError='No browser-visible microphone (audioinput=0). Use full browser + allow mic permissions.';
                updateWsDebugBanner();
            }
        }
    }catch(diagErr){
        payload.enumerate_error=(diagErr&&diagErr.message)?String(diagErr.message):String(diagErr||'');
    }

    if(S.ws&&S.ws.readyState===WebSocket.OPEN){
        S.ws.send(JSON.stringify(payload));
    }
}

async function stopBrowserCapture(){
    try{ if(S.processor) S.processor.disconnect(); }catch(_ ){}
    try{ if(S.audioCtx) await S.audioCtx.close(); }catch(_ ){}
    if(S.mediaStream){
        try{ S.mediaStream.getTracks().forEach(t=>t.stop()); }catch(_ ){}
    }
    S.processor=null;
    S.audioCtx=null;
    S.mediaStream=null;
    S.captureWorkletModuleReady=false;
}

async function disconnectWs(manual=true){
    if(manual) S.wsManualDisconnect=true;
    if(S.wsReconnectTimer){ clearTimeout(S.wsReconnectTimer); S.wsReconnectTimer=null; }
    stopWsPingTimer();
    clearCaptureRetry();

    const ws=S.ws;
    S.ws=null;
    S.wsConnected=false;
    S.wsDebug.status='closed';
    S.wsDebug.lastCloseReason=manual?'manual disconnect':(S.wsDebug.lastCloseReason||'');
    updateWsDebugBanner();
    updateMicInteractivity();

    if(ws && (ws.readyState===WebSocket.OPEN || ws.readyState===WebSocket.CONNECTING)){
        try{ ws.close(1000, manual?'Manual disconnect':'Disconnect'); }catch(_ ){}
    }
    await stopBrowserCapture();
}

function reconnectWs(){
    S.wsManualDisconnect=false;
    if(S.wsReconnectTimer){ clearTimeout(S.wsReconnectTimer); S.wsReconnectTimer=null; }
    connectWs();
}

function connectWs(){
  if(S.wsManualDisconnect) return;
  if(S.ws&&(S.ws.readyState===WebSocket.OPEN||S.ws.readyState===WebSocket.CONNECTING)) return;
    S.wsDebug.status='connecting';
    S.wsDebug.lastError='';
    updateWsDebugBanner();
    updateMicInteractivity();
  S.ws=new WebSocket(wsUrl()); S.ws.binaryType='arraybuffer';
    S.ws.onopen=()=>{
            S.wsConnected=true;
            S.wsDebug.status='open';
                S.wsDebug.lastError='';
                updateWsDebugBanner();
                updateMicInteractivity();
            startWsPingTimer();
            if(S.browserAudioEnabled) ensureBrowserCapture().catch((err)=>reportCaptureFailure(err,'connect'));
            S.ws.send(JSON.stringify({type:'ui_ready'}));
                pushUiPrefsToServer();
        if(S.page==='music') sendAction({type:'music_list_playlists'});
    };
    S.ws.onclose=(evt)=>{
        S.wsConnected=false;
        S.wsDebug.status='closed';
        S.wsDebug.lastCloseCode=(evt&&evt.code!==undefined)?evt.code:null;
        S.wsDebug.lastCloseReason=(evt&&evt.reason)?String(evt.reason):'';
        updateWsDebugBanner();
        updateMicInteractivity();
        stopWsPingTimer();
        S.ws=null;
        if (evt && evt.code === 4001) return;
        if(S.wsManualDisconnect) return;
        S.wsReconnectTimer=setTimeout(()=>{ S.wsReconnectTimer=null; connectWs(); },WS_RECONNECT_MS);
    };
    S.ws.onerror=(evt)=>{
        S.wsDebug.status='error';
        S.wsDebug.lastError=(evt&&evt.message)?String(evt.message):'socket error';
        updateWsDebugBanner();
        updateMicInteractivity();
    };
  S.ws.onmessage=evt=>{ if(!(evt.data instanceof ArrayBuffer)){ try{ handleMsg(JSON.parse(evt.data)); }catch(_){} } };
}

