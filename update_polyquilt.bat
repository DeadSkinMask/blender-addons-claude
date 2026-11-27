@echo off
rem ---------------------------------------------------------------------------
rem  Installs / updates "PolyQuilt Luis Fork" without opening Blender's UI.
rem
rem  Usage:  update_polyquilt.bat [path\to\blender.exe] [path\to\polyquilt_luis_fork-X.Y.Z.zip]
rem
rem  - Blender: 1st argument, else %BLENDER_EXE%, else the newest
rem    "%ProgramFiles%\Blender Foundation\Blender *" folder.
rem  - ZIP: 2nd argument, else the newest polyquilt_luis_fork-*.zip next to this script.
rem
rem  It removes the previous install first (installing over the old one can leave the old
rem  code running), then installs the ZIP into the "user_default" repository and enables it.
rem  The old id "PolyQuilt_Fork" is removed too. Add-on preferences are not carried over when
rem  the id changes (1.46.x -> 1.47.0 only).
rem ---------------------------------------------------------------------------
setlocal EnableExtensions

set "BLENDER=%~1"
if not defined BLENDER if defined BLENDER_EXE set "BLENDER=%BLENDER_EXE%"
if not defined BLENDER (
    for /f "delims=" %%D in ('dir /b /ad /o-n "%ProgramFiles%\Blender Foundation\Blender *" 2^>nul') do (
        if not defined BLENDER set "BLENDER=%ProgramFiles%\Blender Foundation\%%D\blender.exe"
    )
)
if not defined BLENDER (
    echo [ERROR] blender.exe not found. Pass it as the first argument.
    goto :fail
)
if not exist "%BLENDER%" (
    echo [ERROR] "%BLENDER%" does not exist.
    goto :fail
)

set "ZIP=%~2"
if not defined ZIP (
    for /f "delims=" %%Z in ('dir /b /o-n "%~dp0polyquilt_luis_fork-*.zip" 2^>nul') do (
        if not defined ZIP set "ZIP=%~dp0%%Z"
    )
)
if not defined ZIP (
    echo [ERROR] No polyquilt_luis_fork-*.zip found next to this script. Pass it as the second argument.
    goto :fail
)
if not exist "%ZIP%" (
    echo [ERROR] "%ZIP%" does not exist.
    goto :fail
)

echo Blender: %BLENDER%
echo ZIP:     %ZIP%
echo.
echo [1/3] Removing previous versions (errors for "not installed" are fine)...
"%BLENDER%" --command extension remove polyquilt_luis_fork
"%BLENDER%" --command extension remove PolyQuilt_Fork

echo.
echo [2/3] Installing and enabling...
"%BLENDER%" --command extension install-file -r user_default -e "%ZIP%"
if errorlevel 1 (
    echo [ERROR] Installation failed.
    goto :fail
)

echo.
echo [3/3] Installed extensions matching "polyquilt":
"%BLENDER%" --command extension list 2>nul | findstr /i "polyquilt"

echo.
echo Done. Restart Blender if it was open.
if not defined NOPAUSE pause
exit /b 0

:fail
if not defined NOPAUSE pause
exit /b 1
