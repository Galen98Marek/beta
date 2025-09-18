# external_id_updater.py
# ID updater para clientes externos de LMArena Bridge
# Versión adaptada del id_updater.py original para usar en computadoras remotas

import http.server
import socketserver
import json
import re
import threading
import os
import requests

# --- Configuración ---
HOST = "127.0.0.1"
CONFIG_PATH = 'external_client_config.jsonc'
MODEL_ENDPOINT_MAP_PATH = 'model_endpoint_map.json'

def load_config():
    """Carga la configuración del cliente externo."""
    try:
        with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
            content = f.read()
            # Eliminar comentarios
            content = re.sub(r'//.*', '', content)
            content = re.sub(r'/\*.*?\*/', '', content, flags=re.DOTALL)
            return json.loads(content)
    except Exception as e:
        print(f"❌ Error al leer '{CONFIG_PATH}': {e}")
        return None

def read_config():
    """Lee y analiza el archivo de configuración del cliente externo."""
    if not os.path.exists(CONFIG_PATH):
        print(f"❌ Error: El archivo de configuración '{CONFIG_PATH}' no existe.")
        return None
    return load_config()

def save_config_value(key, value):
    """Actualiza un valor en el archivo de configuración del cliente externo."""
    try:
        with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
            content = f.read()

        # Expresión regular para reemplazar el valor
        pattern = re.compile(rf'("{key}"\s*:\s*")[^"]*(")')
        new_content, count = pattern.subn(rf'\g<1>{value}\g<2>', content, 1)

        if count == 0:
            print(f"🤔 Advertencia: No se encontró la clave '{key}' en '{CONFIG_PATH}'.")
            return False

        with open(CONFIG_PATH, 'w', encoding='utf-8') as f:
            f.write(new_content)
        return True
    except Exception as e:
        print(f"❌ Error al actualizar '{CONFIG_PATH}': {e}")
        return False

def load_model_endpoint_map():
    """Carga el mapeo de modelos desde model_endpoint_map.json."""
    try:
        with open(MODEL_ENDPOINT_MAP_PATH, 'r', encoding='utf-8') as f:
            content = f.read()
            if not content.strip():
                return {}
            return json.loads(content)
    except FileNotFoundError:
        print(f"⚠️ No se encontró el archivo '{MODEL_ENDPOINT_MAP_PATH}'. Se creará uno nuevo.")
        return {}
    except json.JSONDecodeError as e:
        print(f"❌ Error al analizar '{MODEL_ENDPOINT_MAP_PATH}': {e}")
        return {}

def save_model_endpoint_map(model_map):
    """Guarda el mapeo de modelos en model_endpoint_map.json."""
    try:
        with open(MODEL_ENDPOINT_MAP_PATH, 'w', encoding='utf-8') as f:
            json.dump(model_map, f, indent=2, ensure_ascii=False)
        return True
    except Exception as e:
        print(f"❌ Error al guardar '{MODEL_ENDPOINT_MAP_PATH}': {e}")
        return False

def get_next_index_for_model(model_data):
    """Obtiene el próximo índice disponible para un modelo."""
    index = 0
    while f"session_id{index}" in model_data and f"message_id{index}" in model_data:
        index += 1
    return index

def add_ids_to_model(model_map, model_name, session_id, message_id, mode):
    """Agrega nuevos session_id y message_id a un modelo específico."""
    if model_name not in model_map:
        # Crear nueva entrada para el modelo
        model_map[model_name] = {
            "session_id0": session_id,
            "message_id0": message_id,
            "mode": mode,
            "current_index": 0
        }
        return 0
    else:
        # Agregar a modelo existente
        model_data = model_map[model_name]
        next_index = get_next_index_for_model(model_data)
        
        model_data[f"session_id{next_index}"] = session_id
        model_data[f"message_id{next_index}"] = message_id
        
        # Actualizar el modo si es necesario
        model_data["mode"] = mode
        
        return next_index

def show_model_selection_menu(model_map):
    """Muestra el menú de selección de modelos y retorna la selección del usuario."""
    if not model_map:
        print("\n📋 No hay modelos disponibles en el mapeo.")
        return None
    
    print("\n" + "=" * 60)
    print("📋 MODELOS DISPONIBLES PARA AGREGAR IDs")
    print("=" * 60)
    
    models = list(model_map.keys())
    for i, model_name in enumerate(models, 1):
        model_data = model_map[model_name]
        current_count = get_next_index_for_model(model_data)
        mode = model_data.get("mode", "N/A")
        print(f"  {i}. {model_name}")
        print(f"     - IDs actuales: {current_count}")
        print(f"     - Modo: {mode}")
        print()
    
    print(f"  {len(models) + 1}. Crear nuevo modelo")
    print(f"  0. Saltar - Solo guardar en configuración global")
    print("=" * 60)
    
    while True:
        try:
            choice = input(f"Seleccione una opción [0-{len(models) + 1}]: ").strip()
            if choice == "0":
                return None
            elif choice == str(len(models) + 1):
                return "new_model"
            else:
                choice_num = int(choice)
                if 1 <= choice_num <= len(models):
                    return models[choice_num - 1]
                else:
                    print(f"❌ Opción inválida. Ingrese un número entre 0 y {len(models) + 1}.")
        except ValueError:
            print("❌ Por favor, ingrese un número válido.")

def save_session_ids(session_id, message_id):
    """Actualiza los nuevos IDs de sesión en la configuración del cliente externo."""
    print(f"\n📝 Intentando escribir los IDs en '{CONFIG_PATH}'...")
    res1 = save_config_value("session_id", session_id)
    res2 = save_config_value("message_id", message_id)
    
    if res1 and res2:
        print(f"✅ IDs actualizados correctamente en la configuración del cliente externo.")
        print(f"   - session_id: {session_id}")
        print(f"   - message_id: {message_id}")
        
        # Opción para agregar a model_endpoint_map.json
        print(f"\n🔄 ¿Desea agregar estos IDs a un modelo en '{MODEL_ENDPOINT_MAP_PATH}'?")
        add_to_model = input("Ingrese 'y' para sí, cualquier otra tecla para no: ").lower().strip()
        
        if add_to_model == 'y':
            model_map = load_model_endpoint_map()
            selected_model = show_model_selection_menu(model_map)
            
            if selected_model is None:
                print("⏭️ Se omitió la adición al mapeo de modelos.")
                return
            
            # Usar modo direct_chat como predeterminado para clientes externos
            current_mode = "direct_chat"
            
            if selected_model == "new_model":
                # Crear nuevo modelo
                new_model_name = input("\n📝 Ingrese el nombre del nuevo modelo: ").strip()
                if not new_model_name:
                    print("❌ Nombre de modelo inválido. Se omitió la adición.")
                    return
                
                index = add_ids_to_model(model_map, new_model_name, session_id, message_id, current_mode)
                
                if save_model_endpoint_map(model_map):
                    print(f"✅ Nuevo modelo '{new_model_name}' creado con IDs en índice {index}.")
                else:
                    print("❌ Error al guardar el mapeo de modelos.")
            else:
                # Agregar a modelo existente
                index = add_ids_to_model(model_map, selected_model, session_id, message_id, current_mode)
                
                if save_model_endpoint_map(model_map):
                    print(f"✅ IDs agregados al modelo '{selected_model}' en índice {index}.")
                    print(f"   - Total de IDs para este modelo: {index + 1}")
                else:
                    print("❌ Error al guardar el mapeo de modelos.")
        else:
            print("⏭️ Se omitió la adición al mapeo de modelos.")
    else:
        print(f"❌ Error al actualizar los IDs. Por favor, revise los mensajes anteriores.")

class RequestHandler(http.server.SimpleHTTPRequestHandler):
    def _send_cors_headers(self):
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')

    def do_OPTIONS(self):
        self.send_response(204)
        self._send_cors_headers()
        self.end_headers()

    def do_POST(self):
        if self.path == '/update':
            try:
                content_length = int(self.headers['Content-Length'])
                post_data = self.rfile.read(content_length)
                data = json.loads(post_data)

                session_id = data.get('sessionId')
                message_id = data.get('messageId')

                if session_id and message_id:
                    print("\n" + "=" * 50)
                    print("🎉 ¡IDs capturados exitosamente desde el navegador!")
                    print(f"  - Session ID: {session_id}")
                    print(f"  - Message ID: {message_id}")
                    print("=" * 50)

                    save_session_ids(session_id, message_id)

                    self.send_response(200)
                    self._send_cors_headers()
                    self.end_headers()
                    self.wfile.write(b'{"status": "success"}')

                    print("\nTarea completada, el servidor se cerrará automáticamente en 1 segundo.")
                    threading.Thread(target=self.server.shutdown).start()

                else:
                    self.send_response(400, "Bad Request")
                    self._send_cors_headers()
                    self.end_headers()
                    self.wfile.write(b'{"error": "Missing sessionId or messageId"}')
            except Exception as e:
                self.send_response(500, "Internal Server Error")
                self._send_cors_headers()
                self.end_headers()
                self.wfile.write(f'{{"error": "Internal server error: {e}"}}'.encode('utf-8'))
        else:
            self.send_response(404, "Not Found")
            self._send_cors_headers()
            self.end_headers()

    def log_message(self, format, *args):
        return

def run_server():
    config = load_config()
    if not config:
        print("❌ No se pudo cargar la configuración. Saliendo...")
        return
    
    PORT = config.get("id_updater_port", 5106)
    
    with socketserver.TCPServer((HOST, PORT), RequestHandler) as httpd:
        print("\n" + "="*50)
        print("  🚀 Monitor de actualización de IDs del Cliente Externo iniciado")
        print(f"  - Dirección de escucha: http://{HOST}:{PORT}")
        print("  - Realice acciones en la página de LMArena para activar la captura de IDs.")
        print("  - Una vez capturados, este script se cerrará automáticamente.")
        print("="*50)
        httpd.serve_forever()

def notify_external_client_server():
    """Notifica al servidor del cliente externo que el proceso de captura ha comenzado."""
    config = load_config()
    if not config:
        return False
    
    # Usar el puerto del servidor del cliente externo (no el de Tampermonkey WebSocket)
    client_port = config.get("port", 5104)
    client_server_url = f"http://127.0.0.1:{client_port}/internal/start_id_capture"
    
    try:
        print(f"📡 Notificando al servidor del cliente externo en {client_server_url}...")
        response = requests.post(client_server_url, timeout=5)
        
        if response.status_code == 200:
            print("✅ El servidor del cliente externo ha sido notificado exitosamente.")
            print("   - Modo de captura de IDs activado en el navegador.")
            return True
        else:
            print(f"⚠️ Fallo al notificar al servidor del cliente externo, código: {response.status_code}")
            print(f"   - Respuesta: {response.text}")
            return False
            
    except requests.ConnectionError:
        print("❌ No se pudo conectar al servidor del cliente externo.")
        print("   - Asegúrese de que external_client_server.py esté ejecutándose.")
        print("   - Verifique que el puerto coincida con external_client_config.jsonc")
        return False
    except requests.Timeout:
        print("❌ Timeout al conectar con el servidor del cliente externo.")
        return False
    except Exception as e:
        print(f"❌ Error desconocido al notificar al servidor del cliente externo: {e}")
        return False

if __name__ == "__main__":
    config = read_config()
    if not config:
        exit(1)

    print("\n" + "="*60)
    print("🔧 EXTERNAL CLIENT ID UPDATER - Cliente Externo LMArena Bridge")
    print("="*60)
    
    client_name = config.get("client_name", "Cliente Externo")
    client_id = config.get("client_id", "unknown")
    
    print(f"Cliente: {client_name} (ID: {client_id})")
    print("Modo: DirectChat (predeterminado para clientes externos)")
    print("\nEste script capturará IDs de sesión desde el navegador del cliente externo.")

    # Notificar al servidor del cliente externo (CRÍTICO)
    print("\n" + "=" * 50)
    print("PASO 1: Activando modo de captura en el navegador...")
    print("=" * 50)
    
    if notify_external_client_server():
        print("\n" + "=" * 50)
        print("PASO 2: Iniciando servidor de captura de IDs...")
        print("=" * 50)
        print("INSTRUCCIONES:")
        print("1. Vaya a la página de LMArena en su navegador")
        print("2. Realice una acción de 'Retry' o envíe un mensaje")
        print("3. Los IDs se capturarán automáticamente")
        print("4. Este script se cerrará una vez capturados los IDs")
        print()
        
        # Iniciar el servidor de captura
        run_server()
        print("El servidor se ha cerrado.")
    else:
        print("\n" + "=" * 60)
        print("❌ ERROR CRÍTICO")
        print("=" * 60)
        print("No se pudo activar el modo de captura en el navegador.")
        print("El proceso de captura de IDs se ha cancelado.")
        print()
        print("SOLUCIÓN:")
        print("1. Verifique que external_client_server.py esté ejecutándose")
        print("2. Asegúrese de que el script de Tampermonkey esté activo")
        print("3. Verifique la configuración de puertos en external_client_config.jsonc")
        print()
        exit(1)
