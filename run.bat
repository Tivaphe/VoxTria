@echo off
REM ============================================================
REM  Lancement de l'Assistant Vocal FR (Windows)
REM  - demarre le serveur
REM  - ouvre la page web dans le navigateur
REM ============================================================
setlocal
cd /d "%~dp0"

REM --- Verifier que l'install a ete faite ---
if not exist ".venv\Scripts\python.exe" (
    echo [ERREUR] Environnement non trouve. Lance d'abord "install.bat".
    pause
    exit /b 1
)

set HOST=127.0.0.1
set PORT=8500

echo.
echo === Assistant Vocal FR ===
echo Serveur : http://%HOST%:%PORT%
echo.
echo RAPPEL : demarre ton serveur LLM (LM Studio ou llama.cpp) avant d'utiliser le chat.
echo Pour arreter l'assistant : ferme cette fenetre ou Ctrl+C.
echo.

REM --- Ouvrir le navigateur apres un court delai (laisse le serveur demarrer) ---
start "" /b cmd /c "timeout /t 3 >nul & start http://%HOST%:%PORT%"

REM --- Demarrer le serveur (bloquant) ---
call ".venv\Scripts\python.exe" -m uvicorn server:app --host %HOST% --port %PORT%

pause
