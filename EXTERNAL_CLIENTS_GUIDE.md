# Guía de Clientes Externos - LMArena Bridge

## Descripción General

El sistema de clientes externos permite distribuir la carga de requests de LMArena entre múltiples computadoras remotas, solucionando eficazmente el problema de rate limiting al usar diferentes sesiones de navegador desde ubicaciones distintas.

## Cómo Funciona

1. **Detección de Rate Limit**: El servidor principal detecta cuando un modelo específico alcanza su rate limit
2. **Búsqueda de Cliente**: Busca un cliente externo disponible que soporte ese modelo
3. **Redirección Automática**: Redirige la request al cliente externo durante 30 minutos
4. **Recuperación**: Después de 30 minutos, vuelve a usar el servidor principal

## Componentes del Sistema

### 1. Servidor Principal
- **Archivo**: `api_server.py`
- **Puerto**: 5102 (por defecto)
- **Función**: Coordina todas las requests y decide cuándo usar clientes externos

### 2. Cliente Externo
- **Archivo**: `external_client_server.py`
- **Puerto**: 5104+ (configurable)
- **Función**: Ejecuta requests de LMArena en computadoras remotas

### 3. Configuración de Cliente
- **Archivo**: `external_client_config.jsonc`
- **Función**: Configura ID, API key, puertos del cliente

### 4. Actualizador de IDs
- **Archivo**: `external_id_updater.py`
- **Función**: Captura session_id y message_id automáticamente

### 5. Script de Tampermonkey
- **Archivo**: `TampermonkeyScript/ExternalLMArenaClient.js`
- **Función**: Se conecta con LMArena en el navegador del cliente

## Instalación y Configuración

### Paso 1: Preparar la Computadora Remota

1. **Copiar archivos necesarios** a la computadora remota:
   ```
   external_client_server.py
   external_client_config.jsonc
   external_id_updater.py
   TampermonkeyScript/ExternalLMArenaClient.js
   ```

2. **Instalar dependencias**:
   ```bash
   pip install fastapi uvicorn websockets aiohttp
   ```

### Paso 2: Configurar el Cliente Externo

1. **Editar `external_client_config.jsonc`**:
   ```json
   {
     "client_id": "client-remote-1",
     "client_name": "Cliente Remoto 1",
     "api_key": "sk-ext-123456789abcdef",
     "server_port": 5104,
     "websocket_port": 5105,
     "id_updater_port": 5106,
     "enabled": true
   }
   ```

2. **Generar API key única** para cada cliente

### Paso 3: Configurar el Script de Tampermonkey

1. **Instalar Tampermonkey** en el navegador de la computadora remota
2. **Instalar el script** `ExternalLMArenaClient.js`
3. **Ajustar puertos** en el script si es necesario:
   ```javascript
   const SERVER_URL = "ws://localhost:5105/ws";
   const ID_UPDATER_PORT = 5106;
   ```

### Paso 4: Capturar IDs de Sesión

1. **Ejecutar el servidor del cliente**:
   ```bash
   python external_client_server.py
   ```

2. **Ejecutar el capturador de IDs**:
   ```bash
   python external_id_updater.py
   ```

3. **Abrir LMArena** en el navegador con el script instalado
4. **Hacer una acción "Retry"** para capturar los IDs automáticamente

### Paso 5: Registrar en el Servidor Principal

1. **Agregar cliente a `external_clients.json`**:
   ```json
   {
     "external_clients": {
       "client-remote-1": {
         "name": "Cliente Remoto 1",
         "url": "http://192.168.1.100:5104",
         "api_key": "sk-ext-123456789abcdef",
         "enabled": true,
         "priority": 1,
         "models": ["claude-3-5-sonnet-20241022", "gpt-4o"],
         "last_used": null,
         "last_health_check": null,
         "browser_connected": false
       }
     }
   }
   ```

2. **Reiniciar el servidor principal** para cargar la nueva configuración

## Panel de Administración

### Gestión de Clientes Externos

El panel de administración incluye una sección para gestionar clientes externos:

- **Ver lista de clientes** con estado de salud
- **Agregar nuevos clientes** mediante formulario
- **Editar configuración** de clientes existentes
- **Habilitar/deshabilitar** clientes
- **Verificar conectividad** en tiempo real

### Acceso al Panel

1. Ir a `http://localhost:5102/admin`
2. Usar la contraseña configurada en `config.jsonc`
3. Navegar a la sección "Clientes Externos"

## Configuración de Red

### Puertos Necesarios

| Componente | Puerto por Defecto | Descripción |
|------------|-------------------|-------------|
| Servidor Principal | 5102 | API principal |
| Cliente Externo | 5104+ | HTTP API del cliente |
| WebSocket Cliente | 5105+ | Conexión con Tampermonkey |
| ID Updater | 5106+ | Captura de IDs |

### Configuración de Firewall

Asegúrate de que estos puertos estén abiertos:
- **En el servidor principal**: Puerto 5102 (entrada)
- **En el cliente externo**: Puertos 5104-5106 (entrada desde servidor principal)

## Troubleshooting

### Problemas Comunes

#### 1. Cliente No Se Conecta
```
Error: Cliente externo no disponible (503)
```
**Solución**:
- Verificar que `external_client_server.py` esté ejecutándose
- Comprobar conectividad de red entre servidor y cliente
- Revisar configuración de firewall

#### 2. Script de Tampermonkey No Funciona
```
Error: El navegador se desconectó durante la operación
```
**Solución**:
- Verificar que el script esté instalado y habilitado
- Comprobar que los puertos en el script coincidan con la configuración
- Refrescar la página de LMArena

#### 3. IDs No Se Capturan
```
Error: La información de sesión está vacía
```
**Solución**:
- Ejecutar `external_id_updater.py`
- Hacer una acción "Retry" en LMArena
- Verificar que el script intercepte las requests

#### 4. API Key Inválida
```
Error: Token de autorización requerido
```
**Solución**:
- Verificar que la API key en `external_client_config.jsonc` coincida con `external_clients.json`
- Regenerar API key si es necesario

### Logs de Diagnóstico

#### Servidor Principal
```bash
tail -f api_server.log
```
Buscar líneas con `EXTERNAL CLIENT:` para ver la actividad de clientes externos.

#### Cliente Externo
```bash
tail -f external_client.log
```
Verificar conexiones WebSocket y procesamiento de requests.

## Monitoreo

### Health Checks Automáticos

El sistema ejecuta verificaciones de salud cada 5 minutos:
- **Estado de conexión** del navegador en cada cliente
- **Conectividad de red** entre servidor y clientes
- **Funcionalidad del WebSocket** de Tampermonkey

### Métricas de Rendimiento

El sistema rastrea:
- **Tiempo de respuesta** de cada cliente
- **Tasa de éxito** de requests procesadas
- **Utilización** de cada cliente (requests por minuto)
- **Tiempos de cooldown** por rate limiting

## Escalabilidad

### Agregar Más Clientes

Para escalar el sistema:

1. **Preparar nueva computadora** con los archivos necesarios
2. **Generar ID único** para el cliente
3. **Configurar puertos diferentes** para evitar conflictos
4. **Registrar en el servidor principal**

### Distribución por Modelos

Puedes configurar clientes específicos para modelos específicos:

```json
{
  "client-claude": {
    "models": ["claude-3-5-sonnet-20241022", "claude-3-opus-20240229"]
  },
  "client-gpt": {
    "models": ["gpt-4o", "gpt-4-turbo"]
  }
}
```

## Seguridad

### Autenticación

- **API Keys únicas** para cada cliente externo
- **Headers de autorización** en todas las comunicaciones
- **Validación de origen** para requests WebSocket

### Red

- **Comunicación interna** entre componentes del mismo cliente
- **HTTPS recomendado** para conexiones entre servidor y clientes remotos
- **VPN sugerida** para conexiones a través de internet público

## Beneficios del Sistema

✅ **Elimina Rate Limiting**: Usa diferentes IPs y sesiones de navegador
✅ **Escalable**: Agrega tantos clientes como necesites
✅ **Automático**: Funciona sin intervención manual
✅ **Inteligente**: Sistema de prioridades y health checks
✅ **Transparente**: El usuario final no nota la diferencia
✅ **Confiable**: Fallback automático al servidor principal

---

Este sistema proporciona una solución robusta y escalable para superar las limitaciones de rate limiting de LMArena, permitiendo un uso óptimo del servicio distribuido entre múltiples ubicaciones.
