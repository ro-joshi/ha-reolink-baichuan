from __future__ import annotations

import asyncio
import contextlib
import logging

from reolink_aio.api import Host

from .demuxer import BcMediaDemuxer, unwrap_baichuan_chunk

_LOGGER = logging.getLogger(__name__)


def _build_preview_xml(stream_xml_name: str) -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8" ?>'
        '<body version="1.1">'
        "<Preview>"
        "<channelId>0</channelId>"
        "<handle>0</handle>"
        f"<streamType>{stream_xml_name}</streamType>"
        "</Preview>"
        "</body>"
    )


class BaichuanTcpBridge:
    """On-demand local TCP loopback server that streams Annex-B video from Baichuan port 9000."""

    def __init__(
        self,
        host_ip: str,
        username: str,
        password: str,
        port: int,
        sub_channel: int,
        stream_xml_name: str,
    ) -> None:
        self._host_ip = host_ip
        self._username = username
        self._password = password
        self._port = port
        self._sub_channel = sub_channel
        self._stream_xml_name = stream_xml_name

        self._server: asyncio.AbstractServer | None = None
        self._clients: set[asyncio.StreamWriter] = set()
        self._pump_task: asyncio.Task[None] | None = None
        self.listen_port: int = 0

    async def start(self) -> int:
        """Start the local loopback server on 127.0.0.1 and return the bound port."""
        if self._server is None:
            self._server = await asyncio.start_server(self._handle_client, host="127.0.0.1", port=0)
            self.listen_port = self._server.sockets[0].getsockname()[1]
        return self.listen_port

    async def stop(self) -> None:
        """Stop the pump task, disconnect all local clients, and close the server."""
        if self._pump_task and not self._pump_task.done():
            self._pump_task.cancel()
        for writer in list(self._clients):
            writer.close()
        self._clients.clear()
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None

    async def _handle_client(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        self._clients.add(writer)
        if self._pump_task is None or self._pump_task.done():
            self._pump_task = asyncio.create_task(self._pump_stream())

        try:
            while await reader.read(1024):
                pass
        except Exception:
            pass
        finally:
            self._clients.discard(writer)
            writer.close()

    async def _pump_stream(self) -> None:
        cam = Host(self._host_ip, self._username, self._password, port=self._port)
        demuxer = BcMediaDemuxer()

        try:
            await cam.baichuan.login()
            aes_key = cam.baichuan._aes_key
            bc_conn = cam.baichuan._connection
            proto = bc_conn._protocol
            orig_push = proto._push_callback

            def on_push(cmd_id: int, data: bytes, len_header: int, payload: bytes) -> None:
                if cmd_id != 3:
                    orig_push(cmd_id, data, len_header, payload)
                    return

                chunk = unwrap_baichuan_chunk(data, len_header, payload, aes_key)
                video_bytes = demuxer.feed(chunk)
                if not video_bytes:
                    return

                for client in list(self._clients):
                    if client.is_closing():
                        self._clients.discard(client)
                        continue
                    try:
                        client.write(video_bytes)
                    except Exception:
                        self._clients.discard(client)

            proto._push_callback = on_push
            bc_conn._push_callback = on_push

            send_task = asyncio.create_task(
                cam.baichuan.send(
                    cmd_id=3,
                    channel=0,
                    sub_channel=self._sub_channel,
                    body=_build_preview_xml(self._stream_xml_name),
                    mess_id=256,
                )
            )

            try:
                while self._clients:
                    await asyncio.sleep(0.5)
            finally:
                send_task.cancel()

        except asyncio.CancelledError:
            pass
        except Exception as err:
            _LOGGER.error("Baichuan stream error (%s): %s", self._stream_xml_name, err)
        finally:
            with contextlib.suppress(Exception):
                await cam.logout()
