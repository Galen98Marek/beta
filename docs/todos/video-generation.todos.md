# Video Generation System - TODOs

## Completed
- [x] Crear PRD del proyecto
- [x] Diseñar arquitectura del sistema
- [x] Planificar componentes necesarios

## Tasks

### Documentation
- [x] Crear PRD del sistema de videos
- [x] Crear lista de TODOs
- [ ] Documentar API endpoints
- [ ] Crear guía de usuario

### Backend Components
- [x] Crear LMArenaVideoInjector.js (Tampermonkey script)
- [x] Crear video_server.py (servidor principal)
- [x] Crear modules/video_generation.py (módulo de procesamiento)
- [x] Crear video_config.json (configuración)

### Frontend Components
- [x] Crear static/video-chat.html (interfaz principal)
- [x] Crear static/video-chat.css (estilos)
- [x] Crear static/video-chat.js (lógica del cliente)
- [x] Implementar autenticación con API key
- [x] Implementar upload de imágenes

### Integration
- [x] Configurar WebSocket communication
- [x] Implementar extracción de URLs de video (corregido - ahora busca campo 'url' directamente)
- [x] Corregir formato de payload para LMArena (añadidos campos id, mode, modality, userMessageId, modelAMessageId)
- [ ] Probar generación texto-a-video
- [ ] Probar generación imagen-a-video
- [ ] Manejar errores y reintentos

### Testing
- [ ] Test con modelos de video disponibles
- [ ] Test de rate limiting
- [ ] Test de Cloudflare challenges
- [ ] Test de múltiples usuarios concurrentes

### Deployment
- [ ] Configurar auto-start del servidor
- [ ] Documentar instalación del script de Tampermonkey
- [ ] Crear script de instalación
