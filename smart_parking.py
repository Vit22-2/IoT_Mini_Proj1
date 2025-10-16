# Smart Parking - ESP32 MicroPython
# Mini Project 1 - 3 Slots, LCD, Servo Gate, Web, Telegram
# --------------------------------------------------------

import network
import usocket as socket
import time
from machine import Pin, PWM, SoftI2C
from machine_i2c_lcd import I2cLcd
import gc
import ssl

# ---------------- CONFIG ----------------
WIFI_SSID = " "
WIFI_PASS = " "

TELEGRAM_BOT_TOKEN = " "
TELEGRAM_CHAT_ID = " "

TRIG_PIN = 27           # Ultrasonic TRIG
ECHO_PIN = 26           # Ultrasonic ECHO
IR_PINS = [14, 16, 17]  # IR sensors for slots S1-S3
SERVO_PIN = 33          # Servo gate
I2C_SDA = 21
I2C_SCL = 22
LCD_I2C_ADDR = 0x27     # check via i2c.scan()

IR_ACTIVE_LOW = True    # True if IR outputs LOW when car present
PRICE_PER_MIN = 0.5

ULTRA_CHECK_INTERVAL = 200      # ms
ULTRA_THRESHOLD_CM = 15.0       # detect car at entry
IR_DEBOUNCE_MS = 150
EXIT_GRACE_MS = 1000            # ms
GATE_OPEN_MS = 4000             # ms gate open duration

SERVO_FREQ = 50
SERVO_OPEN_DUTY = 90
SERVO_CLOSED_DUTY = 40

# ---------------- Utility ----------------
def now_ms(): return time.ticks_ms()
def ticks_diff(a, b): return time.ticks_diff(a, b)

def minutes_rounded_up(duration_ms):
    mins = (duration_ms + 59999) // 60000
    return max(1, mins)

def compute_fee_ms(duration_ms):
    return float(minutes_rounded_up(duration_ms)) * float(PRICE_PER_MIN)

# ---------------- SoftI2C LCD Init ----------------
i2c = SoftI2C(sda=Pin(I2C_SDA), scl=Pin(I2C_SCL), freq=400000)
lcd = I2cLcd(i2c, LCD_I2C_ADDR, 2, 16)

def lcd_show(line1="", line2=""):
    lcd.clear()
    lcd.move_to(0, 0)
    lcd.putstr(line1[:16])
    lcd.move_to(0, 1)
    lcd.putstr(line2[:16])

# ---------------- Time Utilities ----------------
def current_time_str():
    tm = time.localtime()
    return "{:02d}:{:02d}:{:02d}".format(tm[3], tm[4], tm[5])

def format_time_ms(ms):
    s_total = ms // 1000
    hh = s_total // 3600
    mm = (s_total % 3600) // 60
    ss = s_total % 60
    return "{:02d}:{:02d}:{:02d}".format(hh, mm, ss)

# ---------------- Hardware Init ----------------
trig = Pin(TRIG_PIN, Pin.OUT)
echo = Pin(ECHO_PIN, Pin.IN)
ir_pins = [Pin(p, Pin.IN, Pin.PULL_UP) for p in IR_PINS]
servo = PWM(Pin(SERVO_PIN))
servo.freq(SERVO_FREQ)
servo.duty(SERVO_CLOSED_DUTY)

# ---------------- Slot & Ticket State ----------------
class Slot:
    def __init__(self):
        self.occupied = False
        self.assigned_id = 0
        self.time_in_ms = 0
        self.last_raw = False
        self.last_state_change_ms = now_ms()
        self.free_since_ms = 0
        self.time_in_local = None  # store localtime tuple for LCD

slots = [Slot() for _ in range(3)]
id_in_use = [False, False, False, False]  # index 1-3

class Ticket:
    def __init__(self, id_, slot_idx, ti, to=None):
        self.id = id_
        self.slot_index = slot_idx
        self.time_in_ms = ti
        self.time_out_ms = to
        self.closed = False
        self.time_in_local = time.localtime()
        self.time_out_local = None

closed_tickets = []

# ---------------- Servo Control (Fixed) ----------------
def open_gate():
    print("Gate opening...")
    servo.duty(SERVO_OPEN_DUTY)
    time.sleep_ms(300)  # allow servo to move & voltage stabilize
    update_lcd()

def close_gate():
    print("Gate closing...")
    servo.duty(SERVO_CLOSED_DUTY)
    time.sleep_ms(300)
    update_lcd()

# ---------------- IR & Ultrasonic ----------------
def time_pulse_us(pin, value, timeout_us=30000):
    start = time.ticks_us()
    while pin.value() == value:
        if time.ticks_diff(time.ticks_us(), start) > timeout_us: return -2
    start = time.ticks_us()
    while pin.value() != value:
        if time.ticks_diff(time.ticks_us(), start) > timeout_us: return -1
    t0 = time.ticks_us()
    while pin.value() == value:
        if time.ticks_diff(time.ticks_us(), t0) > timeout_us: return -2
    t1 = time.ticks_us()
    return time.ticks_diff(t1, t0)

def read_ultrasonic_cm():
    trig.value(0); time.sleep_us(2); trig.value(1); time.sleep_us(10); trig.value(0)
    pulse = time_pulse_us(echo, 1, 30000)
    if pulse <= 0: return 9999.0
    return (pulse / 2.0) / 29.1

def lowest_available_id():
    for i in range(1, 4):
        if not id_in_use[i]:
            return i
    return 0

def mark_id(id_, used):
    if 1 <= id_ <= 3: id_in_use[id_] = used

# ---------------- LCD Update ----------------
def update_lcd():
    free_slots = [f"S{i+1}" for i, s in enumerate(slots) if not s.occupied]
    if len(free_slots) == 0:
        lcd_show("     FULL", "")
    else:
        lcd_show("Free: " + " ".join(free_slots), "Occ:{} Free:{}".format(3 - len(free_slots), len(free_slots)))

# ---------------- Telegram ----------------
# Percent-encode a string by bytes (safe for UTF-8 / emoji)
def percent_encode(s):
    b = s.encode('utf-8')
    # unreserved characters per RFC 3986
    safe_bytes = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_.~"
    parts = []
    for byte in b:
        if bytes([byte]) in safe_bytes:
            parts.append(chr(byte))
        elif byte == 0x20:  # space
            parts.append('%20')   # form encoding: %20 is fine
        else:
            parts.append('%%%02X' % byte)
    return ''.join(parts)

def send_telegram(text):
    import gc
    gc.collect()
    host = "api.telegram.org"
    path = "/bot{}/sendMessage".format(TELEGRAM_BOT_TOKEN)
    body = "chat_id={}&text={}".format(TELEGRAM_CHAT_ID, percent_encode(text))
    req = (
        "POST {} HTTP/1.1\r\n"
        "Host: {}\r\n"
        "Content-Type: application/x-www-form-urlencoded\r\n"
        "Content-Length: {}\r\n"
        "Connection: close\r\n\r\n"
        "{}"
    ).format(path, host, len(body), body)

    try:
        import socket
        try:
            import ssl
        except:
            ssl = None  # fallback if firmware has no SSL

        print("📤 Sending Telegram message...")
        addr = socket.getaddrinfo(host, 443)[0][-1]
        s = socket.socket()
        s.settimeout(10)
        s.connect(addr)

        if ssl:
            s = ssl.wrap_socket(s)
        else:
            print("⚠️ SSL not available – Telegram send skipped.")
            s.close()
            return False

        s.write(req.encode())
        try:
            s.read(64)  # read small response chunk
        except:
            pass
        s.close()
        print("✅ Telegram message sent OK")
        return True

    except Exception as e:
        print("🚫 Telegram send failed:", e)
        return False

    finally:
        try:
            s.close()
        except:
            pass
        gc.collect()
        
# ---------------- Web Dashboard ----------------
def generate_dashboard_html():
    free_count = sum(1 for s in slots if not s.occupied)
    occ = 3 - free_count

    # ---------- STYLE FIRST ----------
    style = """
    <style>
    body {
        font-family: Arial, sans-serif;
        background: linear-gradient(to bottom right, #e0f7fa, #f1f8e9);
        color: #222;
        margin: 0;
        padding: 20px;
        text-align: center;
    }
    h2 {
        color: #0277bd;
        margin-bottom: 10px;
    }
    .status-bar {
        background: #ffffffcc;
        border-radius: 10px;
        box-shadow: 0 2px 6px rgba(0,0,0,0.2);
        padding: 12px;
        margin-bottom: 20px;
        display: inline-block;
    }
    .slot {
        display: inline-block;
        border-radius: 10px;
        box-shadow: 0 2px 6px rgba(0,0,0,0.2);
        padding: 10px;
        margin: 10px;
        width: 180px;
        text-align: center;
    }
    .slot h3 {
        margin-top: 0;
        color: #01579b;
    }
    .occupied {
        background: #ffebee;
        border: 2px solid #e53935;
    }
    .free {
        background: #e8f5e9;
        border: 2px solid #43a047;
    }
    table {
        margin: 0 auto;
        border-collapse: collapse;
        width: 90%;
        background: #ffffffcc;
        border-radius: 10px;
        overflow: hidden;
        box-shadow: 0 2px 6px rgba(0,0,0,0.2);
    }
    th {
        background-color: #0288d1;
        color: white;
        padding: 8px;
    }
    td {
        padding: 8px;
        border-bottom: 1px solid #ddd;
    }
    tr:nth-child(even) { background-color: #f2f2f2; }
    tr:hover { background-color: #e1f5fe; }
    .footer {
        margin-top: 20px;
        font-size: 13px;
        color: #555;
    }
    </style>
    """

    # ---------- HTML BUILD ----------
    parts = [f"<html><head><meta charset='UTF-8'><meta http-equiv='refresh' content='2'><title>Smart Parking</title>{style}</head><body>"]
    parts.append("<h2>🚗 ESP32 Smart Parking Dashboard</h2>")
    parts.append(
        "<div class='status-bar'>"
        f"<b>Total Slots:</b> 3 &nbsp;&nbsp; "
        f"<b>Free:</b> {free_count} &nbsp;&nbsp; "
        f"<b>Occupied:</b> {occ} &nbsp;&nbsp; "
        f"<b>Status:</b> <span style='color:{'red' if free_count==0 else 'green'};font-weight:bold;'>"
        f"{'FULL' if free_count==0 else 'Available'}</span>"
        "</div>"
    )

    for i, s in enumerate(slots):
        status_class = "occupied" if s.occupied else "free"
        parts.append(f"<div class='slot {status_class}'>")
        parts.append(f"<h3>Slot S{i+1}</h3>")
        if s.occupied:
            elapsed_sec = (now_ms() - s.time_in_ms)//1000
            elapsed_str = "{:02d}:{:02d}:{:02d}".format(elapsed_sec//3600, (elapsed_sec%3600)//60, elapsed_sec%60)
            time_in_str = "{:02d}:{:02d}:{:02d}".format(s.time_in_local[3], s.time_in_local[4], s.time_in_local[5])
            parts.append(
                f"<b>Status:</b> <span style='color:#e53935;'>Occupied</span><br>"
                f"<b>ID:</b> {s.assigned_id}<br>"
                f"<b>Time-In:</b> {time_in_str}<br>"
                f"<b>Elapsed:</b> {elapsed_str}<br>"
            )
        else:
            parts.append("<b>Status:</b> <span style='color:#43a047;'>Free</span>")
        parts.append("</div>")

    parts.append("<h3 style='color:#1565c0;'>Active Tickets</h3>")
    parts.append("<table><tr><th>ID</th><th>Slot</th><th>Time-In</th><th>Elapsed</th></tr>")
    for i, s in enumerate(slots):
        if s.occupied:
            elapsed_sec = (now_ms() - s.time_in_ms)//1000
            elapsed_str = "{:02d}:{:02d}:{:02d}".format(elapsed_sec//3600, (elapsed_sec%3600)//60, elapsed_sec%60)
            time_in_str = "{:02d}:{:02d}:{:02d}".format(s.time_in_local[3], s.time_in_local[4], s.time_in_local[5])
            parts.append(f"<tr><td>{s.assigned_id}</td><td>S{i+1}</td><td>{time_in_str}</td><td>{elapsed_str}</td></tr>")
    parts.append("</table>")

    parts.append("<h3 style='color:#2e7d32;'>Recent Closed Tickets</h3>")
    parts.append("<table><tr><th>ID</th><th>Slot</th><th>Duration</th><th>Fee ($)</th><th>Time-Out</th></tr>")
    for t in closed_tickets[-10:]:
        dur_sec = (t.time_out_ms - t.time_in_ms)//1000
        dur_str = "{:02d}:{:02d}:{:02d}".format(dur_sec//3600, (dur_sec%3600)//60, dur_sec%60)
        time_out_str = "{:02d}:{:02d}:{:02d}".format(t.time_out_local[3], t.time_out_local[4], t.time_out_local[5])
        parts.append(f"<tr><td>{t.id}</td><td>S{t.slot_index+1}</td><td>{dur_str}</td><td>{compute_fee_ms(t.time_out_ms - t.time_in_ms):.2f}</td><td>{time_out_str}</td></tr>")
    parts.append("</table>")

    parts.append("<div class='footer'>Updated automatically every 2 seconds - ESP32 Smart Parking System</div>")
    parts.append("</body></html>")
    return "\n".join(parts)

# ---------------- WiFi & HTTP ----------------
def connect_wifi():
    wlan = network.WLAN(network.STA_IF); wlan.active(True)
    if not wlan.isconnected():
        print("Connecting to WiFi...")
        wlan.connect(WIFI_SSID, WIFI_PASS)
        start = now_ms()
        while not wlan.isconnected():
            if ticks_diff(now_ms(), start) > 15000: break
            time.sleep_ms(300)
    if wlan.isconnected(): print("WiFi OK, IP:", wlan.ifconfig()[0])
    else: print("WiFi not connected")
    return wlan

def start_web_server():
    addr = socket.getaddrinfo('0.0.0.0', 80)[0][-1]
    s = socket.socket(); s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(addr); s.listen(1); s.settimeout(0.5)
    print("HTTP server listening on", addr)
    return s

def handle_http_client(cl):
    try:
        cl_file = cl.makefile('rwb', 0)
        line = cl_file.readline()
        if not line: cl.close(); return
        while True:
            l = cl_file.readline()
            if not l or l == b'\r\n': break
        html = generate_dashboard_html()
        resp = "HTTP/1.0 200 OK\r\nContent-Type: text/html; charset=UTF-8\r\nContent-Length: {}\r\n\r\n{}".format(len(html), html)
        cl.send(resp.encode())
    except Exception as e: print("HTTP error:", e)
    finally:
        try: cl.close()
        except: pass

# ---------------- Main Loop ----------------
def main():
    wlan = connect_wifi()
    srv = None
    if wlan.isconnected():
        try: srv = start_web_server()
        except Exception as e: print("Web start failed:", e); srv = None

    lcd_show("Starting...", ""); time.sleep_ms(400); update_lcd()
    last_ultra_check = now_ms()
    gate_opened = False
    last_gate_open_ms = 0

    for i, p in enumerate(ir_pins):
        raw_pin = p.value()
        raw = (raw_pin == 0) if IR_ACTIVE_LOW else (raw_pin == 1)
        slots[i].last_raw = raw; slots[i].last_state_change_ms = now_ms()

    print("Main loop start")
    while True:
        t = now_ms()
        # HTTP accept
        if srv:
            try: cl, addr = srv.accept(); handle_http_client(cl)
            except OSError: pass
            except Exception: pass

        # Ultrasonic check - gate logic
        if ticks_diff(t, last_ultra_check) >= ULTRA_CHECK_INTERVAL:
            last_ultra_check = t
            try: cm = read_ultrasonic_cm()
            except: cm = 9999.0

            free_id = lowest_available_id()

            # Car detected AND free slot AND gate not opened yet
            if cm < ULTRA_THRESHOLD_CM and free_id != 0 and not gate_opened:
                open_gate()
                gate_opened = True
                last_gate_open_ms = t
                print("Gate opened")

            # Car detected but all slots full
            elif cm < ULTRA_THRESHOLD_CM and free_id == 0:
                lcd_show("    FULL", "")
                if gate_opened:
                    close_gate()
                    gate_opened = False

            # Timeout - close gate
            if gate_opened and gate_should_close(cm, t):
                close_gate()
                gate_opened = False
                print("Gate closed after car left")

        # IR sensors - slot assignment & exit
        for i, p in enumerate(ir_pins):
            raw_pin = p.value()
            raw = (raw_pin == 0) if IR_ACTIVE_LOW else (raw_pin == 1)
            if raw != slots[i].last_raw:
                slots[i].last_raw = raw; slots[i].last_state_change_ms = t
            else:
                if ticks_diff(t, slots[i].last_state_change_ms) >= IR_DEBOUNCE_MS:
                    # New car parked
                    if raw and not slots[i].occupied:
                        id_ = lowest_available_id()
                        if id_:
                            slots[i].occupied = True; slots[i].assigned_id = id_; slots[i].time_in_ms = t
                            slots[i].time_in_local = time.localtime()
                            mark_id(id_, True); slots[i].free_since_ms = 0
                            print("Assigned ID {} to S{}".format(id_, i+1)); update_lcd()
                    # Car exits
                    elif (not raw) and slots[i].occupied:
                        if slots[i].free_since_ms == 0:
                            slots[i].free_since_ms = t
                        elif ticks_diff(t, slots[i].free_since_ms) >= EXIT_GRACE_MS:
                            time_out = t
                            dur = ticks_diff(time_out, slots[i].time_in_ms)  # <-- safe
                            id_ = slots[i].assigned_id
                            tk = Ticket(id_, i, slots[i].time_in_ms, time_out)
                            tk.closed = True
                            tk.time_in_local = slots[i].time_in_local
                            tk.time_out_local = time.localtime()
                            closed_tickets.append(tk)
                            gc.collect()
                            fee = compute_fee_ms(dur)
                            msg = "✅ Ticket CLOSED\nID: {} Slot: S{}\nDuration: {} minute(s)\nFee: ${:.2f}".format(id_, i+1, minutes_rounded_up(dur), fee)
                            print(msg)
                            send_telegram(msg)
                            mark_id(id_, False)
                            slots[i].occupied = False
                            slots[i].assigned_id = 0
                            slots[i].time_in_ms = 0
                            slots[i].free_since_ms = 0
                            slots[i].time_in_local = None
                            update_lcd()
                    else:
                        if raw: slots[i].free_since_ms = 0
            if slots[i].occupied and slots[i].last_raw: slots[i].free_since_ms = 0

        time.sleep_ms(10)
        
def gate_should_close(cm, t):
    global no_car_since
    if cm > ULTRA_THRESHOLD_CM:
        if no_car_since is None:
            no_car_since = t
        elif ticks_diff(t, no_car_since) > 2000:
            return True
    else:
        no_car_since = None
    return False

# ---------------- Run ----------------
try:
    main()
except Exception as e:
    print("Fatal:", e)
    try: lcd_show("Error", "") 
    except: pass
    raise



