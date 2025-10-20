# Mini Project 1
## Features
+ **Entry & Gate Logic**
    - Use the ultrasonic sensor at the gate to detect an arriving car.
    - If all 3 slots are occupied, do not open the gate; LCD shows “FULL”.
    - If any slot is free, LCD lists free slots (e.g., “Free: S1 S3”) and the servo gate opens.
    - Gate closes after the car passes or after a short timeout.

+ **Auto-ID Assignment (no user input)**
    - There are exactly 3 IDs: 1, 2, 3.
    - When a car fully parks (slot’s IR becomes OCCUPIED after debounce), assign the
  lowest available ID at that moment.
    - Record time-in and bind: {ID ↔ Slot}.
    - Each slot tracks: occupied, assigned_id, and time_in.
  
+ **Exit & Billing**
    - When a parked slot’s IR becomes FREE continuously for ≥ 1 seconds (leave grace),
  treat it as exit.
    - Record time-out, compute duration and fee, mark the ticket CLOSED, and free the
  slot & ID.
    - Pricing rule: 1 minutes 0.5$

+ **LCD Messages (16×2)**
    - Display available parking slots

+ **Web Dashboard (served by ESP32)**
    - Live display of parking status

+ **Telegram Notification**
    - On exit, sends a receipt

## Requirements
  - ESP32 Dev Board (MicroPython firmware flashed)
  - 3x IR sensors
  - HC-SR04 ultrasonic distance sensor
  - LCD 16×2 with I²C backpack
  - Breadboard, jumper wires
  - USB cable + laptop with Thonny
  - Wi-Fi access

## Wiring
  <img width="1600" height="900" alt="image" src="https://github.com/user-attachments/assets/72c5358d-df3c-4c30-8ddd-63db8260d4bc" />
  <img width="1600" height="900" alt="image" src="https://github.com/user-attachments/assets/cae02ac1-3c46-4e8a-ac8f-ad8cd5029235" />


## Usage
  <img width="1848" height="873" alt="Screenshot 2025-10-20 094558" src="https://github.com/user-attachments/assets/d3829401-1611-4bda-9176-6205430298f0" />
  <img width="1846" height="869" alt="image" src="https://github.com/user-attachments/assets/3d5ba9c8-6e15-45bb-990b-29e1e3a8bbcd" />

  <img width="1308" height="829" alt="Screenshot 2025-10-14 114052" src="https://github.com/user-attachments/assets/a715ee94-cc04-4efe-b4fc-8bbbb5a51e82" />

## Demo Video
