"""
DDP Sender Module for Spectr aLED
Handles sending frames to WLED devices via DDP protocol for both streams.
"""

import time
import socket
import struct
from queue import Empty


class DDPReceiver:
    """Class that receives data and sends it via DDP to WLED devices"""
    
    def __init__(self, root):
        """
        Initialize DDP receiver with references to app's data structures
        
        Args:
            root: Tkinter root object for after() method
        """
        self.root = root
        # These will be set externally by the main application
        self.running = True
        self.stream1_enabled = True
        self.stream2_enabled = False
        self.streaming_enabled = False
        self.streaming2_enabled = False
        
        # Queues for receiving frames
        self.ddp_queue = None
        self.ddp2_queue = None
        
        # Device slices (mapping info)
        self.device_slices = []
        self.device_slices2 = []
        
        # Last frame storage for keepalive
        self.last_ddp_frame = None
        self.last_ddp2_frame = None
        
        # FPS counters and delays
        self.ddp_delay_ms = 0.0
        self.ddp2_delay_ms = 0.0
        self.ddp_frame_count = 0
        self.ddp2_frame_count = 0
        self.last_ddp_frame_time = 0.0
        self.last_ddp2_frame_time = 0.0
        
        # Application reference (for after method if needed)
        self.app = None
    
    def set_app_reference(self, app):
        """Set reference to main application"""
        self.app = app
    
    def send_ddp(self, ip: str, data: bytes):
        """
        Send DDP packet to device
        
        Args:
            ip: WLED device IP address
            data: Frame data bytes
        """
        global ddp_socket
        if not hasattr(self.__class__, 'ddp_socket'):
            import socket
            self.__class__.ddp_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self.__class__.ddp_socket.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 1024 * 1024)
        
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
            
            self.__class__.ddp_socket.sendto(header + chunk, (ip, DDP_PORT))
    
    def ddp_send_loop(self):
        """
        DDP send loop for Stream 1
        
        Continuously sends frames from ddp_queue to WLED devices.
        Sends keepalive frames when queue is empty but streaming is active.
        """
        while self.running:
            
            if not self.stream1_enabled:
                time.sleep(0.01)
                continue
            
            try:
                frame = self.ddp_queue.get(timeout=0.5)
            except Empty:
                
                if (
                    self.last_ddp_frame is not None
                    and self.streaming_enabled
                    and self.stream1_enabled
                ):
                    frame_view = memoryview(self.last_ddp_frame)
                    
                    for dev in self.device_slices:
                        start = dev["start"] * 3
                        end = dev["end"] * 3
                        
                        chunk = frame_view[start:end]
                        self.send_ddp(dev["ip"], chunk)
                    
                    continue
                
                time.sleep(0.01)
                continue
            
            if not self.streaming_enabled or not self.stream1_enabled:
                continue
            
            send_start = time.perf_counter()
            
            frame_view = memoryview(frame)
            
            for dev in self.device_slices:
                start = dev["start"] * 3
                end = dev["end"] * 3
                
                chunk = frame_view[start:end]
                self.send_ddp(dev["ip"], chunk)
            
            self.last_ddp_frame = frame
            
            self.ddp_delay_ms = (time.perf_counter() - send_start) * 1000
            self.ddp_frame_count += 1
            self.last_ddp_frame_time = time.perf_counter()
    
    def ddp2_send_loop(self):
        """
        DDP send loop for Stream 2
        
        Continuously sends frames from ddp2_queue to WLED devices.
        Sends keepalive frames when queue is empty but streaming is active.
        """
        while self.running:
            
            if not self.stream2_enabled:
                time.sleep(0.01)
                continue
            
            try:
                frame = self.ddp2_queue.get(timeout=0.5)
            
            except Empty:
                
                if (
                    self.last_ddp2_frame is not None
                    and self.streaming2_enabled
                    and self.stream2_enabled
                ):
                    frame_view = memoryview(self.last_ddp2_frame)
                    
                    for dev in self.device_slices2:
                        start = dev["start"] * 3
                        end = dev["end"] * 3
                        
                        chunk = frame_view[start:end]
                        self.send_ddp(dev["ip"], chunk)
                    
                    continue
                
                time.sleep(0.01)
                continue
            
            if not self.streaming2_enabled or not self.stream2_enabled:
                 continue
            
            send_start = time.perf_counter()
            
            frame_view = memoryview(frame)
            
            for dev in self.device_slices2:
                start = dev["start"] * 3
                end = dev["end"] * 3
                
                chunk = frame_view[start:end]
                self.send_ddp(dev["ip"], chunk)
            
            # Save last frame for keepalive
            self.last_ddp2_frame = frame
            
            self.ddp2_delay_ms = (time.perf_counter() - send_start) * 1000
            self.ddp2_frame_count += 1
            self.last_ddp2_frame_time = time.perf_counter()