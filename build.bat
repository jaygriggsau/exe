@echo off
REM Build DiskStat.exe with PyInstaller. Run on Windows with Python 3.8+.
setlocal

where python >nul 2>&1
if errorlevel 1 (
    echo Python not found on PATH. Install Python 3.8+ from python.org.
    exit /b 1
)

python -m pip install --upgrade pip pyinstaller || exit /b 1

python -m PyInstaller ^
    --noconfirm ^
    --clean ^
    --onefile ^
    --windowed ^
    --name DiskStat ^
    diskstat.py || exit /b 1

echo.
echo Built: dist\DiskStat.exe
endlocal
