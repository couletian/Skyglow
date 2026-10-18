@echo off
setlocal
cd /d "%~dp0"

echo Skyglow setup: creating a local .venv with the GRIB decoders.
echo.

where py >nul 2>nul
if not errorlevel 1 goto :pyok
where python >nul 2>nul
if not errorlevel 1 goto :pythonok

echo [X] No Python found.
echo     Install Python 3.9 or newer from https://www.python.org/downloads/
echo     and tick "Add python.exe to PATH" during installation.
pause
exit /b 1

:pyok
set "PYCMD=py -3"
goto :checkvenv

:pythonok
set "PYCMD=python"

:checkvenv
if exist ".venv\Scripts\python.exe" goto :havevenv
echo [1/4] Creating .venv ...
%PYCMD% -m venv .venv
if errorlevel 1 goto :venvfail
goto :install

:havevenv
echo [1/4] Reusing existing .venv

:install
echo [2/4] Upgrading pip ...
".venv\Scripts\python.exe" -m pip install --upgrade pip --quiet

echo [3/4] Installing requirements from requirements.txt ...
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto :pipfail

echo [4/4] Verifying ...
".venv\Scripts\python.exe" -c "import eccodes, numpy, cryptography; print('  eccodes', eccodes.codes_get_api_version()); print('  numpy', numpy.__version__); print('  cryptography ok')"
if errorlevel 1 goto :verifyfail

echo.
echo Done. Start the app with:  run.bat
pause
exit /b 0

:venvfail
echo [X] Could not create .venv.
echo     Try running this file as a normal user in a writable folder.
pause
exit /b 1

:pipfail
echo.
echo [!] pip install failed.
echo     If eccodes has no wheel for your Python version, install Python 3.11
echo     or 3.12 and run this script again.
pause
exit /b 1

:verifyfail
echo.
echo [!] Packages installed but the import check failed.
echo     Check the messages above, then run run.bat and look at the startup banner.
pause
exit /b 1
