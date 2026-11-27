"""把视频的第一条音轨转成带时间轴的知识库切片。"""
from collections.abc import Generator, Iterable
from contextlib import closing
from dataclasses import replace
from functools import lru_cache
import json
from pathlib import Path
from subprocess import DEVNULL, CalledProcessError
from tempfile import NamedTemporaryFile
from typing import TYPE_CHECKING
import wave

from anyio import CapacityLimiter, run_process, to_process
import numpy
from numpy.typing import NDArray

from app.core.config import get_settings
from app.core.errors import KnowledgeUploadContentError, KnowledgeUploadParseError
from app.core.knowledge import DOCUMENT_CONTENT_LENGTH
from app.rag.chunking import ChunkDraft
from app.services.knowledge_channel_common import DRAFT_JOINER, make_draft

if TYPE_CHECKING:
    from faster_whisper import WhisperModel

__all__ = ["parse_video"]

VIDEO_DEMUXERS = {".mp4": "mov", ".webm": "matroska"}
AUDIO_TEMP_PREFIX = "fengxuan-video-audio-"
AUDIO_SUFFIX = ".wav"
AUDIO_SAMPLE_RATE = 16000
AUDIO_CHANNEL_COUNT = 1
AUDIO_SAMPLE_BYTES = 2
AUDIO_PCM_CODEC = "pcm_s16le"
AUDIO_PCM_TYPE = "<i2"
AUDIO_PCM_SCALE = 32768
AUDIO_WINDOW_SECONDS = 300
FIRST_AUDIO_STREAM = "0:a:0"
FFPROBE = "ffprobe"
FFMPEG = "ffmpeg"
MEDIA_INPUT_OPTIONS = ("-v", "error", "-protocol_whitelist", "file")
MEDIA_METADATA_FIELDS = "format=duration:stream=codec_type"
VIDEO_HEADING = "第{}段（{}–{}）"
SECONDS_PER_MINUTE = 60
ASR_DEVICE = "cpu"
ASR_COMPUTE_TYPE = "int8"
ASR_WORKERS = 1
ASR_TOKENIZER_FILENAME = "tokenizer.json"
VIDEO_PROBE_FAILED_MESSAGE = "视频无法读取，请检查文件格式和完整性"
VIDEO_NO_AUDIO_MESSAGE = "视频没有音轨，无法转写"
VIDEO_EXTRACT_FAILED_MESSAGE = "视频音轨提取失败，请检查文件是否损坏"
VIDEO_MODEL_UNAVAILABLE_MESSAGE = "服务器语音转写模型未就绪，请联系管理员"
VIDEO_TRANSCRIBE_FAILED_MESSAGE = "视频语音转写失败，请检查音轨或联系管理员"
VIDEO_EMPTY_MESSAGE = "视频未转写到文字，请检查音轨中的语音"
VIDEO_CONTENT_LIMIT_MESSAGE = f"视频转写正文不能超过 {DOCUMENT_CONTENT_LENGTH} 字，请拆分后上传"
MEDIA_TOOL_MISSING_MESSAGE = "服务器未安装 {}，请联系管理员"

# 新手注意：限流器像单人工作台，每个服务进程共用一份模型，避免同时加载多份权重。
asr_limiter = CapacityLimiter(ASR_WORKERS)


# 做什么：执行媒体工具并等待子进程退出，取消时由 AnyIO 终止子进程
# 入参：固定参数列表和该工具失败时的公开文案
# 返回：工具输出；缺少可执行文件或处理失败时抛解析错误
async def run_media_command(command: list[str], failure_message: str) -> bytes:
    try:
        result = await run_process(command, stdin=DEVNULL, stderr=DEVNULL)
    except FileNotFoundError as error:
        raise KnowledgeUploadParseError(MEDIA_TOOL_MISSING_MESSAGE.format(command[0])) from error
    except (CalledProcessError, OSError) as error:
        raise KnowledgeUploadParseError(failure_message) from error
    return result.stdout


# 做什么：用 ffprobe 读取视频时长和各条轨道的类型
# 入参：本地上传路径和按后缀选择的容器解析器
# 返回：可选时长，部分 WebM 省略时长；无音轨或容器损坏时抛业务错误
async def probe_video(path: str, demuxer: str) -> float | None:
    command = [
        FFPROBE, *MEDIA_INPUT_OPTIONS, "-f", demuxer,
        "-show_entries", MEDIA_METADATA_FIELDS, "-of", "json", path,
    ]
    output = await run_media_command(command, VIDEO_PROBE_FAILED_MESSAGE)
    try:
        metadata = json.loads(output)
        stream_types = {stream["codec_type"] for stream in metadata["streams"]}
        duration = metadata.get("format", {}).get("duration")
        duration_seconds = float(duration) if duration not in (None, "N/A") else None
    except (KeyError, ValueError, TypeError) as error:
        raise KnowledgeUploadParseError(VIDEO_PROBE_FAILED_MESSAGE) from error
    if "audio" not in stream_types:
        raise KnowledgeUploadContentError(VIDEO_NO_AUDIO_MESSAGE)
    if "video" not in stream_types:
        raise KnowledgeUploadParseError(VIDEO_PROBE_FAILED_MESSAGE)
    return duration_seconds


# 做什么：用 ffmpeg 抽取第一条音轨并转换为单声道 WAV
# 入参：视频路径、容器解析器名称和 WAV 输出路径
# 返回：无；工具不可用或音轨损坏时抛解析错误
async def extract_video_audio(path: str, demuxer: str, audio_path: str) -> None:
    command = [
        FFMPEG, *MEDIA_INPUT_OPTIONS, "-nostdin", "-y", "-f", demuxer, "-i", path,
        "-map", FIRST_AUDIO_STREAM, "-vn", "-c:a", AUDIO_PCM_CODEC,
        "-ac", str(AUDIO_CHANNEL_COUNT), "-ar", str(AUDIO_SAMPLE_RATE), audio_path,
    ]
    await run_media_command(command, VIDEO_EXTRACT_FAILED_MESSAGE)


# 做什么：从本机缓存加载并复用已准备的语音模型
# 入参：模型名称或本机路径及模型缓存目录
# 返回：可执行转写的模型；依赖缺失或权重未准备好时抛解析错误
@lru_cache(maxsize=ASR_WORKERS)
def load_video_model(model_name: str, cache_dir: str) -> "WhisperModel":
    try:
        from faster_whisper import WhisperModel
        from faster_whisper.utils import download_model

        model_path = Path(model_name)
        if not model_path.is_dir():
            model_path = Path(download_model(model_name, cache_dir=cache_dir, local_files_only=True))
        # 缺少 tokenizer 时模型库会退回联网加载，上传路径只使用准备齐全的本机权重。
        if not (model_path / ASR_TOKENIZER_FILENAME).is_file():
            raise KnowledgeUploadParseError(VIDEO_MODEL_UNAVAILABLE_MESSAGE)
        return WhisperModel(
            str(model_path), device=ASR_DEVICE, compute_type=ASR_COMPUTE_TYPE, local_files_only=True,
        )
    except (ImportError, OSError, RuntimeError, TypeError, ValueError) as error:
        raise KnowledgeUploadParseError(VIDEO_MODEL_UNAVAILABLE_MESSAGE) from error


# 做什么：把字幕的秒数显示成可定位的时间点
# 入参：非负的时间戳，单位为秒
# 返回：分钟和秒钟，超出一小时仍保留总分钟数
def format_video_time(seconds: float) -> str:
    minutes, remaining_seconds = divmod(int(max(0, seconds)), SECONDS_PER_MINUTE)
    return f"{minutes:02d}:{remaining_seconds:02d}"


# 做什么：为一段字幕制作带时间范围的切片
# 入参：非空字幕的序号、开始及结束秒数和字幕文字
# 返回：可入库切片，长字幕切出的每一块沿用时间范围
def make_video_drafts(number: int, start: float, end: float, text: str) -> list[ChunkDraft]:
    heading = VIDEO_HEADING.format(number, format_video_time(start), format_video_time(end))
    drafts = make_draft(heading, text)
    return [replace(draft, heading_path=heading) for draft in drafts]


# 做什么：逐段读取已经规范化的 PCM 音轨，将整数样本缩放到模型所需范围
# 入参：FFmpeg 生成的单声道 WAV 路径
# 返回：每段音频的全局起点和浮点样本；文件或格式不符时抛异常
def iter_pcm_windows(audio_path: str) -> Generator[tuple[float, NDArray[numpy.float32]], None, None]:
    # 新手注意：PCM 像按固定频率记下声音高度，逐窗读取时内存只装得下一段音频。
    frames_per_window = AUDIO_WINDOW_SECONDS * AUDIO_SAMPLE_RATE
    with wave.open(audio_path, "rb") as audio:
        audio_format = (audio.getframerate(), audio.getnchannels(), audio.getsampwidth())
        if audio_format != (AUDIO_SAMPLE_RATE, AUDIO_CHANNEL_COUNT, AUDIO_SAMPLE_BYTES):
            raise KnowledgeUploadParseError(VIDEO_TRANSCRIBE_FAILED_MESSAGE)
        while frame_bytes := audio.readframes(frames_per_window):
            frames = numpy.frombuffer(frame_bytes, dtype=AUDIO_PCM_TYPE)
            start_frame = audio.tell() - len(frames)
            waveform = frames.astype(numpy.float32)
            waveform /= AUDIO_PCM_SCALE
            yield start_frame / AUDIO_SAMPLE_RATE, waveform


# 做什么：用模型读取每个音频窗口，并把窗口内时间换算到视频时间轴
# 入参：本机语音模型与 WAV 路径
# 返回：从片头计时的字幕片段；错误交由转写入口统一处理
def iter_subtitles(model: "WhisperModel", audio_path: str) -> Generator[tuple[float, float, str], None, None]:
    with closing(iter_pcm_windows(audio_path)) as windows:
        for offset, waveform in windows:
            segments, _information = model.transcribe(waveform)
            yield from ((offset + segment.start, offset + segment.end, segment.text) for segment in segments)


# 做什么：按字幕分块并限制最终拼接的正文长度
# 入参：带全局时间的字幕和视频时长
# 返回：带时间轴的切片；无正文或正文超限时抛业务错误
def collect_video_drafts(subtitles: Iterable[tuple[float, float, str]], duration: float | None) -> list[ChunkDraft]:
    drafts: list[ChunkDraft] = []
    joined_length = -len(DRAFT_JOINER)
    subtitle_number = 0
    for start, end, text in subtitles:
        if not text.strip():
            continue
        subtitle_number += 1
        end = end if duration is None else min(end, max(start, duration))
        pieces = make_video_drafts(subtitle_number, start, end, text)
        joined_length += sum(len(piece.content) + len(DRAFT_JOINER) for piece in pieces)
        if joined_length > DOCUMENT_CONTENT_LENGTH:
            raise KnowledgeUploadContentError(VIDEO_CONTENT_LIMIT_MESSAGE)
        drafts.extend(pieces)
    if not drafts:
        raise KnowledgeUploadContentError(VIDEO_EMPTY_MESSAGE)
    return drafts


# 做什么：加载本地模型，运行逐窗转写并处理音轨异常
# 入参：WAV 路径、可选视频时长以及模型位置
# 返回：可入库切片；模型与转写错误各带明确文案
def transcribe_audio(audio_path: str, duration: float | None, model_name: str, cache_dir: str) -> list[ChunkDraft]:
    model = load_video_model(model_name, cache_dir)
    try:
        with closing(iter_subtitles(model, audio_path)) as subtitles:
            return collect_video_drafts(subtitles, duration)
    except (EOFError, OSError, RuntimeError, TypeError, ValueError, wave.Error) as error:
        raise KnowledgeUploadParseError(VIDEO_TRANSCRIBE_FAILED_MESSAGE) from error


# 做什么：探测视频、抽音轨，然后在可终止的工作进程转写
# 入参：视频原件的临时路径及后缀
# 返回：字幕切片；包括取消在内的退出路径都会清理音轨文件
async def parse_video(path: str, suffix: str) -> list[ChunkDraft]:
    demuxer = VIDEO_DEMUXERS[suffix]
    duration = await probe_video(path, demuxer)
    settings = get_settings()
    # 新手注意：把解码交给子进程，超时像关掉播放器，等它退出再删除打开过的文件。
    with NamedTemporaryFile(prefix=AUDIO_TEMP_PREFIX, suffix=AUDIO_SUFFIX, delete=False) as audio_file:
        audio_path = audio_file.name
    try:
        await extract_video_audio(path, demuxer, audio_path)
        return await to_process.run_sync(
            transcribe_audio, audio_path, duration, settings.knowledge_asr_model, settings.knowledge_asr_cache_dir,
            cancellable=True, limiter=asr_limiter,
        )
    finally:
        Path(audio_path).unlink()
