"""
LED Matrix Thermal Model
------------------------
Background heat simulation for the "Temp Map" window.

Heat source:
    Per-LED heat power (W) = electrical power of the crystals scaled by
    the current brightness, minus the share that leaves as light (КПД):

        heat = V * [  bR * I_R_max * (1 - eff_R)
                   + bG * I_G_max * (1 - eff_G)
                   + bB * I_B_max * (1 - eff_B) ]

    MCU/controller heat (V * I_MCU) is added once for the whole
    matrix via update(mcu_ma=...).

    PSU / wire losses (loss_pct, ~5%) are intentionally NOT included
    (per specification).

Cooling:
    - natural convection (horizontal / vertical plate, Correl. Nusselt)
    - thermal radiation (Stefan-Boltzmann, linearized)
    - heat accumulation in the aluminium substrate
    - heat diffusion through the substrate (Gaussian step, exact form
      sigma = sqrt(2 * alpha * dt), implemented with numpy only - no scipy)

The model keeps its temperature state between updates, so heat
accumulates over time and cools back to ambient when the LED load drops.
"""

from dataclasses import dataclass
from typing import Optional

import numpy as np


# ============================================================
# PHYSICAL CONSTANTS
# ============================================================

SIGMA = 5.670374419e-8       # Stefan-Boltzmann, W/(m2*K4)
G = 9.81                     # gravity, m/s2
RHO_AL = 2700.0              # aluminium density, kg/m3
CP_AL = 900.0                # aluminium specific heat, J/(kg*K)
K_AL = 205.0                 # aluminium thermal conductivity, W/(m*K)

RHO_AIR = 1.225              # kg/m3
CP_AIR = 1005.0              # J/(kg*K)
NU_AIR = 1.56e-5             # m2/s
K_AIR = 0.0262               # W/(m*K)
PR_AIR = 0.71

LED_SUPPLY_VOLTAGE = 5.0     # V, matches main.py (_compute_led_power)


# ============================================================
# CONFIG
# ============================================================

@dataclass
class ThermalConfig:

    # MATRIX (LED grid, cells after mapping)
    leds_x: int = 120
    leds_y: int = 68

    # LED density, LEDs per meter (from the LED Settings window)
    density_x: float = 100.0
    density_y: float = 100.0

    # If not None, these override dimensions calculated from LED density
    width_m: Optional[float] = None
    height_m: Optional[float] = None

    # THERMAL GRID (matrix aspect ratio preserved, longest side capped)
    thermal_max_size: int = 600

    # ALUMINIUM SUBSTRATE
    thickness_m: float = 0.001

    # ENVIRONMENT
    ambient_temperature_c: float = 25.0

    # ORIENTATION ("horizontal" or "vertical")
    orientation: str = "vertical"

    # For horizontal orientation:
    # "up"   = open/hot surface faces upward
    # "down" = open/hot surface faces downward
    horizontal_hot_side: str = "up"

    # COOLING
    # 1 = одна сторона полностью открыта, вторая ограничена/закрыта
    # 2 = обе стороны полностью открыты
    cooling_sides: int = 1

    # Конвекция закрытой стороны относительно полностью открытой
    enclosed_side_convection_factor: float = 0.20

    # Излучение закрытой стороны относительно полностью открытой
    enclosed_side_radiation_factor: float = 0.30

    # MCU / controller physical position
    # 0.0 ... 1.0 across the matrix
    mcu_x: float = 0.0
    mcu_y: float = 0.0

    # SURFACE
    emissivity: float = 0.84

    # VERTICAL PLUME (hot air rising along the panel)
    vertical_plume_strength: float = 1.0
    vertical_air_mixing: float = 0.015

    # NUMERICS
    # Maximum simulation timestep (s)
    max_dt: float = 1.0 / 30.0

    initial_temperature_c: Optional[float] = None


# ============================================================
# NUMPY-ONLY GAUSSIAN (separable, no scipy)
# ============================================================

def _gaussian_kernel(sigma: float) -> np.ndarray:
    """1D normalized Gaussian kernel with odd length covering ~3 sigma."""
    if sigma < 1e-6:
        return np.array([1.0])
    r = int(np.ceil(3.0 * sigma))
    if r < 1:
        r = 1
    x = np.arange(-r, r + 1, dtype=np.float64)
    k = np.exp(-(x * x) / (2.0 * sigma * sigma))
    k /= k.sum()
    return k


def _convolve_axis(arr: np.ndarray, kernel: np.ndarray, axis: int) -> np.ndarray:
    """Separable 1D convolution along one axis with 'reflect' borders."""
    k = kernel.shape[0]
    pad = k // 2
    if pad == 0:
        return arr * float(kernel[0])
    padded = np.pad(
        arr,
        [(pad, pad) if a == axis else (0, 0) for a in range(arr.ndim)],
        mode="reflect",
    )
    n = arr.shape[axis]
    out = np.zeros_like(arr)
    for i in range(k):
        sl = [slice(None)] * arr.ndim
        sl[axis] = slice(i, i + n)
        out += padded[tuple(sl)] * float(kernel[i])
    return out


def gaussian_filter2d(arr: np.ndarray, sigma_x: float, sigma_y: float) -> np.ndarray:
    """2D Gaussian smoothing (numpy only) with reflect borders."""
    out = arr
    if sigma_y >= 0.15:
        out = _convolve_axis(out, _gaussian_kernel(sigma_y), axis=0)
    if sigma_x >= 0.15:
        out = _convolve_axis(out, _gaussian_kernel(sigma_x), axis=1)
    return out


# ============================================================
# THERMAL MODEL
# ============================================================

class LEDThermalModel:

    def __init__(self, config: ThermalConfig):
        self.cfg = config

        if self.cfg.orientation not in ("horizontal", "vertical"):
            raise ValueError("orientation must be 'horizontal' or 'vertical'")

        if self.cfg.horizontal_hot_side not in ("up", "down"):
            raise ValueError(
                "horizontal_hot_side must be 'up' or 'down'"
            )

        if self.cfg.cooling_sides not in (1, 2):
            raise ValueError("cooling_sides must be 1 or 2")

        # PHYSICAL DIMENSIONS (m)
        if config.width_m is not None:
            self.width_m = float(config.width_m)
        else:
            self.width_m = max(config.leds_x / max(config.density_x, 1.0), 0.01)

        if config.height_m is not None:
            self.height_m = float(config.height_m)
        else:
            self.height_m = max(config.leds_y / max(config.density_y, 1.0), 0.01)

        # THERMAL GRID (aspect ratio preserved, max 600 px)
        base = float(config.thermal_max_size) / float(
            max(config.leds_x, config.leds_y, 1)
        )
        self.nx = max(2, int(round(config.leds_x * base)))
        self.ny = max(2, int(round(config.leds_y * base)))

        self.dx = self.width_m / self.nx
        self.dy = self.height_m / self.ny
        self.cell_area = self.dx * self.dy

        # Thermal mass per square meter of substrate, J/(m2*K)
        self.heat_capacity_per_area = RHO_AL * CP_AL * self.cfg.thickness_m
        # Thermal diffusivity, m2/s
        self.alpha = K_AL / (RHO_AL * CP_AL)

        # TEMPERATURE STATE (kept between updates - heat accumulates)
        initial_t = (
            config.initial_temperature_c
            if config.initial_temperature_c is not None
            else config.ambient_temperature_c
        )
        self.temperature_c = np.full(
            (self.ny, self.nx), float(initial_t), dtype=np.float32
        )

        # LED -> THERMAL GRID MAP (positions after mapping)
        self._create_led_mapping()

        # VERTICAL PLUME STATE
        self.air_profile_c = np.full(
            self.ny, config.ambient_temperature_c, dtype=np.float32
        )

    # ========================================================
    # PROPERTIES / STATS
    # ========================================================

    @property
    def led_count(self) -> int:
        return int(self.led_cell_indices.size)

    @property
    def area_m2(self) -> float:
        return self.width_m * self.height_m

    @property
    def sheet_mass_kg(self) -> float:
        return self.width_m * self.height_m * self.cfg.thickness_m * RHO_AL

    # ========================================================
    # LED MAPPING
    # ========================================================

    def _create_led_mapping(self):
        """Default mapping: LED (r, c) sits at grid cell (c, r)."""
        rows = np.arange(self.cfg.leds_y, dtype=np.int32)
        cols = np.arange(self.cfg.leds_x, dtype=np.int32)
        idx = (
            np.repeat(rows[:, None], self.cfg.leds_x, axis=1) *
            self.cfg.leds_x + cols[None, :]
        ).ravel().astype(np.int64)
        self.led_cell_indices = idx

    def set_led_positions(self, rows: np.ndarray, cols: np.ndarray):
        """
        Set explicit LED positions after mapping.

        rows / cols : 1D arrays, same length - LED cell coordinates
                      (0..leds_y-1, 0..leds_x-1) in WLED mapping order.
        """
        rows = np.asarray(rows, dtype=np.int32).ravel()
        cols = np.asarray(cols, dtype=np.int32).ravel()
        if rows.shape != cols.shape:
            raise ValueError("rows and cols must have the same length")
        if rows.size == 0:
            self._create_led_mapping()
            return

        rows = np.clip(rows, 0, self.cfg.leds_y - 1)
        cols = np.clip(cols, 0, self.cfg.leds_x - 1)

        px = (cols + 0.5) / self.cfg.leds_x * self.width_m
        py = (rows + 0.5) / self.cfg.leds_y * self.height_m

        gx = np.clip((px / self.width_m * self.nx).astype(np.int32), 0, self.nx - 1)
        gy = np.clip((py / self.height_m * self.ny).astype(np.int32), 0, self.ny - 1)

        self.led_cell_indices = (
            gy.astype(np.int64) * self.nx + gx.astype(np.int64)
        )

    # ========================================================
    # BRIGHTNESS -> HEAT POWER (W per LED)
    # ========================================================

    def brightness_to_heat_power(self, rgb: np.ndarray, st: dict) -> np.ndarray:
        """
        Per-LED heat power in watts.

        rgb : (N, 3) float array 0.0-1.0 (R, G, B brightness)
              in the same order as the LED positions (after mapping).
        st  : live LED settings dict (r_ma, g_ma, b_ma,
              eff_r_pct, eff_g_pct, eff_b_pct) from the Settings window.

        heat = V * ( bR*I_R*(1-eff_R) + bG*I_G*(1-eff_G) + bB*I_B*(1-eff_B) )
        PSU/wire losses are NOT included (per specification).
        MCU/controller heat is NOT included here - it is added ONCE for
        the whole matrix in update() (mcu_ma parameter).
        """
        rgb = np.asarray(rgb, dtype=np.float32).reshape(-1, 3)

        v = LED_SUPPLY_VOLTAGE
        heat = (
            rgb[:, 0] * float(st.get("r_ma", 0.0)) *
            (1.0 - float(st.get("eff_r_pct", 0.0)) / 100.0)
            + rgb[:, 1] * float(st.get("g_ma", 0.0)) *
            (1.0 - float(st.get("eff_g_pct", 0.0)) / 100.0)
            + rgb[:, 2] * float(st.get("b_ma", 0.0)) *
            (1.0 - float(st.get("eff_b_pct", 0.0)) / 100.0)
        ) * v / 1000.0  # mA -> A
        # MCU/controller heat is global for the whole matrix,
        # not per LED.
        return heat.astype(np.float32)

    # ========================================================
    # NATURAL CONVECTION
    # ========================================================

    @staticmethod
    def _vertical_nusselt(delta_t, characteristic_length):
        delta_t = np.maximum(np.abs(delta_t), 0.001)
        beta = 1.0 / 300.0
        ra = (
            G * beta * delta_t * characteristic_length ** 3 /
            (NU_AIR ** 2) * PR_AIR
        )
        ra = np.maximum(ra, 1.0)
        denominator = (
            1.0 + (0.492 / PR_AIR) ** (9.0 / 16.0)
        ) ** (8.0 / 27.0)
        nu = (
            0.825 +
            0.387 * ra ** (1.0 / 6.0) / denominator
        ) ** 2
        return nu

    @staticmethod
    def _horizontal_nusselt(delta_t, characteristic_length):
        delta_t = np.maximum(delta_t, 0.001)
        beta = 1.0 / 300.0
        ra = (
            G * beta * delta_t * characteristic_length ** 3 /
            (NU_AIR ** 2) * PR_AIR
        )
        if ra < 1e5:
            nu = 0.54 * max(ra, 1.0) ** 0.25
        elif ra < 1e10:
            nu = 0.54 * ra ** 0.25
        else:
            nu = 0.15 * ra ** (1.0 / 3.0)
        return nu

    def _calculate_convection(
        self,
        temperature_c: np.ndarray
    ):
        """
        Natural convection from the exposed surfaces.

        cooling_sides:
            1 = one fully exposed side + one restricted side
            2 = both sides fully exposed

        Vertical:
            air temperature increases from bottom to top.

        Horizontal:
            upper and lower surfaces use different natural
            convection correlations.
        """

        ambient = float(
            self.cfg.ambient_temperature_c
        )

        # ====================================================
        # VERTICAL
        # ====================================================

        if self.cfg.orientation == "vertical":

            air_temp = (
                self._calculate_vertical_air_temperature(
                    temperature_c
                )
            )

            air_ref = air_temp.reshape(
                -1,
                1
            )

            delta_t = np.maximum(
                temperature_c - air_ref,
                0.0
            )

            characteristic = max(
                self.height_m,
                1e-6
            )

            nu = self._vertical_nusselt(
                delta_t,
                characteristic
            )

            h_open = (
                nu *
                K_AIR /
                characteristic
            )

            h_open = np.maximum(
                h_open,
                0.5
            )

            # Fully exposed aluminum side
            q_open = (
                h_open *
                delta_t
            )

            if self.cfg.cooling_sides == 1:

                # Restricted LED / enclosure side
                q_restricted = (
                    h_open *
                    self.cfg.enclosed_side_convection_factor *
                    delta_t
                )

                q_conv = (
                    q_open +
                    q_restricted
                )

            else:

                # Two fully exposed sides
                q_conv = (
                    2.0 *
                    q_open
                )

            return (
                q_conv.astype(np.float32),
                air_temp
            )

        # ====================================================
        # HORIZONTAL
        # ====================================================

        delta_t = np.maximum(
            temperature_c - ambient,
            0.01
        )

        characteristic = max(
            np.sqrt(
                self.width_m *
                self.height_m
            ),
            1e-6
        )

        beta = 1.0 / 300.0

        ra = (
            G *
            beta *
            delta_t *
            characteristic ** 3 *
            PR_AIR /
            (NU_AIR ** 2)
        )

        ra = np.maximum(
            ra,
            1.0
        )

        # ----------------------------------------------------
        # Upward-facing hot surface
        # ----------------------------------------------------

        nu_up = np.where(
            ra < 1e7,
            0.54 * ra ** 0.25,
            0.15 * ra ** (1.0 / 3.0)
        )

        # ----------------------------------------------------
        # Downward-facing hot surface
        # ----------------------------------------------------

        nu_down = np.where(
            ra < 1e10,
            0.27 * ra ** 0.25,
            0.15 * ra ** (1.0 / 3.0)
        )

        h_up = (
            nu_up *
            K_AIR /
            characteristic
        )

        h_down = (
            nu_down *
            K_AIR /
            characteristic
        )

        h_up = np.maximum(
            h_up,
            0.5
        )

        h_down = np.maximum(
            h_down,
            0.25
        )

        # ====================================================
        # WHICH SIDE IS OPEN / HOT
        # ====================================================

        if self.cfg.horizontal_hot_side == "up":

            h_open = h_up
            h_restricted = h_down

        else:

            h_open = h_down
            h_restricted = h_up

        # ====================================================
        # ONE SIDE OPEN
        # ====================================================

        if self.cfg.cooling_sides == 1:

            q_open = (
                h_open *
                delta_t
            )

            q_restricted = (
                h_restricted *
                self.cfg.enclosed_side_convection_factor *
                delta_t
            )

            q_conv = (
                q_open +
                q_restricted
            )

        # ====================================================
        # TWO SIDES OPEN
        # ====================================================

        else:

            q_open = (
                h_up *
                delta_t
            )

            q_other = (
                h_down *
                delta_t
            )

            q_conv = (
                q_open +
                q_other
            )

        return (
            q_conv.astype(np.float32),
            np.full_like(
                temperature_c,
                ambient
            )
        )

    # ========================================================
    # VERTICAL HOT AIR (PLUME)
    # ========================================================

    def _calculate_vertical_air_temperature(
        self,
        temperature_c
    ):
        """
        Estimate vertical natural-convection air temperature.

        Air enters from the bottom at ambient temperature,
        absorbs heat from the exposed vertical surfaces,
        and becomes progressively warmer toward the top.
        """

        if self.cfg.orientation != "vertical":

            return np.full(
                self.ny,
                self.cfg.ambient_temperature_c,
                dtype=np.float32
            )

        ambient = float(
            self.cfg.ambient_temperature_c
        )

        # ====================================================
        # GLOBAL TEMPERATURE DIFFERENCE
        # ====================================================

        mean_delta = max(
            float(
                np.mean(
                    np.maximum(
                        temperature_c - ambient,
                        0.0
                    )
                )
            ),
            0.1
        )

        beta = 1.0 / 300.0

        # ====================================================
        # NATURAL AIR VELOCITY
        # ====================================================

        velocity = (
            0.5 *
            np.sqrt(
                G *
                beta *
                mean_delta *
                self.height_m
            )
        )

        velocity = max(
            velocity,
            0.005
        )

        # ====================================================
        # BOUNDARY LAYER
        # ====================================================

        boundary_layer = (
            5.0 *
            np.sqrt(
                NU_AIR *
                self.height_m /
                max(
                    G *
                    beta *
                    mean_delta,
                    1e-9
                )
            )
        )

        boundary_layer = np.clip(
            boundary_layer,
            0.001,
            0.05
        )

        # ====================================================
        # MASS FLOW
        # ====================================================

        mass_flow = (
            RHO_AIR *
            velocity *
            boundary_layer *
            self.width_m
        )

        mass_flow = max(
            mass_flow,
            1e-4
        )

        # ====================================================
        # LOCAL h
        # ====================================================

        delta_local = np.maximum(
            temperature_c - ambient,
            0.0
        )

        characteristic = max(
            self.height_m,
            1e-6
        )

        nu = self._vertical_nusselt(
            delta_local,
            characteristic
        )

        h_open = (
            nu *
            K_AIR /
            characteristic
        )

        h_open = np.maximum(
            h_open,
            0.5
        )

        # ====================================================
        # ONLY OPEN SURFACES HEAT THE OUTSIDE AIR
        # ====================================================

        if self.cfg.cooling_sides == 1:

            exposed_factor = 1.0

        else:

            exposed_factor = 2.0

        q_conv_air = (
            h_open *
            delta_local *
            exposed_factor
        )

        # ====================================================
        # HEAT INTO EACH HORIZONTAL AIR LAYER
        # ====================================================

        q_row = (
            np.sum(
                q_conv_air,
                axis=1
            ) *
            self.dx
        )

        # ====================================================
        # AIR TEMPERATURE PROFILE
        # ====================================================

        new_profile = np.full(
            self.ny,
            ambient,
            dtype=np.float64
        )

        air_temp = ambient

        # Bottom -> top
        for row in range(
            self.ny - 1,
            -1,
            -1
        ):

            dT_air = (
                q_row[row] /
                (
                    mass_flow *
                    CP_AIR
                )
            )

            dT_air *= (
                self.cfg.vertical_plume_strength
            )

            air_temp += dT_air

            # Mixing with ambient air
            mix = np.clip(
                self.cfg.vertical_air_mixing,
                0.0,
                1.0
            )

            air_temp = (
                air_temp *
                (1.0 - mix) +
                ambient *
                mix
            )

            new_profile[row] = air_temp

        # ====================================================
        # TEMPORAL SMOOTHING
        # ====================================================

        self.air_profile_c += (
            new_profile.astype(np.float32) -
            self.air_profile_c
        ) * 0.5

        return self.air_profile_c.copy()

    # ========================================================
    # RADIATION
    # ========================================================

    def _calculate_radiation(self, temperature_c: np.ndarray):
        """
        Radiation from open + restricted surfaces.
        """

        ambient_k = (
            self.cfg.ambient_temperature_c +
            273.15
        )

        surface_k = (
            temperature_c +
            273.15
        )

        h_rad = (
            self.cfg.emissivity *
            SIGMA *
            (
                surface_k +
                ambient_k
            ) *
            (
                surface_k ** 2 +
                ambient_k ** 2
            )
        )

        delta_t = (
            temperature_c -
            self.cfg.ambient_temperature_c
        )

        # Fully exposed aluminum side
        q_open = (
            h_rad *
            delta_t
        )

        if self.cfg.cooling_sides == 1:

            # Enclosed LED side still radiates partially.
            q_restricted = (
                h_rad *
                delta_t *
                self.cfg.enclosed_side_radiation_factor
            )

            q_rad = (
                q_open +
                q_restricted
            )

        else:

            q_rad = (
                2.0 *
                q_open
            )

        return q_rad.astype(np.float32)

    # ========================================================
    # THERMAL DIFFUSION (numpy-only Gaussian step)
    # ========================================================

    def _diffuse(self, temperature_c, dt):
        # Exact Gaussian diffusion: sigma = sqrt(2 * alpha * dt)
        sigma_m = np.sqrt(2.0 * self.alpha * dt)
        sigma_x = sigma_m / self.dx
        sigma_y = sigma_m / self.dy
        return gaussian_filter2d(
            temperature_c, sigma_x, sigma_y
        ).astype(np.float32)

    # ========================================================
    # SIMULATION STEP
    # ========================================================

    def update(
        self,
        heat_power_w: Optional[np.ndarray],
        dt: float,
        mcu_ma: float = 0.0,
    ):
        """
        Advance the simulation by dt seconds.

        heat_power_w : (N,) W per LED (same order as the LED positions)
                       or None / empty -> no LED heat (pure cooling).
        mcu_ma       : MCU/controller current in mA for the WHOLE
                       matrix (not per LED) - added once to the model.
        """
        if dt <= 0.0:
            return self.temperature_c
        dt = min(dt, self.cfg.max_dt)

        # ELECTRICAL HEAT SOURCE
        n_cells = self.nx * self.ny
        if heat_power_w is None or len(heat_power_w) == 0:
            power_map = np.zeros(n_cells, dtype=np.float32)
        else:
            heat = np.clip(
                np.asarray(heat_power_w, dtype=np.float32).ravel(), 0.0, None
            )
            if heat.shape[0] != self.led_cell_indices.shape[0]:
                # Mismatch (mapping changed) -> treat as no load this step
                power_map = np.zeros(n_cells, dtype=np.float32)
            else:
                power_map = np.bincount(
                    self.led_cell_indices, weights=heat, minlength=n_cells
                ).astype(np.float32)

        # MCU/controller heat is global for the whole matrix,
        # not per LED (added once, on a single corner cell near
        # the controller input zone).
        mcu_power_w = (
            float(mcu_ma) *
            LED_SUPPLY_VOLTAGE /
            1000.0
        )

        if mcu_power_w > 0.0:

            mcu_x = np.clip(
                float(self.cfg.mcu_x),
                0.0,
                1.0
            )

            mcu_y = np.clip(
                float(self.cfg.mcu_y),
                0.0,
                1.0
            )

            gx = int(
                round(
                    mcu_x *
                    (self.nx - 1)
                )
            )

            gy = int(
                round(
                    mcu_y *
                    (self.ny - 1)
                )
            )

            mcu_cell = (
                gy * self.nx +
                gx
            )

            power_map[mcu_cell] += (
                mcu_power_w
            )

        q_source = (power_map / self.cell_area).reshape(self.ny, self.nx)

        # COOLING
        q_conv, _air = self._calculate_convection(self.temperature_c)
        q_rad = self._calculate_radiation(self.temperature_c)

        q_net = q_source - q_conv - q_rad

        # TEMPERATURE CHANGE + DIFFUSION
        dT = q_net * dt / self.heat_capacity_per_area
        self.temperature_c += dT
        self.temperature_c = self._diffuse(self.temperature_c, dt)

        # Safety: if numerics blew up, return to ambient
        if not np.all(np.isfinite(self.temperature_c)):
            self.reset()
        return self.temperature_c

    # ========================================================
    # RESET
    # ========================================================

    def reset(self, temperature_c: Optional[float] = None):
        if temperature_c is None:
            temperature_c = self.cfg.ambient_temperature_c
        self.temperature_c.fill(float(temperature_c))
        self.air_profile_c.fill(float(self.cfg.ambient_temperature_c))

    # ========================================================
    # STATISTICS
    # ========================================================

    def statistics(self):
        t = self.temperature_c
        return {
            "min_c": float(np.min(t)),
            "max_c": float(np.max(t)),
            "mean_c": float(np.mean(t)),
            "sheet_area_m2": self.area_m2,
            "sheet_mass_kg": self.sheet_mass_kg,
        }


# ============================================================
# COLORMAP (inferno-like, numpy only)
# ============================================================

_INFERNO_STOPS = np.array([
    [0.00,   0,   0,   4],
    [0.13,  40,  11,  84],
    [0.25,  87,  17, 110],
    [0.38, 127,  38, 117],
    [0.50, 188,  57, 109],
    [0.63, 229, 127,  61],
    [0.75, 249, 179,  24],
    [0.88, 247, 233,  69],
    [1.00, 252, 255, 164],
], dtype=np.float64)


def thermal_colormap(
    t: np.ndarray,
    vmin: float,
    vmax: float,
    over_color: Optional[tuple] = None,
) -> np.ndarray:
    """Map a temperature array to RGB (H, W, 3) uint8, inferno-like.

    If ``over_color`` (e.g. (255, 255, 255)) is given, every cell with a
    temperature above ``vmax`` is painted with that color instead of being
    clamped to the end of the scale.
    """
    span = max(vmax - vmin, 1e-6)
    x = np.clip((np.asarray(t, dtype=np.float64) - vmin) / span, 0.0, 1.0)
    pos = _INFERNO_STOPS[:, 0]
    rgb = np.empty((x.shape[0], x.shape[1], 3), dtype=np.uint8)
    for i in range(3):
        vals = np.interp(x.ravel(), pos, _INFERNO_STOPS[:, i + 1])
        rgb[..., i] = vals.reshape(x.shape).astype(np.uint8)
    if over_color is not None:
        over_mask = np.asarray(t, dtype=np.float64) > float(vmax)
        if np.any(over_mask):
            rgb[over_mask, 0] = int(over_color[0])
            rgb[over_mask, 1] = int(over_color[1])
            rgb[over_mask, 2] = int(over_color[2])
    return rgb