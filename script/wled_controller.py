"""
Module for WLED device control via DDP protocol
"""

import socket
import struct
import json
import urllib.request
from typing import Dict, Optional


def is_host_online(host: str, port: int = 80, timeout: float = 0.3) -> bool:
    """
    Fast check if host is online using TCP socket connection.
    Returns True if connected within timeout, False otherwise.
    
    Args:
        host: IP address or hostname
        port: Port to check (default 80 for HTTP)
        timeout: Connection timeout in seconds (default 0.3)
    
    Returns:
        bool: True if host is reachable, False otherwise
    """
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        result = sock.connect_ex((host, port))
        sock.close()
        return result == 0
    except Exception:
        return False


# Configuration constants (embedded - no external config file)
DEFAULT_LED_COUNT = 2048
DDP_PORT = 4048
DDP_MAX_CHUNK_SIZE = 1440


class WLEDController:
    """Class for WLED device control"""
    
    def __init__(self):
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 1024 * 1024)
        self._seq = 0
    
    def set_ddp_mode(self, ip: str, keep_last_frame: bool = True) -> bool:
        """Switch WLED to DDP mode with fast online check"""
        # Fast check if host is online using TCP socket (0.3s timeout)
        if not is_host_online(ip, port=80, timeout=0.3):
            print(f"[ERROR] WLED connection failed: {ip} (device offline)")
            return False
        
        payload = {
            "on": True,
            "bri": 255,
            "transition": 0,
            "live": True,
            "nl": {"on": False},
            "lor": 0
        }
        
        if not keep_last_frame:
            payload["seg"] = [{
                "id": 0,
                "fx": 0,
                "col": [[10, 10, 10]]
            }]
        
        req = urllib.request.Request(
            f"http://{ip}/json/state",
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
            method="POST"
        )
        
        try:
            urllib.request.urlopen(req, timeout=5)
            print(f"[OK] WLED {ip} switched to DDP mode")
            return True
        except Exception as e:
            print(f"[ERROR] WLED connection failed: {ip}")
            print(e)
            return False
    
    def restore_wled(self, ip: str):
        """Restore normal WLED operation mode with fast online check"""
        # Fast check if host is online using TCP socket (0.3s timeout)
        if not is_host_online(ip, port=80, timeout=0.3):
            return
        
        payload = {
            "live": False,
            "on": True,
            "bri": 255,
            "transition": 0,
            "seg": [{
                "id": 0,
                "fx": 0,
                "col": [[10, 10, 10]]
            }]
        }
        
        req = urllib.request.Request(
            f"http://{ip}/json/state",
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
            method="POST"
        )
        try:
            urllib.request.urlopen(req, timeout=2)
        except Exception as e:
            print(f"[WARN] Failed to restore WLED {ip}: {e}")
    
    def send_ddp(self, ip: str, data: bytes):
        """Send DDP packet to device"""
        total_len = len(data)
        packets = (total_len + DDP_MAX_CHUNK_SIZE - 1) // DDP_MAX_CHUNK_SIZE
        
        self._seq = (self._seq + 1) % 255
        
        for i in range(packets):
            chunk = data[i * DDP_MAX_CHUNK_SIZE:(i + 1) * DDP_MAX_CHUNK_SIZE]
            
            header = struct.pack(
                "!BBBBLH",
                0x40 | (0x01 if i == packets - 1 else 0),
                self._seq,
                0x0B,
                1,
                i * DDP_MAX_CHUNK_SIZE,
                len(chunk)
            )
            
            self.socket.sendto(header + chunk, (ip, DDP_PORT))
    
    def test_color(self, ip: str, r: int, g: int, b: int):
        """Send test color to WLED with fast online check"""
        # Fast check if host is online using TCP socket (0.3s timeout)
        if not is_host_online(ip, port=80, timeout=0.3):
            return
        
        payload = {
            "on": True,
            "bri": 255,
            "live": False,
            "seg": [{
                "id": 0,
                "fx": 0,
                "col": [[r, g, b]]
            }]
        }
        
        req = urllib.request.Request(
            f"http://{ip}/json/state",
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
            method="POST"
        )
        urllib.request.urlopen(req, timeout=2)
    
    def get_info(self, ip: str) -> Optional[Dict]:
        """Get information about WLED device with fast online check"""
        # Fast check if host is online using TCP socket (0.3s timeout)
        if not is_host_online(ip, port=80, timeout=0.3):
            print(f"[ERROR] Failed to get info from {ip}: device offline")
            return None
        
        try:
            with urllib.request.urlopen(f"http://{ip}/json/info", timeout=2) as r:
                data = json.loads(r.read().decode())
                return data
        except Exception as e:
            print(f"[ERROR] Failed to get info from {ip}: {e}")
            return None
    
    def get_name(self, ip: str) -> str:
        """Get WLED device name with fast online check"""
        # Fast check if host is online using TCP socket (0.3s timeout)
        if not is_host_online(ip, port=80, timeout=0.3):
            return "WLED"
        
        info = self.get_info(ip)
        if info:
            return info.get("name", "WLED")
        return "WLED"
    
    def get_led_count(self, ip: str) -> int:
        """Get number of LEDs on device with fast online check"""
        # Fast check if host is online using TCP socket (0.3s timeout)
        if not is_host_online(ip, port=80, timeout=0.3):
            return DEFAULT_LED_COUNT
        
        info = self.get_info(ip)
        if info:
            return info.get("leds", {}).get("count", DEFAULT_LED_COUNT)
        return DEFAULT_LED_COUNT


# Global instance for use in other modules
wled_controller = WLEDController()
