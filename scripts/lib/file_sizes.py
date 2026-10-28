"""macOS bulk directory metadata for file-byte totals."""

import ctypes
import errno
import os
import struct


class AttrList(ctypes.Structure):
    _fields_ = [
        ("bitmapcount", ctypes.c_uint16), ("reserved", ctypes.c_uint16),
        ("common", ctypes.c_uint32), ("volume", ctypes.c_uint32),
        ("directory", ctypes.c_uint32), ("file", ctypes.c_uint32),
        ("fork", ctypes.c_uint32),
    ]


# sys/attr.h: RETURNED_ATTRS | ERROR | NAME | OBJTYPE; data-fork length and allocation.
COMMON = 0xA0000009
DATA_LENGTH = 0x00000200
DATA_ALLOCSIZE = 0x00000400
FILE_SIZES = DATA_LENGTH | DATA_ALLOCSIZE
PACK_INVALID = 0x00000008
LIBC = ctypes.CDLL(None, use_errno=True)
LIBC.getattrlistbulk.argtypes = [
    ctypes.c_int, ctypes.POINTER(AttrList), ctypes.c_void_p, ctypes.c_size_t, ctypes.c_uint64,
]
LIBC.getattrlistbulk.restype = ctypes.c_int


def directory_entries(folder):
    """Yield (name, kind, logical bytes, allocated bytes, errno), never following symlinks."""
    fd = os.open(folder, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        attributes = AttrList(5, 0, COMMON, 0, 0, FILE_SIZES, 0)
        buffer = ctypes.create_string_buffer(64 * 1024)
        while True:
            count = LIBC.getattrlistbulk(fd, ctypes.byref(attributes), buffer, len(buffer), PACK_INVALID)
            if count < 0:
                code = ctypes.get_errno()
                raise OSError(code, os.strerror(code), folder)
            if not count:
                return
            data = buffer.raw
            offset = 0
            for _ in range(count):
                if offset + 40 > len(data):
                    raise OSError(errno.EIO, "truncated bulk metadata record", folder)
                length, common, _, _, file_attributes, _, error = struct.unpack_from("=7I", data, offset)
                if length < 40 or offset + length > len(data):
                    raise OSError(errno.EIO, "invalid bulk metadata record", folder)
                # Common fields stay packed; directories omit file-specific fields even with PACK_INVAL_ATTRS.
                name_offset, name_length, object_type = struct.unpack_from("=iII", data, offset + 28)
                start = offset + 28 + name_offset
                fixed_size = 40 if object_type == 2 or error else 56
                valid_name_range = offset + fixed_size <= start < start + name_length <= offset + length
                if not common & 1 or name_length < 1 or not valid_name_range:
                    raise OSError(errno.EIO, "missing or invalid bulk entry name", folder)
                raw_name = data[start:start + name_length]
                if raw_name[-1:] != b"\0" or b"/" in raw_name or b"\0" in raw_name[:-1]:
                    raise OSError(errno.EIO, "invalid bulk entry name", folder)
                name = os.fsdecode(raw_name[:-1])
                if name in ("", ".", ".."):
                    raise OSError(errno.EIO, "invalid bulk entry name", folder)
                if not error and (not common & 8 or (object_type == 1 and
                        file_attributes & FILE_SIZES != FILE_SIZES)):
                    raise OSError(errno.ENOTSUP, "required bulk metadata unavailable", os.path.join(folder, name))
                kind = "f" if object_type == 1 else "d" if object_type == 2 else "other"
                logical_size, allocated_size = (
                    struct.unpack_from("=2q", data, offset + 40) if kind == "f" and not error else (0, 0)
                )
                if logical_size < 0 or allocated_size < 0:
                    raise OSError(errno.EIO, "invalid bulk file size", os.path.join(folder, name))
                yield name, kind, logical_size, allocated_size, error
                offset += length
    finally:
        os.close(fd)
