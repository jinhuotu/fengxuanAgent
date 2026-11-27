"""用真实 OCR 和本机语音模型走知识库上传路由，数据库仍为隔离 SQLite。"""
from io import BytesIO
from pathlib import Path
import re
from subprocess import DEVNULL, run

import pytest
from PIL import Image, ImageDraw, ImageFont
from pytesseract import get_languages

from app.core.knowledge import DocumentStatus, FileKind
from tests.test_knowledge import DOCUMENT_URL, document_bases, knowledge_client, knowledge_sessions, owner_headers
from tests.test_knowledge_file_upload import FILE_URL
from tests.test_knowledge_media_upload import upload_temp_dir

pytestmark = pytest.mark.smoke

OCR_LINE = "FENGXUAN OCR 123"
OCR_SIZE = (780, 145)
OCR_POSITION = (28, 28)
OCR_FONT_SIZE = 70
# 由本机合成的英文报时短句配黑色画面，不含真实用户数据。
VIDEO_FIXTURE = Path(__file__).with_name("fixtures") / "knowledge_speech.mp4"
VIDEO_TIMELINE = re.compile(r"第1段（\d{2,}:\d{2}–\d{2,}:\d{2}）")
SMOKE_VIDEO_TIMEOUT_SECONDS = 30


# 做什么：生成适合真实 OCR 引擎识别的一行文字图片
# 入参：无，使用 Pillow 自带字体
# 返回：PNG 图片字节；无法渲染时由测试报错
def render_ocr_fixture() -> bytes:
    output = BytesIO()
    with Image.new("RGB", OCR_SIZE, "white") as image:
        font = ImageFont.load_default(size=OCR_FONT_SIZE)
        ImageDraw.Draw(image).text(OCR_POSITION, OCR_LINE, fill="black", font=font)
        image.save(output, format="PNG")
    return output.getvalue()


# 做什么：确认真实 Tesseract 可用且能从上传图片提取预期正文
# 入参：隔离的应用客户端、本人库与鉴权头
# 返回：无；语言包、识别结果或偏移不正确时断言失败
def test_tesseract_reads_uploaded_image(knowledge_client, owner_headers, document_bases) -> None:
    assert {"chi_sim", "eng"} <= set(get_languages())
    response = knowledge_client.post(FILE_URL, files={"file": ("smoke.png", render_ocr_fixture())}, headers=owner_headers)
    assert response.status_code == 200, response.json()
    document = response.json()["data"]
    assert (document["status"], document["file_type"]) == (DocumentStatus.READY, FileKind.IMAGE)
    chunks = knowledge_client.get(f"{DOCUMENT_URL}/{document['id']}/chunks", headers=owner_headers).json()["data"]
    assert chunks["total"] == 1
    first = chunks["items"][0]
    assert (first["content"], first["char_start"], first["char_end"]) == (OCR_LINE, 0, len(OCR_LINE))


# 做什么：准备同一句报时语音的 MP4 和 WebM 两种容器
# 入参：目标后缀和该条测试的临时目录
# 返回：可上传的视频文件路径，FFmpeg 无法转换时直接报错
def smoke_video_path(suffix: str, tmp_path: Path) -> Path:
    if suffix == ".mp4":
        return VIDEO_FIXTURE
    path = tmp_path / "knowledge_speech.webm"
    command = [
        "ffmpeg", "-v", "error", "-nostdin", "-i", str(VIDEO_FIXTURE),
        "-c:v", "libvpx-vp9", "-c:a", "libopus", str(path),
    ]
    run(command, stdin=DEVNULL, check=True, timeout=SMOKE_VIDEO_TIMEOUT_SECONDS)
    return path


# 做什么：真实抽音轨、转写并通过 HTTP 读取带时间轴的切片
# 入参：隔离 SQLite 客户端、鉴权、临时目录和容器后缀
# 返回：无；漏读语音、时间轴不符或遗留临时文件时断言失败
@pytest.mark.parametrize("suffix", [".mp4", ".webm"])
def test_whisper_transcribes_uploaded_video(
    knowledge_client, owner_headers, document_bases, upload_temp_dir, tmp_path, suffix,
) -> None:
    path = smoke_video_path(suffix, tmp_path)
    with path.open("rb") as video:
        response = knowledge_client.post(FILE_URL, files={"file": (path.name, video)}, headers=owner_headers)
    assert response.status_code == 200, response.json()
    document = response.json()["data"]
    assert (document["status"], document["file_type"]) == (DocumentStatus.READY, FileKind.VIDEO)
    chunks = knowledge_client.get(f"{DOCUMENT_URL}/{document['id']}/chunks", headers=owner_headers).json()["data"]
    first = chunks["items"][0]
    assert VIDEO_TIMELINE.fullmatch(first["heading_path"])
    assert "meeting" in first["content"].lower() and "starts" in first["content"].lower()
    assert (first["char_start"], first["char_end"]) == (0, len(first["content"]))
    assert list(upload_temp_dir.glob("fengxuan-video-*")) == []
