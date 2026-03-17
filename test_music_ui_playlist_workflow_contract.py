from pathlib import Path


def test_music_page_uses_save_playlist_label_and_conflict_warning() -> None:
    source = Path("orchestrator/web/realtime_service.py").read_text(encoding="utf-8")

    assert "Save Playlist" in source
    assert "Playlist exists. Saving will overwrite it." in source
    assert "const loadedPlaylist=String((S.music&&S.music.loaded_playlist)||'').trim().toLowerCase();" in source


def test_remove_selected_sends_song_ids_for_stable_deletes() -> None:
    source = Path("orchestrator/web/realtime_service.py").read_text(encoding="utf-8")

    assert "sendMusicAction('music_remove_selected', {{positions: [], song_ids}});" in source
    assert "musicQueueSelectionByIds" in source


def test_music_page_uses_consistent_song_id_selection_state() -> None:
    source = Path("orchestrator/web/realtime_service.py").read_text(encoding="utf-8")

    assert "musicQueueSelectionByIds:{{}}, musicQueueLastCheckedId:null" in source
    assert "const selectedCount=Object.keys(S.musicQueueSelectionByIds||{{}})" in source
    assert ".filter(item=>!!S.musicQueueSelectionByIds[String(item.id||'').trim()])" in source
    assert "musicQueueLastCheckedPos" not in source
    assert "musicQueueSelection[String(cb.dataset.position)]" not in source


def test_music_manager_removal_advances_after_current_track_delete() -> None:
    source = Path("orchestrator/music/manager.py").read_text(encoding="utf-8")

    assert "await self.pool.execute(f\"deleteid {sid}\")" in source
    assert "if removed_current and state_before == \"play\":" in source
    assert "await self.pool.execute(f\"play {next_pos}\")" in source


def test_add_songs_requires_explicit_search_button_and_min_query_length() -> None:
    source = Path("orchestrator/web/realtime_service.py").read_text(encoding="utf-8")

    assert "const MUSIC_LIBRARY_SEARCH_MIN_LEN = 3;" in source
    assert "function submitMusicLibrarySearch()" in source
    assert "data-action=\"music-add-search-submit\"" in source
    assert "Enter at least '+MUSIC_LIBRARY_SEARCH_MIN_LEN+' letters to search" in source
    assert "musicAddHasSearched:false" in source
    assert "canSearch && S.musicAddHasSearched ? 'No matches found' : 'Search to find songs to add'" in source
    assert "S.musicLibrarySearchTimer = setTimeout" not in source


def test_music_ui_library_search_uses_mpd_and_min_length_guard() -> None:
    source = Path("orchestrator/music/manager.py").read_text(encoding="utf-8")

    assert "if len(q) < 3:" in source
    assert "f'search any \"{quoted}\"'" in source
    assert "f'search title \"{quoted}\"'" in source
    assert "f'search artist \"{quoted}\"'" in source
    assert "f'search album \"{quoted}\"'" in source
