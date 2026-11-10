@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
title 유튜브 다운로더 준비 중

if exist ".venv\Scripts\python.exe" goto install

rem ---------- 파이썬 찾기 ----------
set "PYCMD="
py -3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>&1
if not errorlevel 1 set "PYCMD=py -3"
if defined PYCMD goto mkvenv
python -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>&1
if not errorlevel 1 set "PYCMD=python"
if defined PYCMD goto mkvenv

echo.
echo  파이썬 3.10 이상이 설치되어 있지 않습니다.
echo  winget 으로 Python 3.12 자동 설치를 시도합니다...
echo.
winget install -e --id Python.Python.3.12 --accept-package-agreements --accept-source-agreements
echo.
echo  ----------------------------------------------------------------
echo  설치가 끝났으면 이 창을 닫고 "실행.bat" 을 다시 실행해 주세요.
echo  자동 설치가 안 되면 https://www.python.org/downloads/ 에서 설치하고,
echo  설치 첫 화면에서 "Add python.exe to PATH" 를 꼭 체크하세요.
echo  ----------------------------------------------------------------
pause
exit /b 1

:mkvenv
echo [준비] 처음 실행입니다. 가상환경을 만드는 중... (1~2분)
%PYCMD% -m venv .venv
if errorlevel 1 goto venvfail

:install
echo [준비] yt-dlp 최신 버전 확인 중...
".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -q -U -r requirements.txt
if errorlevel 1 echo [경고] yt-dlp 설치/업데이트에 실패했습니다. 인터넷 연결을 확인하세요.

".venv\Scripts\python.exe" -c "import yt_dlp, tkinter" >nul 2>&1
if errorlevel 1 goto depfail

start "" ".venv\Scripts\pythonw.exe" app.py
exit /b 0

:venvfail
echo [오류] 가상환경을 만들지 못했습니다.
pause
exit /b 1

:depfail
echo [오류] yt-dlp 또는 tkinter 를 불러오지 못했습니다.
echo  - 인터넷 연결을 확인한 뒤 다시 실행하세요.
echo  - 계속 안 되면 .venv 폴더를 지우고 다시 실행하세요.
echo  - 파이썬 설치 시 "tcl/tk and IDLE" 항목이 체크되어 있어야 합니다.
pause
exit /b 1
