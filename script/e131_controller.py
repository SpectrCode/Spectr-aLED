"""
E1.31 sACN Controller Module for Spectr aLED
Provides functions to start/stop E1.31 sACN send loops and manage device mappings.

This module allows dynamic switching between streams for each WLED device using sACN protocol.
"""

import socket
import struct
import time
import uuid
from queue import Empty
from typing import List, Dict, Optional

# === CONSTANTS ===
SACN_PORT = 5568
START_UNIVERSE = 1
LEDS_PER_UNIVERSE = 170  # One RGB universe = 170 LEDs = 510 DMX channels
CHANNELS_PER_UNIVERSE = LEDS_PER_UNIVERSE * 3  # 510 channels per universe

# sACN priority
PRIORITY = 100

# Unique CID for this sender (generated once)
CID = uuid.uuid4().bytes

SOURCE_NAME = b"Spectr-aLED"


# === GLOBAL SACN SOCKET ===
_sacn_socket = None


def get_sacn_socket() -> Optional[socket.socket]:
    """Get or create global E1.31 sACN socket"""
    global _sacn_socket
    if _sacn_socket is None:
        try:
            _sacn_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            _sacn_socket.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 1024 * 1024)
            print("[OK] E1.31 sACN socket created")
        except Exception as e:
            print(f"[ERROR] Failed to create sACN socket: {e}")
    return _sacn_socket


def close_sacn_socket():
    """Close global E1.31 sACN socket"""
    global _sacn_socket
    if _sacn_socket is not None:
        try:
            _sacn_socket.close()
            print("[OK] E1.31 sACN socket closed")
        except Exception as e:
            print(f"[WARN] Failed to close sACN socket: {e}")
        finally:
            _sacn_socket = None


def build_sacn_packet(
    universe: int,
    data: bytes,
    sequence: int,
    priority: int = PRIORITY
) -> bytes:
    """
    Build E1.31 / sACN Data Packet.
    
    Args:
        universe: DMX universe number (1-based)
        data: RGB DMX data, maximum 510 bytes (170 LEDs * 3 channels)
        sequence: Sequence number (0-255)
        priority: Priority (0-200, higher = more important)
    
    Returns:
        Complete sACN packet as bytes
    """
    # --------------------------------------------------------
    # Root Layer
    # --------------------------------------------------------
    
    # Preamble
    preamble = struct.pack("!HH", 0x0010, 0x0000)
    
    # ACN Packet Identifier
    acn_pid = b"ASC-E1.17\x00\x00\x00"
    
    # Root vector
    root_vector = struct.pack("!I", 0x00000004)
    
    # DMP + Framing + data lengths
    dmp_length = 1 + len(data)
    framing_length = (
        64 +       # Source Name
        1 +        # Priority
        2 +        # Sync Address
        1 +        # Sequence
        1 +        # Options
        2 +        # Universe
        2 +        # DMP length/vector area
        1 +        # Address type
        2 +        # First property address
        2 +        # Address increment
        2 +        # Property value count
        len(data) + 1
    )
    
    # More exact layer lengths
    dmp_layer_length = 10 + len(data) + 1
    framing_layer_length = 77 + dmp_layer_length
    
    root_layer_length = (
        4 +                 # root vector
        16 +                # CID
        framing_layer_length
    )
    
    root_flags_length = 0x7000 | root_layer_length
    framing_flags_length = 0x7000 | framing_layer_length
    dmp_flags_length = 0x7000 | dmp_layer_length
    
    # --------------------------------------------------------
    # Root Layer
    # --------------------------------------------------------
    
    root = (
        struct.pack("!H", root_flags_length)
        + root_vector
        + CID
    )
    
    # --------------------------------------------------------
    # Framing Layer
    # --------------------------------------------------------
    
    source_name = SOURCE_NAME.ljust(64, b"\x00")[:64]
    
    framing_vector = struct.pack("!I", 0x00000002)
    
    sync_address = 0
    options = 0
    
    framing = (
        struct.pack("!H", framing_flags_length)
        + framing_vector
        + source_name
        + struct.pack("!B", priority)
        + struct.pack("!H", sync_address)
        + struct.pack("!B", sequence)
        + struct.pack("!B", options)
        + struct.pack("!H", universe)
    )
    
    # --------------------------------------------------------
    # DMP Layer
    # --------------------------------------------------------
    
    dmp_vector = 0x02
    address_type = 0xA1
    first_property_address = 0
    address_increment = 1
    
    # Property values:
    # byte 0 = DMX Start Code
    # bytes 1.. = RGB data
    property_values = b"\x00" + data
    
    dmp = (
        struct.pack("!H", dmp_flags_length)
        + struct.pack("!B", dmp_vector)
        + struct.pack("!B", address_type)
        + struct.pack("!H", first_property_address)
        + struct.pack("!H", address_increment)
        + struct.pack("!H", len(property_values))
        + property_values
    )
    
    return (
        preamble
        + acn_pid
        + root
        + framing
        + dmp
    )


def get_universe_count(led_count: int) -> int:
    """Calculate number of universes needed for given LED count"""
    return (led_count + LEDS_PER_UNIVERSE - 1) // LEDS_PER_UNIVERSE


def create_frame_buffer(led_count: int) -> bytearray:
    """
    Create empty frame buffer for RGB data.
    
    Args:
        led_count: Number of LEDs
        
    Returns:
        Bytearray filled with zeros
    """
    return bytearray(led_count * 3)


def set_pixel(
    frame: bytearray,
    pixel_index: int,
    color: tuple,
    offset: int = 0
):
    """
    Set RGB pixel in frame buffer.
    
    Args:
        frame: Frame buffer (bytearray)
        pixel_index: LED index within this frame (relative to offset)
        color: RGB tuple (0-255 each)
        offset: Start LED index offset for this frame
    """
    abs_pixel = pixel_index + offset
    universe_index = abs_pixel // LEDS_PER_UNIVERSE
    pixel_in_universe = abs_pixel % LEDS_PER_UNIVERSE
    channel_offset = pixel_in_universe * 3
    
    # Check bounds
    if channel_offset + 3 > len(frame):
        return
    
    frame[channel_offset:channel_offset + 3] = bytes(color)


def send_ddp_packet(ip: str, data: bytes) -> bool:
    """
    Send DDP packet to device (helper function for switching)
    
    Args:
        ip: WLED IP address
        data: Frame data in RGB format
        
    Returns:
        True on success, False otherwise
    """
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 1024 * 1024)
        
        DDP_PORT = 4048
        DDP_MAX_CHUNK_SIZE = 1440
        
        total_len = len(data)
        packets = (total_len + DDP_MAX_CHUNK_SIZE - 1) // DDP_MAX_CHUNK_SIZE
        
        seq = 0
        
        for i in range(packets):
            chunk = data[i * DDP_MAX_CHUNK_SIZE:(i + 1) * DDP_MAX_CHUNK_SIZE]
            
            header = struct.pack(
                "!BBBBLH",
                0x40 | (0x01 if i == packets - 1 else 0),
                seq,
                0x0B,
                1,
                i * DDP_MAX_CHUNK_SIZE,
                len(chunk)
            )
            
            sock.sendto(header + chunk, (ip, DDP_PORT))
        
        sock.close()
        return True
    except Exception as e:
        print(f"[ERROR] DDP send failed: {e}")
        return False


def is_host_online(host: str, port: int = 80, timeout: float = 0.3) -> bool:
    """
    Fast check if host is online using TCP socket connection.
    
    Args:
        host: IP address or hostname
        port: Port to check (default 80 for HTTP)
        timeout: Connection timeout in seconds
        
    Returns:
        True if host is reachable, False otherwise
    """
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        result = sock.connect_ex((host, port))
        sock.close()
        return result == 0
    except Exception:
        return False


def set_wled_live(ip: str, enabled: bool) -> bool:
    """
    Set WLED to live mode (required for DDP/sACN input)
    
    Args:
        ip: WLED IP address
        enabled: True to enable live mode, False to disable
        
    Returns:
        True on success, False otherwise
    """
    if not is_host_online(ip, port=80, timeout=0.3):
        print(f"[ERROR] WLED connection failed: {ip} (device offline)")
        return False
    
    try:
        import json
        import urllib.request
        
        url = f"http://{ip}/json/state"
        
        payload = json.dumps({
            "on": True,
            "bri": 255,
            "live": enabled,
            "nl": {"on": False}
        }).encode("utf-8")
        
        request = urllib.request.Request(
            url,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST"
        )
        
        with urllib.request.urlopen(request, timeout=1.0) as response:
            response.read()
        
        print(f"[INFO] WLED {ip} live mode: {'ON' if enabled else 'OFF'}")
        return True
        
    except Exception as e:
        print(f"[ERROR] Failed to set WLED live={enabled}: {e}")
        return False


def restore_wled(ip: str):
    """Restore normal WLED operation mode"""
    try:
        import json
        import urllib.request
        
        if not is_host_online(ip, port=80, timeout=0.3):
            return
        
        url = f"http://{ip}/json/state"
        
        payload = json.dumps({
            "live": False,
            "on": True,
            "bri": 255
        }).encode("utf-8")
        
        request = urllib.request.Request(
            url,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST"
        )
        
        with urllib.request.urlopen(request, timeout=1.0) as response:
            response.read()
            
    except Exception as e:
        print(f"[WARN] Failed to restore WLED {ip}: {e}")


class StreamManager:
    """
    Manages stream state and device mappings for dynamic switching.
    
    Each WLED device can be assigned to Stream 1 or Stream 2 dynamically.
    This class handles the mapping and keeps track of last frame per device
    for keepalive purposes.
    """
    
    def __init__(self):
        # Device info: {ip: {"ip": str, "start": int, "end": int, "stream": int, 
        #                    "last_frame": bytes, "led_count": int}}
        self.devices = {}
        
        # Active stream flag (1 or 2)
        self.active_stream = 1
        
        # Running flag
        self.running = True
    
    def set_active_stream(self, stream: int):
        """Set active stream (1 or 2)"""
        if stream in (1, 2):
            self.active_stream = stream
    
    def add_device(self, ip: str, start: int, end: int, stream: int):
        """
        Add device to manager
        
        Args:
            ip: Device IP
            start: Start LED index
            end: End LED index  
            stream: Stream number (1 or 2)
        """
        self.devices[ip] = {
            "ip": ip,
            "start": start,
            "end": end,
            "stream": stream,
            "last_frame": None,
            "led_count": end - start
        }
    
    def remove_device(self, ip: str):
        """Remove device from manager"""
        if ip in self.devices:
            del self.devices[ip]
    
    def update_stream_for_device(self, ip: str, new_stream: int):
        """Update stream assignment for a specific device"""
        if ip in self.devices:
            self.devices[ip]["stream"] = new_stream
    
    def get_devices_for_active_stream(self) -> list:
        """Get list of devices assigned to active stream"""
        return [
            dev for dev in self.devices.values() 
            if dev["stream"] == self.active_stream
        ]
    
    def get_all_devices(self) -> list:
        """Get all devices regardless of stream"""
        return list(self.devices.values())
    
    def set_last_frame_for_device(self, ip: str, frame_data: bytes):
        """Store last frame for keepalive"""
        if ip in self.devices:
            self.devices[ip]["last_frame"] = frame_data
    
    def get_all_stream_ids(self) -> dict:
        """Get dictionary of IP to stream mapping"""
        return {ip: dev["stream"] for ip, dev in self.devices.items()}


def run_sacn_loop(manager: StreamManager, queue_obj, stream_num: int):
    """
    Run E1.31 sACN send loop for a specific stream
    
    Args:
        manager: StreamManager instance
        queue_obj: Queue to get frames from
        stream_num: Stream number (1 or 2)
    
    This function continuously gets frames from the queue and sends them
    to WLED devices assigned to this stream using E1.31 sACN protocol.
    It also sends keepalive frames when the queue is empty but streaming is active.
    """
    # Sequence counter per device
    sequence_counters = {}
    
    while manager.running:
        
        # Check if current stream matches our stream_num
        if manager.active_stream != stream_num:
            time.sleep(0.01)
            continue
        
        try:
            frame = queue_obj.get(timeout=0.5)
        except Empty:
            
            # Send keepalive frames for devices in this stream
            keepalive_devices = [
                dev for dev in manager.devices.values() 
                if dev["stream"] == stream_num and dev["last_frame"] is not None
            ]
            
            if keepalive_devices:
                send_keepalive(manager, keepalive_devices, sequence_counters)
            
            continue
        
        # Get devices for this stream
        devices = [
            dev for dev in manager.devices.values() 
            if dev["stream"] == stream_num
        ]
        
        if not devices:
            continue
        
        # Parse frame - it's RGB data (full buffer for all devices)
        try:
            frame_view = memoryview(frame)
            
            for dev in devices:
                ip = dev["ip"]
                
                # Calculate LED range within this device
                start_led = dev["start"]
                end_led = dev["end"]
                led_count = end_led - start_led
                
                # Extract RGB data for this device's LEDs
                rgb_start = start_led * 3
                rgb_end = end_led * 3
                
                if len(frame_view) < rgb_end:
                    # Try to send what we have, padding with zeros
                    available = len(frame_view) - rgb_start
                    if available <= 0:
                        continue
                    rgb_data = bytes(frame_view[rgb_start:]) + b'\x00' * (rgb_end - len(frame_view))
                else:
                    rgb_data = bytes(frame_view[rgb_start:rgb_end])
                
                # Calculate number of universes needed
                universe_count = get_universe_count(led_count)
                
                # Get or initialize sequence counter for this device
                if ip not in sequence_counters:
                    sequence_counters[ip] = 0
                
                seq = sequence_counters[ip]
                
                # Build and send packets for each universe
                sock = get_sacn_socket()
                if sock is None:
                    continue
                
                destination = (ip, SACN_PORT)
                
                try:
                    for uni_index in range(universe_count):
                        # Get LED range for this universe
                        led_start = uni_index * LEDS_PER_UNIVERSE
                        led_end = min(led_start + LEDS_PER_UNIVERSE, led_count)
                        
                        if led_start >= led_count:
                            break
                        
                        # Calculate channel offsets within the device's RGB data
                        channel_start = (uni_index * CHANNELS_PER_UNIVERSE)
                        channel_end = min(channel_start + CHANNELS_PER_UNIVERSE, len(rgb_data))
                        
                        if channel_start >= len(rgb_data):
                            break
                        
                        universe_data = rgb_data[channel_start:channel_end]
                        universe_num = START_UNIVERSE + uni_index
                        
                        # Build and send packet
                        packet = build_sacn_packet(
                            universe=universe_num,
                            data=universe_data,
                            sequence=seq
                        )
                        
                        sock.sendto(packet, destination)
                    
                    # Store last frame for keepalive
                    manager.set_last_frame_for_device(ip, bytes(rgb_data))
                    
                    # Increment sequence counter
                    sequence_counters[ip] = (seq + 1) & 0xFF
                    
                except Exception as e:
                    print(f"[ERROR] sACN send failed for {ip}: {e}")
        
        except Exception as e:
            print(f"[ERROR] Frame processing error: {e}")


def send_keepalive(manager: StreamManager, devices: list, sequence_counters: dict):
    """Send keepalive frames to devices (resend last frame)"""
    sock = get_sacn_socket()
    if sock is None:
        return
    
    for dev in devices:
        ip = dev["ip"]
        
        # Get last frame data
        last_frame = dev.get("last_frame")
        if last_frame is None:
            continue
        
        led_count = dev["led_count"]
        universe_count = get_universe_count(led_count)
        
        # Get sequence counter
        seq = sequence_counters.get(ip, 0)
        
        destination = (ip, SACN_PORT)
        
        try:
            for uni_index in range(universe_count):
                led_start = uni_index * LEDS_PER_UNIVERSE
                led_end = min(led_start + LEDS_PER_UNIVERSE, led_count)
                
                if led_start >= led_count:
                    break
                
                channel_start = (uni_index * CHANNELS_PER_UNIVERSE)
                channel_end = min(channel_start + CHANNELS_PER_UNIVERSE, len(last_frame))
                
                if channel_start >= len(last_frame):
                    break
                
                universe_data = last_frame[channel_start:channel_end]
                universe_num = START_UNIVERSE + uni_index
                
                packet = build_sacn_packet(
                    universe=universe_num,
                    data=universe_data,
                    sequence=seq
                )
                
                sock.sendto(packet, destination)
            
            # Increment sequence for next keepalive
            sequence_counters[ip] = (seq + 1) & 0xFF
            
        except Exception as e:
            print(f"[WARN] Keepalive failed for {ip}: {e}")


def stop_sacn_loops(managers: list):
    """Stop all sACN loops by setting running flag to False"""
    for manager in managers:
        manager.running = False