"""隔离验证 OCR、视频时间轴和流式上传的清理边界。"""
from io import BytesIO
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import wave

import pytest
from anyio import run, sleep_forever
from PIL import Image
from pytesseract import TesseractNotFoundError

from app.core.errors import KnowledgeUploadInvalidError, KnowledgeUploadParseError, KnowledgeUploadSizeError
from app.core.knowledge import DOCUMENT_CONTENT_LENGTH, DocumentStatus, FileKind
from app.models.knowledge import KnowledgeDocument
from app.services import knowledge_channel_image, knowledge_channel_video, knowledge_upload
from tests.test_knowledge import DOCUMENT_URL, document_bases, knowledge_client, knowledge_sessions, owner_headers
from tests.test_knowledge_file_upload import FILE_URL, assert_no_documents

VIDEO_BYTES = b"\x00\x00\x00\x18ftypisomexample"
BOUNDARY = b"knowledge-test-boundary"
CONTENT_TYPE = f'multipart/form-data; boundary="{BOUNDARY.decode("ascii")}"'
VIDEO_START = b"--" + BOUNDARY + b'\r\nContent-Disposition: form-data; name="file"; filename="clip.mp4"\r\n\r\n'
VIDEO_END = b"\r\n--" + BOUNDARY + b"--\r\n"
SUBTITLES = [(2.0, 7.8, "第一句话"), (61.0, 66.0, "第二句话")]
PCM_FRAMES = b"\x00\x00\x00\x40\x00\xc0"


# 做什么：现场生成能通过 Pillow 校验的图片
# 入参：Pillow 格式名
# 返回：完整图片文件的字节
def image_bytes(image_format: str) -> bytes:
    output = BytesIO()
    with Image.new("RGB", (24, 24), "white") as image:
        image.save(output, format=image_format)
    return output.getvalue()


# 做什么：验证原文已落库且公开切片的标题和字符偏移完全吻合
# 入参：测试客户端、原文与时间轴及对应文档
# 返回：无；状态、原文、块数或偏移不符时断言失败
def assert_saved_chunks(client, headers, sessions, document, raw_content, headings) -> None:
    assert document["status"] == DocumentStatus.READY
    assert document["char_count"] == len(raw_content)
    with sessions() as session:
        assert session.get(KnowledgeDocument, document["id"]).raw_content == raw_content
    response = client.get(f"{DOCUMENT_URL}/{document['id']}/chunks", headers=headers)
    assert response.status_code == 200
    page = response.json()["data"]
    assert page["total"] == len(headings)
    assert [item["heading_path"] for item in page["items"]] == headings
    start = 0
    for item in page["items"]:
        end = start + len(item["content"])
        assert (item["char_start"], item["char_end"]) == (start, end)
        assert raw_content[start:end] == item["content"]
        start = end + len("\n\n")


# 做什么：用假 OCR 走真实图片上传、分块和入库接口
# 入参：隔离知识库夹具、可替换识别函数和一种图片后缀
# 返回：无；段落、图片类型或偏移错误时断言失败
@pytest.mark.parametrize(("filename", "image_format"), [("文字.png", "PNG"), ("文字.JPG", "JPEG"), ("文字.jpeg", "JPEG")])
def test_image_upload_saves_paragraphs(
    knowledge_client, knowledge_sessions, owner_headers, document_bases, monkeypatch, filename, image_format,
) -> None:
    monkeypatch.setattr(knowledge_channel_image, "recognize_text", lambda _image: "标题行\r\n下一行\r\n \r\n第二段\x0c")
    payload = image_bytes(image_format)
    response = knowledge_client.post(FILE_URL, files={"file": (filename, payload)}, headers=owner_headers)
    assert response.status_code == 200, response.json()
    document = response.json()["data"]
    assert (document["file_type"], document["file_name"], document["file_size"]) == (FileKind.IMAGE, filename, len(payload))
    assert_saved_chunks(
        knowledge_client, owner_headers, knowledge_sessions, document, "标题行\n下一行\n\n第二段", ["第1段", "第2段"],
    )


# 做什么：确认图片损坏、空 OCR 和未安装引擎都返回明确失败
# 入参：隔离上传夹具和待测试的图片错误
# 返回：无；错误被吃掉、文案改变或留下文档时断言失败
@pytest.mark.parametrize(("failure", "code", "message"), [
    ("broken", 4007, "图片无法解码，请检查文件是否损坏"),
    ("empty", 4008, "图片未识别到文字，请换更清晰的图片"),
    ("missing", 4007, "服务器未安装 OCR 引擎，请联系管理员"),
])
def test_image_errors_leave_no_document(
    knowledge_client, knowledge_sessions, owner_headers, document_bases, monkeypatch, failure, code, message,
) -> None:
    payload = image_bytes("PNG")
    if failure == "broken":
        payload = payload[:32]
    if failure == "empty":
        monkeypatch.setattr(knowledge_channel_image, "recognize_text", lambda _image: " \r\n\x0c")
    if failure == "missing":
        monkeypatch.setattr(knowledge_channel_image, "image_to_string", missing_tesseract)
    response = knowledge_client.post(FILE_URL, files={"file": ("图片.png", payload)}, headers=owner_headers)
    assert (response.status_code, response.json()["code"], response.json()["message"]) == (422, code, message)
    assert_no_documents(knowledge_sessions)


# 做什么：模拟系统找不到 Tesseract
# 入参：识别图片、语言组合与超时
# 返回：抛与 pytesseract 一致的程序缺失错误
def missing_tesseract(image, lang, timeout):
    raise TesseractNotFoundError()


class FakeSpeechModel:
    # 做什么：提供一组带真实接口形状的字幕
    # 入参：FFmpeg 产生的 WAV 解码后的样本
    # 返回：片段迭代器和未使用的模型信息
    def transcribe(self, waveform):
        assert waveform.tolist() == [0.0, 0.5, -0.5]
        segments = [SimpleNamespace(start=start, end=end, text=text) for start, end, text in SUBTITLES]
        return iter(segments), None


# 做什么：提供 ffprobe 的常规元数据和 ffmpeg 抽音轨的替身
# 入参：应用构造的媒体命令及错误文案
# 返回：模拟的探测输出，或在要求的路径写入一份可验证的 WAV 占位文件
async def fake_media_command(command: list[str], failure_message: str) -> bytes:
    if command[0] == "ffprobe":
        assert Path(command[-1]).read_bytes() == VIDEO_BYTES
        metadata = {"format": {"duration": "90.0"}, "streams": [{"codec_type": "video"}, {"codec_type": "audio"}]}
        return json.dumps(metadata).encode()
    assert command[0] == "ffmpeg"
    assert command[-5:] == ["-ac", "1", "-ar", "16000", command[-1]]
    with wave.open(command[-1], "wb") as audio:
        audio.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
        audio.writeframes(PCM_FRAMES)
    return b""


# 做什么：在当前进程中调用真实的字幕分块代码
# 入参：转写入口、WAV 文件、视频长度、模型位置及进程运行参数
# 返回：真实分块函数的结果，底层模型被测试替换
async def run_transcription_inline(transcribe, audio_path, duration, model_name, cache_dir, *, cancellable, limiter):
    assert cancellable is True
    return transcribe(audio_path, duration, model_name, cache_dir)


# 做什么：为临时文件建立专用目录，避免把其他请求的文件算成泄漏
# 入参：pytest 临时目录和替换系统临时目录的 monkeypatch
# 返回：本用例所有应用上传文件所在目录
@pytest.fixture
def upload_temp_dir(tmp_path, monkeypatch) -> Path:
    directory = tmp_path / "upload"
    directory.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(directory))
    return directory


# 做什么：对真实上传路由验证时间轴、拼接正文、偏移及原件信息
# 入参：隔离 SQLite 路由、媒体工具和模型替身
# 返回：无；时间轴、原文或文件清理不符合预期时断言失败
def test_video_upload_saves_timeline(
    knowledge_client, knowledge_sessions, owner_headers, document_bases, upload_temp_dir, monkeypatch,
) -> None:
    monkeypatch.setattr(knowledge_channel_video, "run_media_command", fake_media_command)
    monkeypatch.setattr(knowledge_channel_video, "load_video_model", lambda _model, _directory: FakeSpeechModel())
    monkeypatch.setattr(knowledge_channel_video.to_process, "run_sync", run_transcription_inline)
    response = knowledge_client.post(FILE_URL, files={"file": ("字幕.MP4", VIDEO_BYTES)}, headers=owner_headers)
    assert response.status_code == 200, response.json()
    document = response.json()["data"]
    assert (document["file_type"], document["file_name"], document["file_size"]) == (FileKind.VIDEO, "字幕.MP4", len(VIDEO_BYTES))
    assert_saved_chunks(
        knowledge_client, owner_headers, knowledge_sessions, document,
        "第一句话\n\n第二句话", ["第1段（00:02–00:07）", "第2段（01:01–01:06）"],
    )
    assert list(upload_temp_dir.glob("fengxuan-video-*")) == []


# 做什么：验证超限当场中止读取，并清理已写入的部分视频
# 入参：被缩小的上限及独立的上传临时目录
# 返回：无；继续消费后续请求块或留下临时文件时断言失败
def test_video_stream_stops_at_size_limit(monkeypatch, upload_temp_dir) -> None:
    monkeypatch.setattr(knowledge_upload, "DOCUMENT_VIDEO_MAX_BYTES", 1024)
    # 做什么：让超限发生在没有结束边界的第二个正文块
    # 入参：无，使用固定大小的独立请求分块
    # 返回：请求字节流；如果超限后继续读就让测试失败
    async def request_chunks():
        yield VIDEO_START + b"a" * 1024
        yield b"b"
        pytest.fail("达到视频大小上限后继续读取请求")

    with pytest.raises(KnowledgeUploadSizeError, match="视频文件不能超过 200 MB"):
        run(knowledge_upload.prepare_file_upload, CONTENT_TYPE, request_chunks(), 1, 1)
    assert list(upload_temp_dir.glob("fengxuan-video-*")) == []


# 做什么：覆盖拆成单字节的 multipart 边界和像边界的合法二进制正文
# 入参：实际的流式接收入口、假转写通道和隔离临时目录
# 返回：无；字节被截断、改写或未清理时断言失败
def test_split_multipart_preserves_video_bytes(monkeypatch, upload_temp_dir) -> None:
    payload = VIDEO_BYTES + b"\r\n--" + BOUNDARY + b"--fake\xff\x00\r\n"

    # 做什么：把完整请求逐字节送给接收器
    # 入参：无，使用预先构造的文件头、正文与边界
    # 返回：一个字节一个网络块的请求流
    async def request_chunks():
        for byte in VIDEO_START + payload + VIDEO_END:
            yield bytes([byte])

    # 做什么：验证视频通道获得可完整读取的临时文件
    # 入参：路径和扩展名
    # 返回：一块可入库字幕，无需运行模型
    async def parse_video_file(path: str, suffix: str):
        assert suffix == ".mp4" and Path(path).read_bytes() == payload
        return knowledge_channel_video.make_video_drafts(1, 0, 1, "流式上传")

    from app.services.knowledge_channel_registry import CHANNELS

    monkeypatch.setitem(CHANNELS, FileKind.VIDEO, parse_video_file)
    uploaded = run(knowledge_upload.prepare_file_upload, CONTENT_TYPE, request_chunks(), 1, 1)
    assert uploaded.file_size == len(payload)
    assert uploaded.parsed.text == "流式上传"
    assert list(upload_temp_dir.glob("fengxuan-video-*")) == []


# 做什么：确认 multipart 分隔线允许尾随空格且不能绕过单文件约束
# 入参：分隔线两侧分块的请求流和隔离临时目录
# 返回：无；接受第二个字段或泄漏临时文件时断言失败
def test_multipart_rejects_second_part_after_padded_boundary(upload_temp_dir) -> None:
    # 做什么：构造用合法空格修饰分隔线的第二个文件字段
    # 入参：无，使用固定的测试请求
    # 返回：不允许作为单文件上传的两个请求块
    async def request_chunks():
        yield VIDEO_START + VIDEO_BYTES + b"\r\n--" + BOUNDARY + b" \t"
        yield b'\r\nContent-Disposition: form-data; name="extra"\r\n\r\nextra' + VIDEO_END

    with pytest.raises(KnowledgeUploadInvalidError):
        run(knowledge_upload.prepare_file_upload, CONTENT_TYPE, request_chunks(), 1, 1)
    assert list(upload_temp_dir.glob("fengxuan-video-*")) == []


# 做什么：验证第二段 PCM 的字幕从视频开头计时
# 入参：独立 WAV、缩短的转写窗口和可预测的模型返回值
# 返回：无；窗口跨越后时间重置或遗漏片段时断言失败
def test_subtitle_offsets_cross_pcm_windows(tmp_path, monkeypatch) -> None:
    sample_rate = 3
    monkeypatch.setattr(knowledge_channel_video, "AUDIO_SAMPLE_RATE", sample_rate)
    monkeypatch.setattr(knowledge_channel_video, "AUDIO_WINDOW_SECONDS", 1)
    audio_path = str(tmp_path / "two-windows.wav")
    with wave.open(audio_path, "wb") as audio:
        audio.setparams((1, 2, sample_rate, 0, "NONE", "not compressed"))
        audio.writeframes(PCM_FRAMES * 2)
    segment = SimpleNamespace(start=0.25, end=0.75, text="片段")
    model = SimpleNamespace(transcribe=lambda _waveform: ([segment], None))
    subtitles = list(knowledge_channel_video.iter_subtitles(model, audio_path))
    assert subtitles == [(0.25, 0.75, "片段"), (1.25, 1.75, "片段")]


# 做什么：确认没有完整权重的本机模型目录会返回明确错误
# 入参：不含 tokenizer.json 的隔离目录
# 返回：无；模型继续联网、加载或提示不清时断言失败
def test_video_model_needs_local_tokenizer(tmp_path) -> None:
    with pytest.raises(KnowledgeUploadParseError, match="服务器语音转写模型未就绪，请联系管理员"):
        knowledge_channel_video.load_video_model(str(tmp_path), str(tmp_path))


# 做什么：让工作进程阶段抛错或等待取消，验证原件与音轨都被删除
# 入参：隔离请求、临时目录和注入的错误类型
# 返回：无；任一临时文件或数据库行残留时断言失败
@pytest.mark.parametrize(("timeout", "status", "code"), [(False, 422, 4007), (True, 408, 4009)])
def test_video_failure_cleans_upload_and_wav(
    knowledge_client, knowledge_sessions, owner_headers, document_bases,
    upload_temp_dir, monkeypatch, timeout, status, code,
) -> None:
    # 做什么：确认 WAV 已落盘，再模拟解析阶段抛错或超时
    # 入参：子进程入口、WAV 与转写设置
    # 返回：总是抛业务错误或等待取消
    async def fail_transcription(transcribe, audio_path, duration, model_name, cache_dir, *, cancellable, limiter):
        assert Path(audio_path).is_file()
        if timeout:
            await sleep_forever()
        raise KnowledgeUploadParseError("模拟转写失败")

    monkeypatch.setattr(knowledge_channel_video, "run_media_command", fake_media_command)
    monkeypatch.setattr(knowledge_channel_video.to_process, "run_sync", fail_transcription)
    monkeypatch.setattr(knowledge_upload, "DOCUMENT_VIDEO_PARSE_TIMEOUT_SECONDS", 0.15)
    response = knowledge_client.post(FILE_URL, files={"file": ("视频.mp4", VIDEO_BYTES)}, headers=owner_headers)
    assert (response.status_code, response.json()["code"]) == (status, code)
    assert list(upload_temp_dir.glob("fengxuan-video-*")) == []
    assert_no_documents(knowledge_sessions)


# 做什么：验证无音轨的视频在抽音之前拒绝
# 入参：隔离路由、无音轨的 ffprobe 返回值及临时目录
# 返回：无；与指定文案不符或有文件残留时断言失败
def test_video_without_audio_is_rejected(
    knowledge_client, knowledge_sessions, owner_headers, document_bases, upload_temp_dir, monkeypatch,
) -> None:
    # 做什么：让 ffprobe 只找到一条视频轨
    # 入参：当前媒体命令和它的失败文案
    # 返回：工具输出；调用其他工具时让测试失败
    async def video_only(command, failure_message):
        assert command[0] == "ffprobe"
        return b'{"streams":[{"codec_type":"video"}]}'

    monkeypatch.setattr(knowledge_channel_video, "run_media_command", video_only)
    response = knowledge_client.post(FILE_URL, files={"file": ("静音.mp4", VIDEO_BYTES)}, headers=owner_headers)
    assert (response.status_code, response.json()["code"]) == (422, 4008)
    assert response.json()["message"] == "视频没有音轨，无法转写"
    assert list(upload_temp_dir.glob("fengxuan-video-*")) == []
    assert_no_documents(knowledge_sessions)


# 做什么：让过长字幕在拼接时触发文件正文限制
# 入参：隔离路由、假媒体处理与超长的转写结果
# 返回：无；提示缺少拆分说明或留下文档时断言失败
def test_video_text_limit_rejects_document(
    knowledge_client, knowledge_sessions, owner_headers, document_bases, monkeypatch,
) -> None:
    overlong_text = "长" * (DOCUMENT_CONTENT_LENGTH + 1)
    monkeypatch.setattr(knowledge_channel_video, "run_media_command", fake_media_command)
    monkeypatch.setattr(knowledge_channel_video, "load_video_model", lambda _model, _directory: FakeSpeechModel())
    monkeypatch.setattr(knowledge_channel_video.to_process, "run_sync", run_transcription_inline)
    monkeypatch.setattr("tests.test_knowledge_media_upload.SUBTITLES", [(2, 8, overlong_text)])
    response = knowledge_client.post(FILE_URL, files={"file": ("太长.mp4", VIDEO_BYTES)}, headers=owner_headers)
    assert (response.status_code, response.json()["code"]) == (422, 4008)
    assert response.json()["message"] == "视频转写正文不能超过 50000 字，请拆分后上传"
    assert_no_documents(knowledge_sessions)
