// Dynamic JavaScript Engine for Parking Management Dashboard

// API Configuration
const API_BASE = `${window.location.protocol}//${window.location.host}/api`;
const WS_BASE = `${window.location.protocol === 'https:' ? 'wss:' : 'ws:'}//${window.location.host}/ws`;

let socket = null;
let reconnectInterval = 1000;
let overrideTab = 'entry';

// Load Elements
document.addEventListener('DOMContentLoaded', () => {
    // Read API Key from session storage or use default
    const savedKey = sessionStorage.getItem('parking_api_key');
    if (savedKey) {
        document.getElementById('api-key-input').value = savedKey;
    }

    // Save key changes
    document.getElementById('api-key-input').addEventListener('input', (e) => {
        sessionStorage.setItem('parking_api_key', e.target.value.trim());
    });

    // Setup forms & buttons
    document.getElementById('pricing-config-form').addEventListener('submit', savePricingRules);
    document.getElementById('trigger-manual-entry-btn').addEventListener('click', executeManualEntry);
    document.getElementById('trigger-manual-exit-btn').addEventListener('click', executeManualExit);
    document.getElementById('search-logs-btn').addEventListener('click', queryAuditLogs);
    document.getElementById('refresh-revenue-btn').addEventListener('click', loadRevenue);

    // Session Handling
    const token = sessionStorage.getItem('parking_jwt_token');
    if (token) {
        document.getElementById('login-modal').classList.add('hidden');
        initDashboardSession();
    } else {
        document.getElementById('login-modal').classList.remove('hidden');
    }
    
    // Connect WebSockets
    connectWS();
});

// Helper for API headers
function getHeaders() {
    const key = document.getElementById('api-key-input').value.trim();
    const token = sessionStorage.getItem('parking_jwt_token');
    const headers = {
        'Content-Type': 'application/json',
        'X-API-Key': key
    };
    if (token) {
        headers['Authorization'] = `Bearer ${token}`;
    }
    return headers;
}

// 1. WebSocket Handler
function connectWS() {
    const wsStatusText = document.getElementById('ws-status-text');
    const wsStatusDiv = document.getElementById('ws-status');

    wsStatusText.textContent = "Connecting...";
    wsStatusDiv.className = "ws-status offline";

    socket = new WebSocket(WS_BASE);

    socket.onopen = () => {
        console.log("[WS] WebSocket connected.");
        wsStatusText.textContent = "Live Stream";
        wsStatusDiv.className = "ws-status online";
        reconnectInterval = 1000; // Reset backoff
    };

    socket.onmessage = (event) => {
        const data = JSON.parse(event.data);
        console.log("[WS MESSAGE]", data);

        // Push to the Live Alerts sidebar
        if (data.event && data.event !== "connected") {
            pushAlertToSidebar(data.event, data.message || "", data.timestamp || new Date().toISOString());
        }

        // Process different WS events
        if (data.event === "connected") {
            // Success handshake
            if (data.message) showToastNotification(data.message, "info");
        } else if (data.event === "slots_updated") {
            loadSlots();
            if (data.message) showToastNotification(data.message, "info");
        } else if (data.event === "pricing_updated") {
            loadPricing();
            loadPricingForecast();
            if (data.message) showToastNotification(data.message, "info");
        } else if (data.event === "entry") {
            logCameraActivity("ENTRY", `Vehicle ${data.plate} entered. Assigned to ${data.slot}.`, data.timestamp);
            loadSlots();
            loadRevenue();
            loadPricingForecast();
            const msg = data.message || `Vehicle ${data.plate} entered slot ${data.slot}.`;
            showToastNotification(msg, "success");
        } else if (data.event === "exit") {
            logCameraActivity("EXIT", `Vehicle ${data.plate} exited. Fee: ₹${data.fee.toFixed(2)} (${data.duration} mins).`, data.timestamp);
            loadSlots();
            loadRevenue();
            loadPricingForecast();
            if (data.fee > 0) {
                openPaymentModal(data.transaction_id, data.plate, data.slot, data.duration, data.fee);
            } else {
                const msg = data.message || `Vehicle ${data.plate} exited (Grace Period). Fee: ₹0.00`;
                showToastNotification(msg, "primary");
            }
        } else if (data.event === "payment") {
            logCameraActivity("PAYMENT", `Payment of ₹${data.amount.toFixed(2)} for ${data.plate} confirmed.`, data.timestamp);
            loadRevenue();
            const msg = data.message || `Payment of ₹${data.amount.toFixed(2)} for ${data.plate} confirmed.`;
            showToastNotification(msg, "success");
            const modalTxnId = document.getElementById('payment-transaction-id').value;
            if (modalTxnId == data.transaction_id) {
                closePaymentModal();
            }
        } else if (data.event === "lot_full_attempt") {
            logCameraActivity("ALERT", `ACCESS DENIED: Lot Full. Vehicle ${data.plate} kept out.`, data.timestamp);
            loadSlots();
            const msg = data.message || `ACCESS DENIED: Lot Full. Vehicle ${data.plate} kept out.`;
            showToastNotification(msg, "danger");
        }
    };

    socket.onclose = () => {
        console.log("[WS] WebSocket connection closed. Reconnecting...");
        wsStatusText.textContent = "Offline";
        wsStatusDiv.className = "ws-status offline";
        
        // Exponential backoff reconnect
        setTimeout(() => {
            reconnectInterval = Math.min(reconnectInterval * 1.5, 30000);
            connectWS();
        }, reconnectInterval);
    };

    socket.onerror = (err) => {
        console.error("[WS ERROR]", err);
        socket.close();
    };
}

// Simple Toast Notification function
function showToastNotification(message, type = 'info') {
    const toast = document.createElement('div');
    toast.className = `toast-notification ${type}`;
    
    let icon = '<i class="fa-solid fa-bell"></i>';
    if (type === 'success') icon = '<i class="fa-solid fa-circle-check"></i>';
    else if (type === 'danger') icon = '<i class="fa-solid fa-triangle-exclamation"></i>';
    else if (type === 'primary') icon = '<i class="fa-solid fa-right-from-bracket"></i>';
    
    toast.innerHTML = `${icon} &nbsp; <span>${message}</span>`;
    document.body.appendChild(toast);
    
    setTimeout(() => {
        toast.remove();
    }, 4000);
}

// 2. Fetch and Render Parking Slots
async function loadSlots() {
    try {
        const response = await fetch(`${API_BASE}/slots`, {
            headers: getHeaders()
        });
        if (!response.ok) throw new Error("Could not fetch slots data");
        const slots = await response.json();
        renderParkingSlots(slots);
    } catch (err) {
        console.error("[SLOTS ERROR]", err);
    }
}

function renderParkingSlots(slots) {
    const grid = document.getElementById('parking-slots-grid');
    const totalCountSpan = document.getElementById('total-slots-count');
    const availableCountSpan = document.getElementById('available-slots-count');
    const occupiedCountSpan = document.getElementById('occupied-slots-count');
    const rateText = document.getElementById('occupancy-percentage');
    const progressBar = document.getElementById('occupancy-progress-bar');
    const lotFullAlert = document.getElementById('lot-full-alert');

    const role = sessionStorage.getItem('parking_user_role') || 'viewer';
    const isAdmin = (role === 'admin');

    grid.innerHTML = '';

    if (slots.length === 0) {
        grid.innerHTML = '<div class="grid-skeleton">No slots configured. Click "Configure Slot" to add one!</div>';
        totalCountSpan.textContent = 0;
        availableCountSpan.textContent = 0;
        occupiedCountSpan.textContent = 0;
        rateText.textContent = '0%';
        progressBar.style.width = '0%';
        lotFullAlert.classList.add('hidden');
        return;
    }

    let occupiedCount = 0;
    let availableCount = 0;

    slots.forEach(slot => {
        const isOccupied = slot.status === 'OCCUPIED';
        const isReserved = slot.status === 'RESERVED';
        if (isOccupied) occupiedCount++;
        else availableCount++;

        const card = document.createElement('div');
        card.className = `parking-slot-card ${slot.status.toLowerCase()}`;
        
        let actionsHtml = '';
        if (isAdmin) {
            actionsHtml = `
                <div class="slot-actions">
                    <button class="slot-action-btn" title="Rename Slot" onclick="openEditModal(${slot.id}, '${slot.name}')">
                        <i class="fa-solid fa-pen-to-square"></i>
                    </button>
                    <button class="slot-action-btn delete-btn" title="Delete Slot" onclick="deleteSlot(${slot.id})">
                        <i class="fa-solid fa-trash"></i>
                    </button>
                </div>
            `;
        }

        let badgeHtml = '';
        if (slot.slot_type && slot.slot_type !== 'STANDARD') {
            badgeHtml = `<span class="slot-badge ${slot.slot_type.toLowerCase()}">${slot.slot_type}</span>`;
        }

        let detailsHtml = '<span style="color:transparent; font-size: 0.8rem;">-</span>';
        if (isOccupied) {
            detailsHtml = `<span class="slot-vehicle-plate">${slot.current_vehicle_id}</span>`;
        } else if (isReserved) {
            detailsHtml = `
                <span class="slot-vehicle-plate" style="background:rgba(255,145,0,0.1); border-color:var(--warning); color:var(--warning)">
                    ${slot.current_vehicle_id}
                </span>
                <span class="reservation-countdown" data-expiry="${slot.reservation_expiry}">--:--</span>
            `;
        }

        card.innerHTML = `
            ${actionsHtml}
            <div class="slot-title">${slot.name}</div>
            <div class="slot-status-lbl">${slot.status}</div>
            ${detailsHtml}
            ${badgeHtml}
        `;

        grid.appendChild(card);
    });

    // Update stats counters
    totalCountSpan.textContent = slots.length;
    availableCountSpan.textContent = availableCount;
    occupiedCountSpan.textContent = occupiedCount;

    // Calculate Occupancy percentage
    const occupancyRate = slots.length > 0 ? Math.round((occupiedCount / slots.length) * 100) : 0;
    rateText.textContent = `${occupancyRate}%`;
    progressBar.style.width = `${occupancyRate}%`;

    // Toggle "Lot Full" Warning
    if (availableCount === 0) {
        lotFullAlert.classList.remove('hidden');
    } else {
        lotFullAlert.classList.add('hidden');
    }
}

// 3. Dynamic Pricing Functions
async function loadPricing() {
    try {
        const response = await fetch(`${API_BASE}/pricing`);
        if (!response.ok) throw new Error("Could not fetch pricing rules");
        const pricing = await response.json();
        
        document.getElementById('price-rule-name').value = pricing.rule_name;
        document.getElementById('price-free-mins').value = pricing.free_minutes;
        document.getElementById('price-base-fee').value = pricing.base_fee;
        document.getElementById('price-base-hours').value = pricing.base_hours;
        document.getElementById('price-hourly-rate').value = pricing.hourly_rate;
    } catch (err) {
        console.error("[PRICING ERROR]", err);
    }
}

async function savePricingRules(e) {
    e.preventDefault();
    const btn = document.getElementById('save-pricing-btn');
    const originalText = btn.innerHTML;
    btn.innerHTML = `<i class="fa-solid fa-spinner fa-spin"></i> Saving...`;
    btn.disabled = true;

    const payload = {
        rule_name: document.getElementById('price-rule-name').value,
        free_minutes: parseInt(document.getElementById('price-free-mins').value),
        base_fee: parseFloat(document.getElementById('price-base-fee').value),
        base_hours: parseInt(document.getElementById('price-base-hours').value),
        hourly_rate: parseFloat(document.getElementById('price-hourly-rate').value)
    };

    try {
        const response = await fetch(`${API_BASE}/pricing`, {
            method: 'POST',
            headers: getHeaders(),
            body: JSON.stringify(payload)
        });
        const data = await response.json();
        
        if (!response.ok) throw new Error(data.detail || "Failed to update pricing rules.");
        
        // Visual confirmation
        btn.innerHTML = `<i class="fa-solid fa-circle-check"></i> Applied!`;
        setTimeout(() => {
            btn.innerHTML = originalText;
            btn.disabled = false;
        }, 1500);

    } catch (err) {
        alert(err.message);
        btn.innerHTML = originalText;
        btn.disabled = false;
    }
}

// 4. Revenue Aggregation load
async function loadRevenue() {
    try {
        const response = await fetch(`${API_BASE}/revenue`, {
            headers: getHeaders()
        });
        if (!response.ok) throw new Error("Failed to load revenue data");
        const data = await response.json();
        
        document.getElementById('revenue-daily').textContent = `₹${data.daily.toFixed(2)}`;
        document.getElementById('revenue-weekly').textContent = `₹${data.weekly.toFixed(2)}`;
        document.getElementById('revenue-monthly').textContent = `₹${data.monthly.toFixed(2)}`;
        document.getElementById('revenue-lifetime').textContent = `₹${data.lifetime.toFixed(2)}`;
    } catch (err) {
        console.error("[REVENUE ERROR]", err);
    }
}

// 5. Manual Overrides Implementation
function switchOverrideTab(tab) {
    overrideTab = tab;
    document.getElementById('tab-entry-btn').className = tab === 'entry' ? 'tab-btn active' : 'tab-btn';
    document.getElementById('tab-exit-btn').className = tab === 'exit' ? 'tab-btn active' : 'tab-btn';
    document.getElementById('tab-reserve-btn').className = tab === 'reserve' ? 'tab-btn active' : 'tab-btn';

    document.getElementById('manual-entry-form').classList.add('hidden');
    document.getElementById('manual-exit-form').classList.add('hidden');
    document.getElementById('manual-reserve-form').classList.add('hidden');

    if (tab === 'entry') {
        document.getElementById('manual-entry-form').classList.remove('hidden');
    } else if (tab === 'exit') {
        document.getElementById('manual-exit-form').classList.remove('hidden');
    } else if (tab === 'reserve') {
        document.getElementById('manual-reserve-form').classList.remove('hidden');
    }
}

async function executeManualEntry() {
    const input = document.getElementById('manual-entry-plate');
    const plate = input.value.trim().toUpperCase();
    const vehicleTypeSelect = document.getElementById('manual-entry-type');
    const vehicleType = vehicleTypeSelect ? vehicleTypeSelect.value : "STANDARD";
    const statusDiv = document.getElementById('override-status-message');

    if (!plate) {
        showFeedback(statusDiv, "Please enter a valid license plate number.", "error");
        return;
    }

    try {
        const response = await fetch(`${API_BASE}/entry`, {
            method: 'POST',
            headers: getHeaders(),
            body: JSON.stringify({ license_plate: plate, vehicle_type: vehicleType })
        });
        const data = await response.json();
        
        if (!response.ok) {
            throw new Error(data.detail || "Entry request failed");
        }

        if (data.status === "already_parked") {
            showFeedback(statusDiv, `Vehicle ${plate} is already inside at ${data.slot_name}.`, "error");
        } else {
            showFeedback(statusDiv, `SUCCESS: Slot '${data.slot_name}' assigned to ${plate}. Barrier OPEN.`, "success");
            input.value = '';
        }
    } catch (err) {
        showFeedback(statusDiv, err.message, "error");
    }
}

async function executeManualExit() {
    const input = document.getElementById('manual-exit-plate');
    const plate = input.value.trim().toUpperCase();
    const statusDiv = document.getElementById('override-status-message');

    if (!plate) {
        showFeedback(statusDiv, "Please enter a valid license plate number.", "error");
        return;
    }

    try {
        const response = await fetch(`${API_BASE}/exit`, {
            method: 'POST',
            headers: getHeaders(),
            body: JSON.stringify({ license_plate: plate })
        });
        const data = await response.json();
        
        if (!response.ok) {
            throw new Error(data.detail || "Exit request failed");
        }

        showFeedback(statusDiv, `SUCCESS: Exited ${data.slot_name}. Fee: ₹${data.amount_paid.toFixed(2)} (${data.duration_minutes}m). Barrier OPEN.`, "success");
        input.value = '';
    } catch (err) {
        showFeedback(statusDiv, err.message, "error");
    }
}

function showFeedback(el, msg, type) {
    el.textContent = msg;
    el.className = `feedback-msg ${type}`;
    el.classList.remove('hidden');
    setTimeout(() => {
        el.classList.add('hidden');
    }, 6000);
}

// 6. Real-Time Camera Log Stream Overlay
function logCameraActivity(action, details, timestamp) {
    const list = document.getElementById('live-camera-events');
    
    // Remove placeholder
    const placeholder = list.querySelector('.event-placeholder');
    if (placeholder) placeholder.remove();

    const formattedTime = new Date(timestamp).toLocaleTimeString();
    const item = document.createElement('li');
    item.className = `event-item ${action.toLowerCase()}`;

    // SVG icon indicators
    let icon = '<i class="fa-solid fa-info-circle"></i>';
    if (action === 'ENTRY') icon = '<i class="fa-solid fa-right-to-bracket" style="color: var(--success)"></i>';
    else if (action === 'EXIT') icon = '<i class="fa-solid fa-right-from-bracket" style="color: var(--primary)"></i>';
    else if (action === 'ALERT') icon = '<i class="fa-solid fa-triangle-exclamation" style="color: var(--danger)"></i>';
    else if (action === 'PAYMENT') icon = '<i class="fa-solid fa-receipt" style="color: var(--success)"></i>';

    item.innerHTML = `
        <div class="event-text">${icon} &nbsp; <strong>[${action}]</strong> ${details}</div>
        <div class="event-time">${formattedTime}</div>
    `;

    list.insertBefore(item, list.firstChild);

    // Keep log max to 30 entries
    while (list.children.length > 30) {
        list.removeChild(list.lastChild);
    }
}

// 7. Slots CRUD dynamic configure
function toggleAddSlotPanel() {
    const panel = document.getElementById('add-slot-panel');
    panel.classList.toggle('hidden');
}

async function submitNewSlot() {
    const input = document.getElementById('new-slot-name-input');
    const name = input.value.trim();
    const typeSelect = document.getElementById('new-slot-type-select');
    const slotType = typeSelect ? typeSelect.value : "STANDARD";
    if (!name) return;

    try {
        const response = await fetch(`${API_BASE}/slots`, {
            method: 'POST',
            headers: getHeaders(),
            body: JSON.stringify({ name: name, slot_type: slotType })
        });
        const data = await response.json();
        
        if (!response.ok) throw new Error(data.detail || "Could not add slot");
        
        input.value = '';
        toggleAddSlotPanel();
    } catch (err) {
        alert(err.message);
    }
}

async function deleteSlot(slotId) {
    if (!confirm("Are you sure you want to delete this parking slot?")) return;

    try {
        const response = await fetch(`${API_BASE}/slots/${slotId}`, {
            method: 'DELETE',
            headers: getHeaders()
        });
        const data = await response.json();
        if (!response.ok) throw new Error(data.detail || "Could not delete slot");
    } catch (err) {
        alert(err.message);
    }
}

// Edit Slot renaming modal
function openEditModal(slotId, currentName) {
    document.getElementById('edit-slot-id').value = slotId;
    document.getElementById('edit-slot-name').value = currentName;
    document.getElementById('edit-slot-modal').classList.remove('hidden');
}

function closeEditModal() {
    document.getElementById('edit-slot-modal').classList.add('hidden');
}

async function saveSlotRename() {
    const slotId = document.getElementById('edit-slot-id').value;
    const newName = document.getElementById('edit-slot-name').value.trim();

    if (!newName) return;

    try {
        const response = await fetch(`${API_BASE}/slots/${slotId}`, {
            method: 'PUT',
            headers: getHeaders(),
            body: JSON.stringify({ name: newName })
        });
        const data = await response.json();
        if (!response.ok) throw new Error(data.detail || "Could not rename slot");
        closeEditModal();
    } catch (err) {
        alert(err.message);
    }
}

// 8. Historical Search Audit Logs
async function queryAuditLogs() {
    const searchPlate = document.getElementById('search-plate').value.trim();
    const searchSlot = document.getElementById('search-slot').value.trim();
    const startDate = document.getElementById('search-start-date').value;
    const endDate = document.getElementById('search-end-date').value;
    const tableBody = document.getElementById('audit-results-body');

    tableBody.innerHTML = '<tr><td colspan="7" class="table-placeholder"><i class="fa-solid fa-spinner fa-spin"></i> Running security search queries...</td></tr>';

    let url = `${API_BASE}/logs?`;
    const params = [];
    if (searchPlate) params.push(`license_plate=${encodeURIComponent(searchPlate)}`);
    if (searchSlot) params.push(`slot_name=${encodeURIComponent(searchSlot)}`);
    if (startDate) params.push(`start_date=${encodeURIComponent(new Date(startDate).toISOString())}`);
    if (endDate) params.push(`end_date=${encodeURIComponent(new Date(endDate).toISOString())}`);
    
    url += params.join('&');

    try {
        const response = await fetch(url, {
            headers: getHeaders()
        });
        if (!response.ok) throw new Error("Search request failed.");
        const logs = await response.json();

        tableBody.innerHTML = '';

        if (logs.length === 0) {
            tableBody.innerHTML = '<tr><td colspan="7" class="table-placeholder">No matching audit logs found.</td></tr>';
            return;
        }

        logs.forEach(log => {
            const row = document.createElement('tr');
            
            const entryTime = new Date(log.entry_time).toLocaleString();
            const exitTime = log.exit_time ? new Date(log.exit_time).toLocaleString() : '-';
            const duration = log.duration_minutes !== null ? `${log.duration_minutes} mins` : '-';
            const amount = log.amount_paid !== null ? `₹${log.amount_paid.toFixed(2)}` : '-';
            
            const badgeClass = log.status === 'COMPLETED' ? 'badge success' : 'badge danger';
            const statusLabel = log.status === 'COMPLETED' ? 'Completed' : 'Parked Inside';

            row.innerHTML = `
                <td><strong>${log.license_plate}</strong></td>
                <td>${log.slot_name}</td>
                <td>${entryTime}</td>
                <td>${exitTime}</td>
                <td>${duration}</td>
                <td>${amount}</td>
                <td><span class="${badgeClass}">${statusLabel}</span></td>
            `;
            tableBody.appendChild(row);
        });
    } catch (err) {
        tableBody.innerHTML = `<tr><td colspan="7" class="table-placeholder" style="color:var(--danger)">Error: ${err.message}</td></tr>`;
    }
}

// --- PHASE 2 ADVANCED PRICING & PAYMENT FLOWS ---

let pricingChartInstance = null;
let currentPaymentMethod = 'CASH';
let stripeInstance = null;
let stripeElements = null;
let stripeCardElement = null;
let stripeClientSecret = null;

async function silentLogin() {
    // Deprecated in favor of JWT interactive login modal overlay.
    // Retained as fallback helper for internal testing or background setup.
    try {
        const response = await fetch(`${API_BASE}/login`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({
                username: 'admin',
                password: 'password123'
            })
        });
        if (response.ok) {
            const data = await response.json();
            sessionStorage.setItem('parking_jwt_token', data.access_token);
            sessionStorage.setItem('parking_user_role', data.role);
            sessionStorage.setItem('parking_user_name', 'admin');
            console.log("[AUTH] Silent login as admin successful.");
        }
    } catch (err) {
        console.error("[AUTH ERROR]", err);
    }
}

async function loadPricingForecast() {
    try {
        // Fetch current live rate
        const currentRes = await fetch(`${API_BASE}/pricing/current`);
        if (currentRes.ok) {
            const currentPricing = await currentRes.json();
            const liveRateBadge = document.getElementById('current-live-rate');
            if (liveRateBadge) {
                liveRateBadge.textContent = `Current Rate: ₹${currentPricing.final_rate.toFixed(2)}/hr`;
            }
        }

        // Fetch 24 hours forecast
        const response = await fetch(`${API_BASE}/pricing/forecast`);
        if (!response.ok) throw new Error("Could not fetch pricing forecast");
        const forecast = await response.json();

        const labels = forecast.map(f => f.hour);
        const data = forecast.map(f => f.rate);

        const ctx = document.getElementById('pricingForecastChart').getContext('2d');
        if (pricingChartInstance) {
            pricingChartInstance.destroy();
        }

        pricingChartInstance = new Chart(ctx, {
            type: 'line',
            data: {
                labels: labels,
                datasets: [{
                    label: 'Hourly Rate (₹)',
                    data: data,
                    borderColor: '#38bdf8',
                    backgroundColor: 'rgba(56, 189, 248, 0.15)',
                    borderWidth: 2,
                    fill: true,
                    tension: 0.4,
                    pointBackgroundColor: '#38bdf8',
                    pointBorderColor: '#fff',
                    pointHoverBackgroundColor: '#fff',
                    pointHoverBorderColor: '#38bdf8'
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: {
                    legend: {
                        display: false
                    },
                    tooltip: {
                        mode: 'index',
                        intersect: false,
                        callbacks: {
                            label: function(context) {
                                return `Rate: ₹${context.raw.toFixed(2)}/hr`;
                            }
                        }
                    }
                },
                scales: {
                    x: {
                        grid: {
                            color: 'rgba(255, 255, 255, 0.05)'
                        },
                        ticks: {
                            color: 'rgba(255, 255, 255, 0.6)',
                            font: {
                                size: 10,
                                family: "'Space Grotesk', sans-serif"
                            },
                            maxTicksLimit: 8
                        }
                    },
                    y: {
                        grid: {
                            color: 'rgba(255, 255, 255, 0.05)'
                        },
                        ticks: {
                            color: 'rgba(255, 255, 255, 0.6)',
                            font: {
                                size: 10,
                                family: "'Space Grotesk', sans-serif"
                            },
                            callback: function(value) {
                                return '₹' + value;
                            }
                        }
                    }
                }
            }
        });
    } catch (err) {
        console.error("[FORECAST CHART ERROR]", err);
    }
}

function openPaymentModal(transactionId, plate, slotName, duration, fee) {
    document.getElementById('payment-transaction-id').value = transactionId;
    document.getElementById('payment-vehicle-plate').textContent = plate;
    document.getElementById('payment-slot-name').textContent = slotName;
    document.getElementById('payment-duration').textContent = `${duration} mins`;
    document.getElementById('payment-total-fee').textContent = `₹${fee.toFixed(2)}`;
    
    // Clear inputs and errors
    document.getElementById('payment-ref-input').value = '';
    document.getElementById('payment-status-text').textContent = '';
    document.getElementById('payment-status-text').className = 'payment-status-message';
    
    // Default to Cash
    selectPaymentMethod('CASH');

    // Show modal overlay
    document.getElementById('payment-modal').classList.remove('hidden');
}

function closePaymentModal() {
    document.getElementById('payment-modal').classList.add('hidden');
    if (stripeCardElement) {
        stripeCardElement.destroy();
        stripeCardElement = null;
    }
}

async function selectPaymentMethod(method) {
    currentPaymentMethod = method;
    
    const buttons = ['btn-pay-cash', 'btn-pay-card', 'btn-pay-upi', 'btn-pay-stripe'];
    buttons.forEach(btnId => {
        const btn = document.getElementById(btnId);
        if (btn) {
            if (btnId === `btn-pay-${method.toLowerCase()}`) {
                btn.classList.add('active');
            } else {
                btn.classList.remove('active');
            }
        }
    });

    const refGroup = document.getElementById('payment-ref-group');
    const refLabel = document.getElementById('payment-ref-label');
    const stripeContainer = document.getElementById('stripe-container');

    // Reset visibility
    refGroup.classList.add('hidden');
    stripeContainer.classList.add('hidden');
    if (stripeCardElement) {
        stripeCardElement.destroy();
        stripeCardElement = null;
    }

    if (method === 'CASH') {
        refGroup.classList.add('hidden');
    } else if (method === 'CARD' || method === 'UPI') {
        refGroup.classList.remove('hidden');
        refLabel.textContent = method === 'CARD' ? 'Card Auth Code / Receipt No.' : 'UPI Transaction ID (UTR)';
    } else if (method === 'STRIPE') {
        stripeContainer.classList.remove('hidden');
        const transactionId = document.getElementById('payment-transaction-id').value;
        await initializeStripePayment(transactionId);
    }
}

async function initializeStripePayment(transactionId) {
    const statusText = document.getElementById('payment-status-text');
    statusText.textContent = "Initializing Stripe Elements...";
    statusText.className = "payment-status-message info";

    try {
        const response = await fetch(`${API_BASE}/payment/stripe`, {
            method: 'POST',
            headers: getHeaders(),
            body: JSON.stringify({ transaction_id: parseInt(transactionId) })
        });
        const data = await response.json();
        
        if (!response.ok) {
            throw new Error(data.detail || "Failed to initialize Stripe payment session");
        }

        stripeClientSecret = data.client_secret;

        if (stripeClientSecret.startsWith("mock_secret_intent")) {
            statusText.textContent = "Stripe Sandbox Mode active. Submit confirmation to proceed.";
            statusText.className = "payment-status-message warning";
            
            const cardElementContainer = document.getElementById('card-element');
            cardElementContainer.innerHTML = `
                <div style="padding: 12px; color: var(--success); background: rgba(56, 189, 248, 0.1); border-radius: 8px; border: 1px dashed var(--primary); text-align: center;">
                    <i class="fa-solid fa-flask" style="margin-right: 5px;"></i> Mock Credit Card Field Active
                </div>
            `;
        } else {
            statusText.textContent = "Secure credit card form loaded.";
            statusText.className = "payment-status-message success";
            
            if (typeof Stripe === 'undefined') {
                throw new Error("Stripe.js was not loaded properly.");
            }
            
            if (!stripeInstance) {
                stripeInstance = Stripe('pk_test_51Iq8tYSJvFhF5XG7xZ9D4V6d6V_placeholder');
            }
            
            stripeElements = stripeInstance.elements();
            stripeCardElement = stripeElements.create('card', {
                style: {
                    base: {
                        color: '#ffffff',
                        fontFamily: '"Outfit", sans-serif',
                        fontSize: '16px',
                        '::placeholder': {
                            color: 'rgba(255, 255, 255, 0.4)'
                        }
                    },
                    invalid: {
                        color: '#ff5e5e',
                        iconColor: '#ff5e5e'
                    }
                }
            });
            
            const cardElementContainer = document.getElementById('card-element');
            cardElementContainer.innerHTML = '';
            stripeCardElement.mount('#card-element');

            stripeCardElement.on('change', (event) => {
                const displayError = document.getElementById('card-errors');
                if (event.error) {
                    displayError.textContent = event.error.message;
                } else {
                    displayError.textContent = '';
                }
            });
        }
    } catch (err) {
        statusText.textContent = `Error: ${err.message}`;
        statusText.className = "payment-status-message danger";
    }
}

async function submitConfirmPayment() {
    const transactionId = document.getElementById('payment-transaction-id').value;
    const refInput = document.getElementById('payment-ref-input').value.trim();
    const statusText = document.getElementById('payment-status-text');
    const submitBtn = document.getElementById('confirm-payment-submit-btn');

    statusText.textContent = "Processing payment confirmation...";
    statusText.className = "payment-status-message info";
    submitBtn.disabled = true;

    try {
        let paymentReference = refInput || null;

        if (currentPaymentMethod === 'STRIPE') {
            if (!stripeClientSecret) {
                throw new Error("Stripe intent has not been loaded.");
            }

            if (stripeClientSecret.startsWith("mock_secret_intent")) {
                paymentReference = `mock_stripe_ref_${Math.floor(Math.random() * 1000000)}`;
            } else {
                statusText.textContent = "Processing card validation with Stripe...";
                const result = await stripeInstance.confirmCardPayment(stripeClientSecret, {
                    payment_method: {
                        card: stripeCardElement
                    }
                });

                if (result.error) {
                    throw new Error(result.error.message);
                } else {
                    if (result.paymentIntent.status === 'succeeded') {
                        paymentReference = result.paymentIntent.id;
                    } else {
                        throw new Error("Payment transaction failed with Stripe gateway.");
                    }
                }
            }
        } else if (currentPaymentMethod === 'CARD' || currentPaymentMethod === 'UPI') {
            if (!paymentReference) {
                throw new Error("Payment reference is required for Card or UPI.");
            }
        }

        // Post to our server endpoint to confirm and finalize in DB
        const response = await fetch(`${API_BASE}/payment/confirm`, {
            method: 'POST',
            headers: getHeaders(),
            body: JSON.stringify({
                transaction_id: parseInt(transactionId),
                payment_method: currentPaymentMethod,
                payment_reference: paymentReference
            })
        });
        const data = await response.json();

        if (!response.ok) {
            throw new Error(data.detail || "Payment finalization failed.");
        }

        statusText.textContent = "Payment successfully confirmed! Gate open.";
        statusText.className = "payment-status-message success";

        // WebSocket event will close the modal, but fallback close:
        setTimeout(() => {
            closePaymentModal();
            submitBtn.disabled = false;
        }, 1000);

    } catch (err) {
        statusText.textContent = `Error: ${err.message}`;
        statusText.className = "payment-status-message danger";
        submitBtn.disabled = false;
    }
}

// --- JWT Authentication Overlay Handlers ---
async function handleLoginSubmit(event) {
    event.preventDefault();
    const usernameInput = document.getElementById('login-username');
    const passwordInput = document.getElementById('login-password');
    const errorMsg = document.getElementById('login-error-msg');
    const submitBtn = document.getElementById('login-submit-btn');
    
    const username = usernameInput.value.trim();
    const password = passwordInput.value;
    
    errorMsg.classList.add('hidden');
    submitBtn.disabled = true;
    submitBtn.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i> Authenticating...';
    
    try {
        const response = await fetch(`${API_BASE}/login`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({ username, password })
        });
        
        const data = await response.json();
        if (!response.ok) {
            throw new Error(data.detail || "Authentication failed");
        }
        
        // Save token and info
        sessionStorage.setItem('parking_jwt_token', data.access_token);
        sessionStorage.setItem('parking_user_role', data.role);
        sessionStorage.setItem('parking_user_name', username);
        
        // Hide login modal
        document.getElementById('login-modal').classList.add('hidden');
        
        // Initialize dashboard session
        initDashboardSession();
        showToastNotification(`Successfully logged in as ${username}.`, "success");
    } catch (err) {
        errorMsg.textContent = err.message;
        errorMsg.classList.remove('hidden');
    } finally {
        submitBtn.disabled = false;
        submitBtn.innerHTML = '<i class="fa-solid fa-right-to-sign"></i> Log In';
    }
}

function initDashboardSession() {
    const token = sessionStorage.getItem('parking_jwt_token');
    const role = sessionStorage.getItem('parking_user_role') || 'viewer';
    const username = sessionStorage.getItem('parking_user_name') || 'Guest';
    
    // Display profile header
    const profileHeader = document.getElementById('user-profile-header');
    if (profileHeader) {
        profileHeader.classList.remove('hidden');
        document.getElementById('user-display-name').textContent = username;
        const roleBadge = document.getElementById('user-display-role');
        roleBadge.textContent = role;
        roleBadge.className = `role-badge ${role}`;
    }
    
    // Show/hide components based on Role
    const isOperator = (role === 'operator');
    const isViewer = (role === 'viewer');
    
    // Hide settings forms and configurations if operator/viewer
    const pricingForm = document.getElementById('pricing-config-form');
    if (pricingForm) {
        const savePricingBtn = document.getElementById('save-pricing-btn');
        if (savePricingBtn) {
            if (isOperator || isViewer) {
                savePricingBtn.classList.add('hidden');
                pricingForm.querySelectorAll('input').forEach(input => input.disabled = true);
            } else {
                savePricingBtn.classList.remove('hidden');
                pricingForm.querySelectorAll('input').forEach(input => input.disabled = false);
            }
        }
    }
    
    const addSlotBtn = document.getElementById('add-slot-toggle-btn');
    if (addSlotBtn) {
        if (isOperator || isViewer) {
            addSlotBtn.classList.add('hidden');
        } else {
            addSlotBtn.classList.remove('hidden');
        }
    }
    
    // Initial fetch for authenticated session
    loadSlots();
    loadPricing();
    loadPricingForecast();
    loadRevenue();
    queryAuditLogs();
}

function logoutUser() {
    sessionStorage.removeItem('parking_jwt_token');
    sessionStorage.removeItem('parking_user_role');
    sessionStorage.removeItem('parking_user_name');
    
    // Hide profile header
    const profileHeader = document.getElementById('user-profile-header');
    if (profileHeader) {
        profileHeader.classList.add('hidden');
    }
    
    // Show login modal
    document.getElementById('login-modal').classList.remove('hidden');
    
    // Clear login inputs
    document.getElementById('login-username').value = '';
    document.getElementById('login-password').value = '';
    document.getElementById('login-error-msg').classList.add('hidden');
    
    showToastNotification("Logged out successfully.", "info");
}

// --- Slot Reservation Trigger ---
async function executeManualReserve() {
    const slotInput = document.getElementById('manual-reserve-slot');
    const plateInput = document.getElementById('manual-reserve-plate');
    const durationInput = document.getElementById('manual-reserve-duration');
    const statusDiv = document.getElementById('override-status-message');
    
    const slotName = slotInput.value.trim();
    const plate = plateInput.value.trim().toUpperCase();
    const duration = parseInt(durationInput.value) || 30;
    
    if (!slotName || !plate) {
        showFeedback(statusDiv, "Please enter slot name and license plate number.", "error");
        return;
    }
    
    try {
        const response = await fetch(`${API_BASE}/slots/reserve`, {
            method: 'POST',
            headers: getHeaders(),
            body: JSON.stringify({
                slot_name: slotName,
                license_plate: plate,
                duration_minutes: duration
            })
        });
        const data = await response.json();
        if (!response.ok) {
            throw new Error(data.detail || "Reservation request failed");
        }
        showFeedback(statusDiv, `SUCCESS: Reserved slot '${slotName}' for ${plate} for ${duration} mins.`, "success");
        slotInput.value = '';
        plateInput.value = '';
    } catch (err) {
        showFeedback(statusDiv, err.message, "error");
    }
}

// --- Cryptographic Audit Log Verification ---
async function verifyAuditLogs() {
    const badge = document.getElementById('audit-integrity-badge');
    const btn = document.getElementById('verify-audit-btn');
    const originalText = btn.innerHTML;
    
    btn.disabled = true;
    btn.innerHTML = `<i class="fa-solid fa-spinner fa-spin"></i> Checking...`;
    
    badge.className = "integrity-badge unknown";
    badge.innerHTML = `<i class="fa-solid fa-shield-halved"></i> Checking Chain...`;
    
    try {
        const response = await fetch(`${API_BASE}/audit/verify`, {
            headers: getHeaders()
        });
        const data = await response.json();
        
        if (!response.ok) {
            throw new Error(data.detail || "Verification request failed");
        }
        
        if (data.status === "SECURE") {
            badge.className = "integrity-badge secure";
            badge.innerHTML = `<i class="fa-solid fa-circle-check"></i> Integrity: Secure`;
            showToastNotification("Audit log cryptographic chain is valid.", "success");
        } else if (data.status === "TAMPERED") {
            badge.className = "integrity-badge tampered";
            badge.innerHTML = `<i class="fa-solid fa-circle-exclamation"></i> Tampered Logs Found`;
            showToastNotification(`WARNING: Tampering isolated at Log IDs: ${data.tampered_ids.join(', ')}`, "danger");
        }
    } catch (err) {
        badge.className = "integrity-badge unknown";
        badge.innerHTML = `<i class="fa-solid fa-triangle-exclamation"></i> Verification Failed`;
        showToastNotification(err.message, "danger");
    } finally {
        btn.disabled = false;
        btn.innerHTML = originalText;
    }
}

// --- Alerts Stream Sidebar display helper ---
function pushAlertToSidebar(event, message, timestamp) {
    const list = document.getElementById('alerts-sidebar-list');
    if (!list) return;
    
    const placeholder = list.querySelector('.alert-placeholder');
    if (placeholder) placeholder.remove();
    
    const time = timestamp ? new Date(timestamp) : new Date();
    const formattedTime = time.toLocaleTimeString();
    
    const item = document.createElement('li');
    item.className = `alert-item ${event}`;
    
    let title = "System Alert";
    if (event === 'lot_full_attempt') title = "Access Denied";
    else if (event === 'payment') title = "Payment Processed";
    else if (event === 'entry') title = "Gate Entry";
    else if (event === 'exit') title = "Gate Exit";
    else if (event === 'pricing_updated') title = "Pricing Config Change";
    else if (event === 'slots_updated') title = "Slot Configuration";
    
    item.innerHTML = `
        <div class="alert-header">
            <span>${title}</span>
        </div>
        <div class="alert-body">${message}</div>
        <div class="alert-time">${formattedTime}</div>
    `;
    
    list.insertBefore(item, list.firstChild);
    
    // Keep max 20 entries
    while (list.children.length > 20) {
        list.removeChild(list.lastChild);
    }
}

// Countdown timer loop for slot reservations
if (!window.reservationInterval) {
    window.reservationInterval = setInterval(() => {
        const countdownEls = document.querySelectorAll('.reservation-countdown');
        countdownEls.forEach(el => {
            const expiryStr = el.dataset.expiry;
            if (!expiryStr || expiryStr === "null") return;
            const expiry = new Date(expiryStr);
            const now = new Date();
            const diffMs = expiry - now;
            if (diffMs <= 0) {
                el.textContent = "Expired";
            } else {
                const totalSecs = Math.floor(diffMs / 1000);
                const mins = Math.floor(totalSecs / 60);
                const secs = totalSecs % 60;
                el.textContent = `${mins}:${secs.toString().padStart(2, '0')}`;
            }
        });
    }, 1000);
}
