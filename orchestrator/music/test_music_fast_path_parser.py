from orchestrator.music.parser import MusicFastPathParser


def test_stop_playing_with_trailing_failure_clause_matches_stop() -> None:
    parser = MusicFastPathParser()
    assert parser.parse("stop playing. didn't work") == ("stop", {})


def test_stop_playing_with_still_did_not_work_clause_matches_stop() -> None:
    parser = MusicFastPathParser()
    assert parser.parse("stop playing still didn't work") == ("stop", {})


def test_id_like_you_to_stop_playing_matches_stop() -> None:
    parser = MusicFastPathParser()
    assert parser.parse("I'd like you to stop playing") == ("stop", {})


def test_stop_transcript_maps_to_stop() -> None:
    parser = MusicFastPathParser()
    assert parser.parse("stop transcript") == ("stop", {})


def test_stop_transcription_maps_to_stop() -> None:
    parser = MusicFastPathParser()
    assert parser.parse("stop transcription") == ("stop", {})


def test_play_some_genre_with_music_matches_genre() -> None:
    parser = MusicFastPathParser()
    assert parser.parse("play some jazz music") == (
        "play_genre",
        {"genre": "jazz", "shuffle": True},
    )


def test_next_track_variant_skip_this_song_matches_next_track() -> None:
    parser = MusicFastPathParser()
    assert parser.parse("skip this song") == ("next_track", {})


def test_next_track_variant_next_one_matches_next_track() -> None:
    parser = MusicFastPathParser()
    assert parser.parse("next one") == ("next_track", {})


def test_next_track_variant_play_next_matches_next_track() -> None:
    parser = MusicFastPathParser()
    assert parser.parse("play next") == ("next_track", {})


def test_next_track_variant_play_the_next_track_matches_next_track() -> None:
    parser = MusicFastPathParser()
    assert parser.parse("play the next track") == ("next_track", {})


def test_next_track_variant_play_the_next_song_matches_next_track() -> None:
    parser = MusicFastPathParser()
    assert parser.parse("play the next song") == ("next_track", {})


def test_next_track_variant_go_to_next_track_matches_next_track() -> None:
    parser = MusicFastPathParser()
    assert parser.parse("go to next track") == ("next_track", {})


def test_next_track_variant_can_you_skip_this_song_matches_next_track() -> None:
    parser = MusicFastPathParser()
    assert parser.parse("can you skip this song") == ("next_track", {})


def test_play_song_by_artist_phrase_matches_play_song() -> None:
    parser = MusicFastPathParser()
    assert parser.parse("play big gun by acdc") == ("play_song", {"title": "big gun"})


def test_play_music_by_artist_phrase_matches_play_artist() -> None:
    parser = MusicFastPathParser()
    assert parser.parse("play music by acdc") == (
        "play_artist",
        {"artist": "acdc", "shuffle": True},
    )
