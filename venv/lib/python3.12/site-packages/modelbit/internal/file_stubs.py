import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional, cast
from functools import lru_cache

import yaml

logger = logging.getLogger(__name__)
LARGE_FILE_STUB_SENTINEL = b'_: MBFileStub'
SCHEMA_VERSION = 1


def repr_str(dumper: yaml.Dumper, data: str) -> yaml.ScalarNode:
  if '\n' in data:
    return dumper.represent_scalar('tag:yaml.org,2002:str', data, style='|')  # type: ignore
  return dumper.represent_scalar('tag:yaml.org,2002:str', data)  # type: ignore


def toYaml(contentHash: str, fileSize: int, objDesc: Dict[str, Any]) -> str:
  metadata: Dict[str, Any] = dict(fileSize=fileSize, **objDesc)

  obj = _toFileStubDict(contentHash, metadata)
  yaml.add_representer(str, repr_str)
  return yaml.dump(obj, width=1000)


def _isLargeFileStub(content: bytes) -> bool:
  return content.startswith(LARGE_FILE_STUB_SENTINEL)


@lru_cache(100)
def _parseStub(content: bytes) -> Dict[str, Any]:
  if not _isLargeFileStub(content):
    return {}

  obj = yaml.safe_load(content.decode("utf-8"))
  if type(obj) is dict:
    return cast(Dict[str, Any], obj)
  return {}


def contentHashFromYaml(content: bytes) -> Optional[str]:
  stub = _parseStub(content)
  return stub.get("contentHash")


def isSharedFromYaml(content: bytes) -> bool:
  stub = _parseStub(content)
  return bool(stub.get("shared"))


def sizeFromYaml(content: bytes) -> Optional[int]:
  stub = _parseStub(content)
  metadata = cast(Optional[Dict[str, Any]], stub.get("metadata"))
  if type(metadata) is not dict:
    return None
  return metadata.get("fileSize")


def _toFileStubDict(contentHash: str, objDesc: Dict[str, Any]) -> Dict[str, Any]:
  return {
      "_": "MBFileStub",
      "createdAt": datetime.now(timezone.utc).replace(microsecond=0),
      "contentHash": contentHash,
      "metadata": objDesc,
      "schemaVersion": SCHEMA_VERSION
  }


def fileIsStub(filepath: str) -> bool:
  with open(filepath, "rb") as f:
    return f.read(len(LARGE_FILE_STUB_SENTINEL)) == LARGE_FILE_STUB_SENTINEL
