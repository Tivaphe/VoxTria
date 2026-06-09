@echo off
REM ============================================================
REM  Installation de l'Assistant Vocal FR (Windows)
REM  - cree un environnement virtuel .venv
REM  - installe toutes les dependances
REM ============================================================
setlocal
cd /d "%~dp0"

echo.
echo === Assistant Vocal FR - Installation ===
echo.

REM --- Verifier Python ---
python --version >nul 2>&1
if errorlevel 1 (
    echo [ERREUR] Python n'est pas trouve dans le PATH.
    echo Installe Python 3.10+ depuis https://www.python.org/downloads/
    echo et coche "Add Python to PATH" pendant l'installation.
    pause
    exit /b 1
)

echo [1/3] Creation de l'environnement virtuel (.venv)...
if not exist ".venv" (
    python -m venv .venv
    if errorlevel 1 ( echo [ERREUR] Echec creation venv & pause & exit /b 1 )
) else (
    echo     .venv existe deja, on reutilise.
)

echo [2/3] Mise a jour de pip...
call ".venv\Scripts\python.exe" -m pip install --upgrade pip

echo [3/3] Installation des dependances (peut prendre quelques minutes)...
call ".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 ( echo [ERREUR] Echec installation dependances & pause & exit /b 1 )

echo.
echo === Installation terminee avec succes ! ===
echo Lance maintenant "run.bat" pour demarrer l'assistant.
echo.
echo NOTE: pour le micro, ffmpeg est recommande.
echo       Installe-le avec : winget install Gyan.FFmpeg
echo.
pause
