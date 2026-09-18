@echo off
rem rosarium webmap launcher (Windows): double-click, or run from any terminal.
rem Activates the conda env and starts "python rosarium.py webmap --open".
rem Keep this window open while using the map; Ctrl+C or closing it stops
rem the server. Extra arguments are passed on: rosarium.bat --port 9000
rem
rem Desktop icon: run make_shortcut.bat once (same folder).

setlocal
set "ENV_NAME=rosarium"

rem Find the conda base install (miniforge first, then the classic ones)
set "CONDA_BASE="
for %%d in ("%USERPROFILE%\miniforge3" "%USERPROFILE%\miniconda3" "%USERPROFILE%\anaconda3" "%LOCALAPPDATA%\miniforge3") do (
    if not defined CONDA_BASE if exist "%%~d\Scripts\activate.bat" set "CONDA_BASE=%%~d"
)
if not defined CONDA_BASE (
    echo Could not find a conda install: set CONDA_BASE in launch\rosarium.bat
    pause
    exit /b 1
)

rem Run from the repository root (this file sits in launch\)
cd /d "%~dp0.."

call "%CONDA_BASE%\Scripts\activate.bat" "%ENV_NAME%"
if errorlevel 1 (
    echo Could not activate the conda env "%ENV_NAME%"
    pause
    exit /b 1
)

python rosarium.py webmap --open %*
rem Keep the window open on a failure so the message can be read
if errorlevel 1 pause
