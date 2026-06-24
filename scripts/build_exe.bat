@echo off
REM ============================================================================
REM Build Claude Session Manager into a single standalone .exe (PyInstaller).
REM
REM   - One file:   everything packed into dist\ClaudeSessionManager.exe
REM   - Windowed:   no console window when the app launches
REM   - PyInstaller is pulled in temporarily via "uv run --with" so it never
REM     becomes a project dependency (same approach as scripts\png_to_ico.py).
REM
REM Usage (from anywhere):  scripts\build_exe.bat
REM Output:                 dist\ClaudeSessionManager.exe
REM ============================================================================

setlocal

REM Project root = the directory above this script.
set "ROOT=%~dp0.."
pushd "%ROOT%"

set "NAME=ClaudeSessionManager"
set "ICON=claude_session_manager\assets\claude.ico"
set "PNG=claude_session_manager\assets\claude.png"
set "ENTRY=claude_session_manager\__main__.py"

REM Fail fast if a bundled resource is missing. Resources must be prepared
REM BEFORE packaging; this script only consumes them, it never generates them.
REM Keeping build decoupled means a missing resource surfaces here instead of
REM being silently papered over.
if not exist "%ICON%" goto :missing
if not exist "%PNG%" goto :missing

echo.
echo === Building %NAME%.exe (single file, windowed) ===
echo.

REM --add-data uses "SRC;DEST" on Windows. Bundle the icon assets so the app
REM resolves them at runtime inside the unpacked bundle.
uv run --with pyinstaller pyinstaller ^
  --onefile ^
  --windowed ^
  --name "%NAME%" ^
  --icon "%ICON%" ^
  --add-data "claude_session_manager\assets\claude.ico;claude_session_manager\assets" ^
  --add-data "claude_session_manager\assets\claude.png;claude_session_manager\assets" ^
  --collect-submodules claude_session_manager ^
  --noconfirm ^
  --clean ^
  "%ENTRY%"

set "RC=%ERRORLEVEL%"

REM Clean up intermediate build artifacts, keep only dist\%NAME%.exe.
if exist "build" rmdir /s /q "build"
if exist "%NAME%.spec" del /q "%NAME%.spec"

echo.
if "%RC%"=="0" (
    echo === Done: dist\%NAME%.exe ===
) else (
    echo === Build FAILED ^(exit code %RC%^) ===
)

popd
endlocal & exit /b %RC%

:missing
echo ERROR: required resource not found.
echo   ICON: %ICON%
echo   PNG:  %PNG%
echo Generate the icon first, e.g.:
echo   uv run --with Pillow python scripts\png_to_ico.py "%PNG%"
popd
endlocal & exit /b 1
