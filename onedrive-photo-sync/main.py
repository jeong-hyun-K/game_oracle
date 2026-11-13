"""원드라이브 → 구글 포토 사진 옮기기: 실행 파일.

실행:  python main.py
"""

import sys


def run() -> None:
    try:
        import requests  # noqa: F401
    except ImportError:
        sys.exit("requests 가 설치되어 있지 않습니다. 먼저 실행하세요:  pip install -r requirements.txt")
    try:
        import tkinter  # noqa: F401
    except ImportError:
        sys.exit("tkinter 가 없습니다. 리눅스라면:  sudo apt install python3-tk")
    from photosync.gui import main

    main()


if __name__ == "__main__":
    run()
