@echo off
rem ---------------------------------------------------------------------------
rem  Installs / updates "PolyQuilt Luis Fork" without opening Blender's UI.
rem
rem  Usage:  update_polyquilt.bat [path\to\blender.exe] [path\to\polyquilt_luis_fork-X.Y.Z.zip] [/force]
rem
rem  - Blender: 1st argument, else %BLENDER_EXE%, else the newest
rem    "%ProgramFiles%\Blender Foundation\Blender *" folder.
rem  - ZIP: 2nd argument, else the newest polyquilt_luis_fork-*.zip next to this script.
rem  - The ZIP's version (taken from its file name, which Blender builds from the manifest) is
rem    compared with the installed blender_manifest.toml of that Blender version. Same version:
rem    nothing to do; installed is newer: it stops. /force installs anyway.
rem  - DRYRUN=1 only prints the Blender commands it would run.
rem
rem  It removes the previous install first (installing over the old one can leave the old
rem  code running), then installs the ZIP into the "user_default" repository and enables it.
rem  The old id "PolyQuilt_Fork" is removed too. Add-on preferences are not carried over when
rem  the id changes (1.46.x -> 1.47.0 only).
rem ---------------------------------------------------------------------------
setlocal EnableExtensions EnableDelayedExpansion

set "BLENDER="
set "ZIP="
set "FORCE="
for %%A in (%*) do (
    if /i "%%~A"=="/force" (
        set "FORCE=1"
    ) else if not defined BLENDER (
        set "BLENDER=%%~A"
    ) else if not defined ZIP (
        set "ZIP=%%~A"
    )
)

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
if not exist "!BLENDER!" (
    echo [ERROR] "!BLENDER!" does not exist.
    goto :fail
)

if not defined ZIP (
    for /f "delims=" %%Z in ('dir /b /o-n "%~dp0polyquilt_luis_fork-*.zip" 2^>nul') do (
        if not defined ZIP set "ZIP=%~dp0%%Z"
    )
)
if not defined ZIP (
    echo [ERROR] No polyquilt_luis_fork-*.zip found next to this script. Pass it as the second argument.
    goto :fail
)
if not exist "!ZIP!" (
    echo [ERROR] "!ZIP!" does not exist.
    goto :fail
)

rem --- version of the ZIP: polyquilt_luis_fork-1.47.0.zip -> 1.47.0
for %%I in ("%ZIP%") do set "ZIPNAME=%%~nI"
set "ZIPVER=%ZIPNAME:polyquilt_luis_fork-=%"

rem --- Blender folder name "Blender 5.3" -> "5.3" -> installed manifest
for %%I in ("%BLENDER%") do set "BPATH=%%~dpI"
set "BPATH=%BPATH:~0,-1%"
for %%J in ("%BPATH%") do set "BDIR=%%~nxJ"
set "BVER=%BDIR:Blender =%"
set "MANIFEST=%APPDATA%\Blender Foundation\Blender\!BVER!\extensions\user_default\polyquilt_luis_fork\blender_manifest.toml"
call :readver "%MANIFEST%" INSTVER

echo Blender:   !BLENDER!
echo ZIP:       !ZIP!  (version !ZIPVER!)
if defined INSTVER (echo Installed:  !INSTVER!) else (echo Installed:  none found in !MANIFEST!)

if defined INSTVER if not defined FORCE (
    call :vnum "!ZIPVER!" NEWN
    call :vnum "!INSTVER!" OLDN
    if !OLDN! EQU !NEWN! (
        echo Already up to date. Use /force to reinstall.
        goto :done
    )
    if !OLDN! GTR !NEWN! (
        echo The installed version is NEWER than the ZIP. Use /force to downgrade.
        goto :done
    )
)

set "RUN="!BLENDER!""
if defined DRYRUN set "RUN=echo [dry run] "!BLENDER!""

echo.
echo [1/3] Removing previous versions (errors for "not installed" are fine)...
%RUN% --command extension remove polyquilt_luis_fork
%RUN% --command extension remove PolyQuilt_Fork

echo.
echo [2/3] Installing and enabling...
%RUN% --command extension install-file -r user_default -e "!ZIP!"
if errorlevel 1 (
    echo [ERROR] Installation failed.
    goto :fail
)

echo.
echo [3/3] Result:
if defined DRYRUN goto :done
call :readver "%MANIFEST%" INSTVER
if "!INSTVER!"=="!ZIPVER!" (
    echo Installed version is now !INSTVER!. Restart Blender if it was open.
) else (
    echo [WARNING] Installed version reads "!INSTVER!" but the ZIP is !ZIPVER!. Check "!MANIFEST!".
)

:done
if not defined NOPAUSE pause
exit /b 0

:fail
if not defined NOPAUSE pause
exit /b 1

rem --- :readver "path\blender_manifest.toml" VAR  ->  VAR = value of its version line (empty if none)
:readver
set "%~2="
if not exist "%~1" exit /b 0
set "_v="
for /f "usebackq tokens=1,2 delims== " %%A in ("%~1") do (
    if /i "%%A"=="version" if not defined _v set "_v=%%~B"
)
set "%~2=%_v%"
exit /b 0

rem --- :vnum "1.47.0" VAR  ->  VAR = 1*1000000 + 47*1000 + 0
:vnum
for /f "tokens=1-3 delims=." %%a in ("%~1") do (
    set /a "%~2=%%a*1000000+%%b*1000+%%c"
)
exit /b 0
