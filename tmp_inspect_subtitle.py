import sys
import os
import pprint

root = os.path.abspath(os.path.dirname(__file__))
sys.path.insert(0, os.path.join(root, 'src'))
from SubtitlePlayer.model.config_manager import ConfigManager
from SubtitlePlayer.model.subtitle_manager import SubtitleManager

config = ConfigManager()
config.set('SUBTITLE_WRAP_LIMIT_PX', 4000)
config.set('SUBTITLE_AUTO_RUBY', True)
manager = SubtitleManager(config)

target = 11 * 60 + 22.765
matches = []
for idx, (clean, start, top, bottom) in enumerate(manager.display_data):
    if abs(start - target) < 0.01:
        matches.append((idx, clean, top, bottom))

pprint.pprint(matches)
