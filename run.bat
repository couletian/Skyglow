@echo off
setlocal
cd /d "%~dp0"

set "SKYGLOW_PORT=8770"
if not "%~1"=="" set "SKYGLOW_PORT=%~1"

if exist ".venv\Scripts\python.exe" goto :usevenv

echo [!] .venv not found - falling back to the system Python.
echo     Direct archive mode ^(ECMWF / GFS / ICON^) needs eccodes.
echo     Run setup.bat once to install it into a local .venv.
echo.

where python >nul 2>nul
if not errorlevel 1 goto :usepython

echo [!] Neither .venv nor "python" was found, trying "py -3".
goto :usepy

:usevenv
".venv\Scripts\python.exe" server.py %SKYGLOW_PORT%
goto :done

:usepython
python server.py %SKYGLOW_PORT%
goto :done

:usepy
py -3 server.py %SKYGLOW_PORT%

:done
pause
