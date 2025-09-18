# api_server.py
# Servicio de backend para el nuevo LMArena Bridge

import asyncio
import json
import logging
import os
import sys
import subprocess
import time
import uuid
import re
import threading
import random
import mimetypes
import secrets
import hashlib
from datetime import datetime, timedelta
from contextlib import asynccontextmanager

import uvicorn
import requests
from packaging.version import parse as parse_version
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, JSONResponse, Response, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

# --- Importar módulos personalizados ---
from modules import image_generation

# --- Configuración básica ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# --- Estado y configuración global ---
CONFIG = {} # Almacena la configuración cargada desde config.jsonc
API_KEYS_DATA = {} # Almacena las API keys cargadas desde el archivo JSON
ADMIN_SESSIONS = {} # Almacena las sesiones de administrador activas
# browser_ws se utiliza para almacenar la conexión WebSocket con un único script de Tampermonkey.
# Nota: Esta arquitectura asume que solo una pestaña del navegador está funcionando.
# Si se necesita admitir múltiples pestañas concurrentes, esto debería expandirse a un diccionario para gestionar múltiples conexiones.
browser_ws: WebSocket | None = None
# response_channels se utiliza para almacenar las colas de respuesta para cada solicitud de API.
# La clave es request_id, y el valor es asyncio.Queue.
response_channels: dict[str, asyncio.Queue] = {}
last_activity_time = None # Registra la última hora de actividad
idle_monitor_thread = None # Hilo de monitoreo de inactividad
main_event_loop = None # Bucle de eventos principal

# --- Auto-Claude tracking ---
AUTO_CLAUDE_DISABLED_MODELS = {}  # {model_name: disabled_until_timestamp}
AUTO_CLAUDE_PRIORITY = [
    "claude-opus-4-1-20250805-thinking-16k",
    "claude-opus-4-1-20250805",
    "claude-opus-4-20250514-thinking-16k",
    "claude-opus-4-20250514",
    "claude-3-7-sonnet-20250219",
    "claude-sonnet-4-20250514",
    "claude-3-5-sonnet-20241022"
]
AUTO_CLAUDE_ACTIVE_REQUESTS = {}  # Track active auto-claude requests {request_id: current_model}

# --- Mapeo de modelos ---
MODEL_NAME_TO_ID_MAP = {}
MODEL_ENDPOINT_MAP = {} # Nuevo: para almacenar el mapeo de modelo a ID de sesión/mensaje
DEFAULT_MODEL_ID = "f44e280a-7914-43ca-a25d-ecfcc5d48d09" # Modelo por defecto: Claude 3.5 Sonnet

def load_model_endpoint_map():
    """Carga el mapeo de modelos a puntos finales desde model_endpoint_map.json."""
    global MODEL_ENDPOINT_MAP
    try:
        with open('model_endpoint_map.json', 'r', encoding='utf-8') as f:
            content = f.read()
            # Permitir archivo vacío
            if not content.strip():
                MODEL_ENDPOINT_MAP = {}
            else:
                MODEL_ENDPOINT_MAP = json.loads(content)
        logger.info(f"Se cargaron exitosamente {len(MODEL_ENDPOINT_MAP)} mapeos de puntos finales de modelos desde 'model_endpoint_map.json'.")
    except FileNotFoundError:
        logger.warning("No se encontró el archivo 'model_endpoint_map.json'. Se usará un mapeo vacío.")
        MODEL_ENDPOINT_MAP = {}
    except json.JSONDecodeError as e:
        logger.error(f"Fallo al cargar o analizar 'model_endpoint_map.json': {e}. Se usará un mapeo vacío.")
        MODEL_ENDPOINT_MAP = {}

def save_model_endpoint_map():
    """Guarda el mapeo de modelos actualizado en model_endpoint_map.json."""
    try:
        with open('model_endpoint_map.json', 'w', encoding='utf-8') as f:
            json.dump(MODEL_ENDPOINT_MAP, f, indent=2, ensure_ascii=False)
        logger.info("Mapeo de puntos finales de modelos guardado exitosamente.")
    except Exception as e:
        logger.error(f"Error al guardar model_endpoint_map.json: {e}")

def detect_rate_limit_error(content: str) -> bool:
    """Detecta si el contenido contiene un error 429 de rate limiting."""
    if not isinstance(content, str):
        return False
    return "429" in content and "Too Many Requests" in content

def generate_rotation_message(model_name: str, rotation_success: bool) -> str:
    """Genera un mensaje amigable para notificar al usuario sobre la rotación."""
    if rotation_success:
        return f"""🔄 **Sistema de Rotación Activado**

He detectado que el endpoint actual ha alcanzado su límite de velocidad para el modelo '{model_name}'. He rotado automáticamente al siguiente endpoint disponible.

**Por favor, reenvía tu mensaje anterior para continuar con la conversación.**

*Este es un mensaje automático del sistema de rotación de Adri, pipipipi.*"""
    else:
        return f"""⚠️ **Límite de Velocidad Detectado**

He detectado un límite de velocidad para el modelo '{model_name}', pero no tengo endpoints adicionales configurados para rotar automáticamente.

**Recomendaciones:**
- Espera unos minutos antes de reintentar
- Considera agregar más session IDs de respaldo para este modelo

*Este es un mensaje automático del sistema de adri, kkkkkkkk.*"""

def get_available_session_count(model_mapping: dict) -> int:
    """Obtiene el número de session/message IDs disponibles para un modelo."""
    count = 0
    index = 0
    while f"session_id{index}" in model_mapping and f"message_id{index}" in model_mapping:
        count += 1
        index += 1
    return count

def rotate_model_session(model_name: str) -> bool:
    """
    Rota al siguiente session/message ID para un modelo específico.
    Retorna True si la rotación fue exitosa, False si no hay más IDs disponibles.
    """
    global MODEL_ENDPOINT_MAP
    
    if model_name not in MODEL_ENDPOINT_MAP:
        logger.warning(f"ROTACIÓN: Modelo '{model_name}' no encontrado en el mapeo.")
        return False
    
    model_mapping = MODEL_ENDPOINT_MAP[model_name]
    
    # Verificar si es el formato nuevo (con índices numerados)
    if "current_index" not in model_mapping:
        logger.warning(f"ROTACIÓN: Modelo '{model_name}' no tiene formato de rotación (falta current_index).")
        return False
    
    current_index = model_mapping.get("current_index", 0)
    available_count = get_available_session_count(model_mapping)
    
    if available_count <= 1:
        logger.warning(f"ROTACIÓN: Modelo '{model_name}' solo tiene {available_count} session ID(s), no se puede rotar.")
        return False
    
    # Calcular el siguiente índice (rotación circular)
    next_index = (current_index + 1) % available_count
    
    # Verificar que el siguiente índice tenga session_id y message_id válidos
    next_session_key = f"session_id{next_index}"
    next_message_key = f"message_id{next_index}"
    
    if next_session_key not in model_mapping or next_message_key not in model_mapping:
        logger.error(f"ROTACIÓN: Índice {next_index} no tiene session_id/message_id válidos para modelo '{model_name}'.")
        return False
    
    # Actualizar el índice actual
    model_mapping["current_index"] = next_index
    
    # Guardar los cambios
    save_model_endpoint_map()
    
    old_session = model_mapping.get(f"session_id{current_index}", "N/A")
    new_session = model_mapping.get(f"session_id{next_index}", "N/A")
    
    logger.info(f"ROTACIÓN: Modelo '{model_name}' rotado de índice {current_index} a {next_index}")
    logger.info(f"  - Session ID anterior: ...{old_session[-6:] if len(old_session) > 6 else old_session}")
    logger.info(f"  - Session ID nuevo: ...{new_session[-6:] if len(new_session) > 6 else new_session}")
    
    return True

def get_current_session_ids(model_name: str) -> tuple:
    """
    Obtiene los session_id y message_id actuales para un modelo.
    Retorna (session_id, message_id, mode, battle_target) o (None, None, None, None) si no se encuentra.
    """
    if model_name not in MODEL_ENDPOINT_MAP:
        return None, None, None, None
    
    model_mapping = MODEL_ENDPOINT_MAP[model_name]
    
    # Verificar si es el formato nuevo (con índices numerados)
    if "current_index" in model_mapping:
        current_index = model_mapping.get("current_index", 0)
        session_key = f"session_id{current_index}"
        message_key = f"message_id{current_index}"
        
        session_id = model_mapping.get(session_key)
        message_id = model_mapping.get(message_key)
        mode = model_mapping.get("mode")
        battle_target = model_mapping.get("battle_target")
        
        return session_id, message_id, mode, battle_target
    
    # Formato legacy (mantener compatibilidad)
    elif isinstance(model_mapping, list) and model_mapping:
        selected_mapping = random.choice(model_mapping)
        return (
            selected_mapping.get("session_id"),
            selected_mapping.get("message_id"),
            selected_mapping.get("mode"),
            selected_mapping.get("battle_target")
        )
    elif isinstance(model_mapping, dict):
        return (
            model_mapping.get("session_id"),
            model_mapping.get("message_id"),
            model_mapping.get("mode"),
            model_mapping.get("battle_target")
        )
    
    return None, None, None, None

# --- Auto-Claude Functions ---
def get_best_available_claude_model():
    """Returns the best available Claude model from the priority list."""
    cleanup_expired_auto_claude_disables()
    
    for model in AUTO_CLAUDE_PRIORITY:
        if is_model_available_for_auto_claude(model) and model in MODEL_NAME_TO_ID_MAP:
            logger.info(f"AUTO-CLAUDE: Seleccionando modelo '{model}'")
            return model
    
    # If all models are disabled, return the last one as fallback
    fallback = AUTO_CLAUDE_PRIORITY[-1]
    logger.warning(f"AUTO-CLAUDE: Todos los modelos están deshabilitados, usando fallback '{fallback}'")
    return fallback

def disable_model_for_auto_claude(model_name: str):
    """Disables a model for auto-claude for 1 hour."""
    disabled_until = datetime.now() + timedelta(hours=1)
    AUTO_CLAUDE_DISABLED_MODELS[model_name] = disabled_until
    logger.info(f"AUTO-CLAUDE: Modelo '{model_name}' deshabilitado hasta {disabled_until.strftime('%Y-%m-%d %H:%M:%S')}")

def is_model_available_for_auto_claude(model_name: str) -> bool:
    """Checks if a model is available for auto-claude."""
    if model_name not in AUTO_CLAUDE_DISABLED_MODELS:
        return True
    
    if datetime.now() > AUTO_CLAUDE_DISABLED_MODELS[model_name]:
        del AUTO_CLAUDE_DISABLED_MODELS[model_name]
        return True
    
    return False

def cleanup_expired_auto_claude_disables():
    """Removes expired disable entries."""
    now = datetime.now()
    expired = [model for model, until in AUTO_CLAUDE_DISABLED_MODELS.items() if now > until]
    for model in expired:
        del AUTO_CLAUDE_DISABLED_MODELS[model]
        logger.info(f"AUTO-CLAUDE: Modelo '{model}' rehabilitado (expiró el período de deshabilitación)")

def load_config():
    """Carga la configuración desde config.jsonc y maneja los comentarios de JSONC."""
    global CONFIG
    try:
        with open('config.jsonc', 'r', encoding='utf-8') as f:
            content = f.read()
            # Eliminar comentarios de línea // y comentarios de bloque /* */
            json_content = re.sub(r'//.*', '', content)
            json_content = re.sub(r'/\*.*?\*/', '', json_content, flags=re.DOTALL)
            CONFIG = json.loads(json_content)
        logger.info("Configuración cargada exitosamente desde 'config.jsonc'.")
        # Imprimir estado de configuración clave
        logger.info(f"  - Modo Taberna (Tavern Mode): {'✅ Habilitado' if CONFIG.get('tavern_mode_enabled') else '❌ Deshabilitado'}")
        logger.info(f"  - Modo de Omisión (Bypass Mode): {'✅ Habilitado' if CONFIG.get('bypass_enabled') else '❌ Deshabilitado'}")
        logger.info(f"  - Assistant Prefill: {'✅ Habilitado' if CONFIG.get('assistant_prefill_enabled', True) else '❌ Deshabilitado'}")
    except (FileNotFoundError, json.JSONDecodeError) as e:
        logger.error(f"Fallo al cargar o analizar 'config.jsonc': {e}. Se usará la configuración por defecto.")
        CONFIG = {}

def load_model_map():
    """Carga el mapeo de modelos desde models.json."""
    global MODEL_NAME_TO_ID_MAP
    try:
        with open('models.json', 'r', encoding='utf-8') as f:
            MODEL_NAME_TO_ID_MAP = json.load(f)
        logger.info(f"Se cargaron exitosamente {len(MODEL_NAME_TO_ID_MAP)} modelos desde 'models.json'.")
    except (FileNotFoundError, json.JSONDecodeError) as e:
        logger.error(f"Fallo al cargar 'models.json': {e}. Se usará una lista de modelos vacía.")
        MODEL_NAME_TO_ID_MAP = {}

# --- Gestión de API Keys ---
def load_api_keys():
    """Carga las API keys desde el archivo JSON configurado."""
    global API_KEYS_DATA
    api_keys_file = CONFIG.get("api_keys_file", "api_keys.json")
    
    try:
        with open(api_keys_file, 'r', encoding='utf-8') as f:
            API_KEYS_DATA = json.load(f)
        logger.info(f"Se cargaron exitosamente {len(API_KEYS_DATA.get('api_keys', {}))} API keys desde '{api_keys_file}'.")
    except FileNotFoundError:
        logger.info(f"No se encontró el archivo '{api_keys_file}'. Se creará uno nuevo.")
        API_KEYS_DATA = {"api_keys": {}}
        save_api_keys()
    except json.JSONDecodeError as e:
        logger.error(f"Error al analizar '{api_keys_file}': {e}. Se usará una estructura vacía.")
        API_KEYS_DATA = {"api_keys": {}}

def save_api_keys():
    """Guarda las API keys en el archivo JSON."""
    api_keys_file = CONFIG.get("api_keys_file", "api_keys.json")
    try:
        with open(api_keys_file, 'w', encoding='utf-8') as f:
            json.dump(API_KEYS_DATA, f, indent=2, ensure_ascii=False)
        logger.info(f"API keys guardadas exitosamente en '{api_keys_file}'.")
    except Exception as e:
        logger.error(f"Error al guardar API keys en '{api_keys_file}': {e}")

def generate_api_key():
    """Genera una nueva API key única."""
    timestamp = str(int(time.time()))[-6:]  # Últimos 6 dígitos del timestamp
    random_part = secrets.token_hex(16)
    return f"sk-{timestamp}-{random_part}"

def validate_api_key(api_key: str, model_name: str = None):
    """
    Valida una API key y verifica permisos.
    Retorna (válida: bool, key_data: dict, error: str).
    """
    if not api_key:
        return False, None, "API key requerida"
    
    # Verificar si es la API key global del config (para compatibilidad)
    config_api_key = CONFIG.get("api_key")
    if config_api_key and api_key == config_api_key:
        # La API key global tiene acceso completo
        return True, {
            "id": "global",
            "name": "API Key Global",
            "models": list(MODEL_NAME_TO_ID_MAP.keys()),
            "usage_limit": None,
            "enabled": True
        }, None
    
    # Verificar en las API keys gestionadas
    api_keys = API_KEYS_DATA.get("api_keys", {})
    if api_key not in api_keys:
        return False, None, "API key inválida"
    
    key_data = api_keys[api_key]
    
    # Verificar si está habilitada
    if not key_data.get("enabled", True):
        return False, None, "API key deshabilitada"
    
    # Verificar límite de usos
    usage_limit = key_data.get("usage_limit")
    usage_count = key_data.get("usage_count", 0)
    if usage_limit is not None and usage_count >= usage_limit:
        return False, None, "Límite de usos excedido"
    
    # Verificar permisos de modelo
    if model_name:
        allowed_models = key_data.get("models", [])
        if allowed_models and model_name not in allowed_models:
            return False, None, f"Modelo '{model_name}' no permitido para esta API key"
    
    return True, key_data, None

def increment_api_key_usage(api_key: str):
    """Incrementa el contador de uso de una API key."""
    # No incrementar para la API key global
    config_api_key = CONFIG.get("api_key")
    if config_api_key and api_key == config_api_key:
        return
    
    api_keys = API_KEYS_DATA.get("api_keys", {})
    if api_key in api_keys:
        api_keys[api_key]["usage_count"] = api_keys[api_key].get("usage_count", 0) + 1
        api_keys[api_key]["last_used"] = datetime.now().isoformat()
        save_api_keys()

def get_models_for_api_key(api_key: str):
    """Obtiene la lista de modelos permitidos para una API key."""
    is_valid, key_data, error = validate_api_key(api_key)
    if not is_valid:
        return []
    
    allowed_models = key_data.get("models", [])
    if not allowed_models:
        # Si no hay modelos especificados, permitir todos
        return list(MODEL_NAME_TO_ID_MAP.keys())
    
    # Filtrar solo los modelos que existen en el sistema
    return [model for model in allowed_models if model in MODEL_NAME_TO_ID_MAP]

# --- Autenticación de administrador ---
def generate_admin_token():
    """Genera un token de sesión para el administrador."""
    return secrets.token_urlsafe(32)

def verify_admin_password(password: str):
    """Verifica la contraseña del administrador."""
    admin_password = CONFIG.get("admin_password")
    if not admin_password:
        return False
    return password == admin_password

def create_admin_session(token: str):
    """Crea una nueva sesión de administrador."""
    ADMIN_SESSIONS[token] = {
        "created_at": datetime.now(),
        "expires_at": datetime.now() + timedelta(hours=8)  # Sesión válida por 8 horas
    }

def validate_admin_token(token: str):
    """Valida un token de administrador."""
    if not token or token not in ADMIN_SESSIONS:
        return False
    
    session = ADMIN_SESSIONS[token]
    if datetime.now() > session["expires_at"]:
        # Sesión expirada, eliminarla
        del ADMIN_SESSIONS[token]
        return False
    
    return True

def cleanup_expired_admin_sessions():
    """Limpia las sesiones de administrador expiradas."""
    now = datetime.now()
    expired_tokens = [
        token for token, session in ADMIN_SESSIONS.items()
        if now > session["expires_at"]
    ]
    for token in expired_tokens:
        del ADMIN_SESSIONS[token]

# --- Verificación de actualizaciones ---
GITHUB_REPO = "Lianues/LMArenaBridge"

def download_and_extract_update(version):
    """Descarga y extrae la última versión a una carpeta temporal."""
    update_dir = "update_temp"
    if not os.path.exists(update_dir):
        os.makedirs(update_dir)

    try:
        zip_url = f"https://github.com/{GITHUB_REPO}/archive/refs/heads/main.zip"
        logger.info(f"Descargando nueva versión desde {zip_url}...")
        response = requests.get(zip_url, timeout=60)
        response.raise_for_status()

        # Es necesario importar zipfile e io
        import zipfile
        import io
        with zipfile.ZipFile(io.BytesIO(response.content)) as z:
            z.extractall(update_dir)
        
        logger.info(f"La nueva versión se ha descargado y extraído exitosamente a la carpeta '{update_dir}'.")
        return True
    except requests.RequestException as e:
        logger.error(f"Fallo al descargar la actualización: {e}")
    except zipfile.BadZipFile:
        logger.error("El archivo descargado no es un archivo zip válido.")
    except Exception as e:
        logger.error(f"Ocurrió un error desconocido al extraer la actualización: {e}")
    
    return False

def check_for_updates():
    """Verifica si hay una nueva versión en GitHub."""
    if not CONFIG.get("enable_auto_update", True):
        logger.info("La actualización automática está deshabilitada, se omite la verificación.")
        return

    current_version = CONFIG.get("version", "0.0.0")
    logger.info(f"Versión actual: {current_version}. Verificando actualizaciones desde GitHub...")

    try:
        config_url = f"https://raw.githubusercontent.com/{GITHUB_REPO}/main/config.jsonc"
        response = requests.get(config_url, timeout=10)
        response.raise_for_status()

        jsonc_content = response.text
        json_content = re.sub(r'//.*', '', jsonc_content)
        json_content = re.sub(r'/\*.*?\*/', '', json_content, flags=re.DOTALL)
        remote_config = json.loads(json_content)
        
        remote_version_str = remote_config.get("version")
        if not remote_version_str:
            logger.warning("No se encontró el número de versión en el archivo de configuración remoto, se omite la verificación de actualización.")
            return

        if parse_version(remote_version_str) > parse_version(current_version):
            logger.info("="*60)
            logger.info(f"🎉 ¡Nueva versión encontrada! 🎉")
            logger.info(f"  - Versión actual: {current_version}")
            logger.info(f"  - Última versión: {remote_version_str}")
            if download_and_extract_update(remote_version_str):
                logger.info("Preparando para aplicar la actualización. El servidor se cerrará en 5 segundos e iniciará el script de actualización.")
                time.sleep(5)
                update_script_path = os.path.join("modules", "update_script.py")
                # Usar Popen para iniciar un proceso independiente
                subprocess.Popen([sys.executable, update_script_path])
                # Salir elegantemente del proceso del servidor actual
                os._exit(0)
            else:
                logger.error(f"La actualización automática falló. Por favor, visite https://github.com/{GITHUB_REPO}/releases/latest para descargar manualmente.")
            logger.info("="*60)
        else:
            logger.info("Su programa ya está en la última versión.")

    except requests.RequestException as e:
        logger.error(f"Fallo al verificar actualizaciones: {e}")
    except json.JSONDecodeError:
        logger.error("Fallo al analizar el archivo de configuración remoto.")
    except Exception as e:
        logger.error(f"Ocurrió un error desconocido al verificar actualizaciones: {e}")

# --- Actualización de modelos ---
def extract_models_from_html(html_content):
    """
    Extrae datos de modelos del contenido HTML, utilizando un método de análisis más robusto.
    """
    script_contents = re.findall(r'<script>(.*?)</script>', html_content, re.DOTALL)
    
    for script_content in script_contents:
        if 'self.__next_f.push' in script_content and 'initialState' in script_content and 'publicName' in script_content:
            match = re.search(r'self\.__next_f\.push\(\[1,"(.*?)"\]\)', script_content, re.DOTALL)
            if not match:
                continue
            
            full_payload = match.group(1)
            
            payload_string = full_payload.split('\\n')[0]
            
            json_start_index = payload_string.find(':')
            if json_start_index == -1:
                continue
            
            json_string_with_escapes = payload_string[json_start_index + 1:]
            json_string = json_string_with_escapes.replace('\\"', '"')
            
            try:
                data = json.loads(json_string)
                
                def find_initial_state(obj):
                    if isinstance(obj, dict):
                        for key, value in obj.items():
                            if key == 'initialState' and isinstance(value, list):
                                if value and isinstance(value[0], dict) and 'publicName' in value[0]:
                                    return value
                            result = find_initial_state(value)
                            if result is not None:
                                return result
                    elif isinstance(obj, list):
                        for item in obj:
                            result = find_initial_state(item)
                            if result is not None:
                                return result
                    return None

                models = find_initial_state(data)
                if models:
                    logger.info(f"Se extrajeron exitosamente {len(models)} modelos del bloque de script.")
                    return models
            except json.JSONDecodeError as e:
                logger.error(f"Error al analizar la cadena JSON extraída: {e}")
                continue

    logger.error("Error: No se encontró un bloque de script con datos de modelo válidos en la respuesta HTML.")
    return None

def compare_and_update_models(new_models_list, models_path):
    """
    Compara las listas de modelos nueva y antigua, imprime las diferencias y actualiza el archivo local models.json con la nueva lista.
    """
    try:
        with open(models_path, 'r', encoding='utf-8') as f:
            old_models = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        old_models = {}

    new_models_dict = {model['publicName']: model for model in new_models_list if 'publicName' in model}
    old_models_set = set(old_models.keys())
    new_models_set = set(new_models_dict.keys())

    added_models = new_models_set - old_models_set
    removed_models = old_models_set - new_models_set
    
    logger.info("--- Verificación de actualización de la lista de modelos ---")
    has_changes = False

    if added_models:
        has_changes = True
        logger.info("\n[+] Nuevos modelos agregados:")
        for name in sorted(list(added_models)):
            model = new_models_dict[name]
            logger.info(f"  - Nombre: {name}, ID: {model.get('id')}, Organización: {model.get('organization', 'N/A')}")

    if removed_models:
        has_changes = True
        logger.info("\n[-] Modelos eliminados:")
        for name in sorted(list(removed_models)):
            logger.info(f"  - Nombre: {name}, ID: {old_models.get(name)}")

    logger.info("\n[*] Verificación de modelos comunes:")
    changed_models = 0
    for name in sorted(list(new_models_set.intersection(old_models_set))):
        new_id = new_models_dict[name].get('id')
        old_id = old_models.get(name)
        if new_id != old_id:
            has_changes = True
            changed_models += 1
            logger.info(f"  - Cambio de ID: '{name}' ID antiguo: {old_id} -> ID nuevo: {new_id}")
    
    if changed_models == 0:
        logger.info("  - Los ID de los modelos comunes no han cambiado.")

    if not has_changes:
        logger.info("\nConclusión: La lista de modelos no ha cambiado, no es necesario actualizar el archivo.")
        logger.info("--- Verificación completada ---")
        return

    logger.info("\nConclusión: Se detectaron cambios en los modelos, actualizando 'models.json'...")
    updated_model_map = {model['publicName']: model.get('id') for model in new_models_list if 'publicName' in model and 'id' in model}
    try:
        with open(models_path, 'w', encoding='utf-8') as f:
            json.dump(updated_model_map, f, indent=4, ensure_ascii=False)
        logger.info(f"'{models_path}' se ha actualizado exitosamente, contiene {len(updated_model_map)} modelos.")
        load_model_map()
    except IOError as e:
        logger.error(f"Error al escribir en el archivo '{models_path}': {e}")
    
    logger.info("--- Verificación y actualización completadas ---")

# --- Lógica de reinicio automático ---
def restart_server():
    """Notifica elegantemente al cliente para que se actualice y luego reinicia el servidor."""
    logger.warning("="*60)
    logger.warning("Se detectó tiempo de inactividad del servidor, preparándose para el reinicio automático...")
    logger.warning("="*60)
    
    # 1. (Asíncrono) Notificar al navegador para que se actualice
    async def notify_browser_refresh():
        if browser_ws:
            try:
                # Priorizar el envío del comando 'reconnect' para que el frontend sepa que es un reinicio planificado
                await browser_ws.send_text(json.dumps({"command": "reconnect"}, ensure_ascii=False))
                logger.info("Se ha enviado el comando 'reconnect' al navegador.")
            except Exception as e:
                logger.error(f"Fallo al enviar el comando 'reconnect': {e}")
    
    # Ejecutar la función de notificación asíncrona en el bucle de eventos principal
    # Usar `asyncio.run_coroutine_threadsafe` para garantizar la seguridad del hilo
    if browser_ws and browser_ws.client_state.name == 'CONNECTED' and main_event_loop:
        asyncio.run_coroutine_threadsafe(notify_browser_refresh(), main_event_loop)
    
    # 2. Retrasar unos segundos para asegurar que el mensaje se envíe
    time.sleep(3)
    
    # 3. Ejecutar el reinicio
    logger.info("Reiniciando el servidor...")
    os.execv(sys.executable, ['python'] + sys.argv)

def idle_monitor():
    """Se ejecuta en un hilo de fondo para monitorear si el servidor está inactivo."""
    global last_activity_time
    
    # Esperar hasta que last_activity_time se establezca por primera vez
    while last_activity_time is None:
        time.sleep(1)
        
    logger.info("El hilo de monitoreo de inactividad se ha iniciado.")
    
    while True:
        if CONFIG.get("enable_idle_restart", False):
            timeout = CONFIG.get("idle_restart_timeout_seconds", 300)
            
            # Si el tiempo de espera se establece en -1, deshabilitar la verificación de reinicio
            if timeout == -1:
                time.sleep(10) # Aún es necesario dormir para evitar un bucle ocupado
                continue

            idle_time = (datetime.now() - last_activity_time).total_seconds()
            
            if idle_time > timeout:
                logger.info(f"El tiempo de inactividad del servidor ({idle_time:.0f}s) ha superado el umbral ({timeout}s).")
                restart_server()
                break # Salir del bucle, ya que el proceso está a punto de ser reemplazado
                
        # Verificar cada 10 segundos
        time.sleep(10)

# --- Eventos del ciclo de vida de FastAPI ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Función de ciclo de vida que se ejecuta al iniciar el servidor."""
    global idle_monitor_thread, last_activity_time, main_event_loop
    main_event_loop = asyncio.get_running_loop() # Obtener el bucle de eventos principal
    load_config() # Cargar primero la configuración
    
    # --- Imprimir el modo de operación actual ---
    mode = CONFIG.get("id_updater_last_mode", "direct_chat")
    target = CONFIG.get("id_updater_battle_target", "A")
    logger.info("="*60)
    logger.info(f"  Modo de operación actual: {mode.upper()}")
    if mode == 'battle':
        logger.info(f"  - Objetivo del modo Batalla: Asistente {target}")
    logger.info("  (Puede modificar el modo ejecutando id_updater.py)")
    logger.info("="*60)

    check_for_updates() # Verificar actualizaciones del programa
    load_model_map() # Cargar mapeo de ID de modelo
    load_model_endpoint_map() # Cargar mapeo de puntos finales de modelo
    load_api_keys() # Cargar API keys desde el archivo JSON
    logger.info("El servidor se ha iniciado correctamente. Esperando conexión del script de Tampermonkey...")

    # Después de la actualización del modelo, marcar el punto de inicio del tiempo de actividad
    last_activity_time = datetime.now()
    
    # Iniciar el hilo de monitoreo de inactividad
    if CONFIG.get("enable_idle_restart", False):
        idle_monitor_thread = threading.Thread(target=idle_monitor, daemon=True)
        idle_monitor_thread.start()
        
    # --- Inicializar módulos personalizados ---
    image_generation.initialize_image_module(
        app_logger=logger,
        channels=response_channels,
        app_config=CONFIG,
        model_map=MODEL_NAME_TO_ID_MAP,
        default_model_id=DEFAULT_MODEL_ID
    )

    yield
    logger.info("El servidor se está cerrando.")

app = FastAPI(lifespan=lifespan)

# --- Configuración del middleware CORS ---
# Permitir todos los orígenes, todos los métodos, todas las cabeceras, lo cual es seguro para herramientas de desarrollo local.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- Configuración de archivos estáticos ---
if os.path.exists("static"):
    app.mount("/static", StaticFiles(directory="static"), name="static")

# --- Autenticación HTTP Bearer ---
security = HTTPBearer(auto_error=False)

async def get_admin_token(credentials: HTTPAuthorizationCredentials = Depends(security)):
    """Extrae y valida el token de administrador desde el header Authorization."""
    if not credentials:
        raise HTTPException(status_code=401, detail="Token de autorización requerido")
    
    if not validate_admin_token(credentials.credentials):
        raise HTTPException(status_code=401, detail="Token de administrador inválido o expirado")
    
    return credentials.credentials

async def get_api_key_from_header(request: Request):
    """Extrae la API key desde el header Authorization."""
    auth_header = request.headers.get('Authorization')
    if not auth_header or not auth_header.startswith('Bearer '):
        raise HTTPException(status_code=401, detail="API key requerida en header Authorization")
    
    return auth_header.split(' ')[1]

# --- Funciones auxiliares ---
def save_config():
    """Escribe el objeto CONFIG actual de nuevo en el archivo config.jsonc, conservando los comentarios."""
    try:
        # Leer el archivo original para conservar comentarios, etc.
        with open('config.jsonc', 'r', encoding='utf-8') as f:
            lines = f.readlines()

        # Usar expresiones regulares para reemplazar valores de forma segura
        def replacer(key, value, content):
            # Esta expresión regular encontrará la clave, luego coincidirá con su parte de valor, hasta una coma o una llave de cierre
            pattern = re.compile(rf'("{key}"\s*:\s*").*?("?)(,?\s*)$', re.MULTILINE)
            replacement = rf'\g<1>{value}\g<2>\g<3>'
            if not pattern.search(content): # Si la clave no existe, agregarla al final del archivo (manejo simplificado)
                 content = re.sub(r'}\s*$', f'  ,"{key}": "{value}"\n}}', content)
            else:
                 content = pattern.sub(replacement, content)
            return content

        content_str = "".join(lines)
        content_str = replacer("session_id", CONFIG["session_id"], content_str)
        content_str = replacer("message_id", CONFIG["message_id"], content_str)
        
        with open('config.jsonc', 'w', encoding='utf-8') as f:
            f.write(content_str)
        logger.info("✅ La información de la sesión se ha actualizado exitosamente en config.jsonc.")
    except Exception as e:
        logger.error(f"❌ Ocurrió un error al escribir en config.jsonc: {e}", exc_info=True)


def _process_openai_message(message: dict) -> dict:
    """
    Procesa mensajes de OpenAI, separando texto y adjuntos.
    - Descompone la lista de contenido multimodal en texto puro y una lista de adjuntos.
    - Asegura que el contenido vacío del rol de usuario se reemplace con un espacio para evitar errores en LMArena.
    - Maneja mensajes de assistant para el prefill de Claude.
    - Genera una estructura base para los adjuntos.
    """
    content = message.get("content")
    role = message.get("role")
    attachments = []
    text_content = ""

    if isinstance(content, list):
        
        text_parts = []
        for part in content:
            if part.get("type") == "text":
                text_parts.append(part.get("text", ""))
            elif part.get("type") == "image_url":
                image_url_data = part.get("image_url", {})
                url = image_url_data.get("url")

                # Nueva lógica: permitir que el cliente pase el nombre de archivo original a través del campo 'detail'
                # El campo 'detail' es parte de la API de OpenAI Vision, aquí lo reutilizamos
                original_filename = image_url_data.get("detail")

                if url and url.startswith("data:"):
                    try:
                        content_type = url.split(';')[0].split(':')[1]
                        
                        # Si el cliente proporciona el nombre de archivo original, usarlo directamente
                        if original_filename and isinstance(original_filename, str):
                            file_name = original_filename
                            logger.info(f"Se procesó un adjunto exitosamente (usando nombre de archivo original): {file_name}")
                        else:
                            # De lo contrario, recurrir a la lógica de nomenclatura antigua basada en UUID
                            main_type, sub_type = content_type.split('/') if '/' in content_type else ('application', 'octet-stream')
                            
                            if main_type == "image": prefix = "image"
                            elif main_type == "audio": prefix = "audio"
                            else: prefix = "file"
                            
                            guessed_extension = mimetypes.guess_extension(content_type)
                            if guessed_extension:
                                file_extension = guessed_extension.lstrip('.')
                            else:
                                file_extension = sub_type if len(sub_type) < 20 else 'bin'
                            
                            file_name = f"{prefix}_{uuid.uuid4()}.{file_extension}"
                            logger.info(f"Se procesó un adjunto exitosamente (generando nombre de archivo): {file_name}")

                        attachments.append({
                            "name": file_name,
                            "contentType": content_type,
                            "url": url
                        })
                    except (IndexError, ValueError) as e:
                        logger.warning(f"No se puede analizar la URI de datos base64: {url[:60]}... Error: {e}")

        text_content = "\n\n".join(text_parts)
    elif isinstance(content, str):
        text_content = content

    # Manejar contenido vacío para diferentes roles
    if role == "user" and not text_content.strip():
        text_content = " "
    elif role == "assistant" and not text_content.strip():
        # Para assistant prefill, permitir contenido vacío (útil para forzar respuestas)
        text_content = ""

    return {
        "role": role,
        "content": text_content,
        "attachments": attachments
    }

def convert_openai_to_lmarena_payload(openai_data: dict, session_id: str, message_id: str, mode_override: str = None, battle_target_override: str = None, is_auto_claude: bool = False) -> dict:
    """
    Convierte el cuerpo de la solicitud de OpenAI a la carga útil simplificada requerida por el script de Tampermonkey,
    y aplica los modos de taberna, omisión, batalla y assistant prefill.
    Se agregaron parámetros de anulación de modo para admitir modos de sesión específicos del modelo.
    """
    # 1. Normalizar roles y procesar mensajes
    #    - Convertir el rol no estándar 'developer' a 'system' para mejorar la compatibilidad.
    #    - Separar texto y adjuntos.
    #    - Manejar assistant prefill si está habilitado.
    messages = openai_data.get("messages", [])
    for msg in messages:
        if msg.get("role") == "developer":
            msg["role"] = "system"
            logger.info("Normalización del rol del mensaje: se convirtió 'developer' a 'system'.")
    
    # Detectar y extraer assistant prefill si está habilitado
    assistant_prefill_content = ""
    assistant_prefill_enabled = CONFIG.get("assistant_prefill_enabled", True)
    
    if assistant_prefill_enabled and messages and messages[-1].get("role") == "assistant":
        # El último mensaje es de assistant, extraer como prefill
        assistant_msg = messages.pop()  # Remover el mensaje de assistant de la lista
        assistant_prefill_content = assistant_msg.get("content", "")
        logger.info(f"ASSISTANT PREFILL: Detectado mensaje de assistant como prefill: '{assistant_prefill_content[:50]}{'...' if len(assistant_prefill_content) > 50 else ''}'")
    elif not assistant_prefill_enabled and messages and messages[-1].get("role") == "assistant":
        # Assistant prefill está deshabilitado, convertir mensaje de assistant a usuario
        assistant_msg = messages[-1]
        assistant_msg["role"] = "user"
        logger.info("ASSISTANT PREFILL: Funcionalidad deshabilitada, convirtiendo mensaje de assistant a usuario.")
            
    processed_messages = [_process_openai_message(msg.copy()) for msg in messages]

    # 2. Aplicar modo Taberna (Tavern Mode)
    if CONFIG.get("tavern_mode_enabled"):
        system_prompts = [msg['content'] for msg in processed_messages if msg['role'] == 'system']
        other_messages = [msg for msg in processed_messages if msg['role'] != 'system']
        
        merged_system_prompt = "\n\n".join(system_prompts)
        final_messages = []
        
        if merged_system_prompt:
            # Los mensajes del sistema no deben tener adjuntos
            final_messages.append({"role": "system", "content": merged_system_prompt, "attachments": []})
        
        final_messages.extend(other_messages)
        processed_messages = final_messages

    # 3. Determinar el ID del modelo de destino
    model_name = openai_data.get("model", "claude-3-5-sonnet-20241022")
    target_model_id = MODEL_NAME_TO_ID_MAP.get(model_name, DEFAULT_MODEL_ID)
    
    # 4. Construir plantillas de mensajes
    message_templates = []
    for msg in processed_messages:
        message_templates.append({
            "role": msg["role"],
            "content": msg.get("content", ""),
            "attachments": msg.get("attachments", [])
        })

    # 5. Aplicar modo de Omisión (Bypass Mode)
    if CONFIG.get("bypass_enabled"):
        # El modo de omisión siempre agrega un mensaje de usuario con posición 'a'
        message_templates.append({"role": "user", "content": " ", "participantPosition": "a", "attachments": []})

    # 6. Aplicar posición del participante (Participant Position)
    # Priorizar el modo anulado, de lo contrario, recurrir a la configuración global
    mode = mode_override or CONFIG.get("id_updater_last_mode", "direct_chat")
    target_participant = battle_target_override or CONFIG.get("id_updater_battle_target", "A")
    target_participant = target_participant.lower() # Asegurarse de que esté en minúsculas

    logger.info(f"Estableciendo posiciones de participante según el modo '{mode}' (objetivo: {target_participant if mode == 'battle' else 'N/A'})...")

    for msg in message_templates:
        if msg['role'] == 'system':
            if mode == 'battle':
                # Modo Batalla: el sistema está del mismo lado que el asistente elegido por el usuario (A para a, B para b)
                msg['participantPosition'] = target_participant
            else:
                # Modo DirectChat: el sistema se fija en 'b'
                msg['participantPosition'] = 'b'
        elif mode == 'battle':
            # En modo Batalla, los mensajes que no son del sistema usan el participante objetivo elegido por el usuario
            msg['participantPosition'] = target_participant
        else: # Modo DirectChat
            # En modo DirectChat, los mensajes que no son del sistema usan el predeterminado 'a'
            msg['participantPosition'] = 'a'

    return {
        "message_templates": message_templates,
        "target_model_id": target_model_id,
        "session_id": session_id,
        "message_id": message_id,
        "assistant_prefill": assistant_prefill_content,
        "is_auto_claude": is_auto_claude
    }

# --- Funciones auxiliares de formato de OpenAI (aseguran una serialización JSON robusta) ---
def format_openai_chunk(content: str, model: str, request_id: str) -> str:
    """Formatea como un bloque de transmisión de OpenAI."""
    chunk = {
        "id": request_id, "object": "chat.completion.chunk",
        "created": int(time.time()), "model": model,
        "choices": [{"index": 0, "delta": {"content": content}, "finish_reason": None}]
    }
    return f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"

def format_openai_finish_chunk(model: str, request_id: str, reason: str = 'stop') -> str:
    """Formatea como un bloque de finalización de OpenAI."""
    chunk = {
        "id": request_id, "object": "chat.completion.chunk",
        "created": int(time.time()), "model": model,
        "choices": [{"index": 0, "delta": {}, "finish_reason": reason}]
    }
    return f"data: {json.dumps(chunk, ensure_ascii=False)}\n\ndata: [DONE]\n\n"

def format_openai_error_chunk(error_message: str, model: str, request_id: str) -> str:
    """Formatea como un bloque de error de OpenAI."""
    content = f"\n\n[Error del Puente LMArena]: {error_message}"
    return format_openai_chunk(content, model, request_id)

def format_openai_non_stream_response(content: str, model: str, request_id: str, reason: str = 'stop') -> dict:
    """Construye un cuerpo de respuesta no transmitido que cumple con la especificación de OpenAI."""
    return {
        "id": request_id,
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [{
            "index": 0,
            "message": {"role": "assistant", "content": content},
            "finish_reason": reason,
        }],
        "usage": {
            "prompt_tokens": 0,
            "completion_tokens": len(content) // 4,
            "total_tokens": len(content) // 4,
        },
    }

async def _process_lmarena_stream(request_id: str, model_name: str = None):
    """
    Generador interno principal: procesa el flujo de datos sin procesar del navegador y produce eventos estructurados.
    Tipos de eventos: ('content', str), ('finish', str), ('error', str)
    """
    queue = response_channels.get(request_id)
    if not queue:
        logger.error(f"PROCESADOR [ID: {request_id[:8]}]: No se pudo encontrar el canal de respuesta.")
        yield 'error', 'Error interno del servidor: no se encontró el canal de respuesta.'
        return

    buffer = ""
    timeout = CONFIG.get("stream_response_timeout_seconds",360)
    text_pattern = re.compile(r'[ab]0:"((?:\\.|[^"\\])*)"')
    finish_pattern = re.compile(r'[ab]d:(\{.*?"finishReason".*?\})')
    error_pattern = re.compile(r'(\{\s*"error".*?\})', re.DOTALL)
    cloudflare_patterns = [r'<title>Just a moment...</title>', r'Enable JavaScript and cookies to continue']

    try:
        while True:
            try:
                raw_data = await asyncio.wait_for(queue.get(), timeout=timeout)
            except asyncio.TimeoutError:
                logger.warning(f"PROCESADOR [ID: {request_id[:8]}]: Tiempo de espera agotado para los datos del navegador ({timeout} segundos).")
                yield 'error', f'La respuesta expiró después de {timeout} segundos.'
                return

            # 1. Verificar errores directos o señales de terminación desde el lado del WebSocket
            if isinstance(raw_data, dict):
                # 1.1. Verificar señal especial de rate limiting del Tampermonkey script
                if raw_data.get('rate_limit_detected'):
                    current_model = AUTO_CLAUDE_ACTIVE_REQUESTS.get(request_id)
                    if current_model:
                        logger.warning(f"AUTO-CLAUDE [ID: {request_id[:8]}]: Se detectó un límite de velocidad para el modelo '{current_model}'.")
                        disable_model_for_auto_claude(current_model)
                        
                        next_model = get_best_available_claude_model()
                        AUTO_CLAUDE_ACTIVE_REQUESTS[request_id] = next_model

                        yield 'content', f"🔄 **Auto-Claude:** Límite de velocidad detectado para '{current_model}'. Cambiando a '{next_model}'..."
                        
                        # Get new session details for the next model
                        new_session_id, new_message_id, _, _ = get_current_session_ids(next_model)
                        new_model_id = MODEL_NAME_TO_ID_MAP.get(next_model)

                        if browser_ws:
                            message = {
                                "command": "switch_model",
                                "request_id": request_id,
                                "new_session_id": new_session_id,
                                "new_message_id": new_message_id,
                                "new_model_id": new_model_id
                            }
                            await browser_ws.send_text(json.dumps(message))
                            logger.info(f"AUTO-CLAUDE [ID: {request_id[:8]}]: Enviando comando 'switch_model' a Tampermonkey.")
                        else:
                            yield 'error', "No se pudo cambiar de modelo automáticamente porque el navegador no está conectado."
                        
                        return # Stop processing the current stream

                    logger.warning(f"PROCESADOR [ID: {request_id[:8]}]: Se detectó señal de rate limiting del script de Tampermonkey.")
                    model_id = raw_data.get('model_id')
                    original_error = raw_data.get('original_error', 'Error 429 detectado')
                    
                    # Intentar encontrar el nombre del modelo basado en el model_id
                    detected_model_name = None
                    if model_id:
                        for model_name, mapped_id in MODEL_NAME_TO_ID_MAP.items():
                            if mapped_id == model_id:
                                detected_model_name = model_name
                                break
                    
                    if detected_model_name:
                        logger.info(f"PROCESADOR [ID: {request_id[:8]}]: Modelo identificado para rotación: {detected_model_name}")
                        rotation_success = rotate_model_session(detected_model_name)
                        friendly_message = generate_rotation_message(detected_model_name, rotation_success)
                        yield 'content', friendly_message
                        yield 'finish', 'stop'
                    else:
                        logger.warning(f"PROCESADOR [ID: {request_id[:8]}]: No se pudo identificar el modelo para rotación (model_id: {model_id})")
                        fallback_message = "🔄 He detectado un límite de velocidad, pero no pude identificar el modelo específico para rotar automáticamente. Por favor, reintenta tu solicitud en unos minutos."
                        yield 'content', fallback_message
                        yield 'finish', 'stop'
                    return
                
                # 1.2. Verificar errores regulares
                if 'error' in raw_data:
                    error_msg = raw_data.get('error', 'Error desconocido del navegador')
                    
                    # Manejo de errores mejorado
                    if isinstance(error_msg, str):
                        # 1. Verificar error 413 de adjunto demasiado grande
                        if '413' in error_msg or 'too large' in error_msg.lower():
                            friendly_error_msg = "Error de carga: El tamaño del adjunto excede el límite del servidor de LMArena (generalmente alrededor de 5 MB). Intente comprimir el archivo o cargar un archivo más pequeño."
                            logger.warning(f"PROCESADOR [ID: {request_id[:8]}]: Se detectó un error de adjunto demasiado grande (413).")
                            yield 'error', friendly_error_msg
                            return

                        # 2. Verificar página de verificación de Cloudflare
                        if any(re.search(p, error_msg, re.IGNORECASE) for p in cloudflare_patterns):
                            friendly_error_msg = "Se detectó la página de verificación humana de Cloudflare. Actualice la página de LMArena en su navegador y complete la verificación manualmente, luego vuelva a intentar la solicitud."
                            if browser_ws:
                                try:
                                    await browser_ws.send_text(json.dumps({"command": "refresh"}, ensure_ascii=False))
                                    logger.info(f"PROCESADOR [ID: {request_id[:8]}]: Se detectó CF en el mensaje de error y se envió la instrucción de actualización.")
                                except Exception as e:
                                    logger.error(f"PROCESADOR [ID: {request_id[:8]}]: Fallo al enviar la instrucción de actualización: {e}")
                            yield 'error', friendly_error_msg
                            return

                    # 3. Otros errores desconocidos
                    yield 'error', error_msg
                    return
            if raw_data == "[DONE]":
                break

            buffer += "".join(str(item) for item in raw_data) if isinstance(raw_data, list) else raw_data

            # Verificar errores de rate limiting en el contenido del chat
            if detect_rate_limit_error(buffer):
                logger.warning(f"PROCESADOR [ID: {request_id[:8]}]: Se detectó error 429 de rate limiting en el contenido.")
                if model_name:
                    rotation_success = rotate_model_session(model_name)
                    friendly_message = generate_rotation_message(model_name, rotation_success)
                    yield 'content', friendly_message
                    yield 'finish', 'stop'
                else:
                    fallback_message = "🔄 He detectado un límite de velocidad, pero no pude identificar el modelo específico para rotar automáticamente. Por favor, reintenta tu solicitud en unos minutos."
                    yield 'content', fallback_message
                    yield 'finish', 'stop'
                return

            if any(re.search(p, buffer, re.IGNORECASE) for p in cloudflare_patterns):
                error_msg = "Se detectó la página de verificación humana de Cloudflare. Actualice la página de LMArena en su navegador y complete la verificación manualmente, luego vuelva a intentar la solicitud."
                if browser_ws:
                    try:
                        await browser_ws.send_text(json.dumps({"command": "refresh"}, ensure_ascii=False))
                        logger.info(f"PROCESADOR [ID: {request_id[:8]}]: Se ha enviado una instrucción de actualización de página al navegador.")
                    except Exception as e:
                        logger.error(f"PROCESADOR [ID: {request_id[:8]}]: Fallo al enviar la instrucción de actualización: {e}")
                yield 'error', error_msg
                return
            
            if (error_match := error_pattern.search(buffer)):
                try:
                    error_json = json.loads(error_match.group(1))
                    yield 'error', error_json.get("error", "Error desconocido de LMArena")
                    return
                except json.JSONDecodeError: pass

            while (match := text_pattern.search(buffer)):
                try:
                    text_content = json.loads(f'"{match.group(1)}"')
                    if text_content: 
                        # Verificar también errores de rate limiting en el contenido de texto
                        if detect_rate_limit_error(text_content):
                            logger.warning(f"PROCESADOR [ID: {request_id[:8]}]: Se detectó error 429 en contenido de texto.")
                            if model_name:
                                rotation_success = rotate_model_session(model_name)
                                friendly_message = generate_rotation_message(model_name, rotation_success)
                                yield 'content', friendly_message
                                yield 'finish', 'stop'
                            else:
                                fallback_message = "🔄 He detectado un límite de velocidad, pero no pude identificar el modelo específico para rotar automáticamente. Por favor, reintenta tu solicitud en unos minutos."
                                yield 'content', fallback_message
                                yield 'finish', 'stop'
                            return
                        yield 'content', text_content
                except (ValueError, json.JSONDecodeError): pass
                buffer = buffer[match.end():]

            if (finish_match := finish_pattern.search(buffer)):
                try:
                    finish_data = json.loads(finish_match.group(1))
                    yield 'finish', finish_data.get("finishReason", "stop")
                except (json.JSONDecodeError, IndexError): pass
                buffer = buffer[finish_match.end():]

    except asyncio.CancelledError:
        logger.info(f"PROCESADOR [ID: {request_id[:8]}]: La tarea fue cancelada.")
    finally:
        if request_id in response_channels:
            del response_channels[request_id]
            logger.info(f"PROCESADOR [ID: {request_id[:8]}]: El canal de respuesta ha sido limpiado.")
        if request_id in AUTO_CLAUDE_ACTIVE_REQUESTS:
            del AUTO_CLAUDE_ACTIVE_REQUESTS[request_id]
            logger.info(f"AUTO-CLAUDE [ID: {request_id[:8]}]: La solicitud activa ha sido limpiada.")

async def stream_generator(request_id: str, model: str, assistant_prefill: str = ""):
    """Formatea el flujo de eventos interno en una respuesta SSE de OpenAI."""
    response_id = f"chatcmpl-{uuid.uuid4()}"
    logger.info(f"STREAMER [ID: {request_id[:8]}]: Generador de transmisión iniciado.")
    
    finish_reason_to_send = 'stop'  # Razón de finalización por defecto
    
    # Si hay assistant prefill, enviarlo primero
    if assistant_prefill:
        logger.info(f"STREAMER [ID: {request_id[:8]}]: Enviando assistant prefill: '{assistant_prefill[:50]}{'...' if len(assistant_prefill) > 50 else ''}'")
        yield format_openai_chunk(assistant_prefill, model, response_id)

    async for event_type, data in _process_lmarena_stream(request_id, model):
        if event_type == 'content':
            yield format_openai_chunk(data, model, response_id)
        elif event_type == 'finish':
            # Registrar la razón de finalización, pero no devolver inmediatamente, esperar a que el navegador envíe [DONE]
            finish_reason_to_send = data
            if data == 'content-filter':
                warning_msg = "\n\nLa respuesta fue terminada, posiblemente debido al límite de contexto o a la censura interna del modelo (muy probable)."
                yield format_openai_chunk(warning_msg, model, response_id)
        elif event_type == 'error':
            logger.error(f"STREAMER [ID: {request_id[:8]}]: Error en el flujo: {data}")
            yield format_openai_error_chunk(str(data), model, response_id)
            yield format_openai_finish_chunk(model, response_id, reason='stop')
            return # Cuando ocurre un error, se puede terminar inmediatamente

    # Solo se ejecuta después de que _process_lmarena_stream termine naturalmente (es decir, reciba [DONE])
    yield format_openai_finish_chunk(model, response_id, reason=finish_reason_to_send)
    logger.info(f"STREAMER [ID: {request_id[:8]}]: El generador de transmisión terminó normalmente.")

async def non_stream_response(request_id: str, model: str, assistant_prefill: str = ""):
    """Agrega el flujo de eventos interno y devuelve una única respuesta JSON de OpenAI."""
    response_id = f"chatcmpl-{uuid.uuid4()}"
    logger.info(f"NO-STREAM [ID: {request_id[:8]}]: Comenzando a procesar la respuesta no transmitida.")
    
    full_content = []
    finish_reason = "stop"
    
    # Si hay assistant prefill, agregarlo al inicio del contenido
    if assistant_prefill:
        logger.info(f"NO-STREAM [ID: {request_id[:8]}]: Incluyendo assistant prefill: '{assistant_prefill[:50]}{'...' if len(assistant_prefill) > 50 else ''}'")
        full_content.append(assistant_prefill)
    
    async for event_type, data in _process_lmarena_stream(request_id, model):
        if event_type == 'content':
            full_content.append(data)
        elif event_type == 'finish':
            finish_reason = data
            if data == 'content-filter':
                full_content.append("\n\nLa respuesta fue terminada, posiblemente debido al límite de contexto o a la censura interna del modelo (muy probable).")
            # No romper aquí, continuar esperando la señal [DONE] del navegador para evitar condiciones de carrera
        elif event_type == 'error':
            logger.error(f"NO-STREAM [ID: {request_id[:8]}]: Error durante el procesamiento: {data}")
            
            # Unificar los códigos de estado de error para respuestas transmitidas y no transmitidas
            status_code = 413 if "El tamaño del adjunto excede" in str(data) else 500

            error_response = {
                "error": {
                    "message": f"[Error del Puente LMArena]: {data}",
                    "type": "bridge_error",
                    "code": "attachment_too_large" if status_code == 413 else "processing_error"
                }
            }
            return Response(content=json.dumps(error_response, ensure_ascii=False), status_code=status_code, media_type="application/json")

    final_content = "".join(full_content)
    response_data = format_openai_non_stream_response(final_content, model, response_id, reason=finish_reason)
    
    logger.info(f"NO-STREAM [ID: {request_id[:8]}]: La agregación de la respuesta está completa.")
    return Response(content=json.dumps(response_data, ensure_ascii=False), media_type="application/json")

# --- Punto final de WebSocket ---
@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """Maneja la conexión WebSocket desde el script de Tampermonkey."""
    global browser_ws
    await websocket.accept()
    if browser_ws is not None:
        logger.warning("Se detectó una nueva conexión de script de Tampermonkey, la conexión antigua será reemplazada.")
    logger.info("✅ El script de Tampermonkey se ha conectado exitosamente al WebSocket.")
    browser_ws = websocket
    try:
        while True:
            # Esperar y recibir mensajes del script de Tampermonkey
            message_str = await websocket.receive_text()
            message = json.loads(message_str)
            
            request_id = message.get("request_id")
            data = message.get("data")

            if not request_id or data is None:
                logger.warning(f"Mensaje inválido recibido del navegador: {message}")
                continue

            # Colocar los datos recibidos en el canal de respuesta correspondiente
            if request_id in response_channels:
                await response_channels[request_id].put(data)
            else:
                logger.warning(f"⚠️ Se recibió una respuesta para una solicitud desconocida o cerrada: {request_id}")

    except WebSocketDisconnect:
        logger.warning("❌ El cliente del script de Tampermonkey se ha desconectado.")
    except Exception as e:
        logger.error(f"Ocurrió un error desconocido durante el manejo del WebSocket: {e}", exc_info=True)
    finally:
        browser_ws = None
        # Limpiar todos los canales de respuesta en espera para evitar que las solicitudes queden colgadas
        for queue in response_channels.values():
            await queue.put({"error": "El navegador se desconectó durante la operación"})
        response_channels.clear()
        logger.info("La conexión WebSocket ha sido limpiada.")

# --- Punto final de actualización de modelos ---
@app.post("/update_models")
async def update_models_endpoint(request: Request):
    """
    Recibe el HTML de la página del script de Tampermonkey, extrae y actualiza la lista de modelos.
    """
    html_content = await request.body()
    if not html_content:
        logger.warning("La solicitud de actualización de modelos no recibió ningún contenido HTML.")
        return JSONResponse(
            status_code=400,
            content={"status": "error", "message": "No se recibió contenido HTML."}
        )
    
    logger.info("Se recibió contenido de la página del script de Tampermonkey, comenzando a verificar y actualizar modelos...")
    new_models_list = extract_models_from_html(html_content.decode('utf-8'))
    
    if new_models_list:
        compare_and_update_models(new_models_list, 'models.json')
        # load_model_map() ahora se llama dentro de compare_and_update_models
        return JSONResponse({"status": "success", "message": "La comparación y actualización de modelos está completa."})
    else:
        logger.error("No se pudieron extraer los datos del modelo del HTML proporcionado por el script de Tampermonkey.")
        return JSONResponse(
            status_code=400,
            content={"status": "error", "message": "No se pudieron extraer los datos del modelo del HTML."}
        )

# --- Punto final de API compatible con OpenAI ---
@app.get("/v1/models")
async def get_models(request: Request):
    """Proporciona una lista de modelos compatible con OpenAI, filtrada por API key."""
    if not MODEL_NAME_TO_ID_MAP:
        return JSONResponse(
            status_code=404,
            content={"error": "La lista de modelos está vacía o no se encontró 'models.json'."}
        )
    
    # Validar API key si está configurada (PERO NO incrementar uso)
    api_key = None
    config_api_key = CONFIG.get("api_key")
    if config_api_key or API_KEYS_DATA.get("api_keys"):
        try:
            api_key = await get_api_key_from_header(request)
        except HTTPException:
            raise HTTPException(
                status_code=401,
                detail="API key requerida para acceder a la lista de modelos."
            )
    
    # Obtener modelos permitidos para esta API key
    if api_key:
        allowed_models = get_models_for_api_key(api_key)
        if not allowed_models:
            # Si la validación falla, obtener el error específico
            is_valid, key_data, error = validate_api_key(api_key)
            if not is_valid:
                raise HTTPException(status_code=401, detail=error)
            # Si la validación pasa pero no hay modelos, devolver lista vacía
            allowed_models = []
    else:
        # Si no hay API key configurada, mostrar todos los modelos
        allowed_models = list(MODEL_NAME_TO_ID_MAP.keys())
    
    # NOTA: /v1/models NO incrementa el contador de uso de la API key
    # Solo las llamadas a /v1/chat/completions deben contar como uso
    
    return {
        "object": "list",
        "data": [
            {
                "id": model_name, 
                "object": "model",
                "created": int(asyncio.get_event_loop().time()), 
                "owned_by": "LMArenaBridge"
            }
            for model_name in allowed_models
        ],
    }

@app.post("/v1/chat/completions")
async def chat_completions(request: Request):
    """
    Maneja las solicitudes de completado de chat.
    Recibe solicitudes en formato OpenAI, las convierte al formato LMArena,
    las envía al script de Tampermonkey a través de WebSocket y luego transmite los resultados.
    """
    global last_activity_time
    last_activity_time = datetime.now() # Actualizar hora de actividad
    logger.info(f"Se recibió una solicitud de API, la hora de actividad se actualizó a: {last_activity_time.strftime('%Y-%m-%d %H:%M:%S')}")

    load_config()  # Cargar la configuración más reciente en tiempo real para asegurar que los ID de sesión, etc., estén actualizados
    
    # Leer el JSON del request una sola vez
    try:
        openai_req = await request.json()
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Cuerpo de la solicitud JSON inválido")

    # --- Verificación de la clave de API ---
    config_api_key = CONFIG.get("api_key")
    if config_api_key or API_KEYS_DATA.get("api_keys"):
        try:
            provided_key = await get_api_key_from_header(request)
        except HTTPException:
            raise HTTPException(
                status_code=401,
                detail="No se proporcionó una clave de API. Por favor, proporciónela en la cabecera de Autorización en formato 'Bearer SU_CLAVE'."
            )
        
        # Obtener el modelo para verificar permisos
        model_name = openai_req.get("model")
        
        # Validar API key y permisos
        is_valid, key_data, error = validate_api_key(provided_key, model_name)
        if not is_valid:
            raise HTTPException(status_code=401, detail=error)
        
        # Incrementar contador de uso
        increment_api_key_usage(provided_key)

    if not browser_ws:
        raise HTTPException(status_code=503, detail="El cliente del script de Tampermonkey no está conectado. Asegúrese de que la página de LMArena esté abierta y el script activado.")

    # --- Lógica de mapeo de modelo e ID de sesión ---
    model_name = openai_req.get("model")
    is_auto_claude = False
    original_model_name = model_name
    
    # Generate request_id early since we need it for auto-claude tracking
    request_id = str(uuid.uuid4())
    
    # Check if using auto-claude
    if model_name == "auto-claude":
        is_auto_claude = True
        model_name = get_best_available_claude_model()
        logger.info(f"AUTO-CLAUDE: Modelo seleccionado automáticamente: '{model_name}'")
        # Store the request as auto-claude
        AUTO_CLAUDE_ACTIVE_REQUESTS[request_id] = model_name
    
    session_id, message_id, mode_override, battle_target_override = get_current_session_ids(model_name)

    # Si no se encontró mapeo específico para el modelo, usar configuración global
    if not session_id:
        if CONFIG.get("use_default_ids_if_mapping_not_found", True):
            session_id = CONFIG.get("session_id")
            message_id = CONFIG.get("message_id")
            # Al usar ID globales, no establecer anulación de modo, dejar que use la configuración global
            mode_override, battle_target_override = None, None
            logger.info(f"No se encontró un mapeo válido para el modelo '{model_name}', usando el ID de sesión global por defecto según la configuración: ...{session_id[-6:] if session_id else 'N/A'}")
        else:
            logger.error(f"No se encontró un mapeo válido para el modelo '{model_name}' en 'model_endpoint_map.json', y se ha deshabilitado el respaldo a los ID por defecto.")
            raise HTTPException(
                status_code=400,
                detail=f"El modelo '{model_name}' no tiene un ID de sesión independiente configurado. Agregue un mapeo válido en 'model_endpoint_map.json' o habilite 'use_default_ids_if_mapping_not_found' en 'config.jsonc'."
            )
    else:
        # Log del mapeo encontrado
        log_msg = f"Se usará el ID de sesión: ...{session_id[-6:] if session_id else 'N/A'}"
        if mode_override:
            log_msg += f" (modo: {mode_override}"
            if mode_override == 'battle':
                log_msg += f", objetivo: {battle_target_override or 'A'}"
            log_msg += ")"
        logger.info(log_msg)

    # --- Validar la información de sesión finalmente determinada ---
    if not session_id or not message_id or "YOUR_" in session_id or "YOUR_" in message_id:
        raise HTTPException(
            status_code=400,
            detail="El ID de sesión o el ID de mensaje finalmente determinados son inválidos. Verifique la configuración en 'model_endpoint_map.json' y 'config.jsonc', o ejecute `id_updater.py` para actualizar los valores por defecto."
        )

    if not model_name or model_name not in MODEL_NAME_TO_ID_MAP:
        logger.warning(f"El modelo solicitado '{model_name}' no está en models.json, se usará el ID de modelo por defecto.")

    response_channels[request_id] = asyncio.Queue()
    logger.info(f"LLAMADA API [ID: {request_id[:8]}]: Se ha creado el canal de respuesta.")

    try:
        # 1. Convertir la solicitud, pasando la posible información de anulación de modo
        lmarena_payload = convert_openai_to_lmarena_payload(
            openai_req,
            session_id,
            message_id,
            mode_override=mode_override,
            battle_target_override=battle_target_override,
            is_auto_claude=is_auto_claude
        )
        
        # 2. Envolver en un mensaje para enviar al navegador
        message_to_browser = {
            "request_id": request_id,
            "payload": lmarena_payload
        }
        
        # 3. Enviar a través de WebSocket
        logger.info(f"LLAMADA API [ID: {request_id[:8]}]: Enviando carga útil al script de Tampermonkey a través de WebSocket.")
        await browser_ws.send_text(json.dumps(message_to_browser))

        # 4. Decidir el tipo de retorno según el parámetro stream
        is_stream = openai_req.get("stream", True)

        if is_stream:
            # Devolver respuesta transmitida
            return StreamingResponse(
                stream_generator(request_id, model_name or "default_model", lmarena_payload.get("assistant_prefill", "")),
                media_type="text/event-stream"
            )
        else:
            # Devolver respuesta no transmitida
            return await non_stream_response(request_id, model_name or "default_model", lmarena_payload.get("assistant_prefill", ""))
    except Exception as e:
        # Si ocurre un error durante la configuración, limpiar el canal
        if request_id in response_channels:
            del response_channels[request_id]
        logger.error(f"LLAMADA API [ID: {request_id[:8]}]: Ocurrió un error fatal al procesar la solicitud: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/v1/images/generations")
async def images_generations(request: Request):
    """
    Maneja las solicitudes de generación de imágenes de texto a imagen.
    Este punto final recibe solicitudes de generación de imágenes en formato OpenAI y devuelve las URL de imagen correspondientes.
    """
    global last_activity_time
    last_activity_time = datetime.now()
    logger.info(f"Se recibió una solicitud de API de generación de imágenes, la hora de actividad se actualizó a: {last_activity_time.strftime('%Y-%m-%d %H:%M:%S')}")
    
    # El módulo ya ha sido inicializado a través de `initialize_image_module`, se puede llamar directamente
    response_data, status_code = await image_generation.handle_image_generation_request(request, browser_ws)
    
    return JSONResponse(content=response_data, status_code=status_code)

# --- Endpoints del Panel de Administración ---
@app.get("/admin")
async def admin_page():
    """Sirve la página de administración."""
    if not CONFIG.get("admin_enabled", True):
        raise HTTPException(status_code=404, detail="Panel de administración deshabilitado")
    
    admin_html_path = os.path.join("static", "admin.html")
    if os.path.exists(admin_html_path):
        return FileResponse(admin_html_path)
    else:
        raise HTTPException(status_code=404, detail="Página de administración no encontrada")

@app.post("/admin/auth")
async def admin_login(request: Request):
    """Autenticación del administrador."""
    if not CONFIG.get("admin_enabled", True):
        raise HTTPException(status_code=404, detail="Panel de administración deshabilitado")
    
    try:
        data = await request.json()
        password = data.get("password")
        
        if not password:
            return JSONResponse(status_code=400, content={"error": "Contraseña requerida"})
        
        if not verify_admin_password(password):
            return JSONResponse(status_code=401, content={"error": "Contraseña incorrecta"})
        
        # Limpiar sesiones expiradas
        cleanup_expired_admin_sessions()
        
        # Crear nueva sesión
        token = generate_admin_token()
        create_admin_session(token)
        
        logger.info("ADMIN: Nueva sesión de administrador creada exitosamente")
        return JSONResponse({"token": token, "expires_in": 28800})  # 8 horas
        
    except json.JSONDecodeError:
        return JSONResponse(status_code=400, content={"error": "JSON inválido"})
    except Exception as e:
        logger.error(f"ADMIN: Error en autenticación: {e}")
        return JSONResponse(status_code=500, content={"error": "Error interno del servidor"})

@app.get("/admin/api/keys")
async def get_api_keys(token: str = Depends(get_admin_token)):
    """Obtiene la lista de todas las API keys."""
    try:
        # Recargar las API keys para asegurar datos actualizados
        load_api_keys()
        
        api_keys = API_KEYS_DATA.get("api_keys", {})
        formatted_keys = []
        
        for key_id, key_data in api_keys.items():
            formatted_keys.append({
                "id": key_id,
                "name": key_data.get("name"),
                "description": key_data.get("description"),
                "usage_limit": key_data.get("usage_limit"),
                "usage_count": key_data.get("usage_count", 0),
                "enabled": key_data.get("enabled", True),
                "models": key_data.get("models", []),
                "created_at": key_data.get("created_at"),
                "last_used": key_data.get("last_used")
            })
        
        return JSONResponse({"api_keys": formatted_keys})
        
    except Exception as e:
        logger.error(f"ADMIN: Error obteniendo API keys: {e}")
        return JSONResponse(status_code=500, content={"error": "Error interno del servidor"})

@app.post("/admin/api/keys")
async def create_api_key(request: Request, token: str = Depends(get_admin_token)):
    """Crea una nueva API key."""
    try:
        data = await request.json()
        
        # Validar y procesar datos
        name = data.get("name") or None
        description = data.get("description") or None
        usage_limit = data.get("usage_limit")
        models = data.get("models", [])
        
        # Validar límite de usos
        if usage_limit is not None:
            try:
                usage_limit = int(usage_limit)
                if usage_limit < 1:
                    return JSONResponse(status_code=400, content={"error": "El límite de usos debe ser mayor a 0"})
            except ValueError:
                return JSONResponse(status_code=400, content={"error": "Límite de usos inválido"})
        
        # NO validar modelos - permitir modelos custom como auto-claude
        # Los modelos se aceptan tal como vienen, sin verificación
        
        # Generar nueva API key
        new_api_key = generate_api_key()
        
        # Crear entrada de API key
        api_key_data = {
            "name": name,
            "description": description,
            "usage_limit": usage_limit,
            "usage_count": 0,
            "enabled": True,
            "models": models,
            "created_at": datetime.now().isoformat(),
            "last_used": None
        }
        
        # Guardar en el archivo
        API_KEYS_DATA["api_keys"][new_api_key] = api_key_data
        save_api_keys()
        
        logger.info(f"ADMIN: Nueva API key creada: {new_api_key[:16]}...")
        return JSONResponse({
            "message": "API key creada exitosamente",
            "api_key": new_api_key
        })
        
    except json.JSONDecodeError:
        return JSONResponse(status_code=400, content={"error": "JSON inválido"})
    except Exception as e:
        logger.error(f"ADMIN: Error creando API key: {e}")
        return JSONResponse(status_code=500, content={"error": "Error interno del servidor"})

@app.put("/admin/api/keys/{key_id}")
async def update_api_key(key_id: str, request: Request, token: str = Depends(get_admin_token)):
    """Actualiza una API key existente."""
    try:
        api_keys = API_KEYS_DATA.get("api_keys", {})
        if key_id not in api_keys:
            return JSONResponse(status_code=404, content={"error": "API key no encontrada"})
        
        data = await request.json()
        
        # Actualizar campos permitidos
        key_data = api_keys[key_id]
        
        if "name" in data:
            key_data["name"] = data["name"] or None
        
        if "description" in data:
            key_data["description"] = data["description"] or None
        
        if "usage_limit" in data:
            usage_limit = data["usage_limit"]
            if usage_limit is not None:
                try:
                    usage_limit = int(usage_limit)
                    if usage_limit < 1:
                        return JSONResponse(status_code=400, content={"error": "El límite de usos debe ser mayor a 0"})
                except ValueError:
                    return JSONResponse(status_code=400, content={"error": "Límite de usos inválido"})
            key_data["usage_limit"] = usage_limit
        
        if "models" in data:
            models = data["models"]
            # NO validar modelos - permitir modelos custom como auto-claude
            key_data["models"] = models
        
        # Guardar cambios
        save_api_keys()
        
        logger.info(f"ADMIN: API key actualizada: {key_id[:16]}...")
        return JSONResponse({"message": "API key actualizada exitosamente"})
        
    except json.JSONDecodeError:
        return JSONResponse(status_code=400, content={"error": "JSON inválido"})
    except Exception as e:
        logger.error(f"ADMIN: Error actualizando API key: {e}")
        return JSONResponse(status_code=500, content={"error": "Error interno del servidor"})

@app.delete("/admin/api/keys/{key_id}")
async def delete_api_key(key_id: str, token: str = Depends(get_admin_token)):
    """Elimina una API key."""
    try:
        api_keys = API_KEYS_DATA.get("api_keys", {})
        if key_id not in api_keys:
            return JSONResponse(status_code=404, content={"error": "API key no encontrada"})
        
        # Eliminar la API key
        del api_keys[key_id]
        save_api_keys()
        
        logger.info(f"ADMIN: API key eliminada: {key_id[:16]}...")
        return JSONResponse({"message": "API key eliminada exitosamente"})
        
    except Exception as e:
        logger.error(f"ADMIN: Error eliminando API key: {e}")
        return JSONResponse(status_code=500, content={"error": "Error interno del servidor"})

@app.post("/admin/api/keys/{key_id}/toggle")
async def toggle_api_key(key_id: str, token: str = Depends(get_admin_token)):
    """Habilita o deshabilita una API key."""
    try:
        api_keys = API_KEYS_DATA.get("api_keys", {})
        if key_id not in api_keys:
            return JSONResponse(status_code=404, content={"error": "API key no encontrada"})
        
        # Cambiar estado
        key_data = api_keys[key_id]
        current_status = key_data.get("enabled", True)
        key_data["enabled"] = not current_status
        
        save_api_keys()
        
        new_status = "habilitada" if key_data["enabled"] else "deshabilitada"
        logger.info(f"ADMIN: API key {new_status}: {key_id[:16]}...")
        return JSONResponse({"message": f"API key {new_status} exitosamente"})
        
    except Exception as e:
        logger.error(f"ADMIN: Error cambiando estado de API key: {e}")
        return JSONResponse(status_code=500, content={"error": "Error interno del servidor"})

@app.post("/admin/api/keys/bulk-add-model")
async def bulk_add_model_to_keys(request: Request, token: str = Depends(get_admin_token)):
    """Agrega un modelo específico a todas las API keys que no lo tengan."""
    try:
        data = await request.json()
        model_name = data.get("model_name")
        
        if not model_name:
            return JSONResponse(status_code=400, content={"error": "Nombre del modelo requerido"})
        
        # NO validar que el modelo existe - permitir modelos custom como auto-claude
        # El modelo se acepta tal como viene
        
        # Recargar API keys para asegurar datos actualizados
        load_api_keys()
        api_keys = API_KEYS_DATA.get("api_keys", {})
        
        if not api_keys:
            return JSONResponse(status_code=400, content={"error": "No hay API keys en el sistema"})
        
        # Contar cuántas keys serán afectadas
        keys_to_modify = []
        keys_already_have_model = []
        
        for key_id, key_data in api_keys.items():
            current_models = key_data.get("models", [])
            if model_name not in current_models:
                keys_to_modify.append(key_id)
            else:
                keys_already_have_model.append(key_id)
        
        if not keys_to_modify:
            return JSONResponse({
                "message": f"Todas las API keys ya tienen el modelo '{model_name}'",
                "keys_modified": 0,
                "keys_already_had_model": len(keys_already_have_model),
                "total_keys": len(api_keys)
            })
        
        # Aplicar el modelo a las keys que no lo tienen
        for key_id in keys_to_modify:
            key_data = api_keys[key_id]
            current_models = key_data.get("models", [])
            current_models.append(model_name)
            key_data["models"] = current_models
        
        # Guardar cambios
        save_api_keys()
        
        logger.info(f"ADMIN: Modelo '{model_name}' agregado a {len(keys_to_modify)} API keys")
        return JSONResponse({
            "message": f"Modelo '{model_name}' agregado exitosamente",
            "keys_modified": len(keys_to_modify),
            "keys_already_had_model": len(keys_already_have_model),
            "total_keys": len(api_keys)
        })
        
    except json.JSONDecodeError:
        return JSONResponse(status_code=400, content={"error": "JSON inválido"})
    except Exception as e:
        logger.error(f"ADMIN: Error en operación masiva de agregar modelo: {e}")
        return JSONResponse(status_code=500, content={"error": "Error interno del servidor"})

@app.get("/admin/api/keys/paginated")
async def get_api_keys_paginated(
    request: Request,
    page: int = 1,
    limit: int = 10,
    name_filter: str = "",
    status_filter: str = "all",
    usage_min: int = None,
    usage_max: int = None,
    models_filter: str = "",
    token: str = Depends(get_admin_token)
):
    """Obtiene la lista paginada y filtrada de API keys."""
    try:
        # Recargar las API keys para asegurar datos actualizados
        load_api_keys()
        
        api_keys = API_KEYS_DATA.get("api_keys", {})
        all_keys = []
        
        for key_id, key_data in api_keys.items():
            all_keys.append({
                "id": key_id,
                "name": key_data.get("name"),
                "description": key_data.get("description"),
                "usage_limit": key_data.get("usage_limit"),
                "usage_count": key_data.get("usage_count", 0),
                "enabled": key_data.get("enabled", True),
                "models": key_data.get("models", []),
                "created_at": key_data.get("created_at"),
                "last_used": key_data.get("last_used")
            })
        
        # Aplicar filtros
        filtered_keys = all_keys
        
        # Filtro por nombre
        if name_filter:
            filtered_keys = [
                key for key in filtered_keys
                if name_filter.lower() in (key.get("name") or "").lower() or
                   name_filter.lower() in key["id"].lower()
            ]
        
        # Filtro por estado
        if status_filter == "active":
            filtered_keys = [key for key in filtered_keys if key["enabled"]]
        elif status_filter == "disabled":
            filtered_keys = [key for key in filtered_keys if not key["enabled"]]
        
        # Filtro por rango de usos
        if usage_min is not None:
            filtered_keys = [key for key in filtered_keys if key["usage_count"] >= usage_min]
        if usage_max is not None:
            filtered_keys = [key for key in filtered_keys if key["usage_count"] <= usage_max]
        
        # Filtro por modelos
        if models_filter:
            model_list = [m.strip() for m in models_filter.split(",") if m.strip()]
            filtered_keys = [
                key for key in filtered_keys
                if any(model in key["models"] for model in model_list)
            ]
        
        # Calcular paginación
        total_keys = len(filtered_keys)
        total_pages = (total_keys + limit - 1) // limit
        start_index = (page - 1) * limit
        end_index = start_index + limit
        paginated_keys = filtered_keys[start_index:end_index]
        
        return JSONResponse({
            "api_keys": paginated_keys,
            "pagination": {
                "current_page": page,
                "total_pages": total_pages,
                "total_keys": total_keys,
                "keys_per_page": limit,
                "has_next": page < total_pages,
                "has_prev": page > 1
            }
        })
        
    except Exception as e:
        logger.error(f"ADMIN: Error obteniendo API keys paginadas: {e}")
        return JSONResponse(status_code=500, content={"error": "Error interno del servidor"})

# --- Punto final de comunicación interna ---
@app.post("/internal/start_id_capture")
async def start_id_capture():
    """
    Recibe una notificación de id_updater.py y, a través de un comando WebSocket,
    activa el modo de captura de ID del script de Tampermonkey.
    """
    if not browser_ws:
        logger.warning("CAPTURA DE ID: Se recibió una solicitud de activación, pero no hay conexión con el navegador.")
        raise HTTPException(status_code=503, detail="Cliente del navegador no conectado.")
    
    try:
        logger.info("CAPTURA DE ID: Se recibió una solicitud de activación, enviando comando a través de WebSocket...")
        await browser_ws.send_text(json.dumps({"command": "activate_id_capture"}))
        logger.info("CAPTURA DE ID: El comando de activación se ha enviado exitosamente.")
        return JSONResponse({"status": "success", "message": "Comando de activación enviado."})
    except Exception as e:
        logger.error(f"CAPTURA DE ID: Error al enviar el comando de activación: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Fallo al enviar el comando a través de WebSocket.")


# --- Punto de entrada principal del programa ---
if __name__ == "__main__":
    # Se recomienda leer el puerto desde config.jsonc, aquí está codificado temporalmente
    api_port = 4102
    logger.info(f"🚀 Iniciando el servidor API de LMArena Bridge v2.0...")
    logger.info(f"   - Dirección de escucha: http://127.0.0.1:{api_port}")
    logger.info(f"   - Punto final de WebSocket: ws://127.0.0.1:{api_port}/ws")
    
    uvicorn.run(app, host="0.0.0.0", port=api_port)
