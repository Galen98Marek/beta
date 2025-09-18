# external_client_server.py
# Servidor API simplificado para clientes externos de LMArena Bridge
# Este servidor corre en computadoras remotas y ejecuta requests de LMArena

import asyncio
import json
import logging
import os
import sys
import time
import uuid
import re
import threading
from datetime import datetime
from contextlib import asynccontextmanager

import uvicorn
import requests
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, JSONResponse, Response
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

# --- Configuración básica ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# --- Estado y configuración global ---
CONFIG = {}
MODEL_NAME_TO_ID_MAP = {}
MODEL_ENDPOINT_MAP = {}
DEFAULT_MODEL_ID = "f44e280a-7914-43ca-a25d-ecfcc5d48d09"  # Claude 3.5 Sonnet

# WebSocket connection con el script de Tampermonkey local
browser_ws: WebSocket | None = None
response_channels: dict[str, asyncio.Queue] = {}
last_activity_time = None

def load_config():
    """Carga la configuración desde external_client_config.jsonc."""
    global CONFIG
    try:
        with open('external_client_config.jsonc', 'r', encoding='utf-8') as f:
            content = f.read()
            # Eliminar comentarios de línea // y comentarios de bloque /* */
            json_content = re.sub(r'//.*', '', content)
            json_content = re.sub(r'/\*.*?\*/', '', json_content, flags=re.DOTALL)
            CONFIG = json.loads(json_content)
        logger.info("Configuración de cliente externo cargada exitosamente desde 'external_client_config.jsonc'.")
    except (FileNotFoundError, json.JSONDecodeError) as e:
        logger.error(f"Fallo al cargar 'external_client_config.jsonc': {e}. Se usará configuración por defecto.")
        CONFIG = {
            "client_id": "external-client-001",
            "client_name": "Cliente Externo",
            "api_key": "change-this-secret-key",
            "session_id": "YOUR_SESSION_ID_HERE",
            "message_id": "YOUR_MESSAGE_ID_HERE",
            "port": 5104,
            "tampermonkey_ws_port": 5105,
            "id_updater_port": 5106
        }

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

def load_model_endpoint_map():
    """Carga el mapeo de modelos a puntos finales desde model_endpoint_map.json."""
    global MODEL_ENDPOINT_MAP
    try:
        with open('model_endpoint_map.json', 'r', encoding='utf-8') as f:
            content = f.read()
            if not content.strip():
                MODEL_ENDPOINT_MAP = {}
            else:
                MODEL_ENDPOINT_MAP = json.loads(content)
        logger.info(f"Se cargaron exitosamente {len(MODEL_ENDPOINT_MAP)} mapeos de puntos finales de modelos.")
    except FileNotFoundError:
        logger.warning("No se encontró 'model_endpoint_map.json'. Se usará un mapeo vacío.")
        MODEL_ENDPOINT_MAP = {}
    except json.JSONDecodeError as e:
        logger.error(f"Fallo al cargar 'model_endpoint_map.json': {e}. Se usará un mapeo vacío.")
        MODEL_ENDPOINT_MAP = {}

def get_current_session_ids(model_name: str) -> tuple:
    """Obtiene los session_id y message_id actuales para un modelo o usa los globales."""
    if model_name in MODEL_ENDPOINT_MAP:
        model_mapping = MODEL_ENDPOINT_MAP[model_name]
        if "current_index" in model_mapping:
            current_index = model_mapping.get("current_index", 0)
            session_key = f"session_id{current_index}"
            message_key = f"message_id{current_index}"
            
            session_id = model_mapping.get(session_key)
            message_id = model_mapping.get(message_key)
            mode = model_mapping.get("mode")
            battle_target = model_mapping.get("battle_target")
            
            if session_id and message_id:
                return session_id, message_id, mode, battle_target
    
    # Usar IDs globales como fallback
    session_id = CONFIG.get("session_id")
    message_id = CONFIG.get("message_id")
    return session_id, message_id, None, None

def _process_openai_message(message: dict) -> dict:
    """Procesa mensajes de OpenAI, separando texto y adjuntos."""
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
                original_filename = image_url_data.get("detail")

                if url and url.startswith("data:"):
                    try:
                        content_type = url.split(';')[0].split(':')[1]
                        
                        if original_filename and isinstance(original_filename, str):
                            file_name = original_filename
                        else:
                            main_type, sub_type = content_type.split('/') if '/' in content_type else ('application', 'octet-stream')
                            prefix = "image" if main_type == "image" else "file"
                            file_name = f"{prefix}_{uuid.uuid4()}.{sub_type}"

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

    if role == "user" and not text_content.strip():
        text_content = " "

    return {
        "role": role,
        "content": text_content,
        "attachments": attachments
    }

def convert_openai_to_lmarena_payload(openai_data: dict, session_id: str, message_id: str, mode_override: str = None, battle_target_override: str = None) -> dict:
    """Convierte el cuerpo de la solicitud de OpenAI a la carga útil de LMArena."""
    messages = openai_data.get("messages", [])
    for msg in messages:
        if msg.get("role") == "developer":
            msg["role"] = "system"
            
    processed_messages = [_process_openai_message(msg.copy()) for msg in messages]

    model_name = openai_data.get("model", "claude-3-5-sonnet-20241022")
    target_model_id = MODEL_NAME_TO_ID_MAP.get(model_name, DEFAULT_MODEL_ID)
    
    message_templates = []
    for msg in processed_messages:
        message_templates.append({
            "role": msg["role"],
            "content": msg.get("content", ""),
            "attachments": msg.get("attachments", [])
        })

    # Aplicar posiciones de participante
    mode = mode_override or "direct_chat"
    target_participant = battle_target_override or "A"
    target_participant = target_participant.lower()

    for msg in message_templates:
        if msg['role'] == 'system':
            msg['participantPosition'] = target_participant if mode == 'battle' else 'b'
        elif mode == 'battle':
            msg['participantPosition'] = target_participant
        else:
            msg['participantPosition'] = 'a'

    return {
        "message_templates": message_templates,
        "target_model_id": target_model_id,
        "session_id": session_id,
        "message_id": message_id
    }

async def _process_lmarena_stream(request_id: str, model_name: str = None):
    """Procesa el flujo de datos del navegador y produce eventos estructurados."""
    queue = response_channels.get(request_id)
    if not queue:
        logger.error(f"PROCESADOR [ID: {request_id[:8]}]: No se encontró el canal de respuesta.")
        yield 'error', 'Error interno del servidor: no se encontró el canal de respuesta.'
        return

    buffer = ""
    timeout = CONFIG.get("stream_response_timeout_seconds", 360)
    text_pattern = re.compile(r'[ab]0:"((?:\\.|[^"\\])*)"')
    finish_pattern = re.compile(r'[ab]d:(\{.*?"finishReason".*?\})')
    error_pattern = re.compile(r'(\{\s*"error".*?\})', re.DOTALL)

    try:
        while True:
            try:
                raw_data = await asyncio.wait_for(queue.get(), timeout=timeout)
            except asyncio.TimeoutError:
                logger.warning(f"PROCESADOR [ID: {request_id[:8]}]: Tiempo de espera agotado ({timeout} segundos).")
                yield 'error', f'La respuesta expiró después de {timeout} segundos.'
                return

            if isinstance(raw_data, dict):
                if 'error' in raw_data:
                    error_msg = raw_data.get('error', 'Error desconocido del navegador')
                    yield 'error', error_msg
                    return
                    
            if raw_data == "[DONE]":
                break

            buffer += "".join(str(item) for item in raw_data) if isinstance(raw_data, list) else raw_data

            if (error_match := error_pattern.search(buffer)):
                try:
                    error_json = json.loads(error_match.group(1))
                    yield 'error', error_json.get("error", "Error desconocido de LMArena")
                    return
                except json.JSONDecodeError:
                    pass

            while (match := text_pattern.search(buffer)):
                try:
                    text_content = json.loads(f'"{match.group(1)}"')
                    if text_content:
                        yield 'content', text_content
                except (ValueError, json.JSONDecodeError):
                    pass
                buffer = buffer[match.end():]

            if (finish_match := finish_pattern.search(buffer)):
                try:
                    finish_data = json.loads(finish_match.group(1))
                    yield 'finish', finish_data.get("finishReason", "stop")
                except (json.JSONDecodeError, IndexError):
                    pass
                buffer = buffer[finish_match.end():]

    except asyncio.CancelledError:
        logger.info(f"PROCESADOR [ID: {request_id[:8]}]: Tarea cancelada.")
    finally:
        if request_id in response_channels:
            del response_channels[request_id]

def format_openai_chunk(content: str, model: str, request_id: str) -> str:
    """Formatea una respuesta como chunk de OpenAI."""
    chunk = {
        "id": request_id, "object": "chat.completion.chunk",
        "created": int(time.time()), "model": model,
        "choices": [{"index": 0, "delta": {"content": content}, "finish_reason": None}]
    }
    return f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"

def format_openai_finish_chunk(model: str, request_id: str, reason: str = 'stop') -> str:
    """Formatea un chunk de finalización de OpenAI."""
    chunk = {
        "id": request_id, "object": "chat.completion.chunk",
        "created": int(time.time()), "model": model,
        "choices": [{"index": 0, "delta": {}, "finish_reason": reason}]
    }
    return f"data: {json.dumps(chunk, ensure_ascii=False)}\n\ndata: [DONE]\n\n"

async def stream_generator(request_id: str, model: str):
    """Genera respuesta en streaming para OpenAI."""
    response_id = f"chatcmpl-{uuid.uuid4()}"
    finish_reason_to_send = 'stop'

    async for event_type, data in _process_lmarena_stream(request_id, model):
        if event_type == 'content':
            yield format_openai_chunk(data, model, response_id)
        elif event_type == 'finish':
            finish_reason_to_send = data
        elif event_type == 'error':
            logger.error(f"STREAMER [ID: {request_id[:8]}]: Error en el flujo: {data}")
            error_chunk = format_openai_chunk(f"\n\n[Error del Cliente Externo]: {data}", model, response_id)
            yield error_chunk
            yield format_openai_finish_chunk(model, response_id, reason='stop')
            return

    yield format_openai_finish_chunk(model, response_id, reason=finish_reason_to_send)

# --- Eventos del ciclo de vida ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Función de ciclo de vida que se ejecuta al iniciar el servidor."""
    global last_activity_time
    
    load_config()
    load_model_map()
    load_model_endpoint_map()
    
    last_activity_time = datetime.now()
    
    client_name = CONFIG.get("client_name", "Cliente Externo")
    logger.info(f"🚀 Cliente Externo '{client_name}' iniciado correctamente.")
    logger.info("Esperando conexión del script de Tampermonkey local...")
    
    yield
    logger.info("El cliente externo se está cerrando.")

app = FastAPI(lifespan=lifespan)

# --- Configuración del middleware CORS ---
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- Autenticación ---
security = HTTPBearer(auto_error=False)

async def verify_api_key(credentials: HTTPAuthorizationCredentials = None):
    """Verifica la API key del cliente externo."""
    if not credentials:
        raise HTTPException(status_code=401, detail="API key requerida")
    
    expected_key = CONFIG.get("api_key", "change-this-secret-key")
    if credentials.credentials != expected_key:
        raise HTTPException(status_code=401, detail="API key inválida")
    
    return credentials.credentials

# --- WebSocket para Tampermonkey local ---
@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """Maneja la conexión WebSocket desde el script de Tampermonkey local."""
    global browser_ws
    await websocket.accept()
    
    if browser_ws is not None:
        logger.warning("Nueva conexión de Tampermonkey detectada, reemplazando la anterior.")
    
    logger.info("✅ Script de Tampermonkey local conectado al WebSocket.")
    browser_ws = websocket
    
    try:
        while True:
            message_str = await websocket.receive_text()
            message = json.loads(message_str)
            
            request_id = message.get("request_id")
            data = message.get("data")

            if not request_id or data is None:
                logger.warning(f"Mensaje inválido recibido del navegador: {message}")
                continue

            if request_id in response_channels:
                await response_channels[request_id].put(data)
            else:
                logger.warning(f"⚠️ Respuesta recibida para solicitud desconocida: {request_id}")

    except WebSocketDisconnect:
        logger.warning("❌ El cliente de Tampermonkey se ha desconectado.")
    except Exception as e:
        logger.error(f"Error durante el manejo del WebSocket: {e}", exc_info=True)
    finally:
        browser_ws = None
        for queue in response_channels.values():
            await queue.put({"error": "El navegador se desconectó durante la operación"})
        response_channels.clear()

# --- Endpoint principal para ejecutar requests ---
@app.post("/execute")
async def execute_request(request: Request, api_key: str = security):
    """Endpoint principal que recibe requests del servidor principal y los ejecuta."""
    global last_activity_time
    last_activity_time = datetime.now()
    
    # Verificar API key
    await verify_api_key(api_key)
    
    if not browser_ws:
        raise HTTPException(
            status_code=503,
            detail="El cliente de Tampermonkey no está conectado. Asegúrese de que la página de LMArena esté abierta."
        )

    try:
        openai_req = await request.json()
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Cuerpo de la solicitud JSON inválido")

    model_name = openai_req.get("model")
    session_id, message_id, mode_override, battle_target_override = get_current_session_ids(model_name)

    if not session_id or not message_id or "YOUR_" in session_id or "YOUR_" in message_id:
        raise HTTPException(
            status_code=400,
            detail="Los IDs de sesión no están configurados correctamente. Ejecute external_id_updater.py para configurarlos."
        )

    request_id = str(uuid.uuid4())
    response_channels[request_id] = asyncio.Queue()
    
    logger.info(f"CLIENTE EXTERNO [ID: {request_id[:8]}]: Ejecutando request para modelo '{model_name}'")

    try:
        lmarena_payload = convert_openai_to_lmarena_payload(
            openai_req,
            session_id,
            message_id,
            mode_override=mode_override,
            battle_target_override=battle_target_override
        )
        
        message_to_browser = {
            "request_id": request_id,
            "payload": lmarena_payload
        }
        
        await browser_ws.send_text(json.dumps(message_to_browser))

        is_stream = openai_req.get("stream", True)
        if is_stream:
            return StreamingResponse(
                stream_generator(request_id, model_name or "default_model"),
                media_type="text/event-stream"
            )
        else:
            # Para simplicidad, siempre devolver streaming por ahora
            return StreamingResponse(
                stream_generator(request_id, model_name or "default_model"),
                media_type="text/event-stream"
            )
            
    except Exception as e:
        if request_id in response_channels:
            del response_channels[request_id]
        logger.error(f"CLIENTE EXTERNO [ID: {request_id[:8]}]: Error fatal: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

# --- Endpoint de salud ---
@app.get("/health")
async def health_check():
    """Endpoint de salud para verificar el estado del cliente externo."""
    client_id = CONFIG.get("client_id", "unknown")
    client_name = CONFIG.get("client_name", "Cliente Externo")
    
    browser_connected = browser_ws is not None and browser_ws.client_state.name == 'CONNECTED'
    
    return {
        "status": "healthy" if browser_connected else "degraded",
        "client_id": client_id,
        "client_name": client_name,
        "browser_connected": browser_connected,
        "timestamp": datetime.now().isoformat(),
        "last_activity": last_activity_time.isoformat() if last_activity_time else None
    }

# --- Endpoint de información ---
@app.get("/info")
async def client_info():
    """Devuelve información básica del cliente externo."""
    return {
        "client_id": CONFIG.get("client_id", "unknown"),
        "client_name": CONFIG.get("client_name", "Cliente Externo"),
        "version": "1.0.0",
        "models_available": len(MODEL_NAME_TO_ID_MAP),
        "endpoints_configured": len(MODEL_ENDPOINT_MAP)
    }

# --- Endpoint interno para activar captura de IDs ---
@app.post("/internal/start_id_capture")
async def start_id_capture():
    """Activa el modo de captura de IDs enviando comando al script de Tampermonkey."""
    if not browser_ws:
        raise HTTPException(
            status_code=503,
            detail="El cliente de Tampermonkey no está conectado."
        )
    
    try:
        # Enviar comando para activar el modo de captura
        command_message = {
            "command": "activate_id_capture"
        }
        await browser_ws.send_text(json.dumps(command_message))
        
        logger.info("✅ Comando 'activate_id_capture' enviado al script de Tampermonkey.")
        
        return {
            "status": "success",
            "message": "Modo de captura de IDs activado correctamente."
        }
        
    except Exception as e:
        logger.error(f"Error al enviar comando de activación: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Error al activar el modo de captura: {str(e)}"
        )

if __name__ == "__main__":
    port = CONFIG.get("port", 5104)
    client_name = CONFIG.get("client_name", "Cliente Externo")
    
    logger.info(f"🚀 Iniciando Cliente Externo '{client_name}'...")
    logger.info(f"   - Dirección de escucha: http://0.0.0.0:{port}")
    logger.info(f"   - WebSocket local: ws://127.0.0.1:{CONFIG.get('tampermonkey_ws_port', 5105)}/ws")
    
    uvicorn.run(app, host="0.0.0.0", port=port)
