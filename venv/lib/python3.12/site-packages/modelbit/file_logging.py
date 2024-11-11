from typing import Any, Tuple, Optional
from types import ModuleType
from .error import UserFacingError
from .utils import boto3Client, inDeployment, deploymentPystateBucket, deploymentWorkspaceId, deploymentPystateKeys
import os, tempfile, hashlib, zstandard, base64, logging
from Cryptodome.Cipher import AES
from Cryptodome.Util.Padding import pad
from Cryptodome.Random import get_random_bytes
from modelbit.internal.retry import retry

logger = logging.getLogger(__name__)
PngThresholdBytes = 100_000  # use png for clearer image if smaller than this


def logImage(obj: Any) -> str:
  if type(obj) is str:
    if not os.path.exists(obj) or not obj.endswith((".png", ".jpg", ".jpeg")):
      raise UserFacingError(
          f"Image logging requires a path to a .png or .jpg image, but '{obj}' was provided.")
    return _uploadImage(*_getImageBytes(obj))
  elif hasattr(obj,
               "__module__") and obj.__module__ == "matplotlib.figure" and type(obj).__name__ == "Figure":
    return _uploadImage(*_getPltBytes(obj))
  elif hasattr(obj, "__module__") and obj.__module__.startswith("PIL."):
    return _uploadImage(*_getPilImgBytes(obj))
  else:
    raise UserFacingError(
        f"Image logging requires a matplotlib Figure, PIL Image, or file path to an image file, but a '{type(obj)}' was provided."
    )


def _getImageBytes(filePath: str, nameOverride: Optional[str] = None) -> Tuple[bytes, str]:
  with open(filePath, "rb") as inFile:
    imgData = inFile.read()
    dataHash = hashlib.sha1(imgData).hexdigest()
    remotePath = "/".join([dataHash, os.path.basename(nameOverride or filePath)])
    return (imgData, remotePath)


def _getPltBytes(plt: ModuleType) -> Tuple[bytes, str]:
  with tempfile.NamedTemporaryFile(suffix=".jpg") as tf:
    plt.savefig(tf.name, bbox_inches='tight')  # type:ignore
    return _getImageBytes(tf.name, "image.jpg")


def _getPilImgBytes(img: Any) -> Tuple[bytes, str]:
  with tempfile.NamedTemporaryFile(suffix=".jpg") as tf:
    img.convert('RGB').save(tf.name)
    return _getImageBytes(tf.name, "image.jpg")


@retry(2, logger)
def _uploadImage(data: bytes, remotePath: str) -> str:
  if not inDeployment():
    print(f"log_image: Success! The image will be logged when run in a deployment.")
    return remotePath

  iv = get_random_bytes(16)
  key = get_random_bytes(32)
  cipher = AES.new(mode=AES.MODE_CBC, key=key, iv=iv)  # type: ignore
  fileKeyCipher = AES.new(mode=AES.MODE_ECB, key=base64.b64decode(deploymentPystateKeys()[0]))  # type: ignore
  encFileKey = fileKeyCipher.encrypt(pad(key, AES.block_size))
  body = cipher.encrypt(pad(zstandard.compress(data, 10), AES.block_size))

  boto3Client("s3").put_object(  # type: ignore
      Bucket=deploymentPystateBucket(),
      Key="/".join([deploymentWorkspaceId(), "inference_outputs", remotePath]),
      Metadata={
          "x-amz-key": base64.b64encode(encFileKey).decode(),
          "x-amz-iv": base64.b64encode(iv).decode()
      },
      Tagging="lifecycle=logs",
      Body=body)
  print(f'![mb:image]({remotePath})')  # for parsing out of stdout later
  return remotePath
