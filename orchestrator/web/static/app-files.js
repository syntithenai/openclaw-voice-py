(function () {
  const FM_API = '/api/file-manager';
  const FM_DEBOUNCE_MS = 500;
  const JSON_EDITOR_MODULE_URL = 'https://cdn.jsdelivr.net/npm/vanilla-jsoneditor/standalone.js';

  function fmEsc(value) {
    return String(value || '')
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  function fmMain() {
    return document.getElementById('main');
  }

  function fmState() {
    if (!S.fileManager) {
      S.fileManager = {
        treeByPath: {},
        expandedByPath: { '/': true },
        saveStateByPath: {},
        saveTimersByPath: {},
        filterResults: null,
        filterText: '',
        filterTimer: null,
        selectedFolderPath: '/',
        selectedFilePath: '',
        currentFile: null,
        folderChildren: [],
        error: '',
        loading: false,
        initialized: false,
        treeScrollTop: 0,
        activePlainEditorPath: '',
        activeMarkdownEditorPath: '',
        activeJsonEditorPath: '',
        markdownEditor: null,
        jsonEditor: null,
        filePickerModalOpen: false,
        filePickerType: '',
        filePickerTitle: '',
        filePickerCurrentPath: '/',
        filePickerExpandedPaths: {},
        filePickerTree: {},
        filePickerChildren: [],
        markdownEditorSelection: null,
        createFolderModalOpen: false,
        createFolderName: '',
        createFolderParentPath: '/',
        deleteModalOpen: false,
        deleteTargetType: '',
        deleteTargetPath: '',
        deleteTargetName: '',
        deleteBusy: false,
        renameModalOpen: false,
        renameTargetType: '',
        renameTargetPath: '',
        renameTargetName: '',
        renameName: '',
        renameBusy: false,
      };
    }
    return S.fileManager;
  }

  function isVirtualPath(path) {
    const p = String(path || '');
    return p === '/__virtual__/openclaw-config' || p.startsWith('/__virtual__/openclaw-config/');
  }

  function parentPath(path) {
    const p = String(path || '/');
    if (p === '/' || !p.startsWith('/')) return '/';
    const parts = p.split('/').filter(Boolean);
    if (!parts.length) return '/';
    parts.pop();
    return parts.length ? ('/' + parts.join('/')) : '/';
  }

  function updateSaveBadgeDom() {
    const st = fmState();
    const badge = document.getElementById('fmSaveBadge');
    if (!badge) return;
    const file = st.currentFile;
    if (!file) {
      badge.classList.add('hidden');
      badge.textContent = '';
      return;
    }
    const value = String(st.saveStateByPath[file.path] || '');
    if (!value) {
      badge.classList.add('hidden');
      badge.textContent = '';
      return;
    }
    const label = value === 'dirty' ? 'Unsaved'
      : value === 'saving' ? 'Saving'
      : value === 'saved' ? 'Saved'
      : value === 'conflict' ? 'Conflict'
      : 'Error';
    badge.classList.remove('hidden');
    badge.textContent = label;
  }

  async function fmFetchJson(url, opts) {
    const response = await fetch(url, Object.assign({
      credentials: 'same-origin',
      cache: 'no-store',
      headers: { 'Content-Type': 'application/json' },
    }, opts || {}));

    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      const message = String(payload && payload.error ? payload.error : 'request failed');
      const error = new Error(message);
      error.status = response.status;
      throw error;
    }
    return payload;
  }

  async function loadTree(path) {
    const st = fmState();
    const data = await fmFetchJson(FM_API + '/tree?path=' + encodeURIComponent(path));
    st.treeByPath[path] = Array.isArray(data.children) ? data.children : [];
    return st.treeByPath[path];
  }

  async function loadFolder(path) {
    const st = fmState();
    const data = await fmFetchJson(FM_API + '/folder?path=' + encodeURIComponent(path));
    st.folderChildren = Array.isArray(data.children) ? data.children : [];
    st.selectedFolderPath = path;
    return st.folderChildren;
  }

  async function loadFile(path) {
    const st = fmState();
    const data = await fmFetchJson(FM_API + '/file?path=' + encodeURIComponent(path));
    st.currentFile = data;
    st.selectedFilePath = path;
    st.activePlainEditorPath = '';
    st.activeMarkdownEditorPath = '';
    st.activeJsonEditorPath = '';
    return data;
  }

  async function saveFile(path, content) {
    const st = fmState();
    if (!st.currentFile || st.currentFile.path !== path) {
      return;
    }

    st.currentFile.content = String(content || '');
    st.saveStateByPath[path] = 'saving';
    updateSaveBadgeDom();

    try {
      const res = await fmFetchJson(FM_API + '/file?path=' + encodeURIComponent(path), {
        method: 'PUT',
        body: JSON.stringify({
          content: String(content || ''),
          expectedEtag: String(st.currentFile.etag || ''),
        }),
      });
      st.currentFile.etag = res.etag;
      st.currentFile.size = res.size;
      st.currentFile.mtime = res.mtime;
      st.currentFile.content = String(content || '');
      st.saveStateByPath[path] = 'saved';
    } catch (err) {
      st.saveStateByPath[path] = String(err && err.status === 409 ? 'conflict' : 'error');
      st.error = String(err && err.message ? err.message : err);
      renderFileManagerPage(fmMain());
      return;
    }

    updateSaveBadgeDom();
  }

  function queueSave(path, content) {
    const st = fmState();
    if (!path) return;
    if (st.saveTimersByPath[path]) {
      clearTimeout(st.saveTimersByPath[path]);
    }
    if (st.currentFile && st.currentFile.path === path) {
      st.currentFile.content = String(content || '');
    }
    st.saveStateByPath[path] = 'dirty';
    st.saveTimersByPath[path] = setTimeout(() => {
      delete st.saveTimersByPath[path];
      void saveFile(path, content);
    }, FM_DEBOUNCE_MS);
    updateSaveBadgeDom();
  }

  function renderTreeRows(path, depth) {
    const st = fmState();
    const nodes = st.treeByPath[path] || [];
    return nodes.map((node) => {
      const nodePath = String(node.path || '');
      const isFolder = String(node.kind || '') === 'folder' || String(node.kind || '') === 'virtual-folder';
      const isExpanded = !!st.expandedByPath[nodePath];
      const isActive = isFolder ? (st.selectedFolderPath === nodePath) : (st.selectedFilePath === nodePath);
      const rowAction = isFolder ? 'fm-select-folder' : 'fm-select-file';
      const left = 10 + (depth * 14);

      const branch = isFolder
        ? '<button type="button" class="text-xs text-gray-300" data-action="fm-toggle-folder" data-path="' + fmEsc(nodePath) + '">' + (isExpanded ? '-' : '+') + '</button>'
        : '<span class="text-xs text-gray-500">.</span>';

      const row = ''
        + '<div class="fm-tree-row ' + (isActive ? 'active' : '') + '" style="margin-left:' + left + 'px" data-action="' + rowAction + '" data-path="' + fmEsc(nodePath) + '">'
        + branch
        + '<span class="text-sm">' + fmEsc(node.name) + '</span>'
        + '</div>';
      if (isFolder && isExpanded) {
        return row + renderTreeRows(nodePath, depth + 1);
      }
      return row;
    }).join('');
  }

  function renderFilterResults() {
    const st = fmState();
    if (st.filterResults === null) {
      return '<div class="px-3 py-3 text-sm text-gray-400">Searching...</div>';
    }
    const results = Array.isArray(st.filterResults) ? st.filterResults : [];
    if (!results.length) {
      return '<div class="px-3 py-3 text-sm text-gray-400">No results found.</div>';
    }
    const tokens = String(st.filterText || '').trim().toLowerCase().split(/\s+/).filter(Boolean);
    return results.map((item) => {
      const p = String(item.path || '');
      const kind = String(item.kind || 'file');
      const isFolder = kind === 'folder' || kind === 'virtual-folder';
      const active = isFolder ? st.selectedFolderPath === p : st.selectedFilePath === p;
      const icon = isFolder ? 'DIR' : 'FILE';
      const action = isFolder ? 'fm-select-folder' : 'fm-select-file';
      return ''
        + '<div class="fm-file-row ' + (active ? 'active' : '') + '" data-action="' + action + '" data-path="' + fmEsc(p) + '">'
        + '<div class="text-xs text-gray-500">' + icon + '</div>'
        + '<div class="min-w-0">'
        + '<div class="text-sm truncate">' + fmEsc(item.name) + '</div>'
        + '<div class="text-xs text-gray-500 truncate">' + highlightTokens(p, tokens) + '</div>'
        + '</div>'
        + '</div>';
    }).join('');
  }

  function highlightTokens(text, tokens) {
    let escaped = fmEsc(text);
    if (!tokens || !tokens.length) return escaped;
    tokens.forEach((token) => {
      const idx = escaped.toLowerCase().indexOf(token);
      if (idx >= 0) {
        escaped = escaped.slice(0, idx)
          + '<mark class="bg-yellow-500/30 rounded">' + escaped.slice(idx, idx + token.length) + '</mark>'
          + escaped.slice(idx + token.length);
      }
    });
    return escaped;
  }

  function renderFolderRows() {
    const st = fmState();
    const items = Array.isArray(st.folderChildren) ? st.folderChildren : [];
    if (!items.length) {
      return '<div class="px-3 py-3 text-sm text-gray-400">Folder is empty.</div>';
    }
    return items.map((item) => {
      const p = String(item.path || '');
      const kind = String(item.kind || 'file');
      const isFolder = kind === 'folder' || kind === 'virtual-folder';
      const isActive = isFolder ? st.selectedFolderPath === p : st.selectedFilePath === p;
      const icon = isFolder ? 'DIR' : 'FILE';
      const action = isFolder ? 'fm-select-folder' : 'fm-select-file';
      const mtime = item.mtime ? new Date(item.mtime * 1000).toLocaleDateString() : '';
      return ''
        + '<div class="fm-file-row ' + (isActive ? 'active' : '') + '" data-action="' + action + '" data-path="' + fmEsc(p) + '">'
        + '<div class="text-xs text-gray-500">' + icon + '</div>'
        + '<div class="min-w-0">'
        + '<div class="text-sm truncate">' + fmEsc(item.name) + '</div>'
        + (mtime ? '<div class="text-xs text-gray-500">' + mtime + '</div>' : '')
        + '</div>'
        + '</div>';
    }).join('');
  }

  function renderPreservingFilter() {
    const fi = document.getElementById('fmFilterInput');
    const focused = fi && document.activeElement === fi;
    const selStart = fi ? fi.selectionStart : null;
    const selEnd = fi ? fi.selectionEnd : null;
    renderFileManagerPage(fmMain());
    if (focused) {
      const fi2 = document.getElementById('fmFilterInput');
      if (fi2) {
        fi2.focus();
        if (selStart !== null) fi2.setSelectionRange(selStart, selEnd);
      }
    }
  }

  async function runFilter(text) {
    const st = fmState();
    const q = String(text || '').trim();
    if (!q || st.filterText !== text) return;
    try {
      const data = await fmFetchJson(FM_API + '/search?q=' + encodeURIComponent(q));
      if (st.filterText === text) {
        st.filterResults = Array.isArray(data.results) ? data.results : [];
        renderPreservingFilter();
      }
    } catch (err) {
      if (st.filterText === text) {
        st.filterResults = [];
        st.error = String(err && err.message ? err.message : err);
        renderPreservingFilter();
      }
    }
  }

  function queueFilter(text) {
    const st = fmState();
    if (st.filterTimer) {
      clearTimeout(st.filterTimer);
      st.filterTimer = null;
    }
    if (!String(text || '').trim()) {
      st.filterResults = null;
      renderPreservingFilter();
      return;
    }
    st.filterResults = null;
    renderPreservingFilter();
    st.filterTimer = setTimeout(() => {
      st.filterTimer = null;
      void runFilter(text);
    }, FM_DEBOUNCE_MS);
  }

  function currentFolderName() {
    const st = fmState();
    const path = String(st.selectedFolderPath || '/');
    if (path === '/') return 'Workspace';
    const parts = path.split('/').filter(Boolean);
    return parts.length ? parts[parts.length - 1] : 'Workspace';
  }

  function renderEditorPane() {
    const st = fmState();
    const file = st.currentFile;
    if (!file) {
      return '<div class="px-4 py-4 text-sm text-gray-400">Select a file to open an editor/preview.</div>';
    }

    const category = String(file.category || 'binary');
    const editable = !!file.editable;
    const readOnly = editable ? '' : ('<div class="text-xs text-amber-300">' + fmEsc(file.readOnlyReason || 'Read-only file') + '</div>');

    if (category === 'media') {
      const mime = String(file.mimeType || '');
      const src = String(file.previewUrl || '');
      if (mime.startsWith('image/')) {
        return '<div class="fm-preview p-3"><img src="' + fmEsc(src) + '" alt="preview" /></div>';
      }
      if (mime.startsWith('audio/')) {
        return '<div class="fm-preview p-3"><audio controls src="' + fmEsc(src) + '" style="width:100%"></audio></div>';
      }
      if (mime.startsWith('video/')) {
        return '<div class="fm-preview p-3"><video controls src="' + fmEsc(src) + '" style="width:100%"></video></div>';
      }
      return '<div class="fm-preview p-3"><a class="underline" href="' + fmEsc(src) + '" target="_blank" rel="noreferrer noopener">Open media preview</a></div>';
    }

    if (category === 'binary') {
      return ''
        + '<div class="p-4 space-y-2">'
        + '<div class="text-sm text-gray-300">Binary file preview only.</div>'
        + '<a class="underline" href="' + fmEsc(file.previewUrl || '') + '" target="_blank" rel="noreferrer noopener">Open file</a>'
        + '</div>';
    }

    if (category === 'markdown') {
      return ''
        + '<div class="p-3 space-y-2">'
        + readOnly
        + '<textarea id="fmMarkdownEditor" class="fm-textarea">' + fmEsc(file.content || '') + '</textarea>'
        + '</div>';
    }

    if (category === 'json') {
      return ''
        + '<div class="p-3 space-y-2">'
        + readOnly
        + '<div id="fmJsonEditor" class="fm-editor-wrap"></div>'
        + '</div>';
    }

    return ''
      + '<div class="fm-text-body">'
      + readOnly
      + '<textarea id="fmTextEditor" class="fm-textarea">' + fmEsc(file.content || '') + '</textarea>'
      + '</div>';
  }

  function destroyEditors() {
    const st = fmState();
    if (st.markdownEditor && typeof st.markdownEditor.toTextArea === 'function') {
      st.markdownEditor.toTextArea();
      st.markdownEditor = null;
    }
    if (st.jsonEditor && typeof st.jsonEditor.destroy === 'function') {
      st.jsonEditor.destroy();
      st.jsonEditor = null;
    }
    st.activePlainEditorPath = '';
    st.activeMarkdownEditorPath = '';
    st.activeJsonEditorPath = '';
  }

  function hasLiveMarkdownEditor(path) {
    const st = fmState();
    if (!st.markdownEditor || st.activeMarkdownEditorPath !== path) {
      return false;
    }
    const codemirror = st.markdownEditor.codemirror;
    if (!codemirror || typeof codemirror.getWrapperElement !== 'function') {
      return false;
    }
    const wrapper = codemirror.getWrapperElement();
    return !!(wrapper && wrapper.isConnected);
  }

  function hasLiveJsonEditor(path, mount) {
    const st = fmState();
    if (!st.jsonEditor || st.activeJsonEditorPath !== path) {
      return false;
    }
    return !!(mount && mount.isConnected && mount.childElementCount > 0);
  }

  async function mountEditors() {
    const st = fmState();
    if (!st.selectedFilePath || S.page !== 'files') return;
    let file = st.currentFile;
    if (!file || String(file.path || '') !== String(st.selectedFilePath || '')) {
      file = await fmFetchJson(FM_API + '/file?path=' + encodeURIComponent(st.selectedFilePath));
      if (!file || S.page !== 'files') return;
      st.currentFile = file;
    }

    const category = String(file.category || 'binary');
    if (!file.editable) return;

    if (category === 'text') {
      const text = document.getElementById('fmTextEditor');
      if (!text) return;
      if (text.dataset.fmBound !== '1') {
        text.dataset.fmBound = '1';
        text.addEventListener('input', () => {
          queueSave(file.path, text.value);
        });
      }
      st.activePlainEditorPath = file.path;
      return;
    }

    if (category === 'markdown') {
      const textarea = document.getElementById('fmMarkdownEditor');
      if (!textarea) return;
      if (hasLiveMarkdownEditor(file.path)) return;
      if (st.markdownEditor && typeof st.markdownEditor.toTextArea === 'function') {
        st.markdownEditor.toTextArea();
        st.markdownEditor = null;
      }
      if (typeof EasyMDE !== 'function') {
        st.error = 'EasyMDE is not loaded';
        renderFileManagerPage(fmMain());
        return;
      }
      st.activeMarkdownEditorPath = file.path;
      st.markdownEditor = new EasyMDE({
        element: textarea,
        autofocus: false,
        spellChecker: false,
        forceSync: true,
        status: false,
        initialValue: String(file.content || ''),
        toolbar: [
          'bold', 'italic', 'heading', '|',
          'quote', 'unordered-list', 'ordered-list', '|',
          {
            name: 'insertImage',
            action: function() { openFilePicker('image'); },
            className: 'fa fa-picture-o',
            title: 'Insert Image',
          },
          {
            name: 'insertLink',
            action: function() { openFilePicker('link'); },
            className: 'fa fa-link',
            title: 'Insert Link',
          },
          '|', 'preview', 'side-by-side', 'fullscreen', '|', 'guide'
        ],
      });
      st.markdownEditor.codemirror.on('change', () => {
        queueSave(file.path, st.markdownEditor.value());
      });
      return;
    }

    if (category === 'json') {
      const mount = document.getElementById('fmJsonEditor');
      if (!mount) return;
      if (hasLiveJsonEditor(file.path, mount)) return;
      if (st.jsonEditor && typeof st.jsonEditor.destroy === 'function') {
        st.jsonEditor.destroy();
        st.jsonEditor = null;
      }
      st.activeJsonEditorPath = file.path;
      try {
        const { JSONEditor } = await import(JSON_EDITOR_MODULE_URL);
        if (S.page !== 'files' || st.activeJsonEditorPath !== file.path) return;
        let parsed = null;
        try { parsed = JSON.parse(file.content || 'null'); } catch (_) { parsed = null; }
        st.jsonEditor = new JSONEditor({
          target: mount,
          props: {
            content: { json: parsed, text: undefined },
            readOnly: !file.editable,
            onChange: (updatedContent) => {
              try {
                const text = updatedContent.text !== undefined
                  ? updatedContent.text
                  : JSON.stringify(updatedContent.json, null, 2);
                queueSave(file.path, text);
              } catch (_) {}
            },
          },
        });
      } catch (err) {
        st.error = 'Failed to load JSON editor: ' + String(err && err.message ? err.message : err);
        renderFileManagerPage(fmMain());
      }
      return;
    }
  }

  function openRenameModal(type, path, currentName) {
    const st = fmState();
    st.renameModalOpen = true;
    st.renameTargetType = String(type || '');
    st.renameTargetPath = String(path || '');
    st.renameTargetName = String(currentName || '');
    st.renameName = String(currentName || '');
    st.renameBusy = false;
    renderFileManagerPage(fmMain());
    setTimeout(() => {
      const inp = document.getElementById('fmRenameName');
      if (inp) { inp.focus(); inp.select(); }
    }, 0);
  }

  function closeRenameModal() {
    const st = fmState();
    st.renameModalOpen = false;
    st.renameTargetType = '';
    st.renameTargetPath = '';
    st.renameTargetName = '';
    st.renameName = '';
    st.renameBusy = false;
    renderFileManagerPage(fmMain());
  }

  async function confirmRename() {
    const st = fmState();
    if (st.renameBusy) return;
    const targetPath = String(st.renameTargetPath || '');
    const targetType = String(st.renameTargetType || '');
    const newName = String(st.renameName || '').trim();
    if (!newName || !targetPath) { closeRenameModal(); return; }

    st.renameBusy = true;
    st.error = '';
    renderFileManagerPage(fmMain());

    const endpoint = targetType === 'file' ? '/file' : '/folder';
    try {
      const result = await fmFetchJson(FM_API + endpoint + '?path=' + encodeURIComponent(targetPath), {
        method: 'PATCH',
        body: JSON.stringify({ newName }),
      });
      const newEntry = result && result.entry;
      const newPath = newEntry ? String(newEntry.path || '') : '';
      st.renameModalOpen = false;
      st.renameTargetType = '';
      st.renameTargetPath = '';
      st.renameTargetName = '';
      st.renameName = '';
      st.renameBusy = false;
      st.treeByPath = {};
      await loadTree('/');
      if (targetType === 'file') {
        if (st.selectedFilePath === targetPath) {
          st.selectedFilePath = '';
          st.currentFile = null;
          destroyEditors();
        }
        await selectFolder(st.selectedFolderPath || '/');
      } else {
        await selectFolder(newPath || parentPath(targetPath));
      }
    } catch (err) {
      st.renameBusy = false;
      st.error = String(err && err.message ? err.message : err);
      st.renameModalOpen = false;
      renderFileManagerPage(fmMain());
    }
  }

  function saveBadgeHtml() {
    return '<span id="fmSaveBadge" class="hidden px-2 py-1 rounded text-xs bg-gray-700"></span>';
  }

  function renderFilePickerTree(path, depth, isRoot) {
    const st = fmState();
    const nodes = st.filePickerTree[path] || [];
    if (!nodes.length && isRoot) {
      return '';
    }
    return nodes.map((node) => {
      const nodePath = String(node.path || '');
      const isFolder = String(node.kind || '') === 'folder' || String(node.kind || '') === 'virtual-folder';
      const isExpanded = !!st.filePickerExpandedPaths[nodePath];
      const isCurrent = st.filePickerCurrentPath === nodePath;
      const left = 10 + (depth * 14);

      const branch = isFolder
        ? '<button type="button" class="text-xs text-gray-300" data-action="fp-toggle-folder" data-path="' + fmEsc(nodePath) + '">' + (isExpanded ? '-' : '+') + '</button>'
        : '<span class="text-xs text-gray-500">.</span>';

      const selectAction = isFolder ? 'fp-enter-folder' : 'fp-select-file';
      const row = ''
        + '<div class="fm-tree-row ' + (isCurrent ? 'active' : '') + '" style="margin-left:' + left + 'px" data-action="' + selectAction + '" data-path="' + fmEsc(nodePath) + '">'
        + branch
        + '<span class="text-sm">' + fmEsc(node.name) + '</span>'
        + '</div>';

      if (isFolder && isExpanded) {
        return row + renderFilePickerTree(nodePath, depth + 1, false);
      }
      return row;
    }).join('');
  }

  function renderFilePickerChildren() {
    const st = fmState();
    let items = Array.isArray(st.filePickerChildren) ? st.filePickerChildren : [];
    if (st.filePickerType === 'image') {
      items = items.filter(item => {
        const kind = String(item.kind || 'file');
        return kind === 'folder' || kind === 'virtual-folder' || isImageFile(item.path);
      });
    }
    if (!items.length) {
      return '<div class="px-3 py-3 text-sm text-gray-400">Folder is empty</div>';
    }
    return items.map((item) => {
      const p = String(item.path || '');
      const kind = String(item.kind || 'file');
      const icon = kind === 'folder' || kind === 'virtual-folder' ? 'DIR' : 'FILE';
      const selectAction = kind === 'folder' || kind === 'virtual-folder' ? 'fp-enter-folder' : 'fp-select-file';
      return ''
        + '<div class="fm-file-row" data-action="' + selectAction + '" data-path="' + fmEsc(p) + '">' 
        + '<div class="text-xs text-gray-500">' + icon + '</div>'
        + '<div class="text-sm truncate">' + fmEsc(item.name) + '</div>'
        + '</div>';
    }).join('');
  }

  function isImageFile(path) {
    const p = String(path || '').toLowerCase();
    return /\.(png|jpg|jpeg|gif|webp|svg)$/i.test(p);
  }

  function openFilePicker(type) {
    const st = fmState();
    // Save current selection before opening picker
    if (st.markdownEditor && st.markdownEditor.codemirror) {
      const doc = st.markdownEditor.codemirror.getDoc();
      st.markdownEditorSelection = doc.listSelections();
    } else {
      st.markdownEditorSelection = null;
    }
    st.filePickerModalOpen = true;
    st.filePickerType = type;
    st.filePickerTitle = type === 'image' ? 'Select an Image' : 'Select a File';
    st.filePickerCurrentPath = '/';
    st.filePickerExpandedPaths = { '/': true };
    st.filePickerTree = {};
    delete st.filePickerChildren;
    renderFileManagerPage(fmMain());
    void loadFilePickerTree('/').then(() => loadFilePickerFolder('/')).then(() => {
      renderFileManagerPage(fmMain());
    });
  }

  function closeFilePicker() {
    const st = fmState();
    st.filePickerModalOpen = false;
    st.filePickerType = '';
    st.filePickerTitle = '';
    delete st.filePickerChildren;
    renderFileManagerPage(fmMain());
  }

  function insertFilePickerResult(filePath) {
    const st = fmState();
    const editor = st.markdownEditor;
    if (!editor || !editor.codemirror) return;

    const path = String(filePath || '');
    const fileName = path.split('/').pop() || 'file';
    let decodedFileName = fileName;
    try {
      decodedFileName = decodeURIComponent(fileName);
    } catch (_) {
      decodedFileName = fileName;
    }
    const linkLabel = decodedFileName
      .replace(/\.[^./]+$/, '')
      .replace(/[_-]+/g, ' ')
      .trim() || decodedFileName;

    const routePath = '/#/files?path=' + encodeURIComponent(path);
    let markdownSyntax = '';
    if (st.filePickerType === 'image') {
      const previewPath = FM_API + '/preview?path=' + encodeURIComponent(path);
      markdownSyntax = '[![' + decodedFileName + '](' + previewPath + ')](' + routePath + ')';
    } else {
      markdownSyntax = '[' + linkLabel + '](' + routePath + ')';
    }

    const cm = editor.codemirror;
    const doc = cm.getDoc();
    // Use the saved selection from state if present
    const selection = st.markdownEditorSelection;
    if (selection && selection.length > 0) {
      doc.setSelections(selection);
    }
    doc.replaceSelection(markdownSyntax, 'around');
    if (st.currentFile && st.currentFile.path) {
      queueSave(st.currentFile.path, editor.value());
    }
    cm.focus();
    st.markdownEditorSelection = null;
  }


  async function loadFilePickerTree(path) {
    const st = fmState();
    const url = FM_API + '/tree?path=' + encodeURIComponent(path);
    try {
      const data = await fmFetchJson(url, {});
      st.filePickerTree[path] = Array.isArray(data && data.children) ? data.children : [];
    } catch (err) {
      st.filePickerTree[path] = [];
      st.error = String(err && err.message ? err.message : err);
      console.error('Failed to load file picker tree:', err);
    }
  }

  async function loadFilePickerFolder(path) {
    const st = fmState();
    st.filePickerCurrentPath = path;
    const url = FM_API + '/folder?path=' + encodeURIComponent(path);
    try {
      const data = await fmFetchJson(url, {});
      st.filePickerChildren = Array.isArray(data && data.children) ? data.children : [];
    } catch (err) {
      st.filePickerChildren = [];
      st.error = String(err && err.message ? err.message : err);
      console.error('Failed to load file picker folder:', err);
    }
  }


  async function ensureInitialized(force) {
    const st = fmState();
    if (st.initialized && !force) return;
    st.loading = true;
    renderFileManagerPage(fmMain());
    try {
      await loadTree('/');
      await loadFolder('/');
      st.initialized = true;
    } catch (err) {
      st.error = String(err && err.message ? err.message : err);
    }
    st.loading = false;
    renderFileManagerPage(fmMain());
    // If a hash path was specified, navigate to it now that the tree is ready
    if (typeof window.applyFilesRouteFromHash === 'function') {
      window.applyFilesRouteFromHash();
    }
  }

  async function selectFolder(path) {
    const st = fmState();
    st.currentFile = null;
    st.selectedFilePath = '';
    st.error = '';
    history.replaceState(null, '', '/#/files?path=' + encodeURIComponent(path));
    renderFileManagerPage(fmMain());
    try {
      if (!st.treeByPath['/']) await loadTree('/');
      await loadFolder(path);
      renderFileManagerPage(fmMain());
    } catch (err) {
      st.error = String(err && err.message ? err.message : err);
      renderFileManagerPage(fmMain());
    }
  }

  async function selectFile(path) {
    const st = fmState();
    st.currentFile = null;
    st.selectedFilePath = path;
    st.error = '';
    history.replaceState(null, '', '/#/files?path=' + encodeURIComponent(path));
    renderFileManagerPage(fmMain());
    try {
      await loadFile(path);
      renderFileManagerPage(fmMain());
    } catch (err) {
      st.error = String(err && err.message ? err.message : err);
      renderFileManagerPage(fmMain());
    }
  }

  async function toggleTreeFolder(path) {
    const st = fmState();
    const isExpanded = !!st.expandedByPath[path];
    if (isExpanded) {
      st.expandedByPath[path] = false;
      renderFileManagerPage(fmMain());
    } else {
      st.expandedByPath[path] = true;
      if (!st.treeByPath[path]) {
        try {
          await loadTree(path);
        } catch (err) {
          st.error = String(err && err.message ? err.message : err);
        }
      }
      renderFileManagerPage(fmMain());
    }
  }

  function openDeleteModal(type, path, name) {
    const st = fmState();
    st.deleteModalOpen = true;
    st.deleteTargetType = String(type || '');
    st.deleteTargetPath = String(path || '');
    st.deleteTargetName = String(name || '');
    st.deleteBusy = false;
    renderFileManagerPage(fmMain());
  }

  function closeDeleteModal() {
    const st = fmState();
    st.deleteModalOpen = false;
    st.deleteTargetType = '';
    st.deleteTargetPath = '';
    st.deleteTargetName = '';
    st.deleteBusy = false;
    renderFileManagerPage(fmMain());
  }

  async function confirmDelete() {
    const st = fmState();
    if (st.deleteBusy) return;
    const targetPath = String(st.deleteTargetPath || '');
    const targetType = String(st.deleteTargetType || '');
    if (!targetPath) { st.deleteModalOpen = false; renderFileManagerPage(fmMain()); return; }
    st.deleteBusy = true;
    renderFileManagerPage(fmMain());
    const endpoint = targetType === 'file' ? '/file' : '/folder';
    try {
      await fmFetchJson(FM_API + endpoint + '?path=' + encodeURIComponent(targetPath), { method: 'DELETE' });
      st.deleteModalOpen = false;
      st.deleteBusy = false;
      st.deleteTargetType = '';
      st.deleteTargetPath = '';
      st.deleteTargetName = '';
      st.treeByPath = {};
      if (targetType === 'file') {
        if (st.selectedFilePath === targetPath) {
          st.currentFile = null;
          st.selectedFilePath = '';
        }
        await loadTree('/');
        await loadFolder(st.selectedFolderPath || '/');
      } else {
        st.selectedFolderPath = '/';
        st.folderChildren = [];
        await loadTree('/');
        await loadFolder('/');
      }
      renderFileManagerPage(fmMain());
    } catch (err) {
      st.deleteBusy = false;
      st.error = String(err && err.message ? err.message : err);
      st.deleteModalOpen = false;
      renderFileManagerPage(fmMain());
    }
  }

  async function createFolder() {
    const st = fmState();
    const name = String(st.createFolderName || '').trim();
    if (!name) { st.createFolderModalOpen = false; renderFileManagerPage(fmMain()); return; }
    const parent = String(st.createFolderParentPath || st.selectedFolderPath || '/');
    try {
      await fmFetchJson(FM_API + '/folder?path=' + encodeURIComponent(parent), {
        method: 'POST',
        body: JSON.stringify({ name }),
      });
      st.createFolderModalOpen = false;
      st.createFolderName = '';
      st.createFolderParentPath = '/';
      st.treeByPath = {};
      await loadTree('/');
      await loadFolder(parent);
      renderFileManagerPage(fmMain());
    } catch (err) {
      st.error = String(err && err.message ? err.message : err);
      st.createFolderModalOpen = false;
      st.createFolderParentPath = '/';
      renderFileManagerPage(fmMain());
    }
  }

  function openCreateFolderModal(parentPath) {
    const st = fmState();
    st.createFolderModalOpen = true;
    st.createFolderName = '';
    st.createFolderParentPath = String(parentPath || st.selectedFolderPath || '/');
    renderFileManagerPage(fmMain());
  }

  window.renderFileManagerPage = function renderFileManagerPage(main) {
    const st = fmState();
    if (!main) return;
    main.dataset.page = 'files';

    // Save tree scroll position before re-rendering
    const treePanel = main.querySelector('.fm-scroll');
    if (treePanel) {
      st.treeScrollTop = treePanel.scrollTop;
    }

    const treeRows = renderTreeRows('/', 0);
    const showingFile = !!st.currentFile;
    const filterActive = !showingFile && String(st.filterText || '').trim().length > 0;
    const folderItems = Array.isArray(st.folderChildren) ? st.folderChildren : [];
    const folderIsEmpty = !showingFile && !filterActive && folderItems.length === 0;
    const selectedFolderPath = String(st.selectedFolderPath || '/');
    const canDeleteEmptyFolder = folderIsEmpty && selectedFolderPath !== '/' && !isVirtualPath(selectedFolderPath);
    const mainPanelBody = showingFile ? renderEditorPane() : (filterActive ? renderFilterResults() : renderFolderRows());
    const mainPanelTitle = showingFile
      ? fmEsc(st.currentFile ? st.currentFile.name : 'Editor / Preview')
      : fmEsc(currentFolderName());
    const mainPanelActions = showingFile
      ? '<div class="flex items-center gap-2"><button type="button" class="px-2 py-1 text-xs rounded bg-gray-700 hover:bg-gray-600" data-action="fm-open-rename-file">Rename</button><button type="button" class="px-2 py-1 text-xs rounded bg-red-800 hover:bg-red-700" data-action="fm-open-delete-file">Delete</button><button type="button" class="px-2 py-1 text-xs rounded bg-gray-700 hover:bg-gray-600" data-action="fm-close-file">Back to folder</button>' + saveBadgeHtml() + '</div>'
      : ('<div class="flex items-center gap-2">'
        + (selectedFolderPath !== '/' && !isVirtualPath(selectedFolderPath) ? '<button type="button" class="px-2 py-1 text-xs rounded bg-gray-700 hover:bg-gray-600" data-action="fm-open-rename-folder">Rename</button>' : '')
        + (canDeleteEmptyFolder ? '<button type="button" class="px-2 py-1 text-xs rounded bg-red-800 hover:bg-red-700" data-action="fm-open-delete-folder">Delete Folder</button>' : '')
        + '<button type="button" class="px-2 py-1 text-xs rounded bg-blue-700 hover:bg-blue-600" data-action="fm-open-create-folder">Create Folder</button>'
        + '</div>');

    let modal = '';
    if (st.deleteModalOpen) {
      const typeLabel = st.deleteTargetType === 'folder' ? 'folder' : 'file';
      const confirmLabel = st.deleteBusy ? 'Deleting...' : 'Delete';
      const disabledAttr = st.deleteBusy ? ' disabled' : '';
      modal = ''
        + '<div class="fm-modal-backdrop">'
        + '<div class="fm-modal space-y-3">'
        + '<div class="text-sm font-semibold">Delete ' + typeLabel + '?</div>'
        + '<div class="text-sm text-gray-300">This permanently deletes <span class="font-semibold">' + fmEsc(st.deleteTargetName || st.deleteTargetPath || typeLabel) + '</span>.</div>'
        + '<div class="flex justify-end gap-2">'
        + '<button type="button" class="px-3 py-1.5 rounded bg-gray-700" data-action="fm-delete-cancel"' + disabledAttr + '>Cancel</button>'
        + '<button id="fmDeleteConfirm" type="button" class="px-3 py-1.5 rounded bg-red-800 hover:bg-red-700" data-action="fm-delete-confirm"' + disabledAttr + '>' + confirmLabel + '</button>'
        + '</div>'
        + '</div>'
        + '</div>';
    } else if (st.createFolderModalOpen) {
      modal = ''
        + '<div class="fm-modal-backdrop">'
        + '<div class="fm-modal space-y-3">'
        + '<div class="text-sm font-semibold">Create folder</div>'
        + '<input id="fmCreateFolderName" type="text" value="' + fmEsc(st.createFolderName || '') + '" class="w-full rounded bg-gray-800 border border-gray-700 px-3 py-2 text-sm" placeholder="Folder name" />'
        + '<div class="flex justify-end gap-2">'
        + '<button type="button" class="px-3 py-1.5 rounded bg-gray-700" data-action="fm-create-cancel">Cancel</button>'
        + '<button type="button" class="px-3 py-1.5 rounded bg-blue-700" data-action="fm-create-confirm">Create</button>'
        + '</div>'
        + '</div>'
        + '</div>';
    }

    if (!st.deleteModalOpen && !st.createFolderModalOpen && st.renameModalOpen) {
      const typeLabel = st.renameTargetType === 'folder' ? 'folder' : 'file';
      const confirmLabel = st.renameBusy ? 'Renaming...' : 'Rename';
      const disabledAttr = st.renameBusy ? ' disabled' : '';
      modal = ''
        + '<div class="fm-modal-backdrop">'
        + '<div class="fm-modal space-y-3">'
        + '<div class="text-sm font-semibold">Rename ' + typeLabel + '</div>'
        + '<input id="fmRenameName" type="text" value="' + fmEsc(st.renameName || '') + '" class="w-full rounded bg-gray-800 border border-gray-700 px-3 py-2 text-sm" placeholder="New name" />'
        + (st.error ? '<div class="text-xs text-red-300">' + fmEsc(st.error) + '</div>' : '')
        + '<div class="flex justify-end gap-2">'
        + '<button type="button" class="px-3 py-1.5 rounded bg-gray-700" data-action="fm-rename-cancel"' + disabledAttr + '>Cancel</button>'
        + '<button id="fmRenameConfirm" type="button" class="px-3 py-1.5 rounded bg-blue-700 hover:bg-blue-600" data-action="fm-rename-confirm"' + disabledAttr + '>' + confirmLabel + '</button>'
        + '</div>'
        + '</div>'
        + '</div>';
    }

    if (!st.deleteModalOpen && !st.createFolderModalOpen && !st.renameModalOpen && st.filePickerModalOpen) {
      const fpTreeRows = renderFilePickerTree('/', 0, true);
      const fpChildRows = renderFilePickerChildren();
      const fpCurrentPath = String(st.filePickerCurrentPath || '/');
      modal = ''
        + '<div class="fm-modal-backdrop">'
        + '<div class="fm-modal space-y-3">'
        + '<div class="text-sm font-semibold">' + fmEsc(st.filePickerTitle) + '</div>'
        + '<div style="display: flex; gap: 8px; max-height: 400px; min-height: 300px; border: 1px solid #374151; border-radius: 6px; overflow: hidden; background-color: #1f2937;">'
        + '<div style="flex: 0 0 200px; border-right: 1px solid #374151; overflow-y: auto;">'
        + '<div style="padding: 8px;">' + fpTreeRows + '</div>'
        + '</div>'
        + '<div style="flex: 1; overflow-y: auto;">'
        + '<div style="padding: 8px;">'
        + (fpCurrentPath && fpCurrentPath !== '/' ? '<div class="fm-file-row" data-action="fp-enter-folder" data-path="' + fmEsc(parentPath(fpCurrentPath)) + '"><div class="text-xs text-gray-500">..</div><div class="text-sm truncate">Parent</div></div>' : '')
        + fpChildRows
        + '</div>'
        + '</div>'
        + '</div>'
        + '<div class="flex justify-end gap-2">'
        + '<button type="button" class="px-3 py-1.5 rounded bg-gray-700" data-action="fp-cancel">Cancel</button>'
        + '</div>'
        + '</div>'
        + '</div>';
    }

    if (st.loading) {
      main.innerHTML = '<div class="px-4 py-4 text-sm text-gray-400">Loading file manager...</div>';
      return;
    }

    main.innerHTML = ''
      + '<div class="h-full min-h-0 p-2">'
      + '<div class="fm-layout">'
      + '<section class="fm-panel">'
      + '<div class="px-3 py-2 border-b border-gray-800 flex items-center justify-between gap-2">'
      + '<div class="text-sm font-semibold">Workspace Tree</div>'
      + '<button type="button" class="px-2 py-1 text-xs rounded bg-blue-700 hover:bg-blue-600 whitespace-nowrap" data-action="fm-open-create-root-folder">Create Folder</button>'
      + '</div>'
      + '<div class="fm-scroll px-2 py-2">' + treeRows + '</div>'
      + '</section>'
      + '<section class="fm-panel">'
      + '<div class="px-3 py-2 border-b border-gray-800 flex items-center justify-between gap-2">'
      + '<div class="text-sm font-semibold truncate">' + mainPanelTitle + '</div>'
      + mainPanelActions
      + '</div>'
      + (!showingFile ? '<div class="px-3 py-2 border-b border-gray-700"><input type="search" id="fmFilterInput" value="' + fmEsc(st.filterText || '') + '" class="w-full rounded bg-gray-800 border border-gray-700 px-3 py-1.5 text-sm" placeholder="Search all files and folders..." /></div>' : '')
      + '<div class="fm-scroll">' + mainPanelBody + '</div>'
      + '</section>'
      + '</div>'
      + (st.error ? '<div class="px-2 pt-2 text-xs text-red-300">' + fmEsc(st.error) + '</div>' : '')
      + '</div>'
      + modal;

    updateSaveBadgeDom();

    if (st.createFolderModalOpen) {
      setTimeout(() => {
        const input = document.getElementById('fmCreateFolderName');
        if (input) input.focus();
      }, 0);
    }
    if (st.renameModalOpen) {
      setTimeout(() => {
        const inp = document.getElementById('fmRenameName');
        if (inp) { inp.focus(); inp.select(); }
      }, 0);
    }
    if (st.deleteModalOpen) {
      setTimeout(() => {
        const btn = document.getElementById('fmDeleteConfirm');
        if (btn && !st.deleteBusy) btn.focus();
      }, 0);
    }

    setTimeout(() => {
      // Restore tree scroll position after DOM update
      const treePanel = main.querySelector('.fm-scroll');
      if (treePanel && st.treeScrollTop > 0) {
        treePanel.scrollTop = st.treeScrollTop;
      }
      void mountEditors();
    }, 0);
  };

  window.handleFileManagerClick = function handleFileManagerClick(target, event) {
    const st = fmState();
    const toggle = target.closest('[data-action="fm-toggle-folder"]');
    if (toggle) {
      event.preventDefault();
      event.stopPropagation();
      void toggleTreeFolder(String(toggle.dataset.path || '/'));
      return true;
    }

    const selectFolderBtn = target.closest('[data-action="fm-select-folder"]');
    if (selectFolderBtn) {
      event.preventDefault();
      const path = String(selectFolderBtn.dataset.path || '/');
      void selectFolder(path);
      return true;
    }

    const selectFileBtn = target.closest('[data-action="fm-select-file"]');
    if (selectFileBtn) {
      event.preventDefault();
      const path = String(selectFileBtn.dataset.path || '');
      if (path) void selectFile(path);
      return true;
    }

    const closeFile = target.closest('[data-action="fm-close-file"]');
    if (closeFile) {
      event.preventDefault();
      st.selectedFilePath = '';
      st.currentFile = null;
      destroyEditors();
      renderFileManagerPage(fmMain());
      return true;
    }

    const openCreate = target.closest('[data-action="fm-open-create-folder"]');
    if (openCreate) {
      event.preventDefault();
      openCreateFolderModal(st.selectedFolderPath || '/');
      return true;
    }

    const openCreateRoot = target.closest('[data-action="fm-open-create-root-folder"]');
    if (openCreateRoot) {
      event.preventDefault();
      openCreateFolderModal('/');
      return true;
    }

    const openDeleteFile = target.closest('[data-action="fm-open-delete-file"]');
    if (openDeleteFile) {
      event.preventDefault();
      if (st.currentFile && st.currentFile.path) {
        openDeleteModal('file', st.currentFile.path, st.currentFile.name || st.currentFile.path);
      }
      return true;
    }

    const openDeleteFolder = target.closest('[data-action="fm-open-delete-folder"]');
    if (openDeleteFolder) {
      event.preventDefault();
      const path = String(st.selectedFolderPath || '/');
      if (path !== '/' && !isVirtualPath(path)) {
        openDeleteModal('folder', path, currentFolderName());
      }
      return true;
    }

    const cancelCreate = target.closest('[data-action="fm-create-cancel"]');
    if (cancelCreate) {
      event.preventDefault();
      st.createFolderModalOpen = false;
      st.createFolderName = '';
      st.createFolderParentPath = '/';
      renderFileManagerPage(fmMain());
      return true;
    }

    const confirmCreate = target.closest('[data-action="fm-create-confirm"]');
    if (confirmCreate) {
      event.preventDefault();
      void createFolder();
      return true;
    }

    const cancelDelete = target.closest('[data-action="fm-delete-cancel"]');
    if (cancelDelete) {
      event.preventDefault();
      if (!st.deleteBusy) closeDeleteModal();
      return true;
    }

    const confirmDeleteBtn = target.closest('[data-action="fm-delete-confirm"]');
    if (confirmDeleteBtn) {
      event.preventDefault();
      if (!st.deleteBusy) {
        void confirmDelete();
      }
      return true;
    }

    const openRenameFile = target.closest('[data-action="fm-open-rename-file"]');
    if (openRenameFile) {
      event.preventDefault();
      if (st.currentFile && st.currentFile.path) {
        openRenameModal('file', st.currentFile.path, st.currentFile.name || st.currentFile.path);
      }
      return true;
    }

    const openRenameFolder = target.closest('[data-action="fm-open-rename-folder"]');
    if (openRenameFolder) {
      event.preventDefault();
      const path = String(st.selectedFolderPath || '/');
      if (path !== '/' && !isVirtualPath(path)) {
        openRenameModal('folder', path, currentFolderName());
      }
      return true;
    }

    const cancelRename = target.closest('[data-action="fm-rename-cancel"]');
    if (cancelRename) {
      event.preventDefault();
      if (!st.renameBusy) closeRenameModal();
      return true;
    }

    const confirmRenameBtn = target.closest('[data-action="fm-rename-confirm"]');
    if (confirmRenameBtn) {
      event.preventDefault();
      if (!st.renameBusy) void confirmRename();
      return true;
    }

    const fpToggleFolder = target.closest('[data-action="fp-toggle-folder"]');
    if (fpToggleFolder) {
      event.preventDefault();
      event.stopPropagation();
      const path = String(fpToggleFolder.dataset.path || '/');
      if (st.filePickerExpandedPaths[path]) {
        delete st.filePickerExpandedPaths[path];
      } else {
        st.filePickerExpandedPaths[path] = true;
        void loadFilePickerTree(path).then(() => renderFileManagerPage(fmMain()));
      }
      renderFileManagerPage(fmMain());
      return true;
    }

    const fpEnterFolder = target.closest('[data-action="fp-enter-folder"]');
    if (fpEnterFolder) {
      event.preventDefault();
      const path = String(fpEnterFolder.dataset.path || '/');
      void loadFilePickerFolder(path).then(() => renderFileManagerPage(fmMain()));
      return true;
    }

    const fpSelectFile = target.closest('[data-action="fp-select-file"]');
    if (fpSelectFile) {
      event.preventDefault();
      if (fpSelectFile.dataset.disabled === 'true') {
        return true;
      }
      const path = String(fpSelectFile.dataset.path || '');
      insertFilePickerResult(path);
      closeFilePicker();
      return true;
    }

    const fpCancel = target.closest('[data-action="fp-cancel"]');
    if (fpCancel) {
      event.preventDefault();
      closeFilePicker();
      return true;
    }

    return false;
  };

  window.handleFileManagerInput = function handleFileManagerInput(target) {
    const st = fmState();
    if (!target) return false;
    if (target.id === 'fmCreateFolderName') {
      st.createFolderName = String(target.value || '');
      return true;
    }
    if (target.id === 'fmRenameName') {
      st.renameName = String(target.value || '');
      return true;
    }
    if (target.id === 'fmFilterInput') {
      st.filterText = String(target.value || '');
      queueFilter(st.filterText);
      return true;
    }
    return false;
  };

  window.handleFileManagerKeydown = function handleFileManagerKeydown(event) {
    const st = fmState();
    const t = event.target;
    if (!t) return false;
    if (t.id === 'fmCreateFolderName' && event.key === 'Enter') {
      event.preventDefault();
      void createFolder();
      return true;
    }
    if (t.id === 'fmRenameName' && event.key === 'Enter') {
      event.preventDefault();
      if (!st.renameBusy) void confirmRename();
      return true;
    }
    if (event.key === 'Escape' && st.renameModalOpen) {
      if (!st.renameBusy) closeRenameModal();
      return true;
    }
    if (st.deleteModalOpen && event.key === 'Enter' && !st.deleteBusy) {
      event.preventDefault();
      void confirmDelete();
      return true;
    }
    if (event.key === 'Escape' && st.deleteModalOpen) {
      event.preventDefault();
      if (!st.deleteBusy) closeDeleteModal();
      return true;
    }
    if (event.key === 'Escape' && st.createFolderModalOpen) {
      st.createFolderModalOpen = false;
      st.createFolderName = '';
      st.createFolderParentPath = '/';
      renderFileManagerPage(fmMain());
      return true;
    }
    return false;
  };

  window.ensureFileManagerReady = function ensureFileManagerReady() {
    void ensureInitialized(false);
  };

  window.fmOpenFile = function fmOpenFile(path, options) {
    if (!path) return;
    const keepHash = !!(options && options.keepHash);
    if (!keepHash) {
      navigate('files');
    }
    setTimeout(() => { void selectFile(path); }, 0);
  };

  // Navigate to a path from a URL hash — treat as file if it has an extension, else as folder
  window.fmNavigatePath = async function fmNavigatePath(path) {
    if (!path) return;
    const lastSegment = path.split('/').pop() || '';
    if (lastSegment.includes('.')) {
      await selectFile(path);
    } else {
      await selectFolder(path);
    }
  };

  window.handleFileManagerFsChanged = function handleFileManagerFsChanged(msg) {
    const st = fmState();
    const resync = !!(msg && msg.resyncRequired);

    if (resync) {
      st.initialized = false;
      st.treeByPath = {};
      st.folderChildren = [];
      void ensureInitialized(true);
      return;
    }

    // Soft refresh: reload tree and current folder
    st.treeByPath = {};
    void loadTree('/').then(() => {
      return loadFolder(st.selectedFolderPath || '/');
    }).then(() => {
      renderFileManagerPage(fmMain());
    }).catch((err) => {
      st.error = String(err && err.message ? err.message : err);
      renderFileManagerPage(fmMain());
    });
  };

})();
