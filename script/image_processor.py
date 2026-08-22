"""
Module for image processing: Tonemap, LUT, Ambilight, Saturation
Optimized to minimize cache misses on CPU and GPU

Uses cache optimization utilities from cache_optimizer module
for improved memory access patterns and reduced cache misses.
"""

import numpy as np
from concurrent.futures import ThreadPoolExecutor
from threading import Lock

# PQ Curve NITS values (embedded - no external config file)
PQ_NITS = [
    0, 0.1, 0.2, 0.4, 0.6, 0.8, 1, 1.4, 1.8, 2.2, 2.6, 3, 3.5, 4, 4.5, 5, 
    6, 7, 8, 9, 10, 15, 20, 25, 30, 40, 50, 60, 70, 80, 90, 100, 120, 140, 
    160, 180, 200, 220, 240, 260, 280, 300, 320, 360, 400, 440, 480, 520, 
    560, 600, 640, 680, 720, 760, 800, 900, 1000, 1200, 1500, 2000, 3000, 
    5000, 8000, 10000
]

# Default calibration (embedded - no external config file)
DEFAULT_CALIBRATION = {
    "white": [1.0, 1.0, 1.0],
    "red": [1.0, 1.0, 1.0],
    "green": [1.0, 1.0, 1.0],
    "blue": [1.0, 1.0, 1.0],
    "yellow": [1.0, 1.0, 1.0],
    "cyan": [1.0, 1.0, 1.0],
    "magenta": [1.0, 1.0, 1.0],
}

# Try to import cache optimizer for advanced optimizations
try:
    from cache_optimizer import (
        CacheOptimizedBuffer,
        CacheOptimizedFrameBuffer,
        GPUKernelCacheOptimizer,
        align_to_cache_line,
        CACHE_LINE_SIZE,
        create_3d_lut_optimized as _cache_create_lut,
        apply_lut_generic_optimized as _cache_apply_lut,
        apply_ambilight_optimized as _cache_apply_ambi,
        apply_saturation_optimized as _cache_apply_sat
    )
    HAS_CACHE_OPTIMIZER = True
except ImportError:
    # Fallback if cache optimizer not available
    CacheOptimizedBuffer = None
    CacheOptimizedFrameBuffer = None
    GPUKernelCacheOptimizer = None
    align_to_cache_line = lambda x: x
    CACHE_LINE_SIZE = 64
    _cache_create_lut = None
    _cache_apply_lut = None
    _cache_apply_ambi = None
    _cache_apply_sat = None
    HAS_CACHE_OPTIMIZER = False

# Create thread pool for LUT generation (optimal number of threads)
LUT_THREAD_POOL = ThreadPoolExecutor(max_workers=None)  # By default - number of CPU cores
# Global frame buffer cache for reuse
_FRAME_BUFFER_CACHE: dict = {}

def generate_3d_lut(calibration: dict, size: int = 128) -> np.ndarray:
    """
    Generate 3D LUT for LED calibration with cache locality optimization.
    
    Delegated to the optimized implementation in cache_optimizer when available,
    which provides:
    - Sequential memory access patterns (C-contiguous)
    - Minimized intermediate array allocations
    - Reusable temporary arrays
    - Optimized memory bandwidth usage
    
    NOTE: The user-selected LUT size is always respected. get_optimal_lut_size()
    is only used as a recommendation when size is not explicitly provided.
    
    Args:
        calibration: Calibration dictionary with color correction values
        size: LUT size (default 128) — NOT reduced automatically
    
    Returns:
        C-contiguous 4D array [size, size, size, 3] with float32 values
    """
    
    # Prefer optimized implementation from cache_optimizer when available
    if HAS_CACHE_OPTIMIZER and _cache_create_lut is not None:
        return _cache_create_lut(calibration, size)
    
    # Fallback — direct numpy implementation (same algorithm, no optimizer imports)
    grid = np.linspace(0.0, 1.0, size, dtype=np.float32)
    
    rr = np.broadcast_to(grid.reshape(size, 1, 1), (size, size, size)).copy()
    gg = np.broadcast_to(grid.reshape(1, size, 1), (size, size, size)).copy()
    bb = np.broadcast_to(grid.reshape(1, 1, size), (size, size, size)).copy()
    
    lut = np.empty((size, size, size, 3), dtype=np.float32)
    
    lut[..., 0] = rr * calibration["white"][0]
    lut[..., 1] = gg * calibration["white"][1]
    lut[..., 2] = bb * calibration["white"][2]
    
    luma = (
        lut[..., 0] * 0.2126 +
        lut[..., 1] * 0.7152 +
        lut[..., 2] * 0.0722
    )
    
    brightness_boost = np.clip(0.25 + 0.75 * luma, 0.25, 1.0)
    
    maxc = np.maximum.reduce([lut[..., 0], lut[..., 1], lut[..., 2]])
    minc = np.minimum.reduce([lut[..., 0], lut[..., 1], lut[..., 2]])
    
    chroma = maxc - minc
    chroma = np.true_divide(chroma, maxc + 1e-5)
    chroma = np.power(chroma, 0.7)
    
    weight = chroma * brightness_boost
    
    def dominance_optimized(a, b, c):
        return np.clip(a - np.maximum(b, c), 0.0, 1.0)
    
    red_w = dominance_optimized(lut[..., 0], lut[..., 1], lut[..., 2])
    green_w = dominance_optimized(lut[..., 1], lut[..., 0], lut[..., 2])
    blue_w = dominance_optimized(lut[..., 2], lut[..., 0], lut[..., 1])
    
    yellow_w = np.clip(np.minimum(lut[..., 0], lut[..., 1]) - lut[..., 2], 0.0, 1.0)
    cyan_w = np.clip(np.minimum(lut[..., 1], lut[..., 2]) - lut[..., 0], 0.0, 1.0)
    magenta_w = np.clip(np.minimum(lut[..., 0], lut[..., 2]) - lut[..., 1], 0.0, 1.0)
    
    sharpen = 1.4
    
    red_w = np.power(red_w, sharpen) * weight
    green_w = np.power(green_w, sharpen) * weight
    blue_w = np.power(blue_w, sharpen) * weight
    
    yellow_w = np.power(yellow_w, 1.3) * weight
    cyan_w = np.power(cyan_w, 1.3) * weight
    magenta_w = np.power(magenta_w, 1.3) * weight
    
    red_corr = np.array(calibration["red"], dtype=np.float32) - 1.0
    green_corr = np.array(calibration["green"], dtype=np.float32) - 1.0
    blue_corr = np.array(calibration["blue"], dtype=np.float32) - 1.0
    yellow_corr = np.array(calibration["yellow"], dtype=np.float32) - 1.0
    cyan_corr = np.array(calibration["cyan"], dtype=np.float32) - 1.0
    magenta_corr = np.array(calibration["magenta"], dtype=np.float32) - 1.0
    
    lut *= (
        1.0
        + red_w[..., None] * red_corr
        + green_w[..., None] * green_corr
        + blue_w[..., None] * blue_corr
        + yellow_w[..., None] * yellow_corr
        + cyan_w[..., None] * cyan_corr
        + magenta_w[..., None] * magenta_corr
    )
    
    lut = np.clip(lut, 0.0, 1.0)
    
    return np.ascontiguousarray(lut.astype(np.float32))


def generate_3d_lut_async(calibration: dict, size: int, callback=None) -> None:
    """
    Asynchronous 3D LUT generation using thread pool
    
    Args:
        calibration: calibration dictionary
        size: LUT size
        callback: callback function with result (called in main thread)
    """
    def worker():
        lut = generate_3d_lut(calibration, size)
        if callback:
            callback(lut)
    
    LUT_THREAD_POOL.submit(worker)


def shutdown_lut_pool():
    """Close thread pool on program termination"""
    try:
        LUT_THREAD_POOL.shutdown(wait=False)
    except Exception:
        pass


# Backward compatibility - export generate_3d_lut_async function

def apply_lut_generic(frame: np.ndarray, lut: np.ndarray) -> np.ndarray:
    """Apply LUT to frame via trilinear interpolation with cache optimization
    
    Uses optimized implementation from cache_optimizer if available.
    
    Args:
        frame: Input frame [H, W, 3] with float32 values in [0, 1]
        lut: 3D LUT array [size, size, size, 3] with float32 values
    
    Returns:
        Processed frame with same dimensions as input
    """
    # Use optimized implementation if available
    if HAS_CACHE_OPTIMIZER and _cache_apply_lut is not None:
        return _cache_apply_lut(frame, lut)
    
    # Fallback implementation
    size = lut.shape[0] - 1
    pos = frame * size
    
    i0 = np.floor(pos).astype(np.int32)
    i1 = np.clip(i0 + 1, 0, size)
    
    f = pos - i0
    fx = f[..., 0:1]
    fy = f[..., 1:2]
    fz = f[..., 2:3]
    
    c000 = lut[i0[..., 0], i0[..., 1], i0[..., 2]]
    c100 = lut[i1[..., 0], i0[..., 1], i0[..., 2]]
    c010 = lut[i0[..., 0], i1[..., 1], i0[..., 2]]
    c110 = lut[i1[..., 0], i1[..., 1], i0[..., 2]]
    
    c001 = lut[i0[..., 0], i0[..., 1], i1[..., 2]]
    c101 = lut[i1[..., 0], i0[..., 1], i1[..., 2]]
    c011 = lut[i0[..., 0], i1[..., 1], i1[..., 2]]
    c111 = lut[i1[..., 0], i1[..., 1], i1[..., 2]]
    
    # Optimized trilinear interpolation
    c00 = (c000 * (1.0 - fx)) + (c100 * fx)
    c01 = (c001 * (1.0 - fx)) + (c101 * fx)
    c10 = (c010 * (1.0 - fx)) + (c110 * fx)
    c11 = (c011 * (1.0 - fx)) + (c111 * fx)
    
    c0 = (c00 * (1.0 - fy)) + (c10 * fy)
    c1 = (c01 * (1.0 - fy)) + (c11 * fy)
    
    return (c0 * (1.0 - fz)) + (c1 * fz)


def apply_ambilight(frame: np.ndarray, percent: float, power: float = 2.0) -> np.ndarray:
    """Apply Ambilight effect to frame with cache optimization
    
    Uses optimized implementation from cache_optimizer if available.
    
    Args:
        frame: Input frame [H, W, 3] with float32 values in [0, 1]
        percent: Border percentage (0-1) for ambilight effect
        power: Power for falloff calculation
    
    Returns:
        Frame with ambilight applied to edges
    """
    # Use optimized implementation if available
    if HAS_CACHE_OPTIMIZER and _cache_apply_ambi is not None:
        return _cache_apply_ambi(frame, percent, power)
    
    # Fallback implementation with optimized code path
    if percent <= 0:
        return frame
    
    h, w = frame.shape[:2]
    
    dx = max(1, int(w * percent))
    dy = max(1, int(h * percent))
    
    def edge_accum(region, axis, alpha: float = 0.6, perc: float = 90):
        mean = np.mean(region, axis=axis)
        bright = np.percentile(region, perc, axis=axis)
        return (1 - alpha) * mean + alpha * bright
    
    top_mean = edge_accum(frame[:dy, :, :], axis=0)
    bot_mean = edge_accum(frame[h-dy:h, :, :], axis=0)
    left_mean = edge_accum(frame[:, :dx, :], axis=1)
    right_mean = edge_accum(frame[:, w-dx:w, :], axis=1)
    
    out = frame.copy()
    
    y = np.arange(h)[:, None]
    x = np.arange(w)[None, :]
    
    top_d = np.clip(y / dy, 0, 1)
    bot_d = np.clip((h - 1 - y) / dy, 0, 1)
    left_d = np.clip(x / dx, 0, 1)
    right_d = np.clip((w - 1 - x) / dx, 0, 1)
    
    top_w = (1.0 - top_d) ** power
    bot_w = (1.0 - bot_d) ** power
    left_w = (1.0 - left_d) ** power
    right_w = (1.0 - right_d) ** power
    
    # Create (h, w) weight arrays for broadcasting with color dimension
    sum_w = top_w + bot_w + left_w + right_w
    sum_w[sum_w == 0] = 1.0
    
    top_w /= sum_w
    bot_w /= sum_w
    left_w /= sum_w
    right_w /= sum_w
    
    # Compute weighted average - correct broadcasting:
    # top_mean: (w, 3) -> [None, :, :] -> (1, w, 3)
    # bot_mean: (w, 3) -> [None, :, :] -> (1, w, 3)
    # left_mean: (h, 3) -> [:, None, :] -> (h, 1, 3)
    # right_mean: (h, 3) -> [:, None, :] -> (h, 1, 3)
    # top_w/bot_w: (h, w) -> [..., None] -> (h, w, 1)
    # left_w/right_w: (h, w) -> [..., None] -> (h, w, 1)
    ambi = (
        top_mean[None, :, :] * top_w[..., None] +
        bot_mean[None, :, :] * bot_w[..., None] +
        left_mean[:, None, :] * left_w[..., None] +
        right_mean[:, None, :] * right_w[..., None]
    )
    
    mask = (y < dy) | (y >= h - dy) | (x < dx) | (x >= w - dx)
    
    out[mask] = ambi[mask]
    
    return out


def apply_saturation(tensor: np.ndarray, strength: float) -> np.ndarray:
    """Apply saturation to frame with cache optimization
    
    Uses optimized implementation from cache_optimizer if available.
    
    Args:
        tensor: Input frame [H, W, 3] with float32 values in [0, 1]
        strength: Saturation strength (1.0 = no change)
    
    Returns:
        Saturated frame with same dimensions as input
    """
    # Use optimized implementation if available
    if HAS_CACHE_OPTIMIZER and _cache_apply_sat is not None:
        return _cache_apply_sat(tensor, strength)
    
    # Fallback optimized implementation
    luma = (
        tensor[..., 0] * 0.2126 +
        tensor[..., 1] * 0.7152 +
        tensor[..., 2] * 0.0722
    )[..., None]
    
    return luma + (tensor - luma) * strength


def generate_pq_exponential(strength: float = 3.0, points: int = 64) -> np.ndarray:
    """Generate PQ exponential curve"""
    x = np.linspace(0.0, 1.0, points)
    y = np.power(x, strength)
    return np.clip(y, 0.0, 1.0).astype(np.float32)


def apply_shadow_bias_to_curve(y: np.ndarray, bias: float) -> np.ndarray:
    """Apply shadow bias to PQ curve"""
    
    if bias <= 0.0:
        return y
    
    n = len(y)
    idx = np.arange(n)
    
    start = 0
    peak = 10
    mid = 14
    end = 20
    
    weight = np.zeros_like(y)
    
    t1 = (idx - start) / (peak - start)
    t1 = np.clip(t1, 0.0, 1.0)
    rise = np.sin(t1 * np.pi / 2.0)
    
    t2 = (idx - peak) / (mid - peak)
    t2 = np.clip(t2, 0.0, 1.0)
    fall1 = 1.0 - 0.01 * np.sin(t2 * np.pi / 2.0)
    
    w_mid = 0.99
    
    t3 = (idx - mid) / (end - mid)
    t3 = np.clip(t3, 0.0, 1.0)
    
    target = 0.01
    fall2 = target + (w_mid - target) * np.cos(t3 * np.pi / 2.0)
    
    mask_rise = (idx >= start) & (idx <= peak)
    mask_fall1 = (idx >= peak) & (idx <= mid)
    mask_fall2 = (idx >= mid) & (idx <= end)
    
    weight[mask_rise] = rise[mask_rise]
    weight[mask_fall1] = fall1[mask_fall1]
    weight[mask_fall2] = fall2[mask_fall2]
    
    weight[idx > end] = 0.0
    weight[idx < start] = 0.0
    
    lift = (1.0 - y)
    bias = bias ** 1.2
    
    out = y + bias * lift * weight
    
    return np.clip(out, 0.0, 1.0)


def apply_custom_gamma(tensor: np.ndarray, gamma_sdr_values: np.ndarray, 
                       gamma_mode: str = "rgb",
                       gamma_sdr_r: np.ndarray = None,
                       gamma_sdr_g: np.ndarray = None,
                       gamma_sdr_b: np.ndarray = None) -> np.ndarray:
    """Apply custom gamma to SDR tensor"""
    
    if gamma_mode == "rgb":
        y = np.interp(tensor, np.linspace(0.0, 1.0, len(gamma_sdr_values)), gamma_sdr_values / 255.0)
        return y.astype(np.float32)
    
    out = np.empty_like(tensor, dtype=np.float32)
    
    # BGR order for CUDA compatibility
    out[..., 0] = np.interp(tensor[..., 0], np.linspace(0.0, 1.0, len(gamma_sdr_b)), gamma_sdr_b / 255.0)  # B
    out[..., 1] = np.interp(tensor[..., 1], np.linspace(0.0, 1.0, len(gamma_sdr_g)), gamma_sdr_g / 255.0)  # G
    out[..., 2] = np.interp(tensor[..., 2], np.linspace(0.0, 1.0, len(gamma_sdr_r)), gamma_sdr_r / 255.0)  # R
    
    return out.astype(np.float32)


def apply_pq_curve(tensor: np.ndarray, pq_values: np.ndarray, 
                   pq_nits: np.ndarray, mode: str = "rgb",
                   pq_values_r: np.ndarray = None,
                   pq_values_g: np.ndarray = None,
                   pq_values_b: np.ndarray = None) -> np.ndarray:
    """Apply PQ curve to HDR tensor"""
    
    x = np.clip(tensor * 80.0, 0.0, 10000.0)
    
    if mode == "rgb":
        y = np.interp(x, pq_nits, pq_values)
        return y.astype(np.float32)
    
    out = np.empty_like(tensor, dtype=np.float32)
    
    out[..., 0] = np.interp(x[..., 0], pq_nits, pq_values_b)  # B
    out[..., 1] = np.interp(x[..., 1], pq_nits, pq_values_g)  # G
    out[..., 2] = np.interp(x[..., 2], pq_nits, pq_values_r)  # R
    
    return out.astype(np.float32)


def apply_phys_lin_tonemap(tensor: np.ndarray, gamma: float,
                           gamma_enabled: bool,
                           hdr_active: bool,
                           tonemap_mode: str = "pq",
                           clip_nits: int = 1000,
                           pq_values: np.ndarray = None,
                           pq_nits: np.ndarray = None) -> dict:
    """Apply physical linear tone mapping"""
    
    if hdr_active and tonemap_mode == "pq":
        if pq_values is not None and pq_nits is not None:
            mapped = apply_pq_curve(tensor, pq_values, pq_nits)
            result_wled = mapped
        else:
            result_wled = np.clip(tensor, 0.0, 1.0)
    else:
        clip_val = float(clip_nits) if hdr_active else 1000.0
        
        tensor_nits = tensor * 80.0
        tensor_nits = np.clip(tensor_nits, 0.0, clip_val)
        
        normalized = tensor_nits / clip_val
        
        if gamma_enabled:
            mapped = np.power(normalized, 1.0 / gamma)
        else:
            mapped = normalized
        
        result_wled = mapped
    
    out = np.clip(result_wled, 0.0, 1.0).astype(np.float32)
    
    return {
        "wled": out,
        "preview": out
    }


class ImageProcessor:
    """Class for image processing"""
    
    def __init__(self):
        self.pq_points = 64
        self.pq_values = generate_pq_exponential(strength=3.0, points=self.pq_points)
        self.pq_values_r = self.pq_values.copy()
        self.pq_values_g = self.pq_values.copy()
        self.pq_values_b = self.pq_values.copy()
        self.pq_nits = np.array(PQ_NITS, dtype=np.float32)
    
    def rebuild_pq_curve(self, strength: float = 3.0, bias: float = 0.0):
        """Rebuild PQ curve"""
        base = generate_pq_exponential(strength=strength, points=self.pq_points)
        self.pq_values = apply_shadow_bias_to_curve(base, bias)
        
        # Reset other channels
        self.pq_values_r[:] = self.pq_values[:]
        self.pq_values_g[:] = self.pq_values[:]
        self.pq_values_b[:] = self.pq_values[:]
    
    def apply_led_calibration(self, tensor: np.ndarray,
                              lut: np.ndarray = None,
                              size: int = 64,
                              calibration: dict = None) -> np.ndarray:
        """Apply LED calibration via LUT"""
        
        if lut is not None:
            return apply_lut_generic(np.clip(tensor, 0.0, 1.0), lut)
        
        # If LUT not provided, generate based on calibration
        if calibration is None:
            calibration = DEFAULT_CALIBRATION
        
        size = max(2, int(size))
        frame = np.clip(tensor, 0.0, 1.0)
        
        lut = generate_3d_lut(calibration, size=size)
        return apply_lut_generic(frame, lut)
    
    def apply_led_calibration_with_external(self, tensor: np.ndarray,
                                           external_lut: np.ndarray = None,
                                           calibration: dict = None,
                                           lut_size: int = 64) -> np.ndarray:
        """Apply LED calibration with external LUT support"""
        
        if external_lut is not None:
            return apply_lut_generic(np.clip(tensor, 0.0, 1.0), external_lut)
        
        lut = generate_3d_lut(calibration or {}, size=max(2, int(lut_size)))
        return self.apply_led_calibration(tensor, lut=lut, size=lut_size, calibration=calibration)
    
    def apply_tonemap(self, tensor: np.ndarray,
                      gamma: float,
                      gamma_enabled: bool,
                      hdr_active: bool,
                      tonemap_mode: str = "pq",
                      clip_nits: int = 1000) -> dict:
        """Apply tone mapping"""
        
        if not hdr_active or tonemap_mode != "pq":
            return apply_phys_lin_tonemap(
                tensor, gamma, gamma_enabled,
                hdr_active, tonemap_mode="gamma",
                clip_nits=clip_nits
            )
        
        # PQ mode
        mapped = apply_pq_curve(tensor, self.pq_values, self.pq_nits)
        out = np.clip(mapped, 0.0, 1.0).astype(np.float32)
        
        return {"wled": out, "preview": out}
    
    def apply_tonemap_separate_rgb(self, tensor: np.ndarray,
                                   gamma: float,
                                   gamma_enabled: bool,
                                   hdr_active: bool,
                                   tonemap_mode: str = "pq",
                                   clip_nits: int = 1000) -> dict:
        """Apply tone mapping with separate RGB channels"""
        
        if not hdr_active or tonemap_mode != "pq":
            return apply_phys_lin_tonemap(
                tensor, gamma, gamma_enabled,
                hdr_active, tonemap_mode="gamma",
                clip_nits=clip_nits
            )
        
        # PQ mode with separate RGB channels
        x = np.clip(tensor * 80.0, 0.0, 10000.0)
        
        out = np.empty_like(tensor, dtype=np.float32)
        out[..., 0] = np.interp(x[..., 0], self.pq_nits, self.pq_values_b)  # B
        out[..., 1] = np.interp(x[..., 1], self.pq_nits, self.pq_values_g)  # G
        out[..., 2] = np.interp(x[..., 2], self.pq_nits, self.pq_values_r)  # R
        
        out = np.clip(out, 0.0, 1.0).astype(np.float32)
        
        return {"wled": out, "preview": out}


# Global instance
image_processor = ImageProcessor()
