import io
import wave


def pcm_to_wav_bytes(pcm: bytes, sample_rate: int) -> bytes:
    with io.BytesIO() as buffer:
        with wave.open(buffer, "wb") as wav_file:
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(sample_rate)
            wav_file.writeframes(pcm)
        return buffer.getvalue()


def wav_bytes_to_pcm(wav_bytes: bytes) -> bytes:
    with io.BytesIO(wav_bytes) as buffer:
        with wave.open(buffer, "rb") as wav_file:
            return wav_file.readframes(wav_file.getnframes())


def wav_bytes_to_pcm_with_rate(wav_bytes: bytes) -> tuple[bytes, int]:
    with io.BytesIO(wav_bytes) as buffer:
        with wave.open(buffer, "rb") as wav_file:
            pcm = wav_file.readframes(wav_file.getnframes())
            return pcm, wav_file.getframerate()
