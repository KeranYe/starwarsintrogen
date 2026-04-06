@echo off
REM setup_and_run.bat
REM Run this in Command Prompt on Windows 11 to set up and launch the Star Wars Intro Editor.
REM Usage: double-click or run from the project root:  script\setup_and_run.bat

cd /d "%~dp0.."

REM Create virtual environment if it doesn't exist
IF NOT EXIST ".venv\" (
    echo Creating virtual environment...
    python -m venv .venv
)

REM Activate virtual environment
call .venv\Scripts\activate.bat

REM Install dependencies
echo Installing dependencies...
pip install --upgrade pip
pip install pillow numpy imageio imageio-ffmpeg

REM Run the app
echo Launching Star Wars Intro Editor...
python script\star_wars_intro_editor.py

pause
