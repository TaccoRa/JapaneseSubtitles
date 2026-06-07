"""
Minimal entry point for launching the SubtitlePlayer app.
#Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned
#Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy Restricted
# keep this for later information: to download subtitles uses mirror on github of:
# https://kitsunekko.net/dirlist.php?dir=subtitles/japanese/One_Piece/&sort=date&order=asc
"""

from app import SubtitlePlayerApp

if __name__ == "__main__":
    app = SubtitlePlayerApp()
    app.run()
