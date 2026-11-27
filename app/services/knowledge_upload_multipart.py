"""逐段读取只含一个 file 字段的 multipart 请求。"""
from collections.abc import AsyncIterable, AsyncIterator
from email import policy
from email.message import Message
from email.parser import BytesHeaderParser
from typing import BinaryIO

from app.core.errors import KnowledgeUploadInvalidError, KnowledgeUploadSizeError
from app.core.knowledge import DOCUMENT_MULTIPART_OVERHEAD_BYTES

MULTIPART_MEDIA_TYPE = "multipart/form-data"
UPLOAD_FIELD = "file"
BOUNDARY_PARAMETER = "boundary"
DISPOSITION = "Content-Disposition"
TRANSFER_ENCODING = "Content-Transfer-Encoding"
LINE_END = b"\r\n"
HEADER_END = LINE_END * 2
BOUNDARY_PREFIX = b"--"
CLOSING_SUFFIX = b"--"
BOUNDARY_PADDING = b" \t"
BOUNDARY_MAX_LENGTH = 70
BOUNDARY_CHARACTERS = "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ'()+_,-./:=? "
READ_CHUNK_BYTES = 64 * 1024


# 做什么：验证请求类型并读出用于分隔文件正文的标记
# 入参：请求 Content-Type
# 返回：ASCII 边界；缺失、过长或包含非法字符时拒绝
def multipart_boundary(content_type: str) -> bytes:
    if "\r" in content_type or "\n" in content_type:
        raise KnowledgeUploadInvalidError()
    header = Message()
    header["Content-Type"] = content_type
    boundary = header.get_param(BOUNDARY_PARAMETER)
    if header.get_content_type() != MULTIPART_MEDIA_TYPE or not isinstance(boundary, str):
        raise KnowledgeUploadInvalidError()
    if not 0 < len(boundary) <= BOUNDARY_MAX_LENGTH or boundary.endswith(" "):
        raise KnowledgeUploadInvalidError()
    if any(character not in BOUNDARY_CHARACTERS for character in boundary):
        raise KnowledgeUploadInvalidError()
    return boundary.encode("ascii")


# 做什么：把 ASGI 给出的请求块拆小，限制解析器的单次缓冲量
# 入参：请求字节流
# 返回：最多 READ_CHUNK_BYTES 的字节段；断开由上层处理
async def iter_multipart_chunks(body: AsyncIterable[bytes]) -> AsyncIterator[bytes]:
    async for chunk in body:
        for offset in range(0, len(chunk), READ_CHUNK_BYTES):
            yield chunk[offset:offset + READ_CHUNK_BYTES]


# 做什么：从有界的文件头解出文件名并验证字段
# 入参：分隔行之后的单个文件头
# 返回：原始文件名；字段错误、嵌套或编码传输时拒绝
def multipart_filename(headers: bytes) -> str:
    part = BytesHeaderParser(policy=policy.default).parsebytes(headers)
    filename = part.get_filename()
    if (part.defects or len(part.get_all(DISPOSITION, [])) != 1
            or part.get_content_disposition() != "form-data"
            or part.get_param("name", header=DISPOSITION) != UPLOAD_FIELD
            or not filename or part.get_content_maintype() == "multipart"
            or TRANSFER_ENCODING in part):
        raise KnowledgeUploadInvalidError()
    return filename


# 做什么：只接收 multipart 起始行与文件头
# 入参：可继续迭代的字节流和分隔标记
# 返回：文件名和同一请求块内已读的正文；封装不完整时拒绝
async def read_upload_header(chunks: AsyncIterator[bytes], boundary: bytes) -> tuple[str, bytes]:
    # 新手注意：multipart 像带标签的信封，文件名在信封头，正文按分隔线截取。
    starting_line = BOUNDARY_PREFIX + boundary + LINE_END
    pending = bytearray()
    async for chunk in chunks:
        pending.extend(chunk)
        separator = pending.find(HEADER_END)
        if separator == -1 and len(pending) < DOCUMENT_MULTIPART_OVERHEAD_BYTES + len(HEADER_END):
            continue
        if not len(starting_line) <= separator <= DOCUMENT_MULTIPART_OVERHEAD_BYTES:
            raise KnowledgeUploadInvalidError()
        if not pending.startswith(starting_line):
            raise KnowledgeUploadInvalidError()
        filename = multipart_filename(bytes(pending[len(starting_line):separator]))
        return filename, bytes(pending[separator + len(HEADER_END):])
    raise KnowledgeUploadInvalidError()


# 新手注意：缓冲区只留可能跨块的分隔线，像读书时用书签记住上一页的尾巴。
class MultipartFileWriter:
    # 做什么：记录写盘目标、正文限制及结束边界
    # 入参：已打开的临时文件、边界和该类型的超限错误
    # 返回：可持续接收正文的写入器
    def __init__(self, target: BinaryIO, boundary: bytes, limit: int, size_error: KnowledgeUploadSizeError):
        self.target = target
        self.marker = LINE_END + BOUNDARY_PREFIX + boundary
        self.limit = limit
        self.size_error = size_error
        self.pending = b""
        self.written = 0
        self.closed = False

    # 做什么：写入已确认属于文件正文的字节
    # 入参：排除边界后的正文片段
    # 返回：无；超限时立即抛错，超出的字节不写盘
    def write(self, content: bytes) -> None:
        if self.written + len(content) > self.limit:
            raise self.size_error
        self.target.write(content)
        self.written += len(content)

    # 做什么：找出可能是下个边界开头的最短未确认区域
    # 入参：本块正文和已消费的位置
    # 返回：需保留的尾部起点；尾部无法成为边界时全部写盘
    def partial_marker_start(self, content: bytes, offset: int) -> int:
        earliest = max(offset, len(content) - len(self.marker) + 1)
        candidate = content.find(LINE_END[:1], earliest)
        while candidate != -1:
            if self.marker.startswith(content[candidate:]):
                return candidate
            candidate = content.find(LINE_END[:1], candidate + 1)
        return len(content)

    # 做什么：识别完整边界；正文里碰巧出现的同前缀字符继续写盘
    # 入参：当前收到的字节与候选边界起点
    # 返回：已消费的位置，或用 None 表示需等下一个请求块
    def consume_marker(self, content: bytes, start: int) -> int | None:
        marker_end = start + len(self.marker)
        suffix = content[marker_end:marker_end + len(CLOSING_SUFFIX)]
        if suffix and suffix != CLOSING_SUFFIX and CLOSING_SUFFIX.startswith(suffix):
            return None
        closing = suffix == CLOSING_SUFFIX
        trailer_start = marker_end + len(CLOSING_SUFFIX) if closing else marker_end
        trailer = content[trailer_start:trailer_start + BOUNDARY_MAX_LENGTH + len(LINE_END)]
        ending = trailer.lstrip(BOUNDARY_PADDING)
        if ending.startswith(LINE_END):
            if not closing:
                raise KnowledgeUploadInvalidError()
            self.closed = True
            return len(content)
        if len(trailer) < BOUNDARY_MAX_LENGTH + len(LINE_END) and LINE_END.startswith(ending):
            return None
        self.write(content[start:trailer_start])
        return trailer_start

    # 做什么：逐块定位结束边界并写盘
    # 入参：当前收到的有限大小的请求块
    # 返回：无；遇到多文件字段或超限立即拒绝
    def feed(self, chunk: bytes) -> None:
        if self.closed:
            return
        content = self.pending + chunk
        offset = 0
        while True:
            start = content.find(self.marker, offset)
            if start == -1:
                tail_start = self.partial_marker_start(content, offset)
                self.write(content[offset:tail_start])
                self.pending = content[tail_start:]
                return
            self.write(content[offset:start])
            next_offset = self.consume_marker(content, start)
            if next_offset is None or self.closed:
                self.pending = content[start:] if next_offset is None else b""
                return
            offset = next_offset

    # 做什么：结束读取时核对最终边界并返回文件大小
    # 入参：无，使用上一次读取保留的边界尾部
    # 返回：实际写入字节数；缺少结束标记时拒绝
    def finish(self) -> int:
        if not self.closed and self.pending.rstrip(BOUNDARY_PADDING) != self.marker + CLOSING_SUFFIX:
            # 残留字节若不是结束标记就是正文，超限应该优先报告大小错误。
            self.write(self.pending)
            raise KnowledgeUploadInvalidError()
        return self.written
