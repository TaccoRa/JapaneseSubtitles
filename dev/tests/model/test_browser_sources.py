"""Subtitle associations restore cached sources without commanding a player."""
from types import SimpleNamespace as NS
import pytest
from model.browser_sources import BrowserSources


class Config:
    def __init__(self): self.values = {}
    def get(self, key): return self.values.get(key)
    def set(self, key, value): self.values[key] = value


def manager(path, name, remote=False, episode=None):
    owner = NS(srt_file=str(path), anime_folder_name=name, display_anime_name=name,
        remote_flag=remote,current_season=1 if episode else None,current_episode=episode,
        is_movie=episode is None,all_results_items=[dict(path="anime.srt",episode=episode)],
        remote_url="https://github.com/example/repo/blob/main/anime.srt" if remote else None,
        config=Config(),loads=[],maps=[])
    def load(path):
        owner.loads.append(path)
        owner.srt_file=path
        # Simulate filename inference; restoration preserves the mapped episode.
        owner.current_episode=None
    owner._load_local_and_process=load
    owner.build_remote_episode_maps=lambda: owner.maps.append(True)
    return owner


def test_movie_anime_movie_round_trip_survives_restart_and_preserves_sources(tmp_path):
    anime,movie=tmp_path/'anime.srt',tmp_path/'Shrek.srt'
    anime.write_text('anime source');movie.write_text('movie source')
    sources=BrowserSources(tmp_path/'bindings.json')
    owner=manager(anime,'Our anime',True,7)
    sources.remember('anime-video',owner)
    owner.srt_file=str(movie);owner.anime_folder_name=owner.display_anime_name='Shrek'
    owner.remote_flag=False;owner.current_season=owner.current_episode=None;owner.is_movie=True
    sources.remember('shrek-video',owner)
    sources=BrowserSources(sources.path)
    assert sources.restore('anime-video',owner)
    assert owner.anime_folder_name=='Our anime' and owner.current_episode==7 and owner.remote_flag
    assert owner.maps==[True]
    assert sources.restore('shrek-video',owner)
    assert owner.anime_folder_name=='Shrek' and owner.current_episode is None and not owner.remote_flag
    assert owner.loads==[str(anime),str(movie)]
    assert not sources.restore('shrek-video',owner)
    assert anime.read_text()=='anime source' and movie.read_text()=='movie source'


def test_unknown_or_missing_media_never_guesses_another_source(tmp_path):
    path=tmp_path/'source.srt';path.write_text('source')
    owner=manager(path,'Anime',True,7)
    sources=BrowserSources(tmp_path/'bindings.json');sources.remember('known',owner)
    assert not sources.restore('unknown',owner)
    path.unlink()
    with pytest.raises(FileNotFoundError): sources.restore('known',owner)
    assert not owner.loads and owner.current_episode==7


def test_failed_restore_rolls_back_source_and_names(tmp_path):
    one,two=tmp_path/'one.srt',tmp_path/'two.srt'
    one.write_text('one');two.write_text('two')
    owner=manager(one,'Anime',True,7)
    sources=BrowserSources(tmp_path/'bindings.json');sources.remember('anime',owner)
    owner.srt_file=str(two);owner.anime_folder_name=owner.display_anime_name='Shrek';owner.remote_flag=False
    def fail(path): raise ValueError('invalid subtitle')
    owner._load_local_and_process=fail
    with pytest.raises(ValueError): sources.restore('anime',owner)
    assert owner.srt_file==str(two) and owner.anime_folder_name=='Shrek' and not owner.remote_flag


def test_known_browser_media_restores_source_once_without_player_commands(tmp_path):
    from test_native_capture import browser
    from model.browser_sources import BrowserSources
    from model.timing_map import digest
    path=tmp_path/'anime.srt';path.write_text('anime')
    owner=manager(path,'Our anime',True,7)
    owner.get_anime_name=lambda: owner.anime_folder_name
    sync=browser()
    sync._source_bindings=BrowserSources(tmp_path/'bindings.json')
    page='https://video.test/anime'
    sync._source_bindings.remember(digest(page.encode()),owner)
    movie=tmp_path/'Shrek.srt';movie.write_text('movie')
    owner.srt_file=str(movie);owner.anime_folder_name='Shrek';owner.remote_flag=False;owner.current_episode=None
    sync.controller.sub_manager=owner
    refreshed=[]
    sync.controller.episode_controller=NS(_after_episode_change=lambda:refreshed.append('source'),
        update_media_presentation=lambda:refreshed.append('title'))
    sync.invalidate=lambda:refreshed.append('invalidate')
    event=dict(type='MediaContext',identity=sync.identity.wire(),pageKey=page,title='Our anime',kind='episode',episode=7,season=1)
    sync._event(event);sync._event(event)
    assert owner.anime_folder_name=='Our anime' and owner.current_episode==7
    assert refreshed==['invalidate','source','title']
    assert sync.sent==[]
