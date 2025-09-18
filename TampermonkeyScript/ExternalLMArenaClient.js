// ==UserScript==
// @name         LMArena API Bridge - Cliente Externo
// @namespace    http://tampermonkey.net/
// @version      1.0
// @description  Cliente externo para LMArena Bridge - corre en computadoras remotas para distribuir la carga
// @author       Lianues
// @match        https://lmarena.ai/*
// @match        https://*.lmarena.ai/*
// @icon         https://www.google.com/s2/favicons?sz=64&domain=lmarena.ai
// @grant        none
// @run-at       document-end
// ==/UserScript==

(function () {
    'use strict';

    // --- Configuración ---
    // IMPORTANTE: El WebSocket usa el puerto principal del servidor (port), no el tampermonkey_ws_port
    const SERVER_URL = "ws://localhost:5104/ws"; // Puerto del WebSocket del cliente externo
    const ID_UPDATER_PORT = 5106; // Puerto del external_id_updater.py
    
    let socket;
    let isCaptureModeActive = false; // Interruptor del modo de captura de ID

    // --- Lógica principal ---
    function connect() {
        console.log(`[Cliente Externo] Conectando al servidor del cliente externo: ${SERVER_URL}...`);
        socket = new WebSocket(SERVER_URL);

        socket.onopen = () => {
            console.log("[Cliente Externo] ✅ Conexión WebSocket con el servidor del cliente externo establecida.");
            document.title = "🔗 " + document.title;
        };

        socket.onmessage = async (event) => {
            try {
                const message = JSON.parse(event.data);

                // Verifica si es un comando y no una solicitud de chat estándar
                if (message.command) {
                    console.log(`[Cliente Externo] ⬇️ Comando recibido: ${message.command}`);
                    if (message.command === 'refresh' || message.command === 'reconnect') {
                        console.log(`[Cliente Externo] Comando '${message.command}' recibido, recargando la página...`);
                        location.reload();
                    } else if (message.command === 'activate_id_capture') {
                        console.log("[Cliente Externo] ✅ Modo de captura de ID activado. Por favor, realiza una acción de 'Retry' en la página.");
                        isCaptureModeActive = true;
                        // Dar señal visual al usuario
                        document.title = "🎯 " + document.title;
                    }
                    return;
                }

                const { request_id, payload } = message;

                if (!request_id || !payload) {
                    console.error("[Cliente Externo] Mensaje inválido recibido del servidor:", message);
                    return;
                }
                
                console.log(`[Cliente Externo] ⬇️ Solicitud de chat recibida ${request_id.substring(0, 8)}. Preparando operación fetch.`);
                await executeFetchAndStreamBack(request_id, payload);

            } catch (error) {
                console.error("[Cliente Externo] Error al procesar el mensaje del servidor:", error);
            }
        };

        socket.onclose = () => {
            console.warn("[Cliente Externo] 🔌 Conexión con el servidor del cliente externo cerrada. Reintentando en 5 segundos...");
            if (document.title.startsWith("🔗 ")) {
                document.title = document.title.substring(2);
            }
            setTimeout(connect, 5000);
        };

        socket.onerror = (error) => {
            console.error("[Cliente Externo] ❌ Error en WebSocket:", error);
            socket.close(); // Esto activará la lógica de reconexión en onclose
        };
    }

    async function executeFetchAndStreamBack(requestId, payload) {
        console.log(`[Cliente Externo] Dominio actual de operación: ${window.location.hostname}`);
        const { is_image_request, message_templates, target_model_id, session_id, message_id } = payload;

        // --- Usar la información de sesión proporcionada por el servidor del cliente externo ---
        if (!session_id || !message_id) {
            const errorMsg = "La información de sesión recibida del servidor (session_id o message_id) está vacía. Por favor, ejecuta external_id_updater.py para configurarla.";
            console.error(`[Cliente Externo] ${errorMsg}`);
            sendToServer(requestId, { error: errorMsg });
            sendToServer(requestId, "[DONE]");
            return;
        }

        // La URL es la misma para chat e imagen
        const apiUrl = `/api/stream/retry-evaluation-session-message/${session_id}/messages/${message_id}`;
        const httpMethod = 'PUT';
        
        console.log(`[Cliente Externo] Usando endpoint de API: ${apiUrl}`);
        
        const newMessages = [];
        let lastMsgIdInChain = null;

        if (!message_templates || message_templates.length === 0) {
            const errorMsg = "La lista de mensajes recibida del servidor está vacía.";
            console.error(`[Cliente Externo] ${errorMsg}`);
            sendToServer(requestId, { error: errorMsg });
            sendToServer(requestId, "[DONE]");
            return;
        }

        // Construir mensajes para LMArena
        for (let i = 0; i < message_templates.length; i++) {
            const template = message_templates[i];
            const currentMsgId = crypto.randomUUID();
            const parentIds = lastMsgIdInChain ? [lastMsgIdInChain] : [];
            
            // Si es una solicitud de imagen, el estado siempre es 'success'
            // De lo contrario, solo el último mensaje es 'pending'
            const status = is_image_request ? 'success' : ((i === message_templates.length - 1) ? 'pending' : 'success');

            newMessages.push({
                role: template.role,
                content: template.content,
                id: currentMsgId,
                evaluationId: null,
                evaluationSessionId: session_id,
                parentMessageIds: parentIds,
                experimental_attachments: template.attachments || [],
                failureReason: null,
                metadata: null,
                participantPosition: template.participantPosition || "a",
                createdAt: new Date().toISOString(),
                updatedAt: new Date().toISOString(),
                status: status,
            });
            lastMsgIdInChain = currentMsgId;
        }

        const body = {
            messages: newMessages,
            modelId: target_model_id,
        };

        console.log("[Cliente Externo] Carga final lista para enviar a LMArena API:", JSON.stringify(body, null, 2));

        // Establece una bandera para que el interceptor de fetch sepa que la solicitud fue iniciada por el script
        window.isExternalClientRequest = true;
        try {
            const response = await fetch(apiUrl, {
                method: httpMethod,
                headers: {
                    'Content-Type': 'text/plain;charset=UTF-8', // LMArena usa text/plain
                    'Accept': '*/*',
                },
                body: JSON.stringify(body),
                credentials: 'include' // Debe incluir cookies
            });

            if (!response.ok || !response.body) {
                const errorBody = await response.text();
                throw new Error(`Respuesta de red no válida. Estado: ${response.status}. Contenido: ${errorBody}`);
            }

            const reader = response.body.getReader();
            const decoder = new TextDecoder();

            while (true) {
                const { value, done } = await reader.read();
                if (done) {
                    console.log(`[Cliente Externo] ✅ El stream de la solicitud ${requestId.substring(0, 8)} ha finalizado.`);
                    sendToServer(requestId, "[DONE]");
                    break;
                }
                const chunk = decoder.decode(value);
                // Reenvía el chunk de datos original al servidor del cliente externo
                sendToServer(requestId, chunk);
            }

        } catch (error) {
            console.error(`[Cliente Externo] ❌ Error al ejecutar fetch para la solicitud ${requestId.substring(0, 8)}:`, error);
            
            // Detectar errores 429 de rate limiting y enviar señal especial
            const errorMessage = error.message || '';
            if (errorMessage.includes('429') && errorMessage.includes('Too Many Requests')) {
                console.log(`[Cliente Externo] 🔄 Error 429 detectado, enviando señal de rate limiting al servidor...`);
                
                // Extraer el nombre del modelo del payload si está disponible
                let modelName = null;
                if (payload && payload.target_model_id) {
                    modelName = payload.target_model_id;
                }
                
                // Enviar señal especial de rate limiting
                sendToServer(requestId, { 
                    rate_limit_detected: true, 
                    model_id: modelName,
                    original_error: errorMessage 
                });
            } else {
                // Para otros errores, enviar como antes
                sendToServer(requestId, { error: errorMessage });
            }
            
            sendToServer(requestId, "[DONE]");
        } finally {
            // Al finalizar la solicitud, restablecer la bandera
            window.isExternalClientRequest = false;
        }
    }

    function sendToServer(requestId, data) {
        if (socket && socket.readyState === WebSocket.OPEN) {
            const message = {
                request_id: requestId,
                data: data
            };
            socket.send(JSON.stringify(message));
        } else {
            console.error("[Cliente Externo] No se puede enviar datos, la conexión WebSocket no está abierta.");
        }
    }

    // --- Interceptor de solicitudes de red ---
    const originalFetch = window.fetch;
    window.fetch = function(...args) {
        const urlArg = args[0];
        let urlString = '';

        // Asegurarse de manejar siempre la URL como string
        if (urlArg instanceof Request) {
            urlString = urlArg.url;
        } else if (urlArg instanceof URL) {
            urlString = urlArg.href;
        } else if (typeof urlArg === 'string') {
            urlString = urlArg;
        }

        // Solo si la URL es válida, realizar el match
        if (urlString) {
            const match = urlString.match(/\/api\/stream\/retry-evaluation-session-message\/([a-f0-9-]+)\/messages\/([a-f0-9-]+)/);

            // Solo actualizar ID si la solicitud no fue iniciada por el propio puente y el modo de captura está activo
            if (match && !window.isExternalClientRequest && isCaptureModeActive) {
                const sessionId = match[1];
                const messageId = match[2];
                console.log(`[Cliente Externo Interceptor] 🎯 ¡ID capturado en modo activo! Enviando...`);

                // Desactivar modo de captura, asegurando que solo se envíe una vez
                isCaptureModeActive = false;
                if (document.title.startsWith("🎯 ")) {
                    document.title = document.title.substring(2);
                }

                // Enviar los IDs capturados al external_id_updater.py local
                fetch(`http://127.0.0.1:${ID_UPDATER_PORT}/update`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ sessionId, messageId })
                })
                .then(response => {
                    if (!response.ok) throw new Error(`El servidor respondió con estado: ${response.status}`);
                    console.log(`[Cliente Externo] ✅ ID enviado correctamente al external_id_updater.py. El modo de captura se ha desactivado automáticamente.`);
                })
                .catch(err => {
                    console.error('[Cliente Externo] Error al enviar la actualización de ID:', err.message);
                    // Aunque falle el envío, el modo de captura ya está desactivado y no se reintentará.
                });
            }
        }

        // Llamar a la función fetch original para no afectar el funcionamiento de la página
        return originalFetch.apply(this, args);
    };

    // --- Enviar el código fuente de la página después de cargar (opcional) ---
    function sendPageSourceAfterLoad() {
        const sendSource = async () => {
            console.log("[Cliente Externo] Página cargada. Enviando el código fuente para actualizar la lista de modelos...");
            try {
                const htmlContent = document.documentElement.outerHTML;
                // Intentar enviar al servidor del cliente externo (puerto puede variar)
                await fetch('http://localhost:5104/update_models', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'text/html; charset=utf-8'
                    },
                    body: htmlContent
                });
                 console.log("[Cliente Externo] El código fuente de la página se ha enviado correctamente.");
            } catch (e) {
                console.log("[Cliente Externo] No se pudo enviar el código fuente (esto es normal si el endpoint no existe):", e.message);
            }
        };

        if (document.readyState === 'complete') {
            sendSource();
        } else {
            window.addEventListener('load', sendSource);
        }
    }

    // --- Inicializar conexión ---
    console.log("========================================");
    console.log("  LMArena Cliente Externo v1.0 está en ejecución.");
    console.log(`  - Función de chat conectada a ${SERVER_URL}`);
    console.log(`  - El capturador de ID enviará a http://localhost:${ID_UPDATER_PORT}`);
    console.log("========================================");
    
    sendPageSourceAfterLoad(); // Enviar el código fuente de la página (opcional)
    connect(); // Establecer la conexión WebSocket

})();
