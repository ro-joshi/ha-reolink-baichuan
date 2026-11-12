from __future__ import annotations

import re
import struct
from typing import Final

from Cryptodome.Cipher import AES
from reolink_aio.baichuan.util import AES_IV

from .mpegts import MpegTsMuxer

ENCRYPT_LEN_RE: Final = re.compile(rb"<encryptLen>(\d+)</encryptLen>")
FRAME_MAGICS: Final = (b"00dc", b"01dc", b"05wb")

# MPEG-4 Audio Sampling Frequency Index (ADTS byte 2 bits 5..2)
ADTS_SAMPLE_RATES: Final[tuple[int, ...]] = (
    96000,
    88200,
    64000,
    48000,
    44100,
    32000,
    24000,
    22050,
    16000,
    12000,
    11025,
    8000,
    7350,
)


def pad8(size: int) -> int:
    """Pad BcMedia frame payload length to an 8-byte boundary."""
    return (size + 7) & ~7


def aes_decrypt_cfb(data: bytes, key: bytes) -> bytes:
    """Decrypt raw bytes using Baichuan AES-128-CFB parameters."""
    cipher = AES.new(key=key, mode=AES.MODE_CFB, iv=AES_IV, segment_size=128)
    return cipher.decrypt(data)


def unwrap_baichuan_chunk(data: bytes, len_header: int, payload: bytes, aes_key: bytes) -> bytes:
    """Decrypt the partial AES prefix of a cmd_id=3 packet and return raw BcMedia bytes."""
    payload_offset = int.from_bytes(data[20:24], "little") if len_header == 24 else 0
    if payload_offset > 0 and payload:
        xml_dec = aes_decrypt_cfb(data[len_header : len_header + payload_offset], aes_key)
        match = ENCRYPT_LEN_RE.search(xml_dec)
        enc_len = int(match.group(1)) if match else len(payload)
        return aes_decrypt_cfb(payload[:enc_len], aes_key) + payload[enc_len:]
    return data[len_header:] + payload


def _adts_duration_90k(adts_frame: bytes) -> int:
    """Return the 90kHz PTS duration of a 1024-sample ADTS AAC frame."""
    if len(adts_frame) >= 3 and adts_frame[0] == 0xFF and (adts_frame[1] & 0xF0) == 0xF0:
        sf_index = (adts_frame[2] >> 2) & 0x0F
        if sf_index < len(ADTS_SAMPLE_RATES):
            return (1024 * 90000) // ADTS_SAMPLE_RATES[sf_index]
    return 5760  # Default for 16kHz AAC (1024 * 90000 // 16000)


class BcMediaDemuxer:
    """Demuxes BcMedia (00dc/01dc/05wb) frames and muxes them into an MPEG-TS stream."""

    def __init__(self) -> None:
        self._buf = bytearray()
        self._muxer = MpegTsMuxer()
        self._base_pts_us: int | None = None
        self._last_pts_90k: int = 0
        self._psi_sent: bool = False

    def reset(self) -> None:
        """Clear framing buffer and timestamp state."""
        self._buf.clear()
        self._muxer = MpegTsMuxer()
        self._base_pts_us = None
        self._last_pts_90k = 0
        self._psi_sent = False

    def _next_video_pts(self, pts_us: int) -> int:
        if self._base_pts_us is None:
            self._base_pts_us = pts_us
        rel_us = max(0, pts_us - self._base_pts_us)
        pts_90k = (rel_us * 9) // 100
        # Guarantee strict monotonicity across interleaved audio/video frames
        if pts_90k <= self._last_pts_90k and self._last_pts_90k > 0:
            pts_90k = self._last_pts_90k + 90
        self._last_pts_90k = pts_90k
        return pts_90k

    def _next_audio_pts(self, adts_bytes: bytes) -> int:
        # Keep audio PTS strictly >= the most recent packet PTS on the wire
        pts_90k = self._last_pts_90k
        # Advance the shared floor slightly (1ms = 90 ticks) so back-to-back audio packets stay monotonic
        # without pushing the clock ahead of the next hardware video frame
        self._last_pts_90k += min(900, _adts_duration_90k(adts_bytes))
        return pts_90k

    def feed(self, chunk: bytes) -> bytes:
        """Feed unwrapped BcMedia bytes and return muxed 188-byte MPEG-TS packets."""
        self._buf.extend(chunk)
        ts_out = bytearray()

        while len(self._buf) >= 8:
            magic = bytes(self._buf[:4])

            if magic in (b"00dc", b"01dc"):
                is_keyframe = magic == b"00dc"
                header_size = 32 if is_keyframe else 24
                if len(self._buf) < header_size:
                    break
                payload_size = struct.unpack_from("<I", self._buf, 8)[0]
                pts_us = struct.unpack_from("<Q", self._buf, 16)[0]
                total_padded = header_size + pad8(payload_size)
                if len(self._buf) < total_padded:
                    break

                nal_bytes = bytes(self._buf[header_size : header_size + payload_size])
                pts_90k = self._next_video_pts(pts_us)
                if not self._psi_sent and not is_keyframe:
                    ts_out.extend(self._muxer.mux_psi())
                    self._psi_sent = True
                elif is_keyframe:
                    self._psi_sent = True

                ts_out.extend(self._muxer.mux_video(nal_bytes, pts_90k, is_keyframe))
                del self._buf[:total_padded]

            elif magic == b"05wb":
                payload_size = struct.unpack_from("<H", self._buf, 4)[0]
                total_padded = 8 + pad8(payload_size)
                if len(self._buf) < total_padded:
                    break

                if self._base_pts_us is not None:
                    adts_bytes = bytes(self._buf[8 : 8 + payload_size])
                    pts_90k = self._next_audio_pts(adts_bytes)
                    ts_out.extend(self._muxer.mux_audio(adts_bytes, pts_90k))
                del self._buf[:total_padded]

            else:
                positions = [pos for m in FRAME_MAGICS if (pos := self._buf.find(m, 1)) != -1]
                if positions:
                    del self._buf[: min(positions)]
                else:
                    del self._buf[:-3]
                    break

        return bytes(ts_out)
