try:
    import cv2
    CV2_AVAILABLE = True
except ImportError:
    CV2_AVAILABLE = False
    class MockCv2:
        # Constants
        COLOR_BGR2GRAY = 6
        RETR_TREE = 3
        CHAIN_APPROX_SIMPLE = 2
        LINE_AA = 16
        FONT_HERSHEY_SIMPLEX = 0
        CAP_PROP_FRAME_WIDTH = 3
        CAP_PROP_FRAME_HEIGHT = 4
        THRESH_BINARY = 0
        THRESH_OTSU = 8
        
        def __getattr__(self, name):
            return lambda *args, **kwargs: None
    cv2 = MockCv2()
    print("[WARNING] OpenCV (cv2) is not installed. GUI camera display will be unavailable.")
import requests
import json
import time
import os
import random
import threading
from datetime import datetime

# Load local .env variables if python-dotenv is installed
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# Try to import EasyOCR, handle ImportError gracefully with demo fallback
try:
    import easyocr
    import numpy as np
    EASYOCR_AVAILABLE = True
    print("[INFO] EasyOCR loaded successfully.")
except ImportError:
    EASYOCR_AVAILABLE = False
    print("[WARNING] EasyOCR or NumPy is not installed. Script will run in MOCK/DEMO mode.")
    print("[WARNING] To enable real OCR, run: pip install easyocr numpy opencv-python requests")

# Configuration
API_URL_ENTRY = "https://automated-parking-mangment-system.onrender.com/api/entry"
API_URL_EXIT = "https://automated-parking-mangment-system.onrender.com/api/exit"
API_KEY = os.getenv("PARKING_API_KEY", "secret_parking_key_2026")
QUEUE_FILE = "offline_queue.json"
queue_lock = threading.Lock()

def load_offline_queue():
    if not os.path.exists(QUEUE_FILE):
        return []
    try:
        with open(QUEUE_FILE, "r") as f:
            return json.load(f)
    except Exception as e:
        print(f"[QUEUE] Error reading queue file: {e}")
        return []

def save_offline_queue(queue):
    try:
        with open(QUEUE_FILE, "w") as f:
            json.dump(queue, f, indent=2)
    except Exception as e:
        print(f"[QUEUE] Error writing queue file: {e}")

def queue_offline_event(plate, mode):
    with queue_lock:
        queue = load_offline_queue()
        queue.append({
            "license_plate": plate,
            "gate_mode": mode,
            "timestamp": datetime.now().isoformat()
        })
        save_offline_queue(queue)


# State Variables
gate_mode = "ENTRY"  # ENTRY or EXIT
auto_mode = False    # True = auto-scan plate when contour detected, False = spacebar trigger
last_plate = "N/A"
api_status_msg = "Ready to Scan"
api_status_color = (255, 255, 255)  # White
barrier_open_until = 0  # Timestamp to keep barrier open display active
detected_plate_crop = None
debug_crop_visible = True

# Initialize EasyOCR Reader if available
reader = None
if EASYOCR_AVAILABLE:
    try:
        # Load English model (will download files on first run, ~30MB)
        reader = easyocr.Reader(['en'], gpu=False)
        print("[INFO] EasyOCR Reader initialized.")
    except Exception as e:
        print(f"[ERROR] Could not initialize EasyOCR Reader: {e}. Falling back to MOCK mode.")
        EASYOCR_AVAILABLE = False

# Mock data for demonstration when no camera is present or OCR is mocked
MOCK_PLATES = ["MH12QW1234", "DL3CAY9876", "KA03MM4567", "HR26BC5678", "UP16AT4321", "VIP0001", "MALL9999"]

def preprocess_plate_image(img):
    """Apply grayscale, blurring, and Otsu's thresholding to optimize OCR read."""
    if img is None or img.size == 0:
        return None
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    # Apply bilateral filter to remove noise while keeping edges sharp
    filtered = cv2.bilateralFilter(gray, 11, 17, 17)
    # Apply thresholding to get high-contrast binary image
    thresh = cv2.threshold(filtered, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)[1]
    return thresh

def detect_plate_contour(frame):
    """
    Step 1: AI Pipeline Optimization - Detect candidate license plate rectangular contour.
    Returns: Bounding box coordinates (x, y, w, h) and the cropped frame.
    """
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    # Noise reduction
    blur = cv2.bilateralFilter(gray, 11, 17, 17)
    # Find edges
    edged = cv2.Canny(blur, 30, 200)
    
    # Find contours
    contours, _ = cv2.findContours(edged.copy(), cv2.RETR_TREE, cv2.CHAIN_APPROX_SIMPLE)
    # Sort contours by area, take top 10
    contours = sorted(contours, key=cv2.contourArea, reverse=True)[:10]
    
    for contour in contours:
        # Approximate contour perimeter
        perimeter = cv2.arcLength(contour, True)
        approx = cv2.approxPolyDP(contour, 0.02 * perimeter, True)
        
        # Check if the contour has 4 vertices (quadrilateral)
        if len(approx) == 4:
            x, y, w, h = cv2.boundingRect(contour)
            aspect_ratio = float(w) / h
            
            # Standard license plate ratio is approx 3:1 to 5:1 (Europe/India/US standard sizes)
            if 2.0 <= aspect_ratio <= 5.5 and w > 80:
                # Return bounding box and crop
                return (x, y, w, h), approx
                
    return None, None

def call_parking_api(plate_number):
    """Sends the detected plate to the FastAPI gateway."""
    global api_status_msg, api_status_color, barrier_open_until
    
    url = API_URL_ENTRY if gate_mode == "ENTRY" else API_URL_EXIT
    headers = {
        "X-API-Key": API_KEY,
        "Content-Type": "application/json"
    }
    payload = {"license_plate": plate_number}
    
    print(f"[API] Sending {gate_mode} request for plate: {plate_number}...")
    
    try:
        response = requests.post(url, headers=headers, json=payload, timeout=5)
        res_data = response.json()
        
        if response.status_code == 200:
            if gate_mode == "ENTRY":
                if res_data.get("status") == "already_parked":
                    api_status_msg = f"Already Parked: {res_data.get('slot_name')}"
                    api_status_color = (0, 165, 255)  # Orange
                else:
                    slot = res_data.get("slot_name")
                    api_status_msg = f"BARRIER OPEN! Slot: {slot}"
                    api_status_color = (0, 255, 0)  # Green
                    barrier_open_until = time.time() + 4.0  # Open for 4 seconds
            else: # EXIT
                slot = res_data.get("slot_name")
                fee = res_data.get("amount_paid")
                duration = res_data.get("duration_minutes")
                api_status_msg = f"EXITED {slot}! Fee: INR {fee:.2f} ({duration}m)"
                api_status_color = (0, 255, 0)  # Green
                barrier_open_until = time.time() + 4.0
        elif response.status_code == 403:
            # Lot Full Edge Case
            api_status_msg = "GATE CLOSED: LOT FULL!"
            api_status_color = (0, 0, 255)  # Red
        else:
            api_status_msg = f"Error: {res_data.get('detail', 'Unknown error')}"
            api_status_color = (0, 0, 255)  # Red
            
    except requests.exceptions.RequestException as e:
        print(f"[API ERROR] HTTP Request failed: {e}")
        # Queue the offline entry or exit log locally
        queue_offline_event(plate_number, gate_mode)
        # Emergency failover: open barrier and indicate bypass HUD state
        api_status_msg = "OFFLINE BYPASS: GATE OPEN"
        api_status_color = (0, 165, 255)  # Orange/Yellow indicator for fallback override
        barrier_open_until = time.time() + 4.0
        print(f"[OFFLINE] Logged plate {plate_number} ({gate_mode}) to offline queue. Gate override activated.")


def run_anpr_pipeline(frame, plate_coords):
    """
    Step 2: AI Pipeline Optimization - Crop, preprocess, and run OCR on the plate region.
    """
    global last_plate, detected_plate_crop
    
    if not EASYOCR_AVAILABLE or reader is None:
        # Mock OCR behavior
        mock_plate = random.choice(MOCK_PLATES)
        last_plate = mock_plate
        print(f"[MOCK OCR] Recognized plate: {mock_plate}")
        call_parking_api(mock_plate)
        return
        
    x, y, w, h = plate_coords
    # Add a small padding around the cropped plate
    padding = 5
    y1 = max(0, y - padding)
    y2 = min(frame.shape[0], y + h + padding)
    x1 = max(0, x - padding)
    x2 = min(frame.shape[1], x + w + padding)
    
    raw_crop = frame[y1:y2, x1:x2]
    processed_crop = preprocess_plate_image(raw_crop)
    
    if processed_crop is not None:
        detected_plate_crop = processed_crop
        
        # Run EasyOCR on the small cropped plate frame
        t0 = time.time()
        results = reader.readtext(processed_crop)
        ocr_time = (time.time() - t0) * 1000
        print(f"[ANPR] OCR processed in {ocr_time:.1f}ms")
        
        if results:
            # Sort OCR results by confidence, pick highest text
            results = sorted(results, key=lambda x: x[2], reverse=True)
            plate_text = results[0][1]
            
            # Clean plate text (alphanumeric only, uppercase)
            cleaned_text = "".join(c for c in plate_text if c.isalnum()).upper()
            
            if len(cleaned_text) >= 4:
                last_plate = cleaned_text
                print(f"[ANPR] Recognized Plate: {cleaned_text}")
                call_parking_api(cleaned_text)
                return
        
        print("[ANPR] Plate contour found, but OCR failed to extract text.")
        api_status_msg = "OCR Failed! Try again or Manual Override"
        api_status_color = (0, 165, 255)  # Orange

def sync_worker():
    """Background daemon thread to periodically sync offline events when backend returns."""
    print("[SYNC] Offline sync worker daemon started.")
    while True:
        # Check and sync every 15 seconds
        time.sleep(15)
        
        with queue_lock:
            queue = load_offline_queue()
            
        if not queue:
            continue
            
        print(f"[SYNC] Found {len(queue)} offline events. Attempting to sync...")
        
        synced_count = 0
        for event in list(queue):
            plate = event["license_plate"]
            mode = event["gate_mode"]
            timestamp = event["timestamp"]
            
            url = API_URL_ENTRY if mode == "ENTRY" else API_URL_EXIT
            headers = {
                "X-API-Key": API_KEY,
                "Content-Type": "application/json"
            }
            # The server expects timestamp
            payload = {"license_plate": plate, "timestamp": timestamp}
            if mode == "ENTRY":
                payload["vehicle_type"] = "STANDARD"
                
            try:
                response = requests.post(url, headers=headers, json=payload, timeout=5)
                # If the request succeeds (returns any response, even status error other than network error),
                # we consider it processed/rejected, so we pop it.
                with queue_lock:
                    current_queue = load_offline_queue()
                    # Verify first item matches to prevent race condition modifications
                    if current_queue and current_queue[0]["timestamp"] == timestamp and current_queue[0]["license_plate"] == plate:
                        current_queue.pop(0)
                        save_offline_queue(current_queue)
                synced_count += 1
                print(f"[SYNC] Successfully replayed and synced plate {plate} ({mode}). Status: {response.status_code}")
            except requests.exceptions.RequestException as conn_err:
                print(f"[SYNC] Connection still down, aborting sync: {conn_err}")
                break
                
        if synced_count > 0:
            print(f"[SYNC] Replayed and synchronized {synced_count} events with backend.")

def main():
    global gate_mode, auto_mode, last_plate, api_status_msg, api_status_color, detected_plate_crop
    
    # Start background sync thread
    sync_thread = threading.Thread(target=sync_worker, daemon=True)
    sync_thread.start()

    
    # Try opening webcam (0 is default built-in camera)
    cap = cv2.VideoCapture(0)
    
    # Check if webcam opened successfully
    webcam_active = cap.isOpened()
    if not webcam_active:
        print("[WARNING] No active webcam found. Simulating camera feed using mock canvas.")
        # Create a black frame for mock camera representation
        frame_width, frame_height = 800, 600
    else:
        frame_width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        frame_height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        print(f"[CAMERA] Feed active at {frame_width}x{frame_height}")

    print("\n--- Parking Camera ANPR Console Interface ---")
    print("Keys:")
    print("  [SPACE] - Scan/Trigger OCR on current frame")
    print("  [E]     - Switch to ENTRY Gate Mode")
    print("  [X]     - Switch to EXIT Gate Mode")
    print("  [A]     - Toggle Auto-OCR Scan Mode")
    print("  [D]     - Toggle cropped plate view")
    print("  [M]     - Mock plate trigger (Manually input text in terminal)")
    print("  [ESC/Q] - Exit application\n")

    # Time tracking for Auto-scan intervals
    last_auto_scan = 0

    while True:
        if webcam_active:
            ret, frame = cap.read()
            if not ret:
                print("[ERROR] Failed to grab frame.")
                break
        else:
            # Generate a mock animated background if no camera is available
            frame = np.zeros((600, 800, 3), dtype=np.uint8)
            # Draw simulation environment
            cv2.rectangle(frame, (50, 50), (750, 550), (20, 20, 20), -1)
            # Add grid lines
            for i in range(100, 800, 100):
                cv2.line(frame, (i, 50), (i, 550), (30, 30, 30), 1)
            # Draw a mock license plate outline in the center
            plate_center_x, plate_center_y = 400, 300
            cv2.rectangle(frame, (plate_center_x - 180, plate_center_y - 45), (plate_center_x + 180, plate_center_y + 45), (200, 200, 200), -1)
            cv2.rectangle(frame, (plate_center_x - 175, plate_center_y - 40), (plate_center_x + 175, plate_center_y + 40), (10, 10, 10), -1)
            # Display simulated plate text
            cv2.putText(frame, "MH 12 QP 5678", (plate_center_x - 145, plate_center_y + 15), 
                        cv2.FONT_HERSHEY_SIMPLEX, 1.3, (255, 255, 255), 3, cv2.LINE_AA)
            cv2.putText(frame, "IND", (plate_center_x - 170, plate_center_y - 15), 
                        cv2.FONT_HERSHEY_SIMPLEX, 0.4, (0, 0, 255), 1, cv2.LINE_AA)

        # Mirror frame for natural camera feel if webcam
        if webcam_active:
            frame = cv2.flip(frame, 1)

        # Detect license plate location (Step 1 of Optimized Pipeline)
        bbox, approx = detect_plate_contour(frame)
        
        # Draw bounding boxes if plate detected
        if bbox:
            x, y, w, h = bbox
            cv2.rectangle(frame, (x, y), (x + w, y + h), (0, 255, 255), 2)
            cv2.putText(frame, "PLATE DETECTED", (x, y - 8), 
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 255), 1, cv2.LINE_AA)
            
            # Auto-Scan Logic
            if auto_mode and (time.time() - last_auto_scan > 5.0):
                run_anpr_pipeline(frame, bbox)
                last_auto_scan = time.time()

        # Check barrier timing to toggle gate indicator
        barrier_active = time.time() < barrier_open_until

        # Draw HUD overlays on frame
        # Header banner
        hud_bg = frame.copy()
        cv2.rectangle(hud_bg, (0, 0), (frame.shape[1], 80), (15, 15, 15), -1)
        # Transparent overlay
        cv2.addWeighted(hud_bg, 0.7, frame, 0.3, 0, frame)

        # Title
        cv2.putText(frame, "SMART PARKING CAMERA GATE INTEGRATION", (15, 30), 
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2, cv2.LINE_AA)
        
        # Mode Status
        mode_text = f"GATE MODE: {gate_mode}"
        mode_color = (0, 255, 0) if gate_mode == "ENTRY" else (0, 200, 255)
        cv2.putText(frame, mode_text, (15, 58), 
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, mode_color, 2, cv2.LINE_AA)

        # Auto Mode status
        auto_text = f"SCAN TRIGGER: {'AUTO' if auto_mode else 'MANUAL (SPACE)'}"
        cv2.putText(frame, auto_text, (220, 58), 
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (250, 250, 250), 1, cv2.LINE_AA)

        # API & OCR HUD info
        cv2.rectangle(frame, (10, frame.shape[0] - 110), (320, frame.shape[0] - 10), (10, 10, 10), -1)
        cv2.rectangle(frame, (10, frame.shape[0] - 110), (320, frame.shape[0] - 10), (50, 50, 50), 1)
        cv2.putText(frame, f"Last Plate: {last_plate}", (20, frame.shape[0] - 85), 
                    cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 0), 2, cv2.LINE_AA)
        cv2.putText(frame, f"API Key: Loaded", (20, frame.shape[0] - 60), 
                    cv2.FONT_HERSHEY_SIMPLEX, 0.45, (200, 200, 200), 1, cv2.LINE_AA)
        cv2.putText(frame, f"OCR Engine: {'EasyOCR' if EASYOCR_AVAILABLE else 'MOCK DEVISE'}", 
                    (20, frame.shape[0] - 35), cv2.FONT_HERSHEY_SIMPLEX, 0.45, 
                    (0, 255, 0) if EASYOCR_AVAILABLE else (0, 165, 255), 1, cv2.LINE_AA)

        # Draw Gate Barrier Visual State
        barrier_text = "BARRIER STATE: OPEN" if barrier_active else "BARRIER STATE: CLOSED"
        barrier_color = (0, 255, 0) if barrier_active else (0, 0, 255)
        cv2.rectangle(frame, (frame.shape[1] - 310, frame.shape[0] - 110), (frame.shape[1] - 10, frame.shape[0] - 10), (10, 10, 10), -1)
        cv2.rectangle(frame, (frame.shape[1] - 310, frame.shape[0] - 110), (frame.shape[1] - 10, frame.shape[0] - 10), (50, 50, 50), 1)
        cv2.putText(frame, barrier_text, (frame.shape[1] - 295, frame.shape[0] - 80), 
                    cv2.FONT_HERSHEY_SIMPLEX, 0.55, barrier_color, 2, cv2.LINE_AA)
        
        # Display API Log Message
        cv2.putText(frame, f"Status: {api_status_msg}", (frame.shape[1] - 295, frame.shape[0] - 40), 
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, api_status_color, 1, cv2.LINE_AA)

        # Draw OCR cropped debug view if available
        if debug_crop_visible and detected_plate_crop is not None:
            # Display cropped plate in corner
            crop_h, crop_w = detected_plate_crop.shape[:2]
            if crop_h > 0 and crop_w > 0:
                # Resize for display
                disp_w = 200
                disp_h = int((crop_h / crop_w) * disp_w)
                resized_crop = cv2.resize(detected_plate_crop, (disp_w, disp_h))
                
                # Make it 3 channels if grayscale
                if len(resized_crop.shape) == 2:
                    resized_crop = cv2.cvtColor(resized_crop, cv2.COLOR_GRAY2BGR)
                
                # Overlay
                cy = 100
                cx = frame.shape[1] - 220
                cv2.rectangle(frame, (cx-5, cy-25), (cx+disp_w+5, cy+disp_h+5), (20, 20, 20), -1)
                cv2.putText(frame, "OCR CROP INPUT", (cx, cy-10), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (255, 255, 255), 1)
                frame[cy:cy+disp_h, cx:cx+disp_w] = resized_crop

        # Display the frame
        cv2.imshow("Parking ANPR Camera Integration Simulator", frame)

        # Keyboard event handler
        key = cv2.waitKey(1) & 0xFF
        
        # SPACE - Scan / Trigger OCR manually
        if key == ord(" "):
            if bbox:
                print("[ANPR] Manual OCR scan triggered on detected plate contour...")
                run_anpr_pipeline(frame, bbox)
            else:
                print("[ANPR] Manual OCR scan requested. No plate contour detected. Scanning whole center region...")
                # Scan standard crop area in the center of the frame
                h, w = frame.shape[:2]
                cw, ch = 360, 90
                cx, cy = (w - cw) // 2, (h - ch) // 2
                run_anpr_pipeline(frame, (cx, cy, cw, ch))

        # E - Entry Mode
        elif key == ord("e") or key == ord("E"):
            gate_mode = "ENTRY"
            api_status_msg = "Switched to ENTRY camera"
            api_status_color = (255, 255, 255)
            print("[CAMERA] Switched to ENTRY Gate mode.")

        # X - Exit Mode
        elif key == ord("x") or key == ord("X"):
            gate_mode = "EXIT"
            api_status_msg = "Switched to EXIT camera"
            api_status_color = (255, 255, 255)
            print("[CAMERA] Switched to EXIT Gate mode.")

        # A - Toggle Auto-scan
        elif key == ord("a") or key == ord("A"):
            auto_mode = not auto_mode
            print(f"[CAMERA] Auto Scan mode: {auto_mode}")

        # D - Toggle debug crop
        elif key == ord("d") or key == ord("D"):
            debug_crop_visible = not debug_crop_visible

        # M - Mock Plate Console Trigger
        elif key == ord("m") or key == ord("M"):
            mock_plate = input("Enter License Plate Number manually: ").strip().upper()
            if mock_plate:
                last_plate = mock_plate
                call_parking_api(mock_plate)

        # Q or ESC - Quit
        elif key == ord("q") or key == 27:
            print("[CAMERA] Exiting ANPR integration script.")
            break

    # Clean up
    if webcam_active:
        cap.release()
    cv2.destroyAllWindows()

if __name__ == "__main__":
    main()
