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
    def __init__(self, speaker_type="mpv"):
        self.api_key = "xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
        self.server_url = f"wss://app.openhome.xyz/websocket/voice-stream/{self.api_key}/0?devkit=true"
        self.frames_per_buffer = 1024
        self.format = pyaudio.paInt16
        self.channels = 1
        self.rate = 16000
        self.speaker_type = speaker_type
        self.websocket = None

        self.last_live_transcription = time()

        # Initialize PyAudio
        self.py_audio_obj = pyaudio.PyAudio()
        self.stream = None
        self.stream = self._create_stream()

        # Initialize MPV process
        self.mpv_process = None
        self.bot_speaking = True

    def _create_stream(self):
        if self.stream is not None:
            self.stream.stop_stream()
            self.stream.close()  # Close the existing stream
        """Create the microphone audio stream."""
        return self.py_audio_obj.open(
            format=self.format,
            channels=self.channels,
            rate=self.rate,
            input=True,
            frames_per_buffer=self.frames_per_buffer,
            start=True
        )
    
    def pause_mic(self):
        """Pause the microphone stream."""
        if self.stream.is_active():
            self.stream.stop_stream()
            print("[+] Microphone stream paused")

    def resume_mic(self):
        """Resume the microphone stream."""
        if not self.stream.is_active():
            self.stream.start_stream()
            print("[+] Microphone stream resumed")

    async def handle_mpv(self):
        """Handle MPV cleanup after audio playback ends."""
        if self.mpv_process and self.mpv_process.stdin:
            try:
                self.mpv_process.stdin.close()
                await self.mpv_process.wait()
            except BrokenPipeError:
                print("[-] Broken pipe error while writing to MPV")
            self.mpv_process = None

        self.bot_speaking = False
        print("[+] MPV playback completed")
        message = {"type": "text", "data": "bot-speak-end"}
        await self.websocket.send(json.dumps(message))

    def generate_silence(self, duration):
        """Generate a silence buffer of specified duration in seconds."""
        num_samples = int(self.rate * duration)  # Total samples for the given duration
        silence = (b'\x00' * 2) * num_samples  # 16-bit audio, 1 channel
        return silence

    async def send_data(self):
        """Capture and send microphone audio data to the server."""
        timeout_duration = 30  # 5 minutes
        while True:
            try:
                audio_bytes = self.stream.read(self.frames_per_buffer, exception_on_overflow=False)
                if audio_bytes:
                    last_activity_time = time()  # Reset the timer if audio is received
                    encoded_bytes = base64.b64encode(audio_bytes).decode("utf-8")
                    json_data = json.dumps({"type": "audio", "data": encoded_bytes})

                    if not self.bot_speaking:
                        await self.websocket.send(json_data)
                        # print(self.stream.is_active(),self.stream.is_stopped())
                        print("%s: Audio sent: %s" % (int(time()), len(encoded_bytes)))
                else:
                    print("[!] No audio data captured.")
                    
                # Check for timeout
                if time() - self.last_live_transcription > timeout_duration:
                    print("[!] Microphone is idle for too long, reinitializing.")
                    self.py_audio_obj = pyaudio.PyAudio()
                    self.stream = self._create_stream()  # Recreate the stream if idle for too long
                    self.last_live_transcription = time()
            except Exception as e:
                print("[!] Error in send_data:", e)
                self.stream = self._create_stream()
            await asyncio.sleep(0.01)

    async def receive_data(self):
        """Receive data from server and handle it."""
        while True:
            try:
                server_response = await self.websocket.recv()
                data = json.loads(server_response)

                if data["type"] == "text":
                    await self.handle_text_message(data)
                elif data["type"] == "audio":
                    await self.handle_audio_message(data)
                elif data["type"] == "message" and data["data"].get("role","") == "user":
                    self.last_live_transcription = time()
                    print(data)

            except websockets.exceptions.ConnectionClosedError:
                print("[!] Connection is closed")
                break
            except Exception as e:
                print("[!] Error in receive_data:", e)

    async def handle_text_message(self, data):
        """Process 'text' type messages from server."""
        if data["data"] == "audio-init":
            print("[+] Bot speaking event is set...")

            if self.speaker_type == "mpv":
                self.mpv_process = await asyncio.create_subprocess_exec(
                    "mpv", "--no-cache", "--no-terminal", "--", "fd://0",
                    stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
                )
                
                # Add 0.5 seconds of silence
                silence = self.generate_silence(10)
                self.mpv_process.stdin.write(silence)
                await self.mpv_process.stdin.drain()

                message = {"type": "text", "data": "bot-speaking"}
                await self.websocket.send(json.dumps(message))
                print("[+] Sent bot speaking event...")
                self.bot_speaking = True

        elif data["data"] == "interrupt":
            print("[+] Interruption received...")
            if self.speaker_type == "mpv" and self.mpv_process:
                self.mpv_process.stdin.write(b"q\n")
                await self.mpv_process.stdin.drain()
                print("[+] Stopped MPV...")

        elif data["data"] == "audio-end":
            print("[+] Audio end received...")
            await self.handle_mpv()

    async def handle_audio_message(self, data):
        """Process 'audio' type messages from server."""
        #message = {"type": "ack", "data": "audio-received"}
        #wait self.websocket.send(json.dumps(message))
        while not self.bot_speaking:
            print("waiting.......")
            await asyncio.sleep(0.2)
        if self.speaker_type == "mpv" and self.mpv_process and self.mpv_process.stdin:
            audio_bytes = base64.b64decode(data["data"])
            self.mpv_process.stdin.write(audio_bytes)
            await self.mpv_process.stdin.drain()

    async def run(self):
        """Establish a connection to the server and start sending/receiving data."""
        try:
            async with websockets.connect(self.server_url) as websocket:
                self.websocket = websocket
                await asyncio.gather(self.send_data(), self.receive_data())
        except Exception as e:
            print(e)

if __name__ == "__main__":
    voice_streamer = VoiceStreamer(speaker_type=os.getenv("SPEAKER_TYPE", "mpv"))
    asyncio.run(voice_streamer.run())