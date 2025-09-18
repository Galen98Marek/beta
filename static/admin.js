// Estado global de la aplicación
let authToken = null;
let currentKeys = [];
let availableModels = [];
let isEditMode = false;
let editingKeyId = null;

// Estado de paginación y filtros
let currentPage = 1;
let keysPerPage = 10;
let currentFilters = {
    name: '',
    status: 'all',
    usageMin: null,
    usageMax: null,
    models: ''
};
let paginationData = null;

// Elementos del DOM
const loginPanel = document.getElementById('loginPanel');
const mainPanel = document.getElementById('mainPanel');
const loginForm = document.getElementById('loginForm');
const loginError = document.getElementById('loginError');
const logoutBtn = document.getElementById('logoutBtn');
const createKeyBtn = document.getElementById('createKeyBtn');
const refreshBtn = document.getElementById('refreshBtn');
const keyModal = document.getElementById('keyModal');
const keyForm = document.getElementById('keyForm');
const confirmModal = document.getElementById('confirmModal');
const loadingOverlay = document.getElementById('loadingOverlay');

// Elementos de estadísticas
const totalKeysEl = document.getElementById('totalKeys');
const activeKeysEl = document.getElementById('activeKeys');
const totalUsageEl = document.getElementById('totalUsage');
const totalModelsEl = document.getElementById('totalModels');

// Elementos de operaciones masivas
const bulkModelSelect = document.getElementById('bulkModelSelect');
const bulkAddModelBtn = document.getElementById('bulkAddModelBtn');

// Elementos de filtros
const nameFilter = document.getElementById('nameFilter');
const statusFilter = document.getElementById('statusFilter');
const usageMinFilter = document.getElementById('usageMinFilter');
const usageMaxFilter = document.getElementById('usageMaxFilter');
const modelsFilter = document.getElementById('modelsFilter');
const applyFiltersBtn = document.getElementById('applyFiltersBtn');
const clearFiltersBtn = document.getElementById('clearFiltersBtn');

// Elementos de paginación
const tableInfo = document.getElementById('tableInfo');
const paginationInfo = document.getElementById('paginationInfo');
const prevPageBtn = document.getElementById('prevPageBtn');
const nextPageBtn = document.getElementById('nextPageBtn');
const pageNumbers = document.getElementById('pageNumbers');

// Inicialización
document.addEventListener('DOMContentLoaded', function() {
    // Event listeners básicos
    loginForm.addEventListener('submit', handleLogin);
    logoutBtn.addEventListener('click', handleLogout);
    createKeyBtn.addEventListener('click', () => showKeyModal());
    refreshBtn.addEventListener('click', loadAPIKeys);
    keyForm.addEventListener('submit', handleSaveKey);
    
    // Event listeners de operaciones masivas
    bulkAddModelBtn.addEventListener('click', handleBulkAddModel);
    
    // Event listeners de filtros
    applyFiltersBtn.addEventListener('click', applyFilters);
    clearFiltersBtn.addEventListener('click', clearFilters);
    
    // Filtros en tiempo real para nombre
    nameFilter.addEventListener('input', debounce(() => {
        currentFilters.name = nameFilter.value;
        applyFilters();
    }, 500));
    
    // Event listeners de paginación
    prevPageBtn.addEventListener('click', () => changePage(currentPage - 1));
    nextPageBtn.addEventListener('click', () => changePage(currentPage + 1));
    
    // Modal event listeners
    document.getElementById('closeModal').addEventListener('click', hideKeyModal);
    document.getElementById('cancelBtn').addEventListener('click', hideKeyModal);
    document.getElementById('confirmBtn').addEventListener('click', handleConfirmAction);
    document.getElementById('cancelConfirmBtn').addEventListener('click', hideConfirmModal);
    
    // Cerrar modal al hacer clic fuera
    keyModal.addEventListener('click', (e) => {
        if (e.target === keyModal) hideKeyModal();
    });
    
    confirmModal.addEventListener('click', (e) => {
        if (e.target === confirmModal) hideConfirmModal();
    });
    
    // Verificar si ya hay una sesión activa
    checkAuthStatus();
});

// Funciones de autenticación
async function handleLogin(e) {
    e.preventDefault();
    const password = document.getElementById('password').value;
    
    showLoading(true);
    
    try {
        const response = await fetch('/admin/auth', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify({ password })
        });
        
        const data = await response.json();
        
        if (response.ok) {
            authToken = data.token;
            localStorage.setItem('adminToken', authToken);
            showMainPanel();
        } else {
            showError(data.error || 'Contraseña incorrecta');
        }
    } catch (error) {
        showError('Error de conexión: ' + error.message);
    } finally {
        showLoading(false);
    }
}

function handleLogout() {
    authToken = null;
    localStorage.removeItem('adminToken');
    showLoginPanel();
}

function checkAuthStatus() {
    const token = localStorage.getItem('adminToken');
    if (token) {
        authToken = token;
        showMainPanel();
    }
}

// Funciones de UI
function showLoginPanel() {
    loginPanel.style.display = 'block';
    mainPanel.style.display = 'none';
    document.getElementById('password').value = '';
    hideError();
}

async function showMainPanel() {
    loginPanel.style.display = 'none';
    mainPanel.style.display = 'block';
    mainPanel.classList.add('fadeIn');
    
    // Cargar datos iniciales
    await Promise.all([
        loadAvailableModels(),
        loadAPIKeys()
    ]);
}

function showError(message) {
    loginError.textContent = message;
    loginError.style.display = 'block';
}

function hideError() {
    loginError.style.display = 'none';
}

function showLoading(show) {
    loadingOverlay.style.display = show ? 'flex' : 'none';
}

// Funciones para cargar datos
async function loadAvailableModels() {
    try {
        const response = await fetch('/v1/models', {
            headers: {
                'Authorization': 'Bearer sk-125980-6d64e4f40b6f7fdbb17836aaab8aed1b'
            }
        });
        const data = await response.json();
        
        if (response.ok) {
            availableModels = data.data.map(model => model.id);
            updateStats();
            loadBulkModelSelect();
        }
    } catch (error) {
        console.error('Error cargando modelos:', error);
    }
}

function loadBulkModelSelect() {
    bulkModelSelect.innerHTML = '<option value="">Seleccionar modelo...</option>';
    availableModels.forEach(modelId => {
        const option = document.createElement('option');
        option.value = modelId;
        option.textContent = modelId;
        bulkModelSelect.appendChild(option);
    });
}

async function loadAPIKeys() {
    if (!authToken) return;
    
    showLoading(true);
    
    try {
        const params = new URLSearchParams({
            page: currentPage,
            limit: keysPerPage,
            name_filter: currentFilters.name,
            status_filter: currentFilters.status,
            models_filter: currentFilters.models
        });
        
        if (currentFilters.usageMin !== null) {
            params.append('usage_min', currentFilters.usageMin);
        }
        if (currentFilters.usageMax !== null) {
            params.append('usage_max', currentFilters.usageMax);
        }
        
        const response = await fetch(`/admin/api/keys/paginated?${params}`, {
            headers: {
                'Authorization': `Bearer ${authToken}`
            }
        });
        
        const data = await response.json();
        
        if (response.ok) {
            currentKeys = data.api_keys;
            paginationData = data.pagination;
            updateKeysTable();
            updatePagination();
            updateStats();
        } else if (response.status === 401) {
            handleLogout();
        } else {
            console.error('Error cargando API keys:', data.error);
        }
    } catch (error) {
        console.error('Error de conexión:', error);
    } finally {
        showLoading(false);
    }
}

// Funciones de estadísticas
function updateStats() {
    // Para las estadísticas globales, necesitamos hacer una llamada separada
    loadGlobalStats();
}

async function loadGlobalStats() {
    try {
        const response = await fetch('/admin/api/keys', {
            headers: {
                'Authorization': `Bearer ${authToken}`
            }
        });
        
        const data = await response.json();
        
        if (response.ok) {
            const allKeys = data.api_keys;
            const totalKeys = allKeys.length;
            const activeKeys = allKeys.filter(key => key.enabled).length;
            const totalUsage = allKeys.reduce((sum, key) => sum + (key.usage_count || 0), 0);
            
            totalKeysEl.textContent = totalKeys;
            activeKeysEl.textContent = activeKeys;
            totalUsageEl.textContent = totalUsage;
            totalModelsEl.textContent = availableModels.length;
        }
    } catch (error) {
        console.error('Error cargando estadísticas:', error);
    }
}

// Funciones de tabla
function updateKeysTable() {
    const tbody = document.getElementById('keysTableBody');
    tbody.innerHTML = '';
    
    if (currentKeys.length === 0) {
        const row = document.createElement('tr');
        row.innerHTML = '<td colspan="8" class="text-center text-muted">No se encontraron API keys</td>';
        tbody.appendChild(row);
        updateTableInfo(0, 0, 0);
        return;
    }
    
    currentKeys.forEach(key => {
        const row = createKeyRow(key);
        tbody.appendChild(row);
    });
    
    updateTableInfo(
        paginationData.total_keys,
        (currentPage - 1) * keysPerPage + 1,
        Math.min(currentPage * keysPerPage, paginationData.total_keys)
    );
}

function updateTableInfo(total, start, end) {
    if (total === 0) {
        tableInfo.textContent = 'No hay API keys para mostrar';
    } else {
        tableInfo.textContent = `Mostrando ${start}-${end} de ${total} API Keys`;
    }
}

function createKeyRow(key) {
    const row = document.createElement('tr');
    
    const usageText = key.usage_limit 
        ? `${key.usage_count || 0}/${key.usage_limit}`
        : `${key.usage_count || 0}`;
    
    const statusClass = key.enabled ? 'status-active' : 'status-disabled';
    const statusText = key.enabled ? 'Activa' : 'Deshabilitada';
    
    const modelsDisplay = key.models && key.models.length > 0 
        ? `${key.models.length} modelo(s)`
        : 'Ninguno';
    
    const createdDate = key.created_at 
        ? new Date(key.created_at).toLocaleDateString('es-ES')
        : 'N/A';
    
    const lastUsed = key.last_used 
        ? new Date(key.last_used).toLocaleDateString('es-ES')
        : 'Nunca';
    
    row.innerHTML = `
        <td>
            <div><strong>${key.name || key.id}</strong></div>
            <div class="text-muted" style="font-size: 12px;">${key.id}</div>
        </td>
        <td>${key.description || '-'}</td>
        <td>${usageText}</td>
        <td><span class="status-badge ${statusClass}">${statusText}</span></td>
        <td>${modelsDisplay}</td>
        <td>${createdDate}</td>
        <td>${lastUsed}</td>
        <td>
            <button class="btn btn-sm btn-secondary" onclick="editKey('${key.id}')">Editar</button>
            <button class="btn btn-sm ${key.enabled ? 'btn-warning' : 'btn-success'}" 
                    onclick="toggleKey('${key.id}')">
                ${key.enabled ? 'Deshabilitar' : 'Habilitar'}
            </button>
            <button class="btn btn-sm btn-danger" onclick="deleteKey('${key.id}')">Eliminar</button>
        </td>
    `;
    
    return row;
}

// Funciones de paginación
function updatePagination() {
    if (!paginationData) return;
    
    const { current_page, total_pages, total_keys, has_prev, has_next } = paginationData;
    
    // Actualizar información de paginación
    paginationInfo.textContent = `Página ${current_page} de ${total_pages}`;
    
    // Actualizar botones de navegación
    prevPageBtn.disabled = !has_prev;
    nextPageBtn.disabled = !has_next;
    
    // Generar números de página
    generatePageNumbers(current_page, total_pages);
}

function generatePageNumbers(currentPage, totalPages) {
    pageNumbers.innerHTML = '';
    
    if (totalPages <= 1) return;
    
    const maxVisible = 5;
    let startPage = Math.max(1, currentPage - Math.floor(maxVisible / 2));
    let endPage = Math.min(totalPages, startPage + maxVisible - 1);
    
    // Ajustar si estamos cerca del final
    if (endPage - startPage < maxVisible - 1) {
        startPage = Math.max(1, endPage - maxVisible + 1);
    }
    
    // Botón primera página
    if (startPage > 1) {
        addPageNumber(1);
        if (startPage > 2) {
            addPageEllipsis();
        }
    }
    
    // Páginas visibles
    for (let i = startPage; i <= endPage; i++) {
        addPageNumber(i, i === currentPage);
    }
    
    // Botón última página
    if (endPage < totalPages) {
        if (endPage < totalPages - 1) {
            addPageEllipsis();
        }
        addPageNumber(totalPages);
    }
}

function addPageNumber(pageNum, isActive = false) {
    const pageBtn = document.createElement('button');
    pageBtn.className = `page-number ${isActive ? 'active' : ''}`;
    pageBtn.textContent = pageNum;
    pageBtn.onclick = () => changePage(pageNum);
    pageNumbers.appendChild(pageBtn);
}

function addPageEllipsis() {
    const ellipsis = document.createElement('span');
    ellipsis.className = 'page-number';
    ellipsis.textContent = '...';
    ellipsis.style.cursor = 'default';
    ellipsis.onclick = null;
    pageNumbers.appendChild(ellipsis);
}

function changePage(newPage) {
    if (newPage < 1 || (paginationData && newPage > paginationData.total_pages)) return;
    
    currentPage = newPage;
    loadAPIKeys();
}

// Funciones de filtros
function applyFilters() {
    // Recopilar valores de filtros
    currentFilters.name = nameFilter.value;
    currentFilters.status = statusFilter.value;
    currentFilters.usageMin = usageMinFilter.value ? parseInt(usageMinFilter.value) : null;
    currentFilters.usageMax = usageMaxFilter.value ? parseInt(usageMaxFilter.value) : null;
    currentFilters.models = modelsFilter.value;
    
    // Resetear a la primera página
    currentPage = 1;
    
    // Cargar datos con filtros
    loadAPIKeys();
}

function clearFilters() {
    // Limpiar campos de filtro
    nameFilter.value = '';
    statusFilter.value = 'all';
    usageMinFilter.value = '';
    usageMaxFilter.value = '';
    modelsFilter.value = '';
    
    // Resetear filtros
    currentFilters = {
        name: '',
        status: 'all',
        usageMin: null,
        usageMax: null,
        models: ''
    };
    
    // Resetear a la primera página
    currentPage = 1;
    
    // Cargar datos sin filtros
    loadAPIKeys();
}

// Función debounce para filtros en tiempo real
function debounce(func, wait) {
    let timeout;
    return function executedFunction(...args) {
        const later = () => {
            clearTimeout(timeout);
            func(...args);
        };
        clearTimeout(timeout);
        timeout = setTimeout(later, wait);
    };
}

// Funciones de operaciones masivas
async function handleBulkAddModel() {
    const selectedModel = bulkModelSelect.value;
    
    if (!selectedModel) {
        alert('Por favor selecciona un modelo');
        return;
    }
    
    const message = `¿Estás seguro de que quieres agregar el modelo "${selectedModel}" a todas las API keys que no lo tengan?`;
    
    showConfirmModal(message, async () => {
        showLoading(true);
        
        try {
            const response = await fetch('/admin/api/keys/bulk-add-model', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'Authorization': `Bearer ${authToken}`
                },
                body: JSON.stringify({ model_name: selectedModel })
            });
            
            const data = await response.json();
            
            if (response.ok) {
                alert(`Operación completada:\n- Keys modificadas: ${data.keys_modified}\n- Keys que ya tenían el modelo: ${data.keys_already_had_model}\n- Total de keys: ${data.total_keys}`);
                bulkModelSelect.value = '';
                await loadAPIKeys();
            } else {
                alert('Error: ' + (data.error || 'Error desconocido'));
            }
        } catch (error) {
            alert('Error de conexión: ' + error.message);
        } finally {
            showLoading(false);
        }
    });
}

// Funciones de modal
function showKeyModal(keyData = null) {
    isEditMode = !!keyData;
    editingKeyId = keyData ? keyData.id : null;
    
    document.getElementById('modalTitle').textContent = 
        isEditMode ? 'Editar API Key' : 'Crear Nueva API Key';
    
    // Limpiar formulario
    document.getElementById('keyName').value = keyData ? (keyData.name || '') : '';
    document.getElementById('keyDescription').value = keyData ? (keyData.description || '') : '';
    document.getElementById('keyLimit').value = keyData ? (keyData.usage_limit || '') : '';
    
    // Cargar checkboxes de modelos
    loadModelsCheckboxes(keyData ? keyData.models : []);
    
    keyModal.style.display = 'flex';
}

function hideKeyModal() {
    keyModal.style.display = 'none';
    isEditMode = false;
    editingKeyId = null;
}

function loadModelsCheckboxes(selectedModels = []) {
    const container = document.getElementById('modelsCheckboxes');
    container.innerHTML = '';
    
    // Lista de modelos requeridos según las especificaciones
    const requiredModels = [
        'claude-3-7-sonnet-20250219',
        'gpt-4.1-2025-04-14',
        'claude-sonnet-4-20250514',
        'claude-3-5-sonnet-20241022',
        'gemini-2.5-pro',
        'claude-opus-4-20250514',
        'chatgpt-4o-latest-20250326',
        'kimi-k2-0711-preview',
        'grok-4-0709',
        'deepseek-r1-0528',
        'claude-opus-4-20250514-thinking-16k',
        'gpt-5',
        'gpt-5-mini',
        'gpt-5-chat',
        'gpt-5-nano',
        'auto-claude',
        'claude-opus-4-1-20250805-thinking-16k',
        'claude-opus-4-1-20250805'
    ];
    
    // Usar solo los modelos requeridos (los que realmente se usan)
    const allModels = requiredModels;
    
    allModels.forEach(modelId => {
        const isChecked = selectedModels.includes(modelId);
        const isRequired = requiredModels.includes(modelId);
        
        const checkboxDiv = document.createElement('div');
        checkboxDiv.className = 'model-checkbox';
        
        checkboxDiv.innerHTML = `
            <input type="checkbox" 
                   id="model_${modelId}" 
                   value="${modelId}" 
                   ${isChecked ? 'checked' : ''}
                   ${isRequired ? 'data-required="true"' : ''}>
            <label for="model_${modelId}">
                ${modelId} ${isRequired ? '(Requerido)' : ''}
            </label>
        `;
        
        container.appendChild(checkboxDiv);
    });
}

async function handleSaveKey(e) {
    e.preventDefault();
    
    const formData = new FormData(keyForm);
    const selectedModels = Array.from(document.querySelectorAll('#modelsCheckboxes input:checked'))
        .map(input => input.value);
    
    const keyData = {
        name: formData.get('name') || null,
        description: formData.get('description') || null,
        usage_limit: formData.get('limit') ? parseInt(formData.get('limit')) : null,
        models: selectedModels
    };
    
    showLoading(true);
    
    try {
        const url = isEditMode ? `/admin/api/keys/${editingKeyId}` : '/admin/api/keys';
        const method = isEditMode ? 'PUT' : 'POST';
        
        const response = await fetch(url, {
            method,
            headers: {
                'Content-Type': 'application/json',
                'Authorization': `Bearer ${authToken}`
            },
            body: JSON.stringify(keyData)
        });
        
        const data = await response.json();
        
        if (response.ok) {
            hideKeyModal();
            await loadAPIKeys();
            
            // Mostrar la nueva API key si es creación
            if (!isEditMode && data.api_key) {
                showNewKeyDialog(data.api_key);
            }
        } else {
            alert('Error: ' + (data.error || 'Error desconocido'));
        }
    } catch (error) {
        alert('Error de conexión: ' + error.message);
    } finally {
        showLoading(false);
    }
}

function showNewKeyDialog(apiKey) {
    alert(`¡API Key creada exitosamente!\n\nTu nueva API Key es:\n${apiKey}\n\n⚠️ IMPORTANTE: Guarda esta clave en un lugar seguro. No podrás verla de nuevo.`);
}

// Funciones de confirmación
function showConfirmModal(message, onConfirm) {
    document.getElementById('confirmMessage').textContent = message;
    document.getElementById('confirmBtn').onclick = () => {
        hideConfirmModal();
        onConfirm();
    };
    confirmModal.style.display = 'flex';
}

function hideConfirmModal() {
    confirmModal.style.display = 'none';
}

let pendingConfirmAction = null;

function handleConfirmAction() {
    if (pendingConfirmAction) {
        pendingConfirmAction();
        pendingConfirmAction = null;
    }
    hideConfirmModal();
}

// Funciones de acciones de API Keys
function editKey(keyId) {
    const key = currentKeys.find(k => k.id === keyId);
    if (key) {
        showKeyModal(key);
    }
}

function toggleKey(keyId) {
    const key = currentKeys.find(k => k.id === keyId);
    if (!key) return;
    
    const action = key.enabled ? 'deshabilitar' : 'habilitar';
    const message = `¿Estás seguro de que quieres ${action} esta API Key?`;
    
    showConfirmModal(message, async () => {
        showLoading(true);
        
        try {
            const response = await fetch(`/admin/api/keys/${keyId}/toggle`, {
                method: 'POST',
                headers: {
                    'Authorization': `Bearer ${authToken}`
                }
            });
            
            if (response.ok) {
                await loadAPIKeys();
            } else {
                const data = await response.json();
                alert('Error: ' + (data.error || 'Error desconocido'));
            }
        } catch (error) {
            alert('Error de conexión: ' + error.message);
        } finally {
            showLoading(false);
        }
    });
}

function deleteKey(keyId) {
    const key = currentKeys.find(k => k.id === keyId);
    if (!key) return;
    
    const message = `¿Estás seguro de que quieres eliminar la API Key "${key.name || key.id}"? Esta acción no se puede deshacer.`;
    
    showConfirmModal(message, async () => {
        showLoading(true);
        
        try {
            const response = await fetch(`/admin/api/keys/${keyId}`, {
                method: 'DELETE',
                headers: {
                    'Authorization': `Bearer ${authToken}`
                }
            });
            
            if (response.ok) {
                await loadAPIKeys();
            } else {
                const data = await response.json();
                alert('Error: ' + (data.error || 'Error desconocido'));
            }
        } catch (error) {
            alert('Error de conexión: ' + error.message);
        } finally {
            showLoading(false);
        }
    });
}

// Utilidades
function formatDate(dateString) {
    if (!dateString) return 'N/A';
    return new Date(dateString).toLocaleDateString('es-ES');
}

function copyToClipboard(text) {
    navigator.clipboard.writeText(text).then(() => {
        // Mostrar feedback visual
        const tooltip = document.createElement('div');
        tooltip.textContent = 'Copiado!';
        tooltip.style.cssText = `
            position: fixed;
            top: 50%;
            left: 50%;
            transform: translate(-50%, -50%);
            background: #28a745;
            color: white;
            padding: 10px 20px;
            border-radius: 5px;
            z-index: 10000;
        `;
        document.body.appendChild(tooltip);
        
        setTimeout(() => {
            document.body.removeChild(tooltip);
        }, 2000);
    }).catch(err => {
        console.error('Error copiando al portapapeles:', err);
    });
}

// Manejo de errores global
window.addEventListener('unhandledrejection', function(event) {
    console.error('Error no manejado:', event.reason);
    showLoading(false);
});
