"""新密码的字符下限和 UTF-8 字节上限。"""

MIN_PASSWORD_LENGTH = 8
MAX_PASSWORD_BYTES = 128


# 做什么：检查密码编码后的字节数
# 入参：完整的明文密码
# 返回：原密码；超限时抛出只包含限制说明的 ValueError
# 新手注意：字节数像行李重量，字符数像件数。相同件数的行李可以有不同重量。
def validate_password_byte_limit(plain_password: str) -> str:
    if (
        len(plain_password) > MAX_PASSWORD_BYTES
        or len(plain_password.encode("utf-8")) > MAX_PASSWORD_BYTES
    ):
        raise ValueError(f"密码最多 {MAX_PASSWORD_BYTES} 个 UTF-8 字节")
    return plain_password
