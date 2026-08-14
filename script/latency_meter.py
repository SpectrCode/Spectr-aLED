"""
Precision Latency Measurement Module for Spectr aLED
Provides high-precision delay measurement with statistical smoothing
for reliable and trustworthy latency data.

Supports:
- Capture delay (DLL frame capture + copy)
- Processing delay (tone mapping, gamma, LUT, ambilight)
- Network send delay (DDP / E1.31 sACN)
- Preview render delay
- End-to-end pipeline delay (capture -> process -> send)
- Per-stage breakdown
- Sliding window averaging for stability
- Percentile calculations (p50, p95, p99)
"""

import time
import threading
from collections import deque
from typing import Optional, Dict, List, Tuple


class LatencyMeasurement:
    """Single latency measurement with timestamp and stage breakdown"""
    __slots__ = ['timestamp', 'total_ms', 'stages']
    
    def __init__(self, total_ms: float, stages: Optional[Dict[str, float]] = None):
        self.timestamp = time.perf_counter()
        self.total_ms = total_ms
        self.stages = stages or {}


class PrecisionLatencyMeter:
    """
    High-precision latency meter with sliding window statistics.
    
    Provides stable, reliable latency measurements using:
    - Configurable sliding window for averaging
    - Percentile calculations (p50/median, p95, p99)
    - Per-stage breakdown tracking
    - Thread-safe operations
    """
    
    def __init__(self, window_size: int = 60, max_history: int = 300):
        """
        Args:
            window_size: Number of recent measurements for averaging (default 60 = ~2 seconds at 30fps)
            max_history: Maximum measurements to keep for statistics (default 300 = ~10 seconds)
        """
        self._lock = threading.Lock()
        self._window = deque(maxlen=max_history)
        self._window_size = window_size
        self._max_history = max_history
        
        # Stage breakdown accumulators
        self._stage_windows = {}
    
    @property
    def window_size(self) -> int:
        return self._window_size
    
    @window_size.setter
    def window_size(self, value: int):
        self._window_size = max(1, value)
    
    def record(self, total_ms: float, stages: Optional[Dict[str, float]] = None) -> LatencyMeasurement:
        """
        Record a new latency measurement.
        
        Args:
            total_ms: Total latency in milliseconds
            stages: Optional dict mapping stage names to their latencies in ms
            
        Returns:
            The recorded measurement
        """
        measurement = LatencyMeasurement(total_ms, stages)
        
        with self._lock:
            self._window.append(measurement)
            
            # Track stages
            if stages:
                for stage_name, stage_ms in stages.items():
                    if stage_name not in self._stage_windows:
                        self._stage_windows[stage_name] = deque(maxlen=self._max_history)
                    self._stage_windows[stage_name].append((measurement.timestamp, stage_ms))
        
        return measurement
    
    def get_average(self) -> float:
        """Get average latency over the sliding window in ms"""
        with self._lock:
            if not self._window:
                return 0.0
            recent = list(self._window)[-self._window_size:]
            return sum(m.total_ms for m in recent) / len(recent)
    
    def get_median(self) -> float:
        """Get median latency (p50) over the sliding window in ms"""
        return self.get_percentile(50)
    
    def get_percentile(self, p: float) -> float:
        """
        Get percentile latency over the sliding window.
        
        Args:
            p: Percentile (0-100)
            
        Returns:
            Latency in ms at the given percentile
        """
        with self._lock:
            if not self._window:
                return 0.0
            recent = list(self._window)[-self._window_size:]
            values = sorted(m.total_ms for m in recent)
            idx = int(len(values) * p / 100.0)
            idx = min(idx, len(values) - 1)
            return values[idx]
    
    def get_min_max(self) -> Tuple[float, float]:
        """Get (min, max) latency over the sliding window in ms"""
        with self._lock:
            if not self._window:
                return (0.0, 0.0)
            recent = list(self._window)[-self._window_size:]
            values = [m.total_ms for m in recent]
            return (min(values), max(values))
    
    def get_std_dev(self) -> float:
        """Get standard deviation over the sliding window in ms"""
        with self._lock:
            if len(self._window) < 2:
                return 0.0
            recent = list(self._window)[-self._window_size:]
            values = [m.total_ms for m in recent]
            mean = sum(values) / len(values)
            variance = sum((v - mean) ** 2 for v in values) / len(values)
            return variance ** 0.5
    
    def get_latest(self) -> float:
        """Get the most recent single measurement in ms"""
        with self._lock:
            if not self._window:
                return 0.0
            return self._window[-1].total_ms
    
    def get_stage_average(self, stage_name: str) -> float:
        """Get average latency for a specific processing stage"""
        with self._lock:
            if stage_name not in self._stage_windows:
                return 0.0
            values = [v for _, v in self._stage_windows[stage_name]]
            if not values:
                return 0.0
            return sum(values) / len(values)
    
    def get_all_stages(self) -> Dict[str, float]:
        """Get average latencies for all tracked stages"""
        result = {}
        with self._lock:
            for stage_name, values in self._stage_windows.items():
                if values:
                    vs = [v for _, v in values]
                    result[stage_name] = sum(vs) / len(vs)
        return result
    
    def get_sample_count(self) -> int:
        """Get number of measurements in history"""
        with self._lock:
            return len(self._window)
    
    def reset(self):
        """Clear all measurements"""
        with self._lock:
            self._window.clear()
            self._stage_windows.clear()
    
    def get_summary(self) -> Dict[str, float]:
        """Get complete statistics summary"""
        avg = self.get_average()
        median = self.get_median()
        p95 = self.get_percentile(95)
        p99 = self.get_percentile(99)
        min_val, max_val = self.get_min_max()
        std_dev = self.get_std_dev()
        
        return {
            'average': avg,
            'median': median,
            'p95': p95,
            'p99': p99,
            'min': min_val,
            'max': max_val,
            'std_dev': std_dev,
            'samples': self.get_sample_count(),
        }
    
    def stats(self) -> Dict[str, float]:
        """Get statistics dict compatible with main.py UI (alias for get_summary)"""
        return self.get_summary()


class PipelineStageTimer:
    """
    Context manager for measuring individual pipeline stages.
    Measures time spent in each stage of the processing pipeline.
    """
    
    def __init__(self):
        self._stages: Dict[str, float] = {}
        self._current_stage: Optional[str] = None
        self._stage_start: float = 0.0
        self._total_start: float = time.perf_counter()
        self._timed_stages: List[str] = []
    
    def begin(self) -> float:
        """Mark the beginning of the pipeline"""
        self._total_start = time.perf_counter()
        self._stages.clear()
        self._timed_stages.clear()
        return self._total_start
    
    def mark_stage(self, stage_name: str) -> float:
        """
        Mark a stage boundary. Call this at the end of each stage.
        
        Args:
            stage_name: Name identifier for this stage
            
        Returns:
            Time spent in this stage in ms
        """
        now = time.perf_counter()
        if self._current_stage is None:
            # First stage - measure from beginning
            elapsed_ms = (now - self._total_start) * 1000.0
        else:
            # Subsequent stages measured from last mark
            elapsed_ms = 0.0  # Already computed at previous mark
        
        self._stages[stage_name] = elapsed_ms
        self._current_stage = stage_name
        self._timed_stages.append(stage_name)
        
        return elapsed_ms
    
    def measure_stage(self, stage_name: str, start_time: float) -> float:
        """
        Measure a stage given its start time.
        
        Args:
            stage_name: Name identifier for this stage
            start_time: Start time from time.perf_counter()
            
        Returns:
            Time spent in this stage in ms
        """
        now = time.perf_counter()
        elapsed_ms = (now - start_time) * 1000.0
        self._stages[stage_name] = elapsed_ms
        return elapsed_ms
    
    def get_stage_elapsed(self, stage_name: str) -> float:
        """Get elapsed time for a specific stage"""
        return self._stages.get(stage_name, 0.0)
    
    def get_total_ms(self) -> float:
        """Get total pipeline time from begin() to now"""
        return (time.perf_counter() - self._total_start) * 1000.0
    
    def get_stages_dict(self) -> Dict[str, float]:
        """Get all measured stages"""
        return dict(self._stages)
    
    def get_missing_stages(self, expected: List[str]) -> List[str]:
        """Get list of expected stages that were not measured"""
        return [s for s in expected if s not in self._stages]


class EndToEndLatencyTracker:
    """
    Tracks end-to-end latency from frame capture to network transmission.
    
    Works by injecting timestamps into the pipeline and measuring
    the complete round-trip time across all stages.
    """
    
    def __init__(self, max_tracked: int = 120):
        self._lock = threading.Lock()
        self._frame_timestamps: deque = deque(maxlen=max_tracked)
        # frame_id -> {capture_time, process_time, send_time}
        self._frame_times: deque = deque(maxlen=max_tracked)
    
    def mark_capture(self, frame_id: int) -> float:
        """Mark when frame capture completed"""
        now = time.perf_counter()
        with self._lock:
            self._frame_times.append({
                'id': frame_id,
                'capture': now,
                'process': None,
                'send': None,
            })
        return now
    
    def mark_process(self, frame_id: int) -> float:
        """Mark when frame processing completed"""
        now = time.perf_counter()
        with self._lock:
            for ft in self._frame_times:
                if ft['id'] == frame_id and ft['process'] is None:
                    ft['process'] = now
                    break
        return now
    
    def mark_send(self, frame_id: int) -> float:
        """Mark when frame was sent to network"""
        now = time.perf_counter()
        with self._lock:
            for ft in self._frame_times:
                if ft['id'] == frame_id and ft['send'] is None:
                    ft['send'] = now
                    break
        return now
    
    def get_e2e_latency_ms(self) -> float:
        """Get average end-to-end latency (capture to send) in ms"""
        with self._lock:
            complete = [ft for ft in self._frame_times 
                       if ft['capture'] is not None and ft['send'] is not None]
            if not complete:
                return 0.0
            latencies = [(ft['send'] - ft['capture']) * 1000.0 for ft in complete]
            return sum(latencies) / len(latencies)
    
    def get_stage_latencies_ms(self) -> Dict[str, float]:
        """Get breakdown of capture->process and process->send latencies"""
        with self._lock:
            complete = [ft for ft in self._frame_times 
                       if ft['capture'] is not None and ft['process'] is not None 
                       and ft['send'] is not None]
            if not complete:
                return {'capture_to_process': 0.0, 'process_to_send': 0.0}
            
            capture_to_proc = [(ft['process'] - ft['capture']) * 1000.0 for ft in complete]
            proc_to_send = [(ft['send'] - ft['process']) * 1000.0 for ft in complete]
            
            return {
                'capture_to_process': sum(capture_to_proc) / len(capture_to_proc),
                'process_to_send': sum(proc_to_send) / len(proc_to_send),
            }


# Module-level singleton meters for easy access
capture_meter = PrecisionLatencyMeter(window_size=60, max_history=300)
processing_meter = PrecisionLatencyMeter(window_size=60, max_history=300)
ddp_send_meter = PrecisionLatencyMeter(window_size=60, max_history=300)
ddp2_send_meter = PrecisionLatencyMeter(window_size=60, max_history=300)
e131_send_meter = PrecisionLatencyMeter(window_size=60, max_history=300)
e131_send2_meter = PrecisionLatencyMeter(window_size=60, max_history=300)
preview_meter = PrecisionLatencyMeter(window_size=60, max_history=300)
pipeline_e2e_meter = PrecisionLatencyMeter(window_size=60, max_history=300)

# End-to-end tracker
e2e_tracker = EndToEndLatencyTracker()