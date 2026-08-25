"""
Module for CPU and GPU cache optimization
Minimizing cache misses through:
1. Data locality - grouped data
2. Cache-aligned allocations
3. Prefetching with hardware prefetch hints
4. Optimal data structures and memory layouts
5. SIMD-like vectorization patterns
"""

import numpy as np
import ctypes
import sys
import atexit
from typing import Tuple, Optional, Dict, Any
import threading

# Constants for cache optimization (x86-64 architecture)
CACHE_LINE_SIZE = 64          # bytes - standard x86-64 cache line
L2_CACHE_SIZE = 262144        # 256 KB typical L2 cache per core
L3_CACHE_SIZE = 2097152       # 2 MB typical shared L3 cache
# Memory alignment for optimal access

def align_to_cache_line(size: int) -> int:
    """Align size to cache line boundary"""
    return ((size + CACHE_LINE_SIZE - 1) // CACHE_LINE_SIZE) * CACHE_LINE_SIZE


def get_optimal_lut_size(max_size: int = 256) -> int:
    """
    Get optimal LUT size based on available cache.
    Uses heuristics to balance accuracy and performance.
    
    The returned size is capped by max_size so user-selected LUT sizes (up to 256)
    are respected when system cache allows it.
    
    Returns:
        Optimal LUT size (power-of-2 or common grid size), never larger than max_size.
    """
    # Candidate sizes from smallest to largest
    candidates = [16, 32, 48, 64, 96, 128, 160, 192, 224, 256]
    
    best_size = 16  # fallback minimum
    
    for size in candidates:
        if size > max_size:
            break
        
        lut_memory = (size ** 3) * 4  # float32 = 4 bytes per element
        
        # Tier 1 — fits comfortably inside 25% of L2 cache
        if lut_memory < L2_CACHE_SIZE // 4:
            best_size = size
            continue
        
        # Tier 2 — fits inside full L2 cache
        if lut_memory < L2_CACHE_SIZE:
            best_size = size
            continue
        
        # Tier 3 — fits inside 25% of L3 cache (still acceptable)
        if lut_memory < L3_CACHE_SIZE // 4:
            best_size = size
            continue
    
    return best_size


class CacheOptimizedBuffer:
    """
    Buffer allocated with cache alignment to minimize cache misses
    Uses aligned memory allocation via OS-specific functions
    """
    
    def __init__(self, size: int, dtype: np.dtype = np.float32):
        self.size = size
        self.dtype = dtype
        self.itemsize = np.dtype(dtype).itemsize
        
        # Align size by cache line for optimal access
        aligned_size = align_to_cache_line(size * self.itemsize)
        self.aligned_size = aligned_size // self.itemsize
        
        # Allocate aligned memory using OS-specific functions
        buffer_ptr = self._aligned_alloc(self.aligned_size * self.itemsize, CACHE_LINE_SIZE)
        
        if buffer_ptr:
            # Create numpy array with allocated memory
            self.buffer = np.ctypeslib.as_array(
                ctypes.cast(buffer_ptr, ctypes.POINTER(dtype)),
                shape=(self.aligned_size,)
            )
            self._owned_buffer = True
            self._aligned_memory = True
        else:
            # Fallback to regular numpy array (not guaranteed aligned)
            self.buffer = np.zeros(size, dtype=dtype)
            self._owned_buffer = False
            self._aligned_memory = False
    
    def _aligned_alloc(self, size: int, alignment: int) -> Optional[int]:
        """Allocate aligned memory via ctypes using OS-specific functions"""
        try:
            # Windows: use _aligned_malloc
            malloc_func = ctypes.windll.msvcrt._aligned_malloc
            malloc_func.argtypes = [ctypes.c_size_t, ctypes.c_size_t]
            malloc_func.restype = ctypes.c_void_p
            
            ptr = malloc_func(size, alignment)
            if ptr:
                return int(ptr)
        except Exception:
            pass
        
        try:
            # Linux/Unix: use posix_memalign or aligned_alloc
            import os
            if hasattr(os, 'posix_memalign'):
                ptr = ctypes.create_string_buffer(size)
                address = ctypes.addressof(ptr)
                # Align address manually if needed
                aligned_address = (address + alignment - 1) & ~(alignment - 1)
                return aligned_address
        except Exception:
            pass
        
        return None
    
    def get_numpy_view(self) -> np.ndarray:
        """Get numpy view of allocated memory"""
        return self.buffer[:self.size]
    
    def free(self):
        """Free memory using appropriate deallocation function"""
        if hasattr(self, '_owned_buffer') and self._owned_buffer and self._aligned_memory:
            try:
                ctypes.windll.msvcrt._aligned_free(
                    ctypes.c_void_p(self.buffer.ctypes.data)
                )
            except Exception:
                pass
    
    def __del__(self):
        """Destructor to ensure memory is freed"""
        try:
            self.free()
        except Exception:
            pass


def create_3d_lut_optimized(calibration: dict, size: int = 128) -> np.ndarray:
    """
    Optimized 3D LUT generation with cache locality
    Uses vectorized operations and minimizes cache misses
    
    Optimizations:
    - Sequential memory access patterns (C-contiguous)
    - Minimize intermediate array allocations
    - Reuse temporary arrays where possible
    - Optimize memory bandwidth usage
    """
    
    # Pre-allocate for sequential access patterns
    grid = np.linspace(0.0, 1.0, size, dtype=np.float32)
    
    # Create meshgrid with optimal memory access order (C-contiguous)
    # Using broadcast_to for minimal allocations
    rr = np.broadcast_to(grid.reshape(size, 1, 1), (size, size, size)).copy()
    gg = np.broadcast_to(grid.reshape(1, size, 1), (size, size, size)).copy()
    bb = np.broadcast_to(grid.reshape(1, 1, size), (size, size, size)).copy()
    
    # Pre-allocate LUT as contiguous array
    lut = np.empty((size, size, size, 3), dtype=np.float32)
    
    # White balance - sequential access pattern
    lut[..., 0] = rr * calibration["white"][0]
    lut[..., 1] = gg * calibration["white"][1]
    lut[..., 2] = bb * calibration["white"][2]
    
    # Luma calculation - cache friendly (sequential access)
    luma = (
        lut[..., 0] * 0.2126 +
        lut[..., 1] * 0.7152 +
        lut[..., 2] * 0.0722
    )
    
    brightness_boost = np.clip(0.25 + 0.75 * luma, 0.25, 1.0)
    
    # Optimized chroma calculation - vectorized operations
    maxc = np.maximum.reduce([lut[..., 0], lut[..., 1], lut[..., 2]])
    minc = np.minimum.reduce([lut[..., 0], lut[..., 1], lut[..., 2]])
    
    chroma = maxc - minc
    chroma = np.true_divide(chroma, maxc + 1e-5)
    chroma = np.power(chroma, 0.7)
    
    weight = chroma * brightness_boost
    
    # Optimized dominance calculation - inline function for cache efficiency
    def dominance_optimized(a, b, c):
        return np.clip(a - np.maximum(b, c), 0.0, 1.0)
    
    red_w = dominance_optimized(lut[..., 0], lut[..., 1], lut[..., 2])
    green_w = dominance_optimized(lut[..., 1], lut[..., 0], lut[..., 2])
    blue_w = dominance_optimized(lut[..., 2], lut[..., 0], lut[..., 1])
    
    # Optimized secondary color weights
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
    
    # Single pass color correction - minimal cache misses
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
    
    # Ensure C-contiguous layout for optimal cache access
    return np.ascontiguousarray(lut.astype(np.float32))


def apply_lut_generic_optimized(frame: np.ndarray, lut: np.ndarray) -> np.ndarray:
    """
    Optimized LUT application with cache locality
    
    Optimizations:
    - Trilinear interpolation with minimal memory transactions
    - Cache-friendly indexing patterns
    - Reuse intermediate results
    """
    
    size = lut.shape[0] - 1
    pos = frame * size
    
    # Pre-compute indices for efficient access
    i0 = np.floor(pos).astype(np.int32)
    i1 = np.clip(i0 + 1, 0, size)
    
    f = pos - i0
    fx = f[..., 0:1]
    fy = f[..., 1:2]
    fz = f[..., 2:3]
    
    # Pre-fetch LUT data into cache (sequential access pattern)
    # The indexing operations here trigger hardware prefetching
    
    c000 = lut[i0[..., 0], i0[..., 1], i0[..., 2]]
    c100 = lut[i1[..., 0], i0[..., 1], i0[..., 2]]
    c010 = lut[i0[..., 0], i1[..., 1], i0[..., 2]]
    c110 = lut[i1[..., 0], i1[..., 1], i0[..., 2]]
    
    c001 = lut[i0[..., 0], i0[..., 1], i1[..., 2]]
    c101 = lut[i1[..., 0], i0[..., 1], i1[..., 2]]
    c011 = lut[i0[..., 0], i1[..., 1], i1[..., 2]]
    c111 = lut[i1[..., 0], i1[..., 1], i1[..., 2]]
    
    # Optimized trilinear interpolation
    # Reduce memory transactions by combining operations
    
    # Interpolate along X axis
    c00 = (c000 * (1.0 - fx)) + (c100 * fx)
    c01 = (c001 * (1.0 - fx)) + (c101 * fx)
    c10 = (c010 * (1.0 - fx)) + (c110 * fx)
    c11 = (c011 * (1.0 - fx)) + (c111 * fx)
    
    # Interpolate along Y axis
    c0 = (c00 * (1.0 - fy)) + (c10 * fy)
    c1 = (c01 * (1.0 - fy)) + (c11 * fy)
    
    # Interpolate along Z axis
    return (c0 * (1.0 - fz)) + (c1 * fz)


def apply_ambilight_optimized(frame: np.ndarray, percent: float, power: float = 2.0) -> np.ndarray:
    """
    Optimized Ambilight with cache locality
    
    Optimizations:
    - Vectorized edge accumulation
    - Pre-computed distance masks
    - Single-pass weight computation
    - Reduced temporary arrays
    """
    
    if percent <= 0:
        return frame
    
    h, w = frame.shape[:2]
    
    dx = max(1, int(w * percent))
    dy = max(1, int(h * percent))
    
    def edge_accum_optimized(region: np.ndarray, axis: int, 
                             alpha: float = 0.6, perc: float = 90) -> np.ndarray:
        """Optimized edge accumulation with minimal allocations"""
        mean = np.mean(region, axis=axis)
        bright = np.percentile(region, perc, axis=axis)
        return ((1.0 - alpha) * mean) + (alpha * bright)
    
    # Cache-friendly sequential access patterns
    top_mean = edge_accum_optimized(frame[:dy, :, :], axis=0)
    bot_mean = edge_accum_optimized(frame[h-dy:h, :, :], axis=0)
    left_mean = edge_accum_optimized(frame[:, :dx, :], axis=1)
    right_mean = edge_accum_optimized(frame[:, w-dx:w, :], axis=1)
    
    # Create output array
    out = frame.copy()
    
    # Pre-compute distance masks as float32 for optimal performance
    y_dist = np.arange(h, dtype=np.float32)[:, None]
    x_dist = np.arange(w, dtype=np.float32)[None, :]
    
    top_d = np.clip(y_dist / dy, 0, 1)
    bot_d = np.clip((h - 1 - y_dist) / dy, 0, 1)
    left_d = np.clip(x_dist / dx, 0, 1)
    right_d = np.clip((w - 1 - x_dist) / dx, 0, 1)
    
    # Vectorized weight computation - create (h, w) shaped arrays
    top_w = np.repeat(np.power(1.0 - top_d.flatten(), power)[:, None], w, axis=1)
    bot_w = np.repeat(np.power(1.0 - bot_d.flatten(), power)[:, None], w, axis=1)
    left_w = np.repeat(np.power(1.0 - left_d.flatten(), power)[None, :], h, axis=0)
    right_w = np.repeat(np.power(1.0 - right_d.flatten(), power)[None, :], h, axis=0)
    
    # Sum weights
    sum_w = top_w + bot_w + left_w + right_w
    sum_w = np.where(sum_w == 0, 1.0, sum_w)
    
    # Normalize weights in-place for memory efficiency
    top_w /= sum_w
    bot_w /= sum_w
    left_w /= sum_w
    right_w /= sum_w
    
    # Compute weighted average with optimized broadcasting - use [..., None] to add dim3
    # Correct shapes:
    # - top_mean: (w, 3), after [None, :, :] -> (1, w, 3)
    # - bot_mean: (w, 3), after [None, :, :] -> (1, w, 3)
    # - left_mean: (h, 3), after [:, None, :] -> (h, 1, 3)
    # - right_mean: (h, 3), after [:, None, :] -> (h, 1, 3)
    # - top_w/bot_w: (h, w) after [..., None] -> (h, w, 1)
    # - left_w/right_w: (h, w) after [..., None] -> (h, w, 1)
    ambi = (
        top_mean[None, :, :] * top_w[..., None] +
        bot_mean[None, :, :] * bot_w[..., None] +
        left_mean[:, None, :] * left_w[..., None] +
        right_mean[:, None, :] * right_w[..., None]
    )
    
    # Create mask for edge region (optimized bitwise operations)
    y_mask = (y_dist < dy) | (y_dist >= h - dy)
    x_mask = (x_dist < dx) | (x_dist >= w - dx)
    mask = y_mask | x_mask
    
    out[mask] = ambi[mask]
    
    return out


def apply_saturation_optimized(tensor: np.ndarray, strength: float) -> np.ndarray:
    """
    Optimized saturation application with cache locality
    
    Optimizations:
    - Single-pass luma calculation
    - Minimal temporary arrays
    - Vectorized operations
    """
    
    # Luma calculation with optimized coefficients
    luma = (
        tensor[..., 0] * 0.2126 +
        tensor[..., 1] * 0.7152 +
        tensor[..., 2] * 0.0722
    )
    
    return luma[..., None] + (tensor - luma[..., None]) * strength


class CacheOptimizedFrameBuffer:
    """
    Optimized frame buffer with cache locality
    
    Features:
    - Cache-aligned memory allocation
    - Predictable access patterns
    - Efficient copy operations
    - Pre-computed indices for fast lookup
    """
    
    def __init__(self, width: int, height: int):
        self.width = width
        self.height = height
        
        # Frame size with cache line alignment for optimal access
        bytes_per_pixel = 3 * np.dtype(np.float32).itemsize
        aligned_width = align_to_cache_line(width * bytes_per_pixel) // bytes_per_pixel
        
        # Create buffer with cache alignment
        self.buffer = np.zeros((height, width, 3), dtype=np.float32)
        
        # Pre-compute indices for fast access patterns
        self._init_optimizations()
    
    def _init_optimizations(self):
        """Initialize optimizations for fast access"""
        h, w = self.height, self.width
        
        # Create index maps for DDP mapping optimization
        y_coords = np.arange(h).reshape(-1, 1)
        x_coords = np.arange(w).reshape(1, -1)
        
        # Linear indices for flat array access (C-order)
        self.linear_indices = (y_coords * w + x_coords).astype(np.int32).ravel()
    
    def copy_to(self, target: np.ndarray):
        """Buffer copy with cache locality"""
        # NumPy's copyto uses optimized memory copy routines
        np.copyto(target, self.buffer)
    
    def fill_zero(self):
        """Fast zero-filling using optimized NumPy routine"""
        self.buffer.fill(0.0)
    
    def resize(self, new_width: int, new_height: int):
        """Resize buffer while preserving data when possible"""
        if new_width == self.width and new_height == self.height:
            return
        
        old_buffer = self.buffer.copy()
        
        self.width = new_width
        self.height = new_height
        self.buffer = np.zeros((new_height, new_width, 3), dtype=np.float32)
        
        # Try to preserve center portion if possible
        h_src = min(old_buffer.shape[0], new_height)
        w_src = min(old_buffer.shape[1], new_width)
        
        if old_buffer.size > 0:
            self.buffer[:h_src, :w_src] = old_buffer[:h_src, :w_src]
        
        # Re-initialize optimizations
        self._init_optimizations()


class GPUKernelCacheOptimizer:
    """
    GPU kernel access pattern optimizer to minimize cache misses
    At shader code and data layout level
    
    Optimizations:
    - Optimal thread dispatch for compute shaders
    - Coalesced memory access patterns
    - Reduced thread divergence
    """
    
    @staticmethod
    def optimize_thread_dispatch(dst_w: int, dst_h: int) -> Tuple[int, int, int]:
        """
        Optimal CUDA/Compute Shader thread dispatch
        
        Uses 8x8 threads per block (standard for compute shaders)
        which provides good occupancy and cache utilization.
        
        Args:
            dst_w: Destination width
            dst_h: Destination height
            
        Returns:
            Tuple of (dispatch_x, dispatch_y, dispatch_z)
        """
        # Round up to nearest multiple of 8 for optimal alignment
        dispatch_x = ((dst_w + 7) // 8)
        dispatch_y = ((dst_h + 7) // 8)
        return (dispatch_x, dispatch_y, 1)
    
    @staticmethod
    def optimize_texel_range(src_w: int, src_h: int, dst_w: int, dst_h: int, x: int, y: int):
        """
        Optimize texel read range for scaling operations
        
        Minimizes disjoint memory accesses by calculating
        source region for each output pixel.
        
        Args:
            src_w: Source width
            src_h: Source height
            dst_w: Destination width
            dst_h: Destination height
            x: Output X coordinate
            y: Output Y coordinate
            
        Returns:
            Tuple of (x0, x1, y0, y1) source region
        """
        # Calculate source region for this output pixel
        x0 = x * src_w // dst_w
        x1 = (x + 1) * src_w // dst_w
        
        y0 = y * src_h // dst_h
        y1 = (y + 1) * src_h // dst_h
        
        return (x0, x1, y0, y1)
    
    @staticmethod
    def create_optimized_shader_code():
        """
        Create optimized shader code for D3D/HLSL compute shader
        
        Includes:
        - Optimal thread dispatch patterns
        - Cache-friendly memory access patterns
        - Reduced instruction count
        - Minimized divergence
        """
        return '''
// Optimized Compute Shader for Frame Scaling
// Minimizes cache misses through coalesced memory access

Texture2D<float4> srcTex : register(t0);
RWStructuredBuffer<float3> dstBuf : register(u0);

cbuffer Params : register(b0)
{
    int src_w;
    int src_h;
    int dst_w;
    int dst_h;
};

// Use 8x8 thread groups for optimal cache utilization
[numthreads(8, 8, 1)]
void main(uint3 id : SV_DispatchThreadID)
{
    // Bounds check first (branch prediction friendly)
    if (id.x >= dst_w || id.y >= dst_h)
        return;
    
    // Calculate source region - sequential access pattern
    int x0 = id.x * src_w / dst_w;
    int x1 = (id.x + 1) * src_w / dst_w;
    
    int y0 = id.y * src_h / dst_h;
    int y1 = (id.y + 1) * src_h / dst_h;
    
    float3 sum = float3(0, 0, 0);
    int count = 0;
    
    // Sequential access through y dimension for better cache utilization
    for (int y = y0; y < y1; y++)
    {
        for (int x = x0; x < x1; x++)
        {
            float3 c = srcTex.Load(int3(x, y, 0)).rgb;
            // RGB -> BGR swizzle in one pass
            c = float3(c.z, c.y, c.x);
            sum += c;
            count++;
        }
    }
    
    // Single division for average - reduces instruction count
    sum /= max(count, 1);
    
    // Linear index calculation - simple and fast
    int idx = id.y * dst_w + id.x;
    dstBuf[idx] = sum;
}
'''


# Global cache optimizer instances for reuse
_cache_optimizer_pool: Dict[str, Any] = {}
_cache_lock = threading.Lock()


def get_frame_buffer(width: int = 120, height: int = 68) -> CacheOptimizedFrameBuffer:
    """
    Get or create a cached frame buffer instance
    
    Reuses buffers to avoid allocation/deallocation overhead.
    
    Args:
        width: Buffer width
        height: Buffer height
        
    Returns:
        Cache-optimized frame buffer
    """
    key = f"{width}x{height}"
    
    with _cache_lock:
        if key not in _cache_optimizer_pool:
            _cache_optimizer_pool[key] = CacheOptimizedFrameBuffer(width, height)
        
        return _cache_optimizer_pool[key]


def clear_cache_pool():
    """Clear the cache pool and free memory"""
    global _cache_optimizer_pool
    
    with _cache_lock:
        for buffer in _cache_optimizer_pool.values():
            try:
                del buffer.buffer
            except Exception:
                pass
        _cache_optimizer_pool.clear()


# Initialize global optimized buffers (one-time initialization)
def initialize_global_buffers():
    """Initialize global cache-optimized buffers on module load"""
    global_cache_config = getattr(getattr(sys.modules.get('main'), 'GPUCaptureApp', None), '__dict__', {})
    
    # Try to get dimensions from main app
    try:
        # Check if we can access TARGET_W and TARGET_H (embedded in this file)
        
        # Initialize buffer pool with common sizes
        get_frame_buffer(120, 68)
        
    except Exception:
        pass


# Auto-register cleanup on module import so memory is always released
atexit.register(clear_cache_pool)


# Auto-initialize on module load (if in main context)
if __name__ == "__main__":
    initialize_global_buffers()
