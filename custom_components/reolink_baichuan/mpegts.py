from __future__ import annotations

import struct
from typing import Final

TS_PACKET_SIZE: Final = 188
TS_SYNC_BYTE: Final = 0x47

PID_PAT: Final = 0x0000
PID_PMT: Final = 0x1000
PID_VIDEO: Final = 0x0100
PID_AUDIO: Final = 0x0101

STREAM_TYPE_H264: Final = 0x1B
STREAM_TYPE_ADTS_AAC: Final = 0x0F

STREAM_ID_VIDEO: Final = 0xE0
STREAM_ID_AUDIO: Final = 0xC0

H264_AUD_NAL: Final = b"\x00\x00\x00\x01\x09\xf0"


def _crc32_mpeg2(data: bytes) -> int:
    """Compute standard MPEG-2 CRC-32 for PSI tables (PAT/PMT)."""
    crc = 0xFFFFFFFF
    for b in data:
        crc ^= b << 24
        for _ in range(8):
            crc = ((crc << 1) ^ 0x04C11DB7) if (crc & 0x80000000) else (crc << 1)
            crc &= 0xFFFFFFFF
    return crc


def _encode_pts(pts_90k: int) -> bytes:
    """Encode a 33-bit 90kHz PTS into the 5-byte PES header format."""
    pts = pts_90k & 0x1FFFFFFFF
    b0 = 0x21 | (((pts >> 30) & 0x07) << 1)
    b1 = (pts >> 22) & 0xFF
    b2 = 0x01 | (((pts >> 15) & 0x7F) << 1)
    b3 = (pts >> 7) & 0xFF
    b4 = 0x01 | ((pts & 0x7F) << 1)
    return bytes((b0, b1, b2, b3, b4))


def _encode_pcr(pcr_90k: int) -> bytes:
    """Encode a 33-bit 90kHz PCR into the 6-byte adaptation field format."""
    base = pcr_90k & 0x1FFFFFFFF
    return bytes(
        (
            (base >> 25) & 0xFF,
            (base >> 17) & 0xFF,
            (base >> 9) & 0xFF,
            (base >> 1) & 0xFF,
            ((base & 1) << 7) | 0x7E,
            0x00,
        )
    )


def _build_pat_section() -> bytes:
    # Program 1 -> PMT PID 0x1000
    body = b"\x00\xb0\x0d\x00\x01\xc1\x00\x00\x00\x01\xf0\x00"
    return body + struct.pack(">I", _crc32_mpeg2(body))


def _build_pmt_section() -> bytes:
    # PCR_PID = 0x0100 (Video), ES #1 = H.264 (0x0100), ES #2 = ADTS AAC (0x0101)
    body = (
        b"\x02\xb0\x17\x00\x01\xc1\x00\x00\xe1\x00\xf0\x00\x1b\xe1\x00\xf0\x00\x0f\xe1\x01\xf0\x00"
    )
    return body + struct.pack(">I", _crc32_mpeg2(body))


PAT_SECTION: Final = _build_pat_section()
PMT_SECTION: Final = _build_pmt_section()


class MpegTsMuxer:
    """Stateful MPEG-TS packetizer for H.264 Annex-B video and ADTS AAC audio."""

    def __init__(self) -> None:
        self._cc: dict[int, int] = {
            PID_PAT: 0,
            PID_PMT: 0,
            PID_VIDEO: 0,
            PID_AUDIO: 0,
        }

    def _next_cc(self, pid: int) -> int:
        cc = self._cc[pid]
        self._cc[pid] = (cc + 1) & 0x0F
        return cc

    def _packetize_psi(self, pid: int, section: bytes) -> bytes:
        cc = self._next_cc(pid)
        header = bytes((TS_SYNC_BYTE, 0x40 | ((pid >> 8) & 0x1F), pid & 0xFF, 0x10 | cc))
        payload = b"\x00" + section
        pad_len = TS_PACKET_SIZE - len(header) - len(payload)
        return header + payload + (b"\xff" * pad_len)

    def _packetize_pes(
        self,
        pid: int,
        pes: bytes,
        pts_90k: int | None = None,
        random_access: bool = False,
    ) -> bytes:
        out = bytearray()
        pos = 0
        total = len(pes)
        first = True

        while pos < total:
            cc = self._next_cc(pid)
            rem = total - pos
            want_pcr = first and pts_90k is not None

            if want_pcr:
                # 8-byte adaptation field: length=7, flags (PCR + random_access), 6-byte PCR
                af_flags = 0x50 if random_access else 0x10
                af = bytes((7, af_flags)) + _encode_pcr(pts_90k)
                max_payload = 184 - len(af)
                if rem < max_payload:
                    stuff_len = max_payload - rem
                    af = (
                        bytes((7 + stuff_len, af_flags))
                        + _encode_pcr(pts_90k)
                        + (b"\xff" * stuff_len)
                    )
                chunk = pes[pos : pos + (184 - len(af))]
                pos += len(chunk)
                pusi = 0x40 if first else 0x00
                out.extend(
                    bytes(
                        (
                            TS_SYNC_BYTE,
                            pusi | ((pid >> 8) & 0x1F),
                            pid & 0xFF,
                            0x30 | cc,
                        )
                    )
                )
                out.extend(af)
                out.extend(chunk)
            elif rem >= 184:
                chunk = pes[pos : pos + 184]
                pos += 184
                pusi = 0x40 if first else 0x00
                out.extend(
                    bytes(
                        (
                            TS_SYNC_BYTE,
                            pusi | ((pid >> 8) & 0x1F),
                            pid & 0xFF,
                            0x10 | cc,
                        )
                    )
                )
                out.extend(chunk)
            else:
                stuff_len = 184 - rem
                if stuff_len == 1:
                    af = b"\x00"
                else:
                    af = bytes((stuff_len - 1, 0x00)) + (b"\xff" * (stuff_len - 2))
                chunk = pes[pos:]
                pos = total
                pusi = 0x40 if first else 0x00
                out.extend(
                    bytes(
                        (
                            TS_SYNC_BYTE,
                            pusi | ((pid >> 8) & 0x1F),
                            pid & 0xFF,
                            0x30 | cc,
                        )
                    )
                )
                out.extend(af)
                out.extend(chunk)

            first = False

        return bytes(out)

    def mux_video(self, nal_bytes: bytes, pts_90k: int, is_keyframe: bool) -> bytes:
        """Mux an H.264 Annex-B frame into 188-byte MPEG-TS packets."""
        out = bytearray()
        if is_keyframe:
            out.extend(self._packetize_psi(PID_PAT, PAT_SECTION))
            out.extend(self._packetize_psi(PID_PMT, PMT_SECTION))

        payload = (
            nal_bytes if nal_bytes.startswith(b"\x00\x00\x00\x01\x09") else H264_AUD_NAL + nal_bytes
        )
        # Video PES length=0 (unbounded) is standard for H.264 in MPEG-TS
        pes_header = bytes(
            (0x00, 0x00, 0x01, STREAM_ID_VIDEO, 0x00, 0x00, 0x80, 0x80, 0x05)
        ) + _encode_pts(pts_90k)
        out.extend(
            self._packetize_pes(
                PID_VIDEO,
                pes_header + payload,
                pts_90k=pts_90k,
                random_access=is_keyframe,
            )
        )
        return bytes(out)

    def mux_audio(self, adts_bytes: bytes, pts_90k: int) -> bytes:
        """Mux an ADTS AAC audio frame into 188-byte MPEG-TS packets."""
        pes_len = len(adts_bytes) + 8
        pes_header = (
            struct.pack(">BBBBHB", 0x00, 0x00, 0x01, STREAM_ID_AUDIO, pes_len, 0x80)
            + b"\x80\x05"
            + _encode_pts(pts_90k)
        )
        return self._packetize_pes(PID_AUDIO, pes_header + adts_bytes)
