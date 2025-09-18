@echo off
setlocal enabledelayedexpansion

echo ==============================================
echo  LMArena Bridge - Cliente Externo Installer
echo ==============================================
echo.

:: Verificar si Python está instalado
echo [1/4] Verificando instalacion de Python...
python --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python no esta instalado en este sistema.
    echo.
    echo Por favor instala Python desde https://python.org
    echo Asegurate de marcar "Add Python to PATH" durante la instalacion.
    echo.
    goto error_exit
)

for /f "tokens=2" %%a in ('python --version') do set PYTHON_VERSION=%%a
echo Python %PYTHON_VERSION% detectado correctamente.
echo.

:: Verificar si pip está disponible
echo [2/4] Verificando pip...
python -m pip --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: pip no esta disponible.
    echo.
    echo Intenta reinstalar Python o ejecutar: python -m ensurepip
    echo.
    goto error_exit
)
echo pip esta disponible.
echo.

:: Instalar dependencias
echo [3/4] Instalando dependencias del requirements.txt...
echo.
if not exist "requirements.txt" (
    echo ERROR: No se encontro el archivo requirements.txt
    echo.
    echo Asegurate de ejecutar este script desde el directorio del proyecto.
    echo.
    goto error_exit
)

python -m pip install -r requirements.txt
if errorlevel 1 (
    echo.
    echo ERROR: Fallo la instalacion de dependencias.
    echo.
    echo Intenta ejecutar manualmente: python -m pip install -r requirements.txt
    echo.
    goto error_exit
)

echo.
echo Dependencias instaladas exitosamente.
echo.

:: Verificar archivos necesarios
echo [4/4] Verificando archivos del cliente externo...
if not exist "external_client_server.py" (
    echo ERROR: No se encontro external_client_server.py
    goto error_exit
)

if not exist "external_client_config.jsonc" (
    echo ERROR: No se encontro external_client_config.jsonc
    echo.
    echo Este archivo es necesario para la configuracion del cliente.
    goto error_exit
)

if not exist "models.json" (
    echo WARNING: No se encontro models.json - se usara configuracion por defecto.
)

if not exist "model_endpoint_map.json" (
    echo WARNING: No se encontro model_endpoint_map.json - se usara configuracion por defecto.
)

echo Archivos verificados correctamente.
echo.

:: Mostrar información del cliente
echo ==============================================
echo  INFORMACION DEL CLIENTE EXTERNO
echo ==============================================
for /f "delims=" %%a in ('findstr /r "\"client_name\":" external_client_config.jsonc') do (
    set line=%%a
    set line=!line:~0,-1!
    for /f "tokens=2 delims=:" %%b in ("!line!") do (
        set client_name=%%b
        set client_name=!client_name: "=!
        set client_name=!client_name:"=!
        set client_name=!client_name:,=!
        echo Cliente: !client_name!
    )
)

for /f "delims=" %%a in ('findstr /r "\"port\":" external_client_config.jsonc') do (
    set line=%%a
    set line=!line:~0,-1!
    for /f "tokens=2 delims=:" %%b in ("!line!") do (
        set port=%%b
        set port=!port: =!
        set port=!port:,=!
        echo Puerto: !port!
    )
)

echo.
echo INSTRUCCIONES:
echo 1. Asegurate de que la pagina de LMArena este abierta en tu navegador
echo 2. Instala y ejecuta el script de Tampermonkey: ExternalLMArenaClient.js
echo 3. El cliente se conectara automaticamente al script del navegador
echo.
echo ==============================================
echo.

:: Ejecutar el servidor
echo Iniciando Cliente Externo...
echo.
echo Presiona Ctrl+C para detener el servidor.
echo.

python external_client_server.py
if errorlevel 1 (
    echo.
    echo ERROR: El servidor se cerro con errores.
    goto error_exit
)

echo.
echo El servidor se cerro normalmente.
goto normal_exit

:error_exit
echo.
echo ==============================================
echo  ERROR - El proceso no se completo
echo ==============================================
echo.
pause
exit /b 1

:normal_exit
echo.
echo ==============================================
echo  Cliente Externo Finalizado
echo ==============================================
echo.
pause
exit /b 0
