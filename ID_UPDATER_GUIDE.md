# Guía del ID Updater Actualizado

## 🆕 Nueva Funcionalidad

El `id_updater.py` ahora incluye la capacidad de agregar automáticamente los session_id y message_id capturados a modelos específicos en el archivo `model_endpoint_map.json`, manteniendo el orden numérico y ahorrando tiempo de copiar y pegar manual.

## 🔄 Flujo de Trabajo Actualizado

### Paso 1: Ejecutar el ID Updater
```bash
python id_updater.py
```

### Paso 2: Seleccionar Modo (Existente)
- **a**: DirectChat
- **b**: Battle (con selección de asistente A o B)

### Paso 3: Capturar IDs (Existente)
- El script se conecta al navegador
- Realiza acciones en LMArena para capturar los IDs
- Los IDs se guardan automáticamente en `config.jsonc`

### Paso 4: 🆕 Opción de Agregar a Modelo
Después de capturar los IDs, aparece una nueva opción:

```
🔄 ¿Desea agregar estos IDs a un modelo en 'model_endpoint_map.json'?
Ingrese 'y' para sí, cualquier otra tecla para no:
```

### Paso 5: 🆕 Selección de Modelo
Si seleccionas 'y', aparece un menú con los modelos disponibles:

```
📋 MODELOS DISPONIBLES PARA AGREGAR IDs
============================================================
  1. o3-2025-04-16
     - IDs actuales: 3
     - Modo: direct_chat

  2. claude-3-7-sonnet-20250219
     - IDs actuales: 1
     - Modo: direct_chat

  3. gpt-4.1-2025-04-14
     - IDs actuales: 1
     - Modo: direct_chat

  4. claude-sonnet-4-20250514
     - IDs actuales: 1
     - Modo: direct_chat

  5. claude-opus-4-20250514
     - IDs actuales: 1
     - Modo: direct_chat

  6. Crear nuevo modelo
  0. Saltar - Solo guardar en config.jsonc
============================================================
```

### Paso 6: 🆕 Agregar IDs Automáticamente

#### Opción A: Agregar a Modelo Existente
- Selecciona el número del modelo (1-5)
- Los IDs se agregan automáticamente en el siguiente índice disponible
- Ejemplo: Si el modelo tiene `session_id0` y `message_id0`, los nuevos se agregan como `session_id1` y `message_id1`

#### Opción B: Crear Nuevo Modelo
- Selecciona "6. Crear nuevo modelo"
- Ingresa el nombre del nuevo modelo
- Se crea automáticamente con `session_id0`, `message_id0`, `mode` y `current_index: 0`

#### Opción C: Solo Guardar en Config
- Selecciona "0" para omitir la adición al mapeo de modelos
- Los IDs solo se guardan en `config.jsonc` (comportamiento original)

## 🎯 Beneficios de la Nueva Funcionalidad

### ⏱️ Ahorro de Tiempo
- **Antes**: Capturar IDs → Abrir `model_endpoint_map.json` → Copiar/pegar manualmente → Actualizar índices
- **Ahora**: Capturar IDs → Seleccionar modelo → ¡Listo!

### 🔢 Orden Numérico Automático
- El sistema detecta automáticamente el próximo índice disponible
- Mantiene la secuencia: 0, 1, 2, 3, 4...
- No hay riesgo de sobrescribir IDs existentes

### 🛡️ Sistema de Rotación Intacto
- Los nuevos IDs se integran perfectamente con el sistema de rotación existente
- El `current_index` se mantiene sin cambios
- La rotación automática funcionará con todos los IDs disponibles

### 🎛️ Flexibilidad
- Puedes agregar a modelos existentes o crear nuevos
- Opción de omitir si solo quieres actualizar `config.jsonc`
- Mantiene compatibilidad total con el flujo de trabajo anterior

## 📊 Ejemplo de Resultado

### Antes de Agregar IDs:
```json
"claude-sonnet-4-20250514": {
  "session_id0": "91ff8fcd-da6a-442f-9fef-6b0fc2819b87",
  "message_id0": "d07b4e98-d0c4-4eec-9a32-1b940dbaa0d5",
  "mode": "direct_chat",
  "current_index": 0
}
```

### Después de Agregar IDs:
```json
"claude-sonnet-4-20250514": {
  "session_id0": "91ff8fcd-da6a-442f-9fef-6b0fc2819b87",
  "message_id0": "d07b4e98-d0c4-4eec-9a32-1b940dbaa0d5",
  "session_id1": "nuevo-session-id-capturado",
  "message_id1": "nuevo-message-id-capturado",
  "mode": "direct_chat",
  "current_index": 0
}
```

## 🔧 Funciones Técnicas Agregadas

- `load_model_endpoint_map()`: Carga el mapeo de modelos
- `save_model_endpoint_map()`: Guarda cambios en el archivo JSON
- `get_next_index_for_model()`: Detecta el próximo índice disponible
- `add_ids_to_model()`: Agrega IDs a un modelo específico
- `show_model_selection_menu()`: Interfaz de selección de modelos

## 🚀 Compatibilidad

- ✅ Totalmente compatible con el sistema existente
- ✅ No afecta el comportamiento del `api_server.py`
- ✅ Mantiene el sistema de rotación automática
- ✅ Funciona con modelos existentes y nuevos
- ✅ Opción de usar el flujo de trabajo original (omitir adición a modelos)

## 💡 Casos de Uso

1. **Agregar Respaldo a Modelo Existente**: Cuando un modelo tiene rate limiting frecuente
2. **Configurar Nuevo Modelo**: Cuando agregas un nuevo modelo al sistema
3. **Actualización Rápida**: Cuando necesitas actualizar múltiples modelos rápidamente
4. **Mantenimiento**: Cuando rotas IDs que ya no funcionan

¡La nueva funcionalidad hace que gestionar los IDs de sesión sea mucho más eficiente y menos propenso a errores!
