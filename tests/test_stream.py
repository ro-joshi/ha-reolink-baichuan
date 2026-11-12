from __future__ import annotations

import asyncio
import struct
import time

from custom_components.reolink_baichuan.bridge import BaichuanTcpBridge
from custom_components.reolink_baichuan.demuxer import BcMediaDemuxer


def test_bcmedia_demuxer_unit() -> None:
    """Verify deterministic frame slicing for 00dc (32B), 05wb (8B), and 01dc (24B)."""
    demuxer = BcMediaDemuxer()

    # 1. 00dc I-Frame: 32-byte header + 5-byte payload (padded to 8 bytes)
    i_header = b"00dcH264" + struct.pack("<I", 5) + bytes(20)
    i_payload = b"\x00\x00\x00\x01\x65" + b"\x00\x00\x00"  # 5B + 3B pad

    # 2. 05wb Audio Frame: 8-byte header + 3-byte payload (padded to 8 bytes)
    a_header = b"05wb" + struct.pack("<HH", 3, 3)
    a_payload = b"\xff\xf1\x60" + bytes(5)

    # 3. 01dc P-Frame: 24-byte header + 4-byte payload (padded to 8 bytes)
    p_header = b"01dcH264" + struct.pack("<I", 4) + bytes(12)
    p_payload = b"\x00\x00\x00\x01" + bytes(4)

    out = demuxer.feed(i_header + i_payload + a_header + a_payload + p_header + p_payload)
    assert out == b"\x00\x00\x00\x01\x65\x00\x00\x00\x01"


async def verify_live_bridge(
    host_ip: str = "192.168.20.185",
    username: str = "admin",
    password: str = "Ybc_!c!8rXGQZ-Z",
    duration: float = 5.0,
) -> None:
    """End-to-end integration test against the local TCP bridge."""
    bridge = BaichuanTcpBridge(
        host_ip=host_ip,
        username=username,
        password=password,
        port=9000,
        sub_channel=1,
        stream_xml_name="subStream",
    )
    port = await bridge.start()
    reader, writer = await asyncio.open_connection("127.0.0.1", port)

    total_bytes = 0
    t0 = time.monotonic()
    try:
        while time.monotonic() - t0 < duration:
            chunk = await asyncio.wait_for(reader.read(65536), timeout=3.0)
            if not chunk:
                break
            total_bytes += len(chunk)
        print(f"Captured {total_bytes / 1024:.1f} KB over tcp://127.0.0.1:{port}")
    finally:
        writer.close()
        await bridge.stop()


if __name__ == "__main__":
    test_bcmedia_demuxer_unit()
    print("Unit test passed. Running 5s live bridge verification...")
    asyncio.run(verify_live_bridge())
