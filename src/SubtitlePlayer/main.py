#Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned
#Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy Restricted
# keep this for later information: to download subtitles:
# https://kitsunekko.net/dirlist.php?dir=subtitles/japanese/One_Piece/&sort=date&order=asc

import sys
from PyQt5.QtWidgets import QApplication
from app import App

def main():
    app = QApplication(sys.argv)
    application = App()
    application.run()
    sys.exit(app.exec_())

if __name__ == "__main__":
    main()