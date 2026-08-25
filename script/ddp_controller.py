"""
DDP Controller Module for Spectr aLED
Provides functions to start/stop DDP send loops and manage device mappings.

This module allows dynamic switching between streams for each WLED device.
"""

import time
import socket
import struct
from queue import Empty


# Global DDP socket (shared across all loops)
_ddp_socket = None


def get_ddp_socket():
    """Get or create global DDP socket"""
    global _ddp_socket
    if _ddp_socket is None:
        _ddp_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        _ddp_socket.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 1024 * 1024)
    return _ddp_socket


def send_ddp_packet(ip: str, data: bytes):
    """
    Send DDP packet to device
    
    Args:
        ip: WLED device IP address
        data: Frame data bytes (RGB format)
    """
    global _ddp_socket
    
    # Use cached socket
    sock = get_ddp_socket()
    
    DDP_PORT = 4048
    DDP_MAX_CHUNK_SIZE = 1440
    
    total_len = len(data)
    packets = (total_len + DDP_MAX_CHUNK_SIZE - 1) // DDP_MAX_CHUNK_SIZE
    
    seq = 0
    seq = (seq + 1) % 255
    
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


class StreamManager:
    """
    Manages stream state and device mappings for dynamic switching.
    
    Each WLED device can be assigned to Stream 1 or Stream 2 dynamically.
    This class handles the mapping and keeps track of last frame per device
    for keepalive purposes.
    """
    
    def __init__(self):
        # Device info: {ip: {"start": int, "end": int, "stream": int, "last_frame": bytes}}
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
            "start": start,
            "end": end,
            "stream": stream,
            "last_frame": None
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


        # You can add FPS tracking here if needed

