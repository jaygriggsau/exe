@echo off
REM Launch DiskStat. Requires Python 3.8+ on PATH (tkinter ships with it on Windows).
python "%~dp0diskstat.py" %*
