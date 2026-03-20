@echo off
setlocal

cd /d %~dp0

set "PY_CMD=python"
where %PY_CMD% >nul 2>nul
if errorlevel 1 (
  set "PY_CMD=py"
  where %PY_CMD% >nul 2>nul
)
if errorlevel 1 (
  echo Python was not found in PATH. Install Python or enable the launcher first.
  exit /b 1
)

echo Installing build dependencies...
%PY_CMD% -m pip install pyinstaller -q
if errorlevel 1 (
  echo Failed to install PyInstaller.
  exit /b 1
)

echo Building fast-start Windows desktop app (onedir)...
%PY_CMD% -m PyInstaller --noconfirm --clean --windowed --name AimployCMS --icon assets\aimploy_icon.ico --add-data "assets\aimploy_icon.ico;assets" --add-data "assets\aimploy_icon.png;assets" desktop_app.py
if errorlevel 1 (
  echo Build failed.
  exit /b 1
)

echo.
echo Build complete.
echo Executable: dist\AimployCMS\AimployCMS.exe
