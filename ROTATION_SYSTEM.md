# Sistema de Rotación Automática de Session/Message IDs

## Descripción General

El sistema de rotación automática permite configurar múltiples session IDs y message IDs para cada modelo en el LMArena Bridge. Cuando se detecta un error 429 (Too Many Requests), el sistema automáticamente rota al siguiente conjunto de IDs disponible para ese modelo específico.

## Características Principales

### ✅ Rotación Automática
- Detecta errores 429 + "Too Many Requests" en el contenido del chat
- Rota automáticamente al siguiente session/message ID disponible
- Solo afecta al modelo que recibió el error

### ✅ Rotación Circular
- Cuando llega al último ID disponible, vuelve al primero
- Garantiza uso continuo de todos los IDs configurados

### ✅ Persistencia
- Los índices de rotación se guardan en `model_endpoint_map.json`
- Sobrevive reinicios del servidor

### ✅ Compatibilidad
- Mantiene compatibilidad con el formato anterior
- Migración gradual modelo por modelo

## Formato del Archivo `model_endpoint_map.json`

### Formato Nuevo (Con Rotación)
```json
{
  "claude-3-7-sonnet-20250219": {
    "session_id0": "primera-session-id",
    "message_id0": "primera-message-id",
    "session_id1": "segunda-session-id",
    "message_id1": "segunda-message-id",
    "session_id2": "tercera-session-id",
    "message_id2": "tercera-message-id",
    "mode": "direct_chat",
    "current_index": 0
  }
}
```

### Formato Legacy (Sin Rotación)
```json
{
  "gpt-4": {
    "session_id": "session-id-unica",
    "message_id": "message-id-unica",
    "mode": "direct_chat"
  }
}
```

## Cómo Configurar Múltiples Session IDs

### 1. Formato de Numeración
Los session IDs y message IDs deben seguir el patrón:
- `session_id0`, `session_id1`, `session_id2`, etc.
- `message_id0`, `message_id1`, `message_id2`, etc.

### 2. Ejemplo de Configuración
```json
{
  "claude-3-7-sonnet-20250219": {
    "session_id0": "88d8287f-6ee7-4b70-b922-c5a4200df6de",
    "message_id0": "bd1665d8-b722-43f2-b923-2042b0a08a2c",
    "session_id1": "nueva-session-backup-1",
    "message_id1": "nueva-message-backup-1",
    "session_id2": "nueva-session-backup-2",
    "message_id2": "nueva-message-backup-2",
    "mode": "direct_chat",
    "current_index": 0
  }
}
```

### 3. Campo `current_index`
- **Obligatorio** para habilitar la rotación
- Indica cuál session/message ID está actualmente en uso
- Se actualiza automáticamente cuando ocurre una rotación

## Funcionamiento del Sistema

### Flujo Normal
1. **Request llega** → Sistema usa `session_id{current_index}` y `message_id{current_index}`
2. **Respuesta exitosa** → No hay cambios
3. **Próximo request** → Usa los mismos IDs

### Flujo con Error 429
1. **Request llega** → Sistema usa `session_id0` y `message_id0`
2. **Error 429 detectado** → Sistema incrementa `current_index` a 1
3. **Próximo request** → Usa `session_id1` y `message_id1`
4. **Si hay más errores** → Continúa rotando: 2, 3, etc.
5. **Rotación circular** → Después del último índice, vuelve a 0

## Detección de Errores

### Patrón de Error
El sistema detecta errores que contengan:
- `"429"` Y `"Too Many Requests"`

### Ejemplo de Error Detectado
```
[Error del Puente LMArena]: Respuesta de red no válida. Estado: 429. Contenido: {"error":"Too Many Requests","modelId":"c5a11495-081a-4dc6-8d9a-64a4fd6f7bbc"}
```

### Ubicaciones de Detección
- En el buffer completo del stream
- En el contenido de texto individual
- Tanto en respuestas streaming como no-streaming

## Logs del Sistema

### Rotación Exitosa
```
ROTACIÓN: Modelo 'claude-3-7-sonnet-20250219' rotado de índice 0 a 1
  - Session ID anterior: ...df6de
  - Session ID nuevo: ...ckup1
```

### Error de Rotación
```
ROTACIÓN: Modelo 'claude-3-7-sonnet-20250219' solo tiene 1 session ID(s), no se puede rotar.
```

### Detección de Error 429
```
PROCESADOR [ID: 12345678]: Se detectó error 429 de rate limiting en el contenido.
```

## Migración desde Formato Anterior

### Paso 1: Identificar Modelos a Migrar
Buscar modelos en formato legacy:
```json
{
  "modelo": {
    "session_id": "valor",
    "message_id": "valor"
  }
}
```

### Paso 2: Convertir al Formato Nuevo
```json
{
  "modelo": {
    "session_id0": "valor-original",
    "message_id0": "valor-original",
    "session_id1": "nuevo-backup-1",
    "message_id1": "nuevo-backup-1",
    "mode": "direct_chat",
    "current_index": 0
  }
}
```

### Paso 3: Agregar Más Backups
Continuar agregando `session_id2`, `session_id3`, etc. según sea necesario.

## Ventajas del Sistema

### 🔄 Recuperación Automática
- No requiere intervención manual cuando hay rate limiting
- Continúa funcionando automáticamente con IDs alternativos

### 📊 Distribución de Carga
- Rota entre múltiples session IDs
- Reduce la probabilidad de rate limiting

### 🛡️ Resistencia a Errores
- Si un session ID está bloqueado, usa automáticamente otro
- Maximiza la disponibilidad del servicio

### 📈 Escalabilidad
- Fácil agregar más session IDs según sea necesario
- No hay límite en el número de IDs de backup

## Consideraciones Importantes

### ⚠️ Compatibilidad
- Los modelos sin `current_index` siguen funcionando en modo legacy
- La migración es opcional y gradual

### ⚠️ Configuración
- Todos los índices deben ser consecutivos (0, 1, 2, 3...)
- No puede haber gaps en la numeración

### ⚠️ Persistencia
- Los cambios en `current_index` se guardan inmediatamente
- El archivo se actualiza cada vez que hay una rotación

## Troubleshooting

### Problema: "No se puede rotar"
**Causa**: Solo hay un session ID configurado o falta `current_index`
**Solución**: Agregar más session IDs o añadir el campo `current_index`

### Problema: "Índice no tiene session_id/message_id válidos"
**Causa**: Hay gaps en la numeración o IDs faltantes
**Solución**: Verificar que todos los índices sean consecutivos

### Problema: Rotación no funciona
**Causa**: El modelo no está en el formato nuevo
**Solución**: Migrar el modelo al formato con `current_index`

## Ejemplo Completo

```json
{
  "claude-3-7-sonnet-20250219": {
    "session_id0": "88d8287f-6ee7-4b70-b922-c5a4200df6de",
    "message_id0": "bd1665d8-b722-43f2-b923-2042b0a08a2c",
    "session_id1": "backup-session-id-1",
    "message_id1": "backup-message-id-1",
    "session_id2": "backup-session-id-2", 
    "message_id2": "backup-message-id-2",
    "mode": "direct_chat",
    "current_index": 0
  },
  "gpt-4": {
    "session_id": "legacy-session-id",
    "message_id": "legacy-message-id",
    "mode": "direct_chat"
  }
}
```

En este ejemplo:
- `claude-3-7-sonnet-20250219` tiene rotación habilitada con 3 session IDs
- `gpt-4` sigue usando el formato legacy sin rotación
- Ambos funcionan correctamente en el mismo sistema
