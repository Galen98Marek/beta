@echo off
setlocal enabledelayedexpansion

echo ==============================================
echo  LMArena Bridge - External ID Updater
echo ==============================================
echo.

:: Verificar si Python está instalado
echo [1/3] Verificando Python...
python --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python no esta instalado.
    echo.
    echo Por favor instala Python desde https://python.org
    echo.
    goto error_exit
)

for /f "tokens=2" %%a in ('python --version') do set PYTHON_VERSION=%%a
echo Python %PYTHON_VERSION% - OK
echo.

:: Verificar archivos necesarios
echo [2/3] Verificando archivos...
if not exist "external_id_updater.py" (
    echo ERROR: No se encontro external_id_updater.py
    echo.
    echo Asegurate de estar en el directorio correcto del proyecto.
    goto error_exit
)

if not exist "external_client_config.jsonc" (
    echo ERROR: No se encontro external_client_config.jsonc
    echo.
    echo Este archivo es necesario para la configuracion.
    goto error_exit
)

echo Archivos verificados - OK
echo.

:: Mostrar configuración del cliente
echo [3/3] Cargando configuracion del cliente...
for /f "delims=" %%a in ('findstr /r "\"client_name\":" external_client_config.jsonc') do (
    set line=%%a
    set line=!line:~0,-1!
    for /f "tokens=2 delims=:" %%b in ("!line!") do (
        set client_name=%%b
        set client_name=!client_name: "=!
        set client_name=!client_name:"=!
        set client_name=!client_name:,=!
    )
)

for /f "delims=" %%a in ('findstr /r "\"id_updater_port\":" external_client_config.jsonc') do (
    set line=%%a
    set line=!line:~0,-1!  
    for /f "tokens=2 delims=:" %%b in ("!line!") do (
        set updater_port=%%b
        set updater_port=!updater_port: =!
        set updater_port=!updater_port:,=!
    )
)

for /f "delims=" %%a in ('findstr /r "\"port\":" external_client_config.jsonc') do (
    set line=%%a
    set line=!line:~0,-1!
    for /f "tokens=2 delims=:" %%b in ("!line!") do (
        set client_port=%%b
        set client_port=!client_port: =!
        set client_port=!client_port:,=!
    )
)

echo.
echo ==============================================
echo  EXTERNAL ID UPDATER CONFIGURADO
echo ==============================================
echo Cliente: !client_name!
echo Puerto del Cliente: !client_port!
echo Puerto del ID Updater: !updater_port!
echo.
echo REQUISITOS PREVIOS:
echo 1. El servidor del cliente externo debe estar ejecutandose
echo    - Ejecuta: start.bat o python external_client_server.py
echo 2. La pagina de LMArena debe estar abierta en el navegador
echo 3. El script de Tampermonkey debe estar activo
echo ==============================================
echo.

:: Verificar si el servidor del cliente externo está ejecutándose
echo Verificando conexion con el servidor del cliente externo...
curl -s -f http://localhost:!client_port!/health >nul 2>&1
if errorlevel 1 (
    echo.
    echo WARNING: No se puede conectar al servidor del cliente externo.
    echo.
    echo El servidor del cliente externo no parece estar ejecutandose.
    echo Por favor:
    echo 1. Ejecuta 'start.bat' en otra ventana de comandos
    echo 2. O ejecuta 'python external_client_server.py'
    echo 3. Luego vuelve a ejecutar este script
    echo.
    set /p continue="Deseas continuar de todas formas? (y/N): "
    if not "!continue!"=="y" if not "!continue!"=="Y" (
        echo Operacion cancelada por el usuario.
        goto normal_exit
    )
) else (
    echo ✅ Servidor del cliente externo detectado correctamente.
)

echo.
echo ==============================================

:: Mostrar timestamp de inicio
for /f "tokens=1-3 delims=/ " %%a in ('date /t') do set current_date=%%a/%%b/%%c
for /f "tokens=1-2 delims=: " %%a in ('time /t') do set current_time=%%a:%%b
echo Iniciando External ID Updater a las %current_time% del %current_date%
echo.

echo INSTRUCCIONES DE USO:
echo 1. El modo de captura se activara automaticamente
echo 2. Ve a la pagina de LMArena en tu navegador
echo 3. Realiza una accion de 'Retry' o envia un mensaje
echo 4. Los IDs se capturaran automaticamente
echo 5. Este script se cerrara una vez completado
echo.
echo Para cancelar: Presiona Ctrl+C
echo.
echo ==============================================
echo.

:: Ejecutar el external_id_updater
python external_id_updater.py
set exit_code=%errorlevel%

echo.
echo ==============================================

if %exit_code% neq 0 (
    echo  ID UPDATER FINALIZADO CON ERRORES
    echo ==============================================
    echo.
    echo Codigo de salida: %exit_code%
    echo.
    echo Posibles causas:
    echo - Servidor del cliente externo no disponible
    echo - Script de Tampermonkey no activo
    echo - Error en configuracion
    echo - Problemas de red o puerto ocupado
    goto error_exit
) else (
    echo  ID UPDATER COMPLETADO EXITOSAMENTE
    echo ==============================================
    echo.
    for /f "tokens=1-2 delims=: " %%a in ('time /t') do set end_time=%%a:%%b
    echo ID Updater finalizado a las %end_time%
    echo.
    echo Los IDs de sesion han sido capturados y guardados correctamente.
    goto normal_exit
)

:error_exit
echo.
echo Presiona cualquier tecla para salir...
pause >nul
exit /b 1

:normal_exit
echo.
echo Presiona cualquier tecla para salir...
pause >nul
exit /b 0
