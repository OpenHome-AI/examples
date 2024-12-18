import websockets
import asyncio
import base64
import json
import subprocess
import pyaudio
import os
from dotenv import load_dotenv
from time import time

load_dotenv(dotenv_path=".env")

class VoiceStreamer:
    def __init__(self):
        self.api_key = os.getenv('API_KEY')
        self.default_agent = os.getenv('DEFAULT_AGENT')
        self.server_url = f"{os.getenv('STREAM_SERVER_URL_WS')}/websocket/voice-stream/{self.api_key}/{self.default_agent}?devkit=true"
        self.frames_per_buffer = 1024
        self.format = pyaudio.paInt16
        self.channels = 1
        self.rate = 16000
        self.websocket = None
        self.last_live_transcription = time()
        self.py_audio_obj = pyaudio.PyAudio()
        self.stream = None
        self.stream = self._create_stream()
        self.mpv_process = None
        self.bot_speaking = True

    def _create_stream(self):
        if self.stream is not None:
            self.stream.stop_stream()
            self.stream.close()
        return self.py_audio_obj.open(
            format=self.format,
            channels=self.channels,
            rate=self.rate,
            input=True,
            frames_per_buffer=self.frames_per_buffer,
            start=True
        )
    
    def pause_mic(self):
        if self.stream.is_active():
            self.stream.stop_stream()

    def resume_mic(self):
        if not self.stream.is_active():
            self.stream.start_stream()

    async def handle_mpv(self):
        if self.mpv_process and self.mpv_process.stdin:
            try:
                self.mpv_process.stdin.close()
                await self.mpv_process.wait()
            except BrokenPipeError:
                pass
            self.mpv_process = None

        self.bot_speaking = False
        message = {"type": "text", "data": "bot-speak-end"}
        await self.websocket.send(json.dumps(message))

    async def send_data(self):
        while True:
            try:
                audio_bytes = self.stream.read(self.frames_per_buffer, exception_on_overflow=False)
                if audio_bytes and not self.bot_speaking:
                    encoded_bytes = base64.b64encode(audio_bytes).decode("utf-8")
                    await self.websocket.send(json.dumps({"type": "audio", "data": encoded_bytes}))

            except Exception:
                self.stream = self._create_stream()
            await asyncio.sleep(0.01)

    async def receive_data(self):
        while True:
            try:
                server_response = await self.websocket.recv()
                data = json.loads(server_response)

                if data["type"] == "text":
                    await self.handle_text_message(data)
                elif data["type"] == "audio":
                    await self.handle_audio_message(data)
                elif data["type"] == "message" and data["data"].get("final",False):
                    print(data["data"], flush=True)

            except websockets.exceptions.ConnectionClosedError:
                break
            except Exception:
                continue

    async def handle_text_message(self, data):
        if data["data"] == "audio-init":
            self.mpv_process = await asyncio.create_subprocess_exec(
                "mpv", "--no-cache", "--no-terminal", "--", "fd://0",
                stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )

            await self.websocket.send(json.dumps({"type": "text", "data": "bot-speaking"}))
            self.bot_speaking = True

        elif data["data"] == "interrupt" and self.mpv_process:
            self.mpv_process.stdin.write(b"q\n")
            await self.mpv_process.stdin.drain()

        elif data["data"] == "audio-end":
            await self.handle_mpv()

    async def handle_audio_message(self, data):
        while not self.bot_speaking:
            await asyncio.sleep(0.2)
        if self.mpv_process and self.mpv_process.stdin:
            self.mpv_process.stdin.write(base64.b64decode(data["data"]))
            await self.mpv_process.stdin.drain()

    async def run(self):
        try:
            async with websockets.connect(self.server_url) as websocket:
                self.websocket = websocket
                await asyncio.gather(self.send_data(), self.receive_data())
        except Exception:
            pass

if __name__ == "__main__":
    voice_streamer = VoiceStreamer()
    asyncio.run(voice_streamer.run())
