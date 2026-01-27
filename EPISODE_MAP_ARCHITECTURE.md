# Episode Map Architecture

## Overview

The new episode mapping system builds a **comprehensive database** of all available episodes during initialization, eliminating the need for filtered searches and fixing the navigation fallback issues.

## How It Works

### 1. **Initialization Phase**

When you load an anime (via URL or local file):

```
Load URL/Local → _extract_and_set_remote_episode_metadata() 
                  → _build_comprehensive_episode_map()
```

The `_build_comprehensive_episode_map()` method:

1. **Searches for subtitle folders** (all folders containing the anime name)
   - Logs each folder found (you can see these in the logs)
   - Caches folder list in `self.cached_folders`

2. **Searches for ALL .srt files** in ALL folders (no season filter)
   - Example: When searching "Shingeki no Kyojin", it finds:
     ```
     subtitles/anime_tv/Shingeki no Kyojin/S1/...
     subtitles/anime_tv/Shingeki no Kyojin 3/...
     subtitles/anime_tv/Shingeki no Kyojin. The Final Season Part 2/...
     ```

3. **Parses every file** to extract:
   - Season and episode (SxxExx format)
   - Global episode number (in parentheses like S3E01(38))
   - File path for direct access

4. **Builds three maps**:
   - **`episode_map`**: `(season, episode) → remote_path`
     - Example: `(2, 13) → "subtitles/...S02E13.srt"`
   - **`global_episode_map`**: `global_episode → (season, episode)`
     - Example: `38 → (3, 1)` (global 38 = season 3 episode 1)
   - **`cached_folders`**: List of all folders searched

5. **Generates debugging files**:
   - `comprehensive_episodes.txt` - Complete map organized by season with parsing details
   - Shows which files parsed, which didn't, and the global episode mappings

### 2. **Navigation Phase**

When you press 'inc' or 'dec' to change episodes:

```
User clicks 'inc' at S2E12
  ↓
change_episode('inc')
  ↓
Try cache for S2E13 → Not found
  ↓
Try next season S3E1 (fetch from GitHub)
  ↓
If not found, use _find_next_from_map()
  ↓
episode_map has (3, 1) → download it
```

**Key difference**: The map is **built once** at initialization, so:
- No need to filter by season repeatedly
- Fallback can find ANY episode (including S6 which uses "Final Season Part 2" folder)
- Navigation works correctly across all seasons

### 3. **Why S2E37→S3E1 works but S2E13 didn't (before fix)**

**Old behavior (filtered searches)**:
- Search for S2E13 → searches folders, filters to S2 only
- S3 files excluded from results
- Fallback looks in filtered results → can't find S3E1
- **FAILS**

**New behavior (comprehensive map)**:
- Map built during init includes ALL episodes
- Fallback _find_next_from_map() looks at complete (season, episode) list
- Finds (2,37) in current position → returns next: (3,1)
- **WORKS**

## File Outputs

### 1. **comprehensive_episodes.txt** (NEW)

```
====================================
COMPREHENSIVE EPISODE MAP
====================================
Time: 2026-01-25T21:55:00.000000
Anime: Shingeki no Kyojin
Total files found: 156

────────────────────────────────────────────────────────────────
SEASON 1 (25 files)
────────────────────────────────────────────────────────────────
S01E01 [Global: 1]      | Attack on Titan.S01E01.Netflix.JA.srt
                        | subtitles/anime_tv/Shingeki no Kyojin/extra/...

...

────────────────────────────────────────────────────────────────
SEASON 3 (10 files)
────────────────────────────────────────────────────────────────
S03E01 [Global: 38]     | [Trix] Shingeki no Kyojin S03E01 (38) (BD 1080p AV1).srt
                        | subtitles/anime_tv/Shingeki no Kyojin 3/...

────────────────────────────────────────────────────────────────
GLOBAL EPISODE MAPPING
────────────────────────────────────────────────────────────────
Global E38 → S3E1
Global E39 → S3E2
...
Global E76 → S6E76
```

This file shows you:
- All files grouped by season
- Which files have global episode numbers
- The mapping between global and season-based numbering

### 2. **found_srt_files.txt** (existing, for season-specific searches)

Still created when you search for a specific season via the GUI. Shows MATCHED vs EXCLUDED for that season only.

## Fixing Your Issues

### Issue 1: S2E13 fallback failure
**Before**: Episode map only contained S2 files (filtered)
**After**: Episode map contains all episodes, so fallback finds S3E1

### Issue 2: S6 not found
**Before**: S6 in "The Final Season Part 2" folder wasn't in season 6 search results
**After**: Folder search finds "The Final Season Part 2", comprehensive map includes all S6 files

### Issue 3: Understanding what's searched
**Before**: Complex multi-step (folder search → file search → filter)
**After**: Clear two-step (all folders found, all files from those folders mapped)

## API Rate Limiting

The comprehensive map uses multiple API calls during initialization:
1. One search for folders (~1 API call)
2. One GET request per folder for files

**Optimization**: If you use the same anime frequently, the folder cache prevents re-searching folders.

## Debugging

When you need to understand the navigation:

1. **Check comprehensive_episodes.txt** to see:
   - All episodes available
   - Which files parsed correctly
   - Global episode mappings

2. **Check logs** for:
   - "Building comprehensive episode map from all files..."
   - "Found X subtitle folders for [anime]:"
   - "Built episode map with Y SxxExx entries and Z global episode mappings"
   - "Found next episode from map: S3E1"

3. **Folder list** is now logged, showing which folders are used for the anime

## Example Workflow

```python
# Initialization
SubtitleManager.__init__()
  → _initialize_remote_path()
    → _extract_and_set_remote_episode_metadata(url)
      → _build_comprehensive_episode_map()
        → _search_subtitle_folders()  # Finds all anime folders
        → For each folder: get all .srt files
        → Parse each file for season/episode/global
        → Build self.episode_map and self.global_episode_map
        → _log_comprehensive_episodes()  # Write debug file

# Later: User changes episode
change_episode('inc', raw=None)
  → Try cache: S2E13 not found
  → Try next season: S3E1
  → Download and load
```

## Summary

| Aspect | Before | After |
|--------|--------|-------|
| **Map building** | Lazy, per-search | Eager, once at init |
| **Coverage** | Filtered by season | All episodes |
| **Fallback** | Limited to filtered results | Works across all seasons |
| **S2E13→S3E1** | ❌ Fails | ✅ Works |
| **S6 discovery** | ❌ Misses S6 | ✅ Finds S6 |
| **Debug info** | `found_srt_files.txt` (season-specific) | `comprehensive_episodes.txt` (complete) + folder logs |

