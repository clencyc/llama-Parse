# This file is duplicated between the modelbit package and the runtime environment. Only import from stdlib since other
# files might not be available in that environment.
#
# Keep in sync with:
#   - notebook/environment/modelbit/modelbit/internal/s3_stream.py
#   - deployer/environment/modelbit_s3_stream.py

from typing import cast, Any, IO, List, Dict, Optional, Generator
from Cryptodome.Cipher import AES
from Cryptodome.Util.Padding import unpad
import os
import zstandard
import random
import base64
from contextlib import contextmanager
from hashlib import sha1


@contextmanager
def storeFileOnSuccess(finalPath: str) -> Generator[str, Any, None]:
  tmpPath = finalPath + str(random.random()).replace(".", "")
  dirName = os.path.dirname(tmpPath)
  if dirName:
    os.makedirs(dirName, exist_ok=True)
  try:
    yield tmpPath

    if os.path.exists(tmpPath):
      if os.path.exists(finalPath):
        os.unlink(finalPath)
      os.rename(tmpPath, finalPath)
  except Exception as err:
    if os.path.exists(tmpPath):  # Remove partially stored file
      os.unlink(tmpPath)
    raise err


class HashStream:

  def __init__(self, outWriter: IO[bytes]):
    self.outWriter = outWriter
    self.hasher = sha1()

  def write(self, data: bytes) -> int:
    self.hasher.update(data)
    return self.outWriter.write(data)

  def getSha(self) -> str:
    return f"sha1:{self.hasher.hexdigest()}"


class DecryptStream:

  def __init__(self, cipher: Any, outWriter: IO[bytes]):
    self.cipher = cipher
    self.outWriter = outWriter
    self.isFirstChunk: bool = True
    self.prevChunk: bytes = b""

  def write(self, data: bytes) -> int:
    decChunk = cast(bytes, self.cipher.decrypt(data))
    if self.isFirstChunk:
      self.isFirstChunk = False
    else:
      self.outWriter.write(self.prevChunk)

    self.prevChunk = decChunk
    return len(self.prevChunk)

  def finish(self) -> None:
    chunk = unpad(self.prevChunk, AES.block_size)
    self.outWriter.write(chunk)

  @classmethod
  @contextmanager
  def wrapper(cls, cipher: Any, outWriter: IO[bytes]) -> Any:
    ds = DecryptStream(cipher=cipher, outWriter=outWriter)
    yield ds
    ds.finish()


def getFileCipher(pystateKey64: str, s3Metadata: Dict[str, str]) -> Any:
  fileKeyCipher = AES.new(base64.b64decode(pystateKey64), AES.MODE_ECB)  # type: ignore
  fileKeyEnc = base64.b64decode(s3Metadata["x-amz-key"])
  fileKey = unpad(fileKeyCipher.decrypt(fileKeyEnc), AES.block_size)
  fileIv = base64.b64decode(s3Metadata["x-amz-iv"])
  return AES.new(fileKey, AES.MODE_CBC, iv=fileIv)  # type: ignore


def downloadDecryptZstdFile(s3Client: Any, s3Bucket: str, s3Key: str, pystateKeys: List[str],
                            contentHash: Optional[str], outputPath: str, isEncrypted: bool) -> None:
  s3Obj = s3Client.head_object(Bucket=s3Bucket, Key=s3Key)
  lastError: Optional[Exception] = None
  for pystateKey64 in pystateKeys:
    try:
      with storeFileOnSuccess(finalPath=outputPath) as tmpPath:
        with open(tmpPath, "wb") as fileWriter:
          hashStreamer = HashStream(outWriter=fileWriter)
          with zstandard.ZstdDecompressor().stream_writer(cast(IO[bytes], hashStreamer),
                                                          closefd=False) as zstdStreamer:
            if isEncrypted:
              fileCipher = getFileCipher(pystateKey64, s3Obj['Metadata'])
              with DecryptStream.wrapper(cipher=fileCipher, outWriter=zstdStreamer) as decryptStreamer:
                s3Client.download_fileobj(Bucket=s3Bucket, Key=s3Key, Fileobj=decryptStreamer)
            else:
              s3Client.download_fileobj(Bucket=s3Bucket, Key=s3Key, Fileobj=zstdStreamer)
            if contentHash and hashStreamer.getSha() != contentHash:
              raise Exception(f"Mismatch For={s3Key} Expected={contentHash} Actual={hashStreamer.getSha()}")
            return
    except Exception as err:
      lastError = err
    if lastError:
      raise lastError
