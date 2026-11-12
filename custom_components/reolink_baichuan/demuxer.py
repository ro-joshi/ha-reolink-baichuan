from __future__ import annotations

import re
import struct
from typing import Final

from Cryptodome.Cipher import AES
from reolink_aio.baichuan.util import AES_IV

ENCRYPT_LEN_RE: Final = re.compile(rb"<encryptLen>(\d+)</encryptLen>")
FRAME_MAGICS: Final = (b"00dc", b"01dc", b"05wb")


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


class BcMediaDemuxer:
    """Stateful demuxer that extracts Annex-B H.264/H.265 NAL units from BcMedia streams."""

    def __init__(self) -> None:
        self._buf = bytearray()

    def reset(self) -> None:
        """Clear internal framing buffer."""
        self._buf.clear()

    def feed(self, chunk: bytes) -> bytes:
        """Feed unwrapped BcMedia bytes and return contiguous Annex-B video bytes."""
        self._buf.extend(chunk)
        video_out = bytearray()

        while len(self._buf) >= 8:
            magic = bytes(self._buf[:4])

            if magic in (b"00dc", b"01dc"):
                header_size = 32 if magic == b"00dc" else 24
                if len(self._buf) < header_size:
                    break
                payload_size = struct.unpack_from("<I", self._buf, 8)[0]
                total_padded = header_size + pad8(payload_size)
                if len(self._buf) < total_padded:
                    break

                video_out.extend(self._buf[header_size : header_size + payload_size])
                del self._buf[:total_padded]

            elif magic == b"05wb":
                payload_size = struct.unpack_from("<H", self._buf, 4)[0]
                total_padded = 8 + pad8(payload_size)
                if len(self._buf) < total_padded:
                    break
                del self._buf[:total_padded]

            else:
                # Resynchronize to the earliest valid magic marker
                positions = [pos for m in FRAME_MAGICS if (pos := self._buf.find(m, 1)) != -1]
                if positions:
                    del self._buf[: min(positions)]
                else:
                    del self._buf[:-3]
                    break

        return bytes(video_out)
