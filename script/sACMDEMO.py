import socket
import struct
import time
import uuid
import json
import urllib.request


# ============================================================
# SETTINGS
# ============================================================

WLED_IP = "192.168.1.11"

START_PIXEL = 0
SEGMENT_LENGTH = 1024

BRIGHTNESS = 10
COLOR = (BRIGHTNESS, 0, 0)

FPS = 120.0
FRAME_TIME = 1.0 / FPS

# sACN
SACN_PORT = 5568
START_UNIVERSE = 1

# One RGB universe = 170 LEDs = 510 DMX channels
LEDS_PER_UNIVERSE = 170
CHANNELS_PER_UNIVERSE = LEDS_PER_UNIVERSE * 3

# sACN priority
PRIORITY = 100

# Unique CID for this sender
CID = uuid.uuid4().bytes

SOURCE_NAME = b"Spectr-aLED"


# ============================================================
# sACN PACKET
# ============================================================

def build_sacn_packet(
    universe,
    data,
    sequence,
    priority=PRIORITY
):
    """
    Build E1.31 / sACN Data Packet.

    data:
        RGB DMX data, maximum 510 bytes.
    """

    # --------------------------------------------------------
    # Root Layer
    # --------------------------------------------------------

    # Preamble
    preamble = struct.pack(
        "!HH",
        0x0010,
        0x0000
    )

    # ACN Packet Identifier
    acn_pid = b"ASC-E1.17\x00\x00\x00"

    # Root vector
    root_vector = struct.pack(
        "!I",
        0x00000004
    )

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
        struct.pack(
            "!H",
            root_flags_length
        )
        + root_vector
        + CID
    )

    # --------------------------------------------------------
    # Framing Layer
    # --------------------------------------------------------

    source_name = SOURCE_NAME.ljust(64, b"\x00")[:64]

    framing_vector = struct.pack(
        "!I",
        0x00000002
    )

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


# ============================================================
# WLED LIVE MODE
# ============================================================

def set_wled_live(enabled):
    """
    Explicitly enter/leave WLED realtime mode.
    """

    url = f"http://{WLED_IP}/json/state"

    payload = json.dumps({
        "live": enabled
    }).encode("utf-8")

    request = urllib.request.Request(
        url,
        data=payload,
        headers={
            "Content-Type": "application/json"
        },
        method="POST"
    )

    try:
        with urllib.request.urlopen(
            request,
            timeout=1.0
        ) as response:

            response.read()

        print(
            f"[INFO] WLED live mode: "
            f"{'ON' if enabled else 'OFF'}"
        )

    except Exception as e:
        print(
            f"[WARNING] Failed to set WLED live={enabled}: {e}"
        )


# ============================================================
# UNIVERSE MANAGEMENT
# ============================================================

def get_universe_count():
    return (
        SEGMENT_LENGTH +
        LEDS_PER_UNIVERSE - 1
    ) // LEDS_PER_UNIVERSE


def create_frame():
    """
    Creates complete sACN frame.

    Returns:
        list[bytes]
        One DMX payload per universe.
    """

    universe_count = get_universe_count()

    frames = []

    for _ in range(universe_count):
        frames.append(
            bytearray(CHANNELS_PER_UNIVERSE)
        )

    return frames


def set_pixel(
    frame,
    pixel,
    color
):
    """
    Set RGB pixel in complete sACN frame.
    """

    universe_index = pixel // LEDS_PER_UNIVERSE
    pixel_in_universe = pixel % LEDS_PER_UNIVERSE

    offset = pixel_in_universe * 3

    frame[
        universe_index
    ][offset:offset + 3] = bytes(color)


# ============================================================
# MAIN
# ============================================================

def main():

    universe_count = get_universe_count()

    print(
        f"[INFO] sACN sender"
    )

    print(
        f"[INFO] LEDs: {SEGMENT_LENGTH}"
    )

    print(
        f"[INFO] Universes: {universe_count}"
    )

    print(
        f"[INFO] LEDs/universe: {LEDS_PER_UNIVERSE}"
    )

    print(
        f"[INFO] FPS: {FPS}"
    )

    print(
        f"[INFO] Frame time: "
        f"{FRAME_TIME * 1000:.3f} ms"
    )

    print(
        f"[INFO] Universe range: "
        f"{START_UNIVERSE} -> "
        f"{START_UNIVERSE + universe_count - 1}"
    )

    # --------------------------------------------------------
    # UDP socket
    # --------------------------------------------------------

    sock = socket.socket(
        socket.AF_INET,
        socket.SOCK_DGRAM
    )

    sock.setsockopt(
        socket.SOL_SOCKET,
        socket.SO_SNDBUF,
        1024 * 1024
    )

    destination = (
        WLED_IP,
        SACN_PORT
    )

    # --------------------------------------------------------
    # Prepare frame
    # --------------------------------------------------------

    frame = create_frame()

    pixel = 0
    sequence = 0

    # Initial pixel
    set_pixel(
        frame,
        pixel,
        COLOR
    )

    # --------------------------------------------------------
    # Enter WLED realtime mode
    # --------------------------------------------------------

    set_wled_live(True)

    next_frame = time.perf_counter()

    try:

        while True:

            # ------------------------------------------------
            # Send complete sACN frame
            # ------------------------------------------------

            for universe_index in range(
                universe_count
            ):

                universe = (
                    START_UNIVERSE +
                    universe_index
                )

                packet = build_sacn_packet(
                    universe=universe,
                    data=bytes(
                        frame[universe_index]
                    ),
                    sequence=sequence
                )

                sock.sendto(
                    packet,
                    destination
                )

            sequence = (
                sequence + 1
            ) & 0xFF

            # ------------------------------------------------
            # Timing
            # ------------------------------------------------

            next_frame += FRAME_TIME

            while True:

                remaining = (
                    next_frame -
                    time.perf_counter()
                )

                if remaining <= 0:
                    break

                if remaining > 0.001:
                    time.sleep(
                        remaining - 0.0005
                    )

            # ------------------------------------------------
            # Move pixel
            # ------------------------------------------------

            old_pixel = pixel

            pixel += 1

            if pixel >= SEGMENT_LENGTH:
                pixel = 0

            # Clear old pixel
            set_pixel(
                frame,
                old_pixel,
                (0, 0, 0)
            )

            # Set new pixel
            set_pixel(
                frame,
                pixel,
                COLOR
            )

    except KeyboardInterrupt:

        print(
            "\n[INFO] Stopping..."
        )

        # Clear current pixel
        set_pixel(
            frame,
            pixel,
            (0, 0, 0)
        )

        # Send final frame
        for universe_index in range(
            universe_count
        ):

            universe = (
                START_UNIVERSE +
                universe_index
            )

            packet = build_sacn_packet(
                universe=universe,
                data=bytes(
                    frame[universe_index]
                ),
                sequence=sequence
            )

            sock.sendto(
                packet,
                destination
            )

        time.sleep(0.02)

    finally:

        # Leave WLED realtime mode
        set_wled_live(False)

        sock.close()

        print(
            "[INFO] Finished"
        )


if __name__ == "__main__":
    main()