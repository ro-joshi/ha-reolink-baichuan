from __future__ import annotations

import asyncio
import struct
import sys
import time
from pathlib import Path

from custom_components.reolink_baichuan.bridge import BaichuanTcpBridge
from custom_components.reolink_baichuan.demuxer import BcMediaDemuxer
from custom_components.reolink_baichuan.mpegts import (
    PID_AUDIO,
    PID_PAT,
    PID_PMT,
    PID_VIDEO,
    TS_PACKET_SIZE,
    TS_SYNC_BYTE,
)


def _extract_pids(ts_stream: bytes) -> set[int]:
    assert len(ts_stream) % TS_PACKET_SIZE == 0
    pids: set[int] = set()
    for offset in range(0, len(ts_stream), TS_PACKET_SIZE):
        assert ts_stream[offset] == TS_SYNC_BYTE
        pid = ((ts_stream[offset + 1] & 0x1F) << 8) | ts_stream[offset + 2]
        pids.add(pid)
    return pids


def test_bcmedia_mpegts_muxer_unit() -> None:
    """Verify 00dc, 05wb, and 01dc mux cleanly into 188-byte MPEG-TS packets

    e.g. with PAT/PMT/Video/Audio PIDs.
    """
    demuxer = BcMediaDemuxer()

    # 1. 00dc I-Frame (32B header, pts_us=1_000_000)
    i_header = b"00dcH264" + struct.pack("<IIQ", 5, 8, 1_000_000) + bytes(8)
    i_payload = b"\x00\x00\x00\x01\x65" + bytes(3)

    # 2. 05wb ADTS AAC Frame (8B header, 7B ADTS header + 1B payload)
    a_header = b"05wb" + struct.pack("<HH", 8, 8)
    a_payload = b"\xff\xf1\x60\x40\x01\x00\xfc\xaa"

    # 3. 01dc P-Frame (24B header, pts_us=1_066_666)
    p_header = b"01dcH264" + struct.pack("<IIQ", 4, 0, 1_066_666)
    p_payload = b"\x00\x00\x00\x01" + bytes(4)

    ts_bytes = demuxer.feed(i_header + i_payload + a_header + a_payload + p_header + p_payload)
    assert len(ts_bytes) > 0
    pids = _extract_pids(ts_bytes)
    assert {PID_PAT, PID_PMT, PID_VIDEO, PID_AUDIO}.issubset(pids)


async def verify_live_bridge(
    host_ip: str = "192.168.20.185",
    username: str = "admin",
    password: str = "Ybc_!c!8rXGQZ-Z",
    duration: float = 5.0,
) -> None:
    """Record 5s of live MPEG-TS (H.264 + AAC) from the local bridge to test_capture.ts."""
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

    captured = bytearray()
    t0 = time.monotonic()
    try:
        while time.monotonic() - t0 < duration:
            chunk = await asyncio.wait_for(reader.read(65536), timeout=3.0)
            if not chunk:
                break
            captured.extend(chunk)
    finally:
        writer.close()
        await bridge.stop()

    out_path = Path("test_capture.ts")
    out_path.write_bytes(captured)
    pids = _extract_pids(bytes(captured))
    print(
        f"Wrote {len(captured) / 1024:.1f} KB ({len(captured) // 188} TS packets) "
        f"with PIDs {[hex(p) for p in sorted(pids)]} -> {out_path}"
    )


if __name__ == "__main__":
    test_bcmedia_mpegts_muxer_unit()
    print("Unit test passed. Running 5s live MPEG-TS bridge capture...")
    asyncio.run(verify_live_bridge(password=sys.argv[1]))
