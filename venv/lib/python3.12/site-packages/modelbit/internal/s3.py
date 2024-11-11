import os
import logging
from typing import List, Optional
from modelbit.utils import tempFilePath

logger = logging.getLogger(__name__)


def getS3FileBytes(keySuffix: str) -> bytes:
  with tempFilePath() as tmpPath:
    _downloadDecrypt(s3Key=f"{_workspaceId()}/{keySuffix}", storePath=tmpPath)
    with open(tmpPath, "rb") as f:
      return f.read()


def getRuntimeObjectToBytes(contentHash: str) -> bytes:
  getRuntimeObjectToFile(contentHash)
  with open(s3ObjectCachePath(contentHash=contentHash), "rb") as f:
    return f.read()


def getRuntimeObjectToFile(contentHash: str) -> None:
  cachePath = s3ObjectCachePath(contentHash=contentHash)
  try:
    return _downloadDecryptRuntimeObject(cachePath=cachePath, contentHash=contentHash, skipIfExists=True)
  except Exception as err:
    logger.info("Failed to read from cache", exc_info=err)
    return _downloadDecryptRuntimeObject(cachePath=cachePath, contentHash=contentHash, skipIfExists=False)


def _workspaceId() -> str:
  return os.environ['WORKSPACE_ID']


def _pystateBucket() -> str:
  return os.environ['PYSTATE_BUCKET']


def _pystateKeys() -> List[str]:
  return os.environ['PYSTATE_KEYS'].split(",")


def s3ObjectCachePath(contentHash: str) -> str:
  tempDir = os.getenv("MB_TEMP_DIR_OVERRIDE", "/tmp/modelbit")
  rtDir = "/runtime_objects" if os.path.exists("/runtime_objects") else f"{tempDir}/runtime_objects"
  return os.path.join(rtDir, contentHash + ".zstd.enc")


# We store the decrypted version in deployment's cache
def _downloadDecryptRuntimeObject(cachePath: str, contentHash: str, skipIfExists: bool = False) -> None:

  return _downloadDecrypt(s3Key=f'{_workspaceId()}/runtime_objects/{contentHash}.zstd.enc',
                          storePath=cachePath,
                          contentHash=contentHash,
                          skipIfExists=skipIfExists)


def _downloadDecrypt(s3Key: str,
                     storePath: str,
                     contentHash: Optional[str] = None,
                     skipIfExists: bool = False) -> None:
  from .s3_stream import downloadDecryptZstdFile
  from modelbit.utils import boto3Client

  if skipIfExists and os.path.exists(storePath):
    return

  return downloadDecryptZstdFile(s3Client=boto3Client("s3"),
                                 s3Bucket=_pystateBucket(),
                                 s3Key=s3Key,
                                 pystateKeys=_pystateKeys(),
                                 contentHash=contentHash,
                                 outputPath=storePath,
                                 isEncrypted=True)
