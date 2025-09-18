# Product Requirements Document: Video Generation System

## Project Overview
Sistema independiente de generación de videos para LMArena que funciona en paralelo al sistema de chat existente, sin requerir configuración manual de session/message IDs.

## Objectives
- Crear un sistema de generación de videos texto-a-video e imagen-a-video
- Funcionar independientemente del sistema de chat existente
- Proveer una interfaz web amigable con autenticación por API key
- Manejar automáticamente la autenticación con Cloudflare Turnstile

## Technical Architecture

### Components
1. **LMArenaVideoInjector.js**: Script de Tampermonkey independiente (puerto 3102)
2. **video_server.py**: Servidor dedicado para videos
3. **modules/video_generation.py**: Módulo de procesamiento de videos
4. **Video Chat Interface**: Aplicación web para usuarios

### Data Flow
```
User → Video Chat Web → video_server.py (:3102) → LMArenaVideoInjector → LMArena API
```

## Features

### Core Features
- [x] Generación de videos desde texto
- [x] Generación de videos desde imágenes
- [x] Autenticación automática con Turnstile
- [x] Manejo de rate limiting y CF challenges
- [x] API compatible con formato OpenAI
- [x] Interfaz web con autenticación por API key

### User Interface
- [x] Pantalla de autenticación con API key
- [x] Chat interface para generación de videos
- [x] Upload de imágenes con drag & drop
- [x] Visualización inline de videos generados
- [x] Historial de conversación
- [x] Descarga de videos
- [x] Indicadores de progreso

### Technical Features
- [x] WebSocket para comunicación en tiempo real
- [x] Auto-detección de modelos de video
- [x] Procesamiento paralelo de múltiples solicitudes
- [x] Sistema de reintentos automático
- [x] Cache de resultados en localStorage

## Video Models Support
- pika-2.2
- hailuo-02-standard
- veo3
- veo2
- kling-2.1-master
- kling-2-master-image-to-video
- seedance-1-lite-image-to-video
- seedance-1-lite-text-to-video

## API Endpoints

### Video Generation
- `POST /v1/videos/generations`: Genera videos desde texto o imagen
- `GET /video-chat`: Sirve la interfaz web
- `WS /ws`: WebSocket para comunicación con Tampermonkey

## Security
- Validación de API keys
- Rate limiting por API key
- Sanitización de inputs
- CORS configurado apropiadamente

## Performance Requirements
- Tiempo de respuesta < 60s para generación de video
- Soporte para múltiples usuarios concurrentes
- Manejo eficiente de uploads de imágenes (hasta 10MB)

## Success Metrics
- Videos generados exitosamente
- Tiempo promedio de generación
- Tasa de errores < 5%
- Uptime del servicio > 99%

## Task Status
- **Done**: Arquitectura diseñada, plan aprobado
- **In Progress**: Implementación de componentes
- **Pending**: Testing e integración final
