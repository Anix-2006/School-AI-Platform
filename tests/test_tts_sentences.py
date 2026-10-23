import shutil
import subprocess
import tempfile
import threading
import time
from pathlib import Path

import pytest

from app.config import settings
from app.voice.tts import GTTSSpeaker, concat_mp3, split_sentences


def test_split_sentences_in_english_hindi_and_telugu():
    assert split_sentences(
        "She is in Std. 5. The score was 4.5 out of 5. Please revise tonight.",
        "en",
    ) == [
        "She is in Std. 5.",
        "The score was 4.5 out of 5.",
        "Please revise tonight.",
    ]
    assert split_sentences("Ok. The school is closed tomorrow.", "en") == [
        "Ok. The school is closed tomorrow.",
    ]
    assert split_sentences("Is the bus late? It leaves at four!", "en") == [
        "Is the bus late?",
        "It leaves at four!",
    ]
    assert split_sentences("स्कूल कल बंद है। होमवर्क नहीं है।", "hi") == [
        "स्कूल कल बंद है।",
        "होमवर्क नहीं है।",
    ]
    assert split_sentences("हाँ। स्कूल कल बंद है।", "hi") == [
        "हाँ। स्कूल कल बंद है।",
    ]
    assert split_sentences(
        "పాఠశాల రేపు మూసి ఉంటుంది. హోంవర్క్ లేదు.",
        "te",
    ) == [
        "పాఠశాల రేపు మూసి ఉంటుంది.",
        "హోంవర్క్ లేదు.",
    ]
    assert split_sentences("అవును. పాఠశాల రేపు మూసి ఉంటుంది.", "te") == [
        "అవును. పాఠశాల రేపు మూసి ఉంటుంది.",
    ]


def test_sentences_are_delivered_in_order_when_later_ones_finish_first(monkeypatch):
    monkeypatch.setattr(settings, "tts_max_parallel", 3)
    speaker = GTTSSpeaker(cache_size=8)
    delays = {
        "Alpha sentence takes longer.": 0.2,
        "Beta sentence finishes first.": 0.01,
        "Gamma sentence finishes second.": 0.01,
    }

    def render(text, _language):
        time.sleep(delays[text])
        return f"ID3:{text}".encode()

    monkeypatch.setattr(speaker, "_render", render)
    heard = []
    speaker.speak_streaming(
        "Alpha sentence takes longer. Beta sentence finishes first. Gamma sentence finishes second.",
        "en",
        on_sentence=lambda index, sentence, _audio: heard.append((index, sentence)),
        join=False,
    )
    assert heard == [
        (0, "Alpha sentence takes longer."),
        (1, "Beta sentence finishes first."),
        (2, "Gamma sentence finishes second."),
    ]


def test_parallel_requests_stay_within_the_configured_limit(monkeypatch):
    monkeypatch.setattr(settings, "tts_max_parallel", 2)
    speaker = GTTSSpeaker(cache_size=8)
    current = 0
    peak = 0
    lock = threading.Lock()

    def render(text, _language):
        nonlocal current, peak
        with lock:
            current += 1
            peak = max(peak, current)
        time.sleep(0.15)
        with lock:
            current -= 1
        return f"ID3:{text}".encode()

    monkeypatch.setattr(speaker, "_render", render)
    monkeypatch.setattr("app.voice.tts.concat_mp3", lambda parts: b"|".join(parts))
    text = (
        "School is closed tomorrow. "
        "Bring the homework sheet. "
        "The bus leaves at four. "
        "Please reach the gate early."
    )
    audio = speaker.speak(text, "en")
    assert peak == 2
    assert audio.startswith(b"ID3:School is closed tomorrow.")
    assert audio.endswith(b"ID3:Please reach the gate early.")


def test_each_sentence_is_cached_on_its_own(monkeypatch):
    speaker = GTTSSpeaker(cache_size=8)
    calls = []

    def render(text, _language):
        calls.append(text)
        return f"ID3:{text}".encode()

    monkeypatch.setattr(speaker, "_render", render)
    monkeypatch.setattr("app.voice.tts.concat_mp3", lambda parts: b"|".join(parts))
    speaker.speak("School is closed tomorrow. Bring the homework sheet.", "en")
    speaker.speak("Bring the homework sheet. The test is on Friday.", "en")
    assert calls.count("Bring the homework sheet.") == 1
    assert calls.count("School is closed tomorrow.") == 1
    assert calls.count("The test is on Friday.") == 1


def _tone_mp3(frequency: int) -> bytes:
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        pytest.fail("ffmpeg is required to build speech clips")
    with tempfile.TemporaryDirectory() as directory:
        target = Path(directory) / "tone.mp3"
        subprocess.run(
            [
                ffmpeg,
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-f",
                "lavfi",
                "-i",
                f"sine=frequency={frequency}:duration=0.35",
                "-c:a",
                "libmp3lame",
                "-q:a",
                "7",
                str(target),
            ],
            check=True,
            capture_output=True,
        )
        return target.read_bytes()


def test_failed_sentence_is_not_cached_or_returned(monkeypatch):
    speaker = GTTSSpeaker(cache_size=8)
    calls = []

    def render(text, _language):
        calls.append(text)
        if text.startswith("Beta"):
            raise RuntimeError("gTTS failed")
        return f"ID3:{text}".encode()

    monkeypatch.setattr(speaker, "_render", render)
    with pytest.raises(RuntimeError, match="gTTS failed"):
        speaker.speak(
            "Alpha sentence is long enough. Beta sentence fails to speak.",
            "en",
        )
    assert calls.count("Beta sentence fails to speak.") == 1
    calls.clear()
    with pytest.raises(RuntimeError, match="gTTS failed"):
        speaker.speak("Beta sentence fails to speak. Alpha sentence is long enough.", "en")
    assert calls.count("Beta sentence fails to speak.") == 1


def test_zero_parallel_setting_still_speaks_one_at_a_time(monkeypatch):
    monkeypatch.setattr(settings, "tts_max_parallel", 0)
    speaker = GTTSSpeaker(cache_size=4)

    def render(text, _language):
        return f"ID3:{text}".encode()

    monkeypatch.setattr(speaker, "_render", render)
    audio = speaker.speak("School is closed tomorrow.", "en")
    assert audio == b"ID3:School is closed tomorrow."


def test_concat_mp3_is_playable_and_keeps_both_clips():
    import av

    first = _tone_mp3(440)
    second = _tone_mp3(880)
    joined = concat_mp3([first, second])
    assert joined[:3] == b"ID3" or (joined and joined[0] == 0xFF)
    with av.open(io_bytes(joined), mode="r") as container:
        stream = next(item for item in container.streams if item.type == "audio")
        duration = 0.0
        for frame in container.decode(stream):
            duration += float(frame.samples / frame.sample_rate)
    assert duration > 0.6


def io_bytes(data: bytes):
    import io

    return io.BytesIO(data)
