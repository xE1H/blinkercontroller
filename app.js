// Prevent zoom on double tap
let lastTouchEnd = 0;
document.addEventListener('touchend', function (event) {
    var now = (new Date()).getTime();
    if (now - lastTouchEnd <= 300) {
        event.preventDefault();
    }
    lastTouchEnd = now;
}, false);

// Relay button debouncing
let relayDebounceTimers = {};
let relayStates = {};
let relayStopTimers = {};

function updateSpeedDisplay(value) {
    document.getElementById('speed_display').textContent = value + 'ms';
}

function testRelay(side, action) {
    const debounceTime = 50; // Reduced debounce time
    const timerId = side + '_' + action;
    
    // Clear any existing timer for this button/action
    if (relayDebounceTimers[timerId]) {
        clearTimeout(relayDebounceTimers[timerId]);
        delete relayDebounceTimers[timerId];
    }
    
    // For stop actions, also clear any existing stop timer for this side
    if (action === 'stop' && relayStopTimers[side]) {
        clearTimeout(relayStopTimers[side]);
        delete relayStopTimers[side];
    }
    
    relayDebounceTimers[timerId] = setTimeout(() => {
        const stateKey = side + '_state';
        
        if (action === 'start') {
            // Cancel any pending stop for this side
            if (relayStopTimers[side]) {
                clearTimeout(relayStopTimers[side]);
                delete relayStopTimers[side];
            }
            
            relayStates[stateKey] = true;
            const leftState = (side === 'left' || side === 'both') ? '1' : '0';
            const rightState = (side === 'right' || side === 'both') ? '1' : '0';
            fetch('/relay_control?left=' + leftState + '&right=' + rightState)
                .catch(e => console.log('Relay start failed:', e));
                
        } else if (action === 'stop') {
            // Set a fallback timer to ensure relays turn off
            relayStopTimers[side] = setTimeout(() => {
                fetch('/relay_control?left=0&right=0')
                    .catch(e => console.log('Relay emergency stop failed:', e));
                delete relayStopTimers[side];
                relayStates[stateKey] = false;
            }, 100);
            
            relayStates[stateKey] = false;
            fetch('/relay_control?left=0&right=0')
                .catch(e => console.log('Relay stop failed:', e));
        }
        
        delete relayDebounceTimers[timerId];
    }, debounceTime);
}

// Emergency stop function to ensure relays don't get stuck
function emergencyStopRelays() {
    fetch('/relay_control?left=0&right=0')
        .catch(e => console.log('Emergency stop failed:', e));
    
    // Clear all timers and states
    Object.values(relayDebounceTimers).forEach(clearTimeout);
    Object.values(relayStopTimers).forEach(clearTimeout);
    relayDebounceTimers = {};
    relayStopTimers = {};
    relayStates = {};
}

// Add emergency stop on page visibility change (when user switches tabs/apps)
document.addEventListener('visibilitychange', function() {
    if (document.hidden) {
        emergencyStopRelays();
    }
});

// Add emergency stop on window blur
window.addEventListener('blur', emergencyStopRelays);

function addFrame(effectName) {
    const container = document.getElementById(effectName + '_frames');
    const frameCount = container.children.length;
    
    const frameDiv = document.createElement('div');
    frameDiv.className = 'frame';
    frameDiv.setAttribute('data-effect', effectName);
    frameDiv.setAttribute('data-index', frameCount);
    
    frameDiv.innerHTML = '<div class="frame-header"><span class="frame-number">Frame ' + (frameCount + 1) + '</span><button type="button" class="frame-delete" onclick="delFrame(\'' + effectName + '\', ' + frameCount + ')">✗</button></div><div class="frame-controls"><div class="form-group"><label>Light Status</label><select name="' + effectName + '_frame_' + frameCount + '_light_status" onchange="updatePreview(\'' + effectName + '\')"><option value="off">💡 Off</option><option value="left">← Left</option><option value="right">Right →</option><option value="both">↔ Both</option></select></div><div class="form-group"><label>Duration (ms)</label><input type="number" name="' + effectName + '_frame_' + frameCount + '_duration" value="200" min="10" max="5000" onchange="updatePreview(\'' + effectName + '\')"></div></div>';
    
    container.appendChild(frameDiv);
    updateFrameNumbers(effectName);
    updatePreview(effectName);
}

function delFrame(effectName, index) {
    const container = document.getElementById(effectName + '_frames');
    const frames = container.children;
    if (frames.length > 1) {
        frames[index].remove();
        updateFrameNumbers(effectName);
        updatePreview(effectName);
    }
}

function updateFrameNumbers(effectName) {
    const container = document.getElementById(effectName + '_frames');
    const frames = container.children;
    for (let i = 0; i < frames.length; i++) {
        const frame = frames[i];
        frame.setAttribute('data-index', i);
        frame.querySelector('.frame-number').textContent = 'Frame ' + (i + 1);
        
        const lightSelect = frame.querySelector('select');
        const durationInput = frame.querySelector('input[type="number"]');
        lightSelect.name = effectName + '_frame_' + i + '_light_status';
        durationInput.name = effectName + '_frame_' + i + '_duration';
        
        const deleteBtn = frame.querySelector('.frame-delete');
        deleteBtn.setAttribute('onclick', 'delFrame(\'' + effectName + '\', ' + i + ')');
    }
}

function updatePreview(effectName) {
    const container = document.getElementById(effectName + '_frames');
    const preview = document.getElementById(effectName + '_preview');
    const frames = container.children;
    
    preview.innerHTML = '';
    for (let i = 0; i < frames.length; i++) {
        const lightStatus = frames[i].querySelector('select').value;
        const duration = frames[i].querySelector('input[type="number"]').value;
        
        const light = document.createElement('div');
        light.className = 'preview-light preview-' + lightStatus;
        light.title = lightStatus + ' - ' + duration + 'ms';
        preview.appendChild(light);
    }
}

// Initialize previews on page load
window.addEventListener('load', function() {
    updatePreview('left_effect');
    updatePreview('right_effect');
});

function triggerEffect(side) {
    fetch('/trigger_effect?side=' + side)
        .then(response => response.text())
        .then(data => {
            console.log('Effect triggered:', side);
            // Optional: Show feedback to user
            const button = document.querySelector('.effect-trigger-button.' + side + '-effect');
            if (button) {
                button.style.transform = 'scale(0.95)';
                setTimeout(() => {
                    button.style.transform = '';
                }, 200);
            }
        })
        .catch(e => console.log('Effect trigger failed:', e));
}
