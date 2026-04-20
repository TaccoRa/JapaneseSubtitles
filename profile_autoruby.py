#!/usr/bin/env python3
"""
Profile auto-ruby performance for:
1. Episode switching with auto-ruby ON
2. Episode switching with auto-ruby OFF
3. Analyze cache behavior and bottlenecks
"""

import os
import sys
import logging
import time

# Setup logging to see the profiling output
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src', 'SubtitlePlayer'))

from model.config_manager import ConfigManager
from model.subtitle_manager import SubtitleManager

def test_scenario(desc: str, auto_ruby_enabled: bool, episodes: list):
    """Test a scenario with the given settings and episodes"""
    print("\n" + "="*80)
    print(f"TEST: {desc}")
    print(f"AutoRuby: {auto_ruby_enabled}")
    print("="*80 + "\n")
    
    # Setup config
    config = ConfigManager()
    config.set("SUBTITLE_AUTO_RUBY", auto_ruby_enabled)
    
    # Initialize subtitle manager
    manager = SubtitleManager(config)
    
    print(f"\nLoaded initial episode. Cache size: {len(manager._auto_ruby_cache)}\n")
    
    # Simulate episode switching
    for i, episode_rec in enumerate(episodes):
        print(f"\n--- Episode Switch {i+1}/{len(episodes)} ---")
        print(f"Loading: {episode_rec['name']}")
        
        start = time.time()
        manager._load_local_record(episode_rec)
        elapsed = time.time() - start
        
        print(f"✓ Loaded in {elapsed:.3f}s")
        print(f"  Cache size now: {len(manager._auto_ruby_cache)}")

def main():
    """Main profiling entry point"""
    
    # Discover available episodes
    subs_path = os.path.join(os.path.dirname(__file__), "subs", "Dr.Stone")
    if not os.path.isdir(subs_path):
        print(f"ERROR: Cannot find test episodes at {subs_path}")
        sys.exit(1)
    
    # Get first 5 episodes
    episodes = []
    for fn in sorted(os.listdir(subs_path)):
        if fn.endswith(('.srt', '.ass', '.ssa')):
            episodes.append({
                "name": fn,
                "path": os.path.join(subs_path, fn),
            })
            if len(episodes) >= 5:
                break
    
    if not episodes:
        print(f"ERROR: No subtitle files found in {subs_path}")
        sys.exit(1)
    
    print(f"Found {len(episodes)} test episodes")
    for ep in episodes:
        print(f"  - {ep['name']}")
    
    # Test scenario 1: AutoRuby OFF
    test_scenario(
        "BASELINE: AutoRuby OFF (no ruby processing)",
        auto_ruby_enabled=False,
        episodes=episodes[1:]  # Switch to remaining episodes
    )
    
    # Test scenario 2: AutoRuby ON
    test_scenario(
        "AUTO RUBY ON: Ruby processing enabled",
        auto_ruby_enabled=True,
        episodes=episodes[1:]  # Switch to remaining episodes
    )

if __name__ == "__main__":
    main()
