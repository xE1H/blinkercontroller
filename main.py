import machine
import network
import socket
import time
import json
import gc
import struct
from machine import Pin, Timer

# Pin definitions for ESP32-C3
LEFT_RELAY_PIN = 2
RIGHT_RELAY_PIN = 3
HAZARD_BUTTON_PIN = 5
LEFT_EFFECT_BUTTON_PIN = 6
RIGHT_EFFECT_BUTTON_PIN = 7

# Hardware setup
left_relay = Pin(LEFT_RELAY_PIN, Pin.OUT)
right_relay = Pin(RIGHT_RELAY_PIN, Pin.OUT)
hazard_button = Pin(HAZARD_BUTTON_PIN, Pin.IN, Pin.PULL_UP)
left_effect_button = Pin(LEFT_EFFECT_BUTTON_PIN, Pin.IN, Pin.PULL_UP)
right_effect_button = Pin(RIGHT_EFFECT_BUTTON_PIN, Pin.IN, Pin.PULL_UP)

# Global state variables
hazard_mode = False
left_effect_active = False
right_effect_active = False
hazard_blink_state = False
hazard_last_blink_time = 0
blink_interval = 500  # milliseconds

# Effect playback state
effect_playing = False
effect_start_time = 0
effect_sequence = []
effect_sequence_index = 0
manual_relay_control = {'left': False, 'right': False}

# WiFi dashboard control
wifi_enabled = False
wifi_activation_start_time = 0
wifi_activation_threshold = 3000  # 3 seconds in milliseconds
wifi_timeout = 900000  # 15 minutes in milliseconds
wifi_enable_time = 0
wifi_confirmation_active = False
wifi_confirmation_start_time = 0
wifi_confirmation_duration = 3000  # 3 seconds
wifi_confirmation_blink_state = False
wifi_confirmation_last_blink_time = 0

# Relay state tracking for debug prints
left_relay_state = False
right_relay_state = False

# Button debouncing
button_states = {
    'hazard': {'last_state': 1, 'last_time': 0, 'pressed': False},
    'left_effect': {'last_state': 1, 'last_time': 0, 'pressed': False},
    'right_effect': {'last_state': 1, 'last_time': 0, 'pressed': False}
}

# Settings storage
SETTINGS_FILE = 'settings.json'
default_settings = {
    'wifi_ssid': 'BlinkerController',
    'wifi_password': 'blinker123',
    'blink_speed': 500,
    'left_effect': {
        'frames': [
            {'light_status': 'left', 'duration': 200},
            {'light_status': 'off', 'duration': 200},
            {'light_status': 'left', 'duration': 200},
            {'light_status': 'off', 'duration': 200},
            {'light_status': 'left', 'duration': 200},
            {'light_status': 'off', 'duration': 500}
        ]
    },
    'right_effect': {
        'frames': [
            {'light_status': 'right', 'duration': 100},
            {'light_status': 'off', 'duration': 100},
            {'light_status': 'right', 'duration': 100},
            {'light_status': 'off', 'duration': 100},
            {'light_status': 'both', 'duration': 300},
            {'light_status': 'off', 'duration': 200}
        ]
    }
}

settings = default_settings.copy()

def load_settings():
    global settings
    try:
        with open(SETTINGS_FILE, 'r') as f:
            loaded_settings = json.load(f)
            settings.update(loaded_settings)
        print("Settings loaded successfully")
    except:
        print("Using default settings")

def save_settings():
    try:
        with open(SETTINGS_FILE, 'w') as f:
            json.dump(settings, f)
        print("Settings saved successfully")
    except Exception as e:
        print(f"Error saving settings: {e}")

def debounce_button(button_name, pin, debounce_time=50):
    current_time = time.ticks_ms()
    current_state = pin.value()
    button_data = button_states[button_name]
    
    # If state changed, update the time but don't update last_state yet
    if current_state != button_data['last_state']:
        button_data['last_time'] = current_time
        
        # Only process if enough time has passed since the last change
        if time.ticks_diff(current_time, button_data.get('last_change_time', 0)) > debounce_time:
            button_data['last_state'] = current_state
            button_data['last_change_time'] = current_time
            
            if current_state == 0:  # Button pressed (active low due to pull-up)
                button_data['pressed'] = True
                print(f"🔘 RAW BUTTON {button_name}: Pin {pin} = {current_state} (PRESSED)")
                return True
            else:
                print(f"🔘 RAW BUTTON {button_name}: Pin {pin} = {current_state} (RELEASED)")
    
    return False

def handle_buttons():
    global hazard_mode, effect_playing, wifi_enabled, wifi_activation_start_time, wifi_enable_time, wifi_confirmation_active, wifi_confirmation_start_time
    
    # Add raw pin readings for debugging
    hazard_pin_val = hazard_button.value()
    left_pin_val = left_effect_button.value()
    right_pin_val = right_effect_button.value()
    
    # Print pin states every 100 loops for debugging
    global debug_counter
    if 'debug_counter' not in globals():
        debug_counter = 0
    debug_counter += 1
    if debug_counter >= 1000:  # Every 1000 loops (~10 seconds)
        print(f"🔍 PIN DEBUG: Hazard={hazard_pin_val}, Left={left_pin_val}, Right={right_pin_val}")
        debug_counter = 0
    
    # Handle hazard button (direct state reading - not toggle)
    # Button is pressed when value is 0 (active low due to pull-up)
    new_hazard_state = (hazard_pin_val == 0)
    if new_hazard_state != hazard_mode:
        hazard_mode = new_hazard_state
        if not hazard_mode:
            set_both_relays(False)  # Turn off relays when hazard is released
        print(f"🟡 HAZARD BUTTON {'PRESSED' if new_hazard_state else 'RELEASED'} - Mode: {'ON' if hazard_mode else 'OFF'}")
    
    # Check for dual button hold to enable WiFi dashboard
    both_buttons_pressed = (left_pin_val == 0) and (right_pin_val == 0)
    current_time = time.ticks_ms()
    
    if both_buttons_pressed and not wifi_enabled and not wifi_confirmation_active:
        # Both buttons are pressed - start or continue timing
        if wifi_activation_start_time == 0:
            wifi_activation_start_time = current_time
            print("📶 Both effect buttons pressed - WiFi activation timer started")
        elif time.ticks_diff(current_time, wifi_activation_start_time) >= wifi_activation_threshold:
            # 3 seconds have passed - enable WiFi
            wifi_enabled = True
            wifi_enable_time = current_time
            wifi_confirmation_active = True
            wifi_confirmation_start_time = current_time
            wifi_activation_start_time = 0
            print("📶 WiFi dashboard ENABLED - Starting confirmation blink")
    elif not both_buttons_pressed:
        # Reset activation timer if buttons are released
        if wifi_activation_start_time != 0:
            wifi_activation_start_time = 0
            print("📶 WiFi activation cancelled - buttons released")
    
    # Handle individual effect buttons only if WiFi not being activated and no confirmation active
    if not both_buttons_pressed and not wifi_confirmation_active:
        # Handle left effect button (single press triggers full effect)
        if debounce_button('left_effect', left_effect_button):
            if not hazard_mode and not effect_playing:  # Only work when hazard is off and no effect playing
                start_effect('left_effect')
                print("🟢 LEFT EFFECT BUTTON PRESSED - Effect started")
            else:
                print(f"🟢 LEFT EFFECT BUTTON PRESSED - Blocked (Hazard: {hazard_mode}, Effect playing: {effect_playing})")
        
        # Handle right effect button (single press triggers full effect)
        if debounce_button('right_effect', right_effect_button):
            if not hazard_mode and not effect_playing:  # Only work when hazard is off and no effect playing
                start_effect('right_effect')
                print("🔴 RIGHT EFFECT BUTTON PRESSED - Effect started")
            else:
                print(f"🔴 RIGHT EFFECT BUTTON PRESSED - Blocked (Hazard: {hazard_mode}, Effect playing: {effect_playing})")

def set_left_relay(state):
    global left_relay_state
    if not manual_relay_control['left']:
        new_state = bool(state)
        if new_state != left_relay_state:
            left_relay_state = new_state
            left_relay.value(0 if new_state else 1)
            print(f"🔵 LEFT RELAY: {'ON' if new_state else 'OFF'}")

def set_right_relay(state):
    global right_relay_state
    if not manual_relay_control['right']:
        new_state = bool(state)
        if new_state != right_relay_state:
            right_relay_state = new_state
            right_relay.value(0 if new_state else 1)
            print(f"🔴 RIGHT RELAY: {'ON' if new_state else 'OFF'}")

def set_both_relays(state):
    set_left_relay(state)
    set_right_relay(state)

def manual_relay_override(left_state, right_state):
    """Direct relay control for testing - overrides automatic control"""
    global left_relay_state, right_relay_state
    
    if left_state is not None:
        new_left_state = bool(left_state)
        if new_left_state != left_relay_state:
            left_relay_state = new_left_state
            left_relay.value(0 if new_left_state else 1)
            print(f"🔵 LEFT RELAY (MANUAL): {'ON' if new_left_state else 'OFF'}")
        manual_relay_control['left'] = left_state
        
    if right_state is not None:
        new_right_state = bool(right_state)
        if new_right_state != right_relay_state:
            right_relay_state = new_right_state
            right_relay.value(0 if new_right_state else 1)
            print(f"🔴 RIGHT RELAY (MANUAL): {'ON' if new_right_state else 'OFF'}")
        manual_relay_control['right'] = right_state

def start_effect(effect_type):
    global effect_playing, effect_start_time, effect_sequence, effect_sequence_index
    effect_config = settings[effect_type]
    
    # Use frames directly as the sequence
    effect_sequence = convert_frames_to_sequence(effect_config['frames'])
    effect_playing = True
    effect_start_time = time.ticks_ms()
    effect_sequence_index = 0
    print(f"Effect sequence loaded: {len(effect_sequence)} frames")

def convert_frames_to_sequence(frames):
    sequence = []
    for frame in frames:
        light_status = frame['light_status']
        sequence.append({
            'left_state': light_status in ['left', 'both'],
            'right_state': light_status in ['right', 'both'],
            'duration': frame['duration']
        })
    return sequence

def update_effect_playback():
    global effect_playing, effect_sequence_index, effect_start_time
    
    if not effect_playing or len(effect_sequence) == 0:
        return
    
    current_time = time.ticks_ms()
    
    if effect_sequence_index >= len(effect_sequence):
        # Effect finished
        effect_playing = False
        set_both_relays(False)
        print("Effect completed")
        return
    
    current_step = effect_sequence[effect_sequence_index]
    
    # Check if it's time to move to next step
    if time.ticks_diff(current_time, effect_start_time) >= current_step['duration']:
        effect_sequence_index += 1
        effect_start_time = current_time
        
        if effect_sequence_index < len(effect_sequence):
            next_step = effect_sequence[effect_sequence_index]
            set_left_relay(next_step['left_state'])
            set_right_relay(next_step['right_state'])
    else:
        # Apply current step
        set_left_relay(current_step['left_state'])
        set_right_relay(current_step['right_state'])

def update_wifi_confirmation():
    global wifi_confirmation_active, wifi_confirmation_start_time, wifi_confirmation_blink_state, wifi_confirmation_last_blink_time
    current_time = time.ticks_ms()
    
    if wifi_confirmation_active:
        # Check if confirmation period is over
        if time.ticks_diff(current_time, wifi_confirmation_start_time) >= wifi_confirmation_duration:
            wifi_confirmation_active = False
            set_both_relays(False)
            print("📶 WiFi confirmation complete - Dashboard ready")
            return
        
        # Blink both relays at 4Hz (250ms on/off) during confirmation
        if time.ticks_diff(current_time, wifi_confirmation_last_blink_time) >= 250:
            wifi_confirmation_blink_state = not wifi_confirmation_blink_state
            wifi_confirmation_last_blink_time = current_time
            set_both_relays(wifi_confirmation_blink_state)

def update_blinkers():
    global hazard_blink_state, hazard_last_blink_time
    current_time = time.ticks_ms()
    
    # Check if manual control is active
    if manual_relay_control['left'] or manual_relay_control['right']:
        return  # Skip automatic control
    
    # WiFi confirmation takes priority over all other patterns
    if wifi_confirmation_active:
        update_wifi_confirmation()
    elif effect_playing:
        update_effect_playback()
    elif hazard_mode:
        # Standard hazard blink pattern - use dedicated hazard timing variables
        if time.ticks_diff(current_time, hazard_last_blink_time) >= settings['blink_speed']:
            hazard_blink_state = not hazard_blink_state
            hazard_last_blink_time = current_time
            set_both_relays(hazard_blink_state)
    else:
        set_both_relays(False)


def check_wifi_timeout():
    global wifi_enabled, wifi_enable_time
    current_time = time.ticks_ms()
    
    if wifi_enabled and time.ticks_diff(current_time, wifi_enable_time) >= wifi_timeout:
        wifi_enabled = False
        print("📶 WiFi dashboard DISABLED - 15 minute timeout reached")
        return True  # Indicates WiFi was disabled
    return False

def setup_access_point(ap=None):
    if not wifi_enabled:
        # Only start AP if WiFi is enabled
        return None
        
    if ap is None:
        ap = network.WLAN(network.AP_IF)
    
    ap.active(True)
    
    try:
        # Configure access point
        ap.config(
            essid=settings['wifi_ssid'], 
            password=settings['wifi_password'], 
            authmode=network.AUTH_WPA_WPA2_PSK,
            channel=6
        )
        
        # Wait for AP to be active
        timeout = 10
        while not ap.active() and timeout > 0:
            time.sleep(0.5)
            timeout -= 1
        
        if ap.active():
            ip_info = ap.ifconfig()
            print(f"Access Point '{settings['wifi_ssid']}' started")
            print(f"IP: {ip_info[0]}, Gateway: {ip_info[2]}")
            return ap
        else:
            print("Failed to start Access Point")
            return None
    except Exception as e:
        print(f"AP setup error: {e}")
        return None

def disable_wifi_services(ap, web_server, dns_server):
    """Safely disable WiFi services"""
    try:
        if ap and ap.active():
            ap.active(False)
            print("📶 Access Point disabled")
    except:
        pass
    
    try:
        if web_server:
            web_server.close()
            print("📶 Web server closed")
    except:
        pass
    
    try:
        if dns_server:
            dns_server.close()
            print("📶 DNS server closed")
    except:
        pass

def update_wifi_settings(new_ssid, new_password, ap):
    """Update WiFi settings instantly without restart"""
    global settings
    old_ssid = settings['wifi_ssid']
    
    # Update settings
    settings['wifi_ssid'] = new_ssid
    settings['wifi_password'] = new_password
    save_settings()
    
    try:
        print(f"Updating WiFi: '{old_ssid}' -> '{new_ssid}'")
        
        # Reconfigure the existing access point
        ap.config(
            essid=settings['wifi_ssid'], 
            password=settings['wifi_password'], 
            authmode=network.AUTH_WPA_WPA2_PSK,
            channel=6
        )
        
        print(f"WiFi updated successfully to '{new_ssid}'")
        return True
        
    except Exception as e:
        print(f"Error updating WiFi: {e}")
        # Revert settings on failure
        settings['wifi_ssid'] = old_ssid
        return False

def start_dns_server(ap_ip='192.168.4.1'):
    """Start DNS server for captive portal - redirects all domains to AP IP"""
    try:
        dns_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        dns_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        dns_socket.bind(('0.0.0.0', 53))
        dns_socket.settimeout(0.1)  # Non-blocking
        print(f"DNS server started for captive portal on {ap_ip}")
        return dns_socket
    except Exception as e:
        print(f"DNS server error: {e}")
        return None

def handle_dns_request(dns_socket, ap_ip='192.168.4.1'):
    """Handle DNS requests and redirect all domains to AP IP"""
    try:
        data, addr = dns_socket.recvfrom(512)
        if len(data) < 12:
            return
        
        # Parse DNS header
        transaction_id = data[0:2]
        flags = struct.unpack('>H', data[2:4])[0]
        
        # Only handle queries (not responses)
        if flags & 0x8000 == 0:
            # Build DNS response - redirect all queries to AP IP
            response = bytearray()
            response.extend(transaction_id)  # Transaction ID
            response.extend(b'\x81\x80')     # Flags: response, no error
            response.extend(data[4:6])       # Questions count
            response.extend(data[4:6])       # Answers count (same as questions)
            response.extend(b'\x00\x00')     # Authority RRs
            response.extend(b'\x00\x00')     # Additional RRs
            
            # Copy question section
            questions_start = 12
            i = questions_start
            while i < len(data) and data[i] != 0:
                i += 1
            i += 5  # Skip null byte + QTYPE + QCLASS
            response.extend(data[questions_start:i])
            
            # Add answer section - point to AP IP
            response.extend(b'\xc0\x0c')     # Name pointer to question
            response.extend(b'\x00\x01')     # Type A
            response.extend(b'\x00\x01')     # Class IN
            response.extend(b'\x00\x00\x00\x3c')  # TTL: 60 seconds
            response.extend(b'\x00\x04')     # Data length: 4 bytes
            
            # Convert IP string to bytes
            ip_parts = ap_ip.split('.')
            for part in ip_parts:
                response.append(int(part))
            
            dns_socket.sendto(response, addr)
            
    except OSError:
        # Timeout or no data - this is normal
        pass
    except Exception as e:
        print(f"DNS error: {e}")

def get_frame_html(effect_name, frames):
    frame_html = ""
    for i, frame in enumerate(frames):
        selected_off = 'selected' if frame['light_status'] == 'off' else ''
        selected_left = 'selected' if frame['light_status'] == 'left' else ''
        selected_right = 'selected' if frame['light_status'] == 'right' else ''
        selected_both = 'selected' if frame['light_status'] == 'both' else ''
        
        frame_html += '<div class="frame">'
        frame_html += f'<div class="frame-header">'
        frame_html += f'<span class="frame-number">Frame {i+1}</span>'
        frame_html += f'<button type="button" class="frame-delete" onclick="delFrame(\'{effect_name}\',{i})">✗</button>'
        frame_html += '</div>'
        frame_html += f'<div class="frame-controls">'
        frame_html += f'<div class="form-group">'
        frame_html += f'<label>Light Status</label>'
        frame_html += f'<select name="{effect_name}_frame_{i}_light_status" onchange="updatePreview(\'{effect_name}\')">'
        frame_html += f'<option value="off" {selected_off}>💡 Off</option>'
        frame_html += f'<option value="left" {selected_left}>← Left</option>'
        frame_html += f'<option value="right" {selected_right}>Right →</option>'
        frame_html += f'<option value="both" {selected_both}>↔ Both</option>'
        frame_html += '</select>'
        frame_html += '</div>'
        frame_html += f'<div class="form-group">'
        frame_html += f'<label>Duration (ms)</label>'
        frame_html += f'<input type="number" name="{effect_name}_frame_{i}_duration" value="{frame["duration"]}" min="10" max="5000" onchange="updatePreview(\'{effect_name}\')">'
        frame_html += '</div>'
        frame_html += '</div>'
        frame_html += '</div>'
    return frame_html

def load_file(filename):
    try:
        # Run garbage collection before file operations
        gc.collect()
        with open(filename, 'r') as f:
            content = f.read()
        gc.collect()  # GC after reading file
        return content
    except Exception as e:
        print(f"Error loading file {filename}: {e}")
        return None

def get_content_type(filename):
    if filename.endswith('.html'):
        return 'text/html'
    elif filename.endswith('.css'):
        return 'text/css'
    elif filename.endswith('.js'):
        return 'application/javascript'
    else:
        return 'text/plain'

def get_main_page():
    try:
        # Run garbage collection before heavy operations
        gc.collect()
        
        # Load the HTML template
        html_template = load_file('index.html')
        if not html_template:
            return get_fallback_page()
        
        # Run GC after loading template
        gc.collect()
        
        # Generate frame HTML
        left_frames_html = get_frame_html('left_effect', settings['left_effect']['frames'])
        gc.collect()  # GC after generating left frames
        
        right_frames_html = get_frame_html('right_effect', settings['right_effect']['frames'])
        gc.collect()  # GC after generating right frames
        
        # Prepare template variables
        wifi_ssid = settings.get('wifi_ssid', 'BlinkerController')
        wifi_password = settings.get('wifi_password', 'blinker123')
        blink_speed = str(settings.get('blink_speed', 500))
        hazard_status = 'ON' if hazard_mode else 'OFF'
        effect_status = 'ON' if effect_playing else 'OFF'
        
        # Status indicators
        hazard_indicator = 'active' if hazard_mode else ''
        effect_indicator = 'active' if effect_playing else ''
        
        # Replace template variables using simple string replacement
        html = html_template.replace('{{wifi_ssid}}', wifi_ssid)
        html = html.replace('{{wifi_password}}', wifi_password)
        html = html.replace('{{blink_speed}}', blink_speed)
        html = html.replace('{{left_frames_html}}', left_frames_html)
        html = html.replace('{{right_frames_html}}', right_frames_html)
        html = html.replace('{{hazard_status}}', hazard_status)
        html = html.replace('{{effect_status}}', effect_status)
        html = html.replace('{{hazard_indicator}}', hazard_indicator)
        html = html.replace('{{effect_indicator}}', effect_indicator)
        
        # Clean up temporary variables to free memory
        del html_template, left_frames_html, right_frames_html
        gc.collect()
        
        return html
        
    except Exception as e:
        print(f"Error generating main page: {e}")
        return get_fallback_page()

def get_fallback_page():
    """Simple fallback page when main template fails"""
    wifi_ssid = settings.get('wifi_ssid', 'BlinkerController')
    wifi_password = settings.get('wifi_password', 'blinker123')
    blink_speed = settings.get('blink_speed', 500)
    hazard_status = 'ON' if hazard_mode else 'OFF'
    effect_status = 'ON' if effect_playing else 'OFF'
    
    html = '<html><head><meta name="viewport" content="width=device-width,initial-scale=1"><title>Blinker Controller</title></head><body>'
    html += '<h1>🚗 Blinker Controller</h1>'
    html += '<h2>Test Relays</h2>'
    html += '<button onmousedown="testRelay(\'left\',\'start\')" onmouseup="testRelay(\'left\',\'stop\')">Left</button> '
    html += '<button onmousedown="testRelay(\'right\',\'start\')" onmouseup="testRelay(\'right\',\'stop\')">Right</button> '
    html += '<button onmousedown="testRelay(\'both\',\'start\')" onmouseup="testRelay(\'both\',\'stop\')">Both</button>'
    html += f'<p>Status: Hazard={hazard_status}, Effect={effect_status}</p>'
    html += '<script>function testRelay(s,a){if(a=="start")fetch("/relay_control?left="+(s=="left"||s=="both"?"1":"0")+"&right="+(s=="right"||s=="both"?"1":"0"));else fetch("/relay_control?left=0&right=0");}</script>'
    html += '</body></html>'
    
    return html

def parse_form_data(data):
    params = {}
    pairs = data.split('&')
    for pair in pairs:
        if '=' in pair:
            key, value = pair.split('=', 1)
            # URL decode
            value = value.replace('+', ' ')
            value = value.replace('%20', ' ')
            params[key] = value
    return params

def update_frames_from_params(effect_name, params):
    frames = []
    frame_index = 0
    
    while True:
        light_status_key = f"{effect_name}_frame_{frame_index}_light_status"
        duration_key = f"{effect_name}_frame_{frame_index}_duration"
        
        if light_status_key not in params or duration_key not in params:
            break
        
        try:
            frame = {
                'light_status': params[light_status_key],
                'duration': int(params[duration_key])
            }
            
            # Validate frame
            if frame['light_status'] in ['off', 'left', 'right', 'both'] and frame['duration'] >= 10:
                frames.append(frame)
        except ValueError:
            pass  # Skip invalid frames
        
        frame_index += 1
    
    # Update settings with new frames (ensure at least one frame exists)
    if frames:
        settings[effect_name]['frames'] = frames
        print(f"Updated {effect_name} with {len(frames)} frames")

def start_web_server():
    addr = socket.getaddrinfo('0.0.0.0', 80)[0][-1]
    server_socket = socket.socket()
    server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server_socket.bind(addr)
    server_socket.listen(1)
    server_socket.settimeout(0.1)  # Very short timeout for non-blocking
    
    print('Web server started on port 80')
    return server_socket

def handle_web_request(server_socket, ap):
    try:
        client_socket, addr = server_socket.accept()
        client_socket.settimeout(0.5)  # Short timeout
        
        try:
            request = client_socket.recv(512).decode('utf-8')  # Smaller buffer
            
            # Handle captive portal detection
            if ('detectportal' in request or 'generate_204' in request or 
                'hotspot-detect' in request or 'connectivity-check' in request or
                'connecttest' in request):
                # Respond with redirect for captive portal detection
                redirect_response = 'HTTP/1.1 302 Found\r\nLocation: http://192.168.4.1/\r\nContent-Length: 0\r\n\r\n'
                client_socket.send(redirect_response.encode('utf-8'))
                return
            
            # Check Host header for captive portal redirect
            host_header = ''
            for line in request.split('\r\n'):
                if line.lower().startswith('host:'):
                    host_header = line.split(':', 1)[1].strip().lower()
                    break
            
            # If accessing via different hostname, redirect to AP IP
            if host_header and host_header not in ['192.168.4.1', '192.168.4.1:80']:
                redirect_response = 'HTTP/1.1 302 Found\r\nLocation: http://192.168.4.1/\r\nContent-Length: 0\r\n\r\n'
                client_socket.send(redirect_response.encode('utf-8'))
                return
            
            if 'GET /relay_control' in request:
                # Handle relay control for testing
                query_start = request.find('?')
                if query_start != -1:
                    query_end = request.find(' ', query_start)
                    query_string = request[query_start+1:query_end]
                    params = parse_form_data(query_string)
                    
                    left_state = params.get('left') == '1' if 'left' in params else None
                    right_state = params.get('right') == '1' if 'right' in params else None
                    manual_relay_override(left_state, right_state)
                
                # Send minimal response for relay control
                client_socket.send('HTTP/1.1 200 OK\r\n\r\nOK'.encode('utf-8'))
                
            elif 'POST /config' in request:
                # Handle form submission
                content_length = 0
                lines = request.split('\r\n')
                for line in lines:
                    if line.startswith('Content-Length:'):
                        content_length = int(line.split(': ')[1])
                        break
                
                if content_length > 0 and content_length < 2048:  # Limit body size
                    body = request.split('\r\n\r\n', 1)[1]
                    if len(body) < content_length:
                        try:
                            remaining = client_socket.recv(content_length - len(body)).decode('utf-8')
                            body += remaining
                        except:
                            pass  # Continue with partial data
                    
                    params = parse_form_data(body)
                    
                    # Handle WiFi settings with instant update
                    wifi_updated = False
                    if 'wifi_ssid' in params and len(params['wifi_ssid']) < 32:
                        new_ssid = params['wifi_ssid']
                        if new_ssid != settings['wifi_ssid']:
                            wifi_updated = True
                    else:
                        new_ssid = settings['wifi_ssid']
                        
                    if 'wifi_password' in params and len(params['wifi_password']) < 64:
                        new_password = params['wifi_password']
                        if new_password != settings['wifi_password']:
                            wifi_updated = True
                    else:
                        new_password = settings['wifi_password']
                    
                    # Update WiFi instantly if changed
                    if wifi_updated:
                        update_wifi_settings(new_ssid, new_password, ap)
                    
                    # Update other settings
                    if 'blink_speed' in params:
                        try:
                            settings['blink_speed'] = max(100, min(2000, int(params['blink_speed'])))
                        except:
                            pass
                    
                    # Parse and update frame data
                    update_frames_from_params('left_effect', params)
                    update_frames_from_params('right_effect', params)
                    
                    save_settings()
                
                # Send simple redirect
                client_socket.send('HTTP/1.1 302 Found\r\nLocation: /\r\n\r\n'.encode('utf-8'))
                
            elif 'GET /style.css' in request:
                # Serve CSS file
                try:
                    gc.collect()  # GC before loading CSS
                    css_content = load_file('style.css')
                    if css_content:
                        response = 'HTTP/1.1 200 OK\r\nContent-Type: text/css\r\nConnection: close\r\n\r\n'
                        response += css_content
                        client_socket.send(response.encode('utf-8'))
                        del css_content  # Clean up
                        gc.collect()
                    else:
                        client_socket.send('HTTP/1.1 404 Not Found\r\n\r\nCSS not found'.encode('utf-8'))
                except:
                    client_socket.send('HTTP/1.1 500 Error\r\n\r\nCSS Error'.encode('utf-8'))
            
            elif 'GET /app.js' in request:
                # Serve JavaScript file
                try:
                    gc.collect()  # GC before loading JS
                    js_content = load_file('app.js')
                    if js_content:
                        response = 'HTTP/1.1 200 OK\r\nContent-Type: application/javascript\r\nConnection: close\r\n\r\n'
                        response += js_content
                        client_socket.send(response.encode('utf-8'))
                        del js_content  # Clean up
                        gc.collect()
                    else:
                        client_socket.send('HTTP/1.1 404 Not Found\r\n\r\nJS not found'.encode('utf-8'))
                except:
                    client_socket.send('HTTP/1.1 500 Error\r\n\r\nJS Error'.encode('utf-8'))
            
            elif 'GET /trigger_effect' in request:
                # Handle effect trigger from dashboard
                query_start = request.find('?')
                if query_start != -1:
                    query_end = request.find(' ', query_start)
                    query_string = request[query_start+1:query_end]
                    params = parse_form_data(query_string)
                    
                    side = params.get('side')
                    if side in ['left', 'right'] and not hazard_mode and not effect_playing:
                        # Trigger the effect
                        start_effect(side + '_effect')
                        client_socket.send('HTTP/1.1 200 OK\r\n\r\nOK'.encode('utf-8'))
                    else:
                        client_socket.send('HTTP/1.1 409 Conflict\r\n\r\nEffect blocked'.encode('utf-8'))
                else:
                    client_socket.send('HTTP/1.1 400 Bad Request\r\n\r\nMissing side parameter'.encode('utf-8'))
            
            else:
                # Send main page response
                try:
                    gc.collect()  # GC before generating main page
                    html_content = get_main_page()
                    response = 'HTTP/1.1 200 OK\r\nContent-Type: text/html\r\nConnection: close\r\n\r\n'
                    response += html_content
                    
                    client_socket.send(response.encode('utf-8'))
                    del html_content  # Clean up
                    gc.collect()
                except:
                    # Fallback simple page
                    simple_response = 'HTTP/1.1 200 OK\r\nContent-Type: text/html\r\n\r\n<html><body><h1>Blinker Controller</h1><p>Simple mode active</p></body></html>'
                    client_socket.send(simple_response.encode('utf-8'))
            
        except Exception as e:
            # Send error response
            try:
                client_socket.send('HTTP/1.1 500 Error\r\n\r\nError'.encode('utf-8'))
                print(f"Web request error: {e}")
            except:
                pass
        finally:
            try:
                client_socket.close()
            except:
                pass
                
    except OSError as e:
        # This is expected when no connection is available (timeout)
        if e.errno != 116:  # Only print non-timeout errors
            print(f"Web server error: {e}")
    except Exception as e:
        print(f"Web request error: {e}")

def init_hardware():
    left_relay.value(1)
    right_relay.value(1)
    print("Hardware initialized")

def main():
    print("ESP32-C3 Blinker Controller starting...")
    load_settings()
    init_hardware()
    
    # WiFi services initially disabled - will be enabled on button combination
    ap = None
    dns_server = None
    web_server = None
    ap_ip = None
    
    print("System ready! Hold both effect buttons for 3 seconds to enable WiFi dashboard")
    
    loop_count = 0
    gc_count = 0
    wifi_check_count = 0
    
    # Main control loop
    while True:
        try:
            # Handle button inputs (includes WiFi activation logic)
            handle_buttons()
            
            # Update blinker patterns
            update_blinkers()
            
            # Check for WiFi services startup
            if wifi_enabled and not ap:
                print("📶 Starting WiFi services...")
                ap = setup_access_point()
                if ap:
                    ap_ip = ap.ifconfig()[0]
                    dns_server = start_dns_server(ap_ip)
                    web_server = start_web_server()
                    if web_server:
                        print("📶 WiFi dashboard ready!")
                    else:
                        print("📶 Failed to start web server")
                else:
                    print("📶 Failed to start access point")
            
            # Handle web requests if services are running
            if wifi_enabled and web_server and ap:
                handle_web_request(web_server, ap)
                if dns_server:
                    handle_dns_request(dns_server, ap_ip)
            
            # Check WiFi timeout periodically (every 100 loops ~1 second)
            wifi_check_count += 1
            if wifi_check_count >= 100:
                if check_wifi_timeout():
                    # WiFi was disabled due to timeout - clean up services
                    disable_wifi_services(ap, web_server, dns_server)
                    ap = None
                    web_server = None
                    dns_server = None
                    ap_ip = None
                wifi_check_count = 0
            
            # Periodic garbage collection (every 1000 loops)
            gc_count += 1
            if gc_count >= 1000:
                gc.collect()
                gc_count = 0
            
            # Short delay for responsiveness
            time.sleep_ms(10)
            
            loop_count += 1
            if loop_count >= 5000:  # Print status every ~50 seconds
                wifi_status = "ON" if wifi_enabled else "OFF"
                print(f"System running: Hazard={hazard_mode}, Effect={effect_playing}, WiFi={wifi_status}")
                loop_count = 0
            
        except KeyboardInterrupt:
            print("Shutting down...")
            set_both_relays(False)
            disable_wifi_services(ap, web_server, dns_server)
            break
        except Exception as e:
            print(f"Error in main loop: {e}")
            # Don't sleep long on errors to keep system responsive
            time.sleep_ms(100)

if __name__ == '__main__':
    main()
