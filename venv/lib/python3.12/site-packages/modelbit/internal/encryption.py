from io import BytesIO, BufferedReader, BufferedWriter
from typing import Any, Union, Optional, cast, List
import base64

from .describe import calcHash, calcHashFromFile, fileSize
from modelbit.utils import storeFileOnSuccess, progressBar
from modelbit.ux import SHELL_FORMAT_FUNCS as SFF

ZSTD_MAGIC_NUMBER = b"\x28\xB5\x2F\xFD"
GZIP_MAGIC_NUMBER = b"\x1f\x8b"
readBlockSize = 16 * 1024 * 1024  # 16MB


def _isLikelyZstd(data: bytes) -> bool:
  return data[0:4] == ZSTD_MAGIC_NUMBER


def _isLikelyGzip(data: bytes) -> bool:
  return data[0:2] == GZIP_MAGIC_NUMBER


# Cipher is stateful, so we need to make new ones for each read operation
def _makeCipher(key64: str, iv64: str) -> Any:
  from Cryptodome.Cipher import AES
  return AES.new(base64.b64decode(key64), AES.MODE_CBC, iv=base64.b64decode(iv64))  # type: ignore


def _assertHashMatch(expected: str, actual: str) -> None:
  if expected != actual:
    raise ValueError(f"Hash mismatch. Expected={expected} Found={actual}")


def _decryptZstd(cipher: Any, data: BufferedReader, outWriter: Union[BytesIO, BufferedWriter], inputSize: int,
                 desc: str) -> None:
  import zstandard
  from Cryptodome.Cipher import AES
  from Cryptodome.Util.Padding import unpad

  with progressBar(inputSize=inputSize, desc=(SFF["green"]("Decrypting") + f" '{desc}'")) as t:
    with zstandard.ZstdDecompressor().stream_writer(outWriter, write_size=50_000_000, closefd=False) as dCom:
      rData = data.read(readBlockSize)
      while rData:
        t.update(len(rData))
        chunk = cipher.decrypt(rData)
        if len(chunk) != readBlockSize or len(data.peek(1)) == 0:  # last block
          chunk = unpad(chunk, AES.block_size)
        dCom.write(chunk)
        rData = data.read(readBlockSize)


def _unZstd(data: BufferedReader, outWriter: Union[BytesIO, BufferedWriter], inputSize: int,
            desc: str) -> None:
  import zstandard

  with progressBar(inputSize=inputSize, desc=(SFF["green"]("Decompressing") + f" '{desc}'")) as t:
    with zstandard.ZstdDecompressor().stream_writer(outWriter, write_size=50_000_000, closefd=False) as dCom:
      rData = data.read(readBlockSize)
      while rData:
        t.update(len(rData))
        dCom.write(rData)
        rData = data.read(readBlockSize)


def decryptAndValidateToBytes(encFilePath: str,
                              key64: str,
                              iv64: str,
                              expectedHash: Optional[str],
                              desc: str,
                              isEncrypted: bool = True) -> memoryview:
  byteStream = BytesIO()
  _decryptAndValidate(encFilePath=encFilePath,
                      key64=key64,
                      iv64=iv64,
                      outWriter=byteStream,
                      desc=desc,
                      isEncrypted=isEncrypted)
  buff = byteStream.getbuffer()

  if expectedHash:
    _assertHashMatch(expectedHash, calcHash(buff))

  return buff


def decryptAndValidateToFile(encFilePath: str,
                             key64: str,
                             iv64: str,
                             toFile: str,
                             expectedHash: Optional[str],
                             desc: str,
                             isEncrypted: bool = True) -> None:
  with storeFileOnSuccess(finalPath=toFile) as tmpPath:
    with open(tmpPath, "wb") as f:
      _decryptAndValidate(encFilePath=encFilePath,
                          key64=key64,
                          iv64=iv64,
                          outWriter=f,
                          desc=desc,
                          isEncrypted=isEncrypted)

  if expectedHash:
    _assertHashMatch(expectedHash, calcHashFromFile(toFile))


def _decryptAndValidate(encFilePath: str, key64: str, iv64: str, outWriter: Union[BytesIO, BufferedWriter],
                        desc: str, isEncrypted: bool) -> None:
  from Cryptodome.Cipher import AES
  from Cryptodome.Util.Padding import unpad

  with open(encFilePath, "rb") as f:
    if isEncrypted:
      head = _makeCipher(key64=key64, iv64=iv64).decrypt(f.read(AES.block_size))
    else:
      head = f.read(AES.block_size)

  with open(encFilePath, "rb") as data:
    if _isLikelyZstd(head):
      if isEncrypted:
        _decryptZstd(cipher=_makeCipher(key64=key64, iv64=iv64),
                     data=data,
                     outWriter=outWriter,
                     inputSize=fileSize(encFilePath),
                     desc=desc)
      else:
        _unZstd(data=data, outWriter=outWriter, inputSize=fileSize(encFilePath), desc=desc)
    elif _isLikelyGzip(head):
      import zlib
      if isEncrypted:
        zData = unpad(_makeCipher(key64=key64, iv64=iv64).decrypt(data.read()), AES.block_size)
      else:
        zData = data.read()
      uncompressedData = zlib.decompress(zData, zlib.MAX_WBITS | 32)
      outWriter.write(uncompressedData)
    else:
      raise Exception("Unknown compression format")


def encryptDataFromBytes(inData: bytes, outFile: str, key64: str, iv64: str, desc: str) -> None:
  with open(outFile, "wb") as outWriter:
    return _encryptData(inReader=BytesIO(inData),
                        outWriter=outWriter,
                        key64=key64,
                        iv64=iv64,
                        inputSize=len(inData),
                        desc=desc)


def encryptDataFromFile(inFile: str, outFile: str, key64: str, iv64: str, desc: str) -> None:
  with open(inFile, "rb") as inReader:
    with open(outFile, "wb") as outWriter:
      return _encryptData(inReader=inReader,
                          outWriter=outWriter,
                          key64=key64,
                          iv64=iv64,
                          inputSize=fileSize(inFile),
                          desc=desc)


def _encryptData(inReader: Union[BytesIO, BufferedReader], outWriter: Union[BytesIO, BufferedWriter],
                 key64: str, iv64: str, inputSize: int, desc: str) -> None:
  import zstandard
  from Cryptodome.Cipher import AES
  from Cryptodome.Util.Padding import pad
  cipher = AES.new(mode=AES.MODE_CBC, key=base64.b64decode(key64), iv=base64.b64decode(iv64))  # type: ignore

  cCtx = zstandard.ZstdCompressor()
  chunker = cCtx.chunker(chunk_size=readBlockSize, size=inputSize)

  with progressBar(inputSize=inputSize, desc=(SFF["green"]("Encrypting") + f" '{desc}'")) as t:
    inChunk = inReader.read(readBlockSize)
    while inChunk:
      for outChunk in cast(List[bytes], chunker.compress(inChunk)):  # type: ignore
        outWriter.write(cipher.encrypt(outChunk))
      t.update(len(inChunk))
      inChunk = inReader.read(readBlockSize)
    finalChunks = cast(List[bytes], list(chunker.finish()))  # type: ignore
    finalChunk = b"".join(finalChunks)
    outWriter.write(cipher.encrypt(pad(finalChunk, AES.block_size)))
