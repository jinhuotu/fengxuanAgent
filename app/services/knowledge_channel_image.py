"""用本地 OCR 读取图片文字，并以原有段落组织切片。"""
from io import BytesIO
import re

from PIL import Image
from pytesseract import TesseractError, TesseractNotFoundError, image_to_string

from app.core.errors import KnowledgeUploadContentError, KnowledgeUploadParseError
from app.core.knowledge import DOCUMENT_FILE_PARSE_TIMEOUT_SECONDS
from app.rag.chunking import ChunkDraft
from app.services.knowledge_channel_common import make_draft

__all__ = ["parse_image", "recognize_text"]

OCR_LANGUAGES = "chi_sim+eng"
IMAGE_HEADING = "第{}段"
BLANK_LINE = re.compile(r"\n[^\S\n]*\n")
IMAGE_INVALID_MESSAGE = "图片无法解码，请检查文件是否损坏"
OCR_ENGINE_MISSING_MESSAGE = "服务器未安装 OCR 引擎，请联系管理员"
OCR_FAILED_MESSAGE = "图片 OCR 失败，请检查图片或联系管理员确认语言包"
OCR_EMPTY_MESSAGE = "图片未识别到文字，请换更清晰的图片"


# 做什么：通过 Pillow 完整解码图片，确认正文像素可读
# 入参：上传图片的原始字节
# 返回：无；无效图片或解码体积过大时抛错误
def validate_image(image_bytes: bytes) -> None:
    with Image.open(BytesIO(image_bytes)) as image:
        if Image.MAX_IMAGE_PIXELS is not None and image.width * image.height > Image.MAX_IMAGE_PIXELS:
            raise KnowledgeUploadParseError(IMAGE_INVALID_MESSAGE)
        image.load()


# 做什么：用 Tesseract 识别简体中文和英文
# 入参：已经验证能解码的图片字节
# 返回：OCR 文字；程序缺失或识别失败时抛解析错误
def recognize_text(image_bytes: bytes) -> str:
    # 新手注意：OCR 像把照片上的字抄进笔记本，原有行与空行用于保留段落。
    try:
        with Image.open(BytesIO(image_bytes)) as image:
            return image_to_string(image, lang=OCR_LANGUAGES, timeout=DOCUMENT_FILE_PARSE_TIMEOUT_SECONDS)
    except TesseractNotFoundError as error:
        raise KnowledgeUploadParseError(OCR_ENGINE_MISSING_MESSAGE) from error
    except (TesseractError, RuntimeError, OSError) as error:
        raise KnowledgeUploadParseError(OCR_FAILED_MESSAGE) from error


# 做什么：把 OCR 文字按空行划分段落，再沿用通用切块规则
# 入参：图片字节和由文件探测确认的后缀
# 返回：切片列表；识别不到文字时抛正文错误
def parse_image(file_bytes: bytes, suffix: str) -> list[ChunkDraft]:
    try:
        validate_image(file_bytes)
    except (OSError, ValueError, SyntaxError, Image.DecompressionBombError) as error:
        raise KnowledgeUploadParseError(IMAGE_INVALID_MESSAGE) from error
    recognized_text = recognize_text(file_bytes)
    normalized_text = "\n".join(recognized_text.splitlines())
    paragraphs = [paragraph.strip() for paragraph in BLANK_LINE.split(normalized_text) if paragraph.strip()]
    if not paragraphs:
        raise KnowledgeUploadContentError(OCR_EMPTY_MESSAGE)
    drafts = []
    for index, paragraph in enumerate(paragraphs, start=1):
        drafts.extend(make_draft(IMAGE_HEADING.format(index), paragraph))
    return drafts
