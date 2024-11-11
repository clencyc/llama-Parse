from typing import Any, cast
import inspect
import warnings
from .utils import tempFilePath


# Keras doesn't pickle well on Windows/Mac. This wrapper saves/loads it from a file format that does work cross-OS
class KerasWrapper:
  saveFormat = "h5"  # default, maybe "keras" will work soon. That might also remove the need for this custom code

  def __init__(self, kerasModel: Any):
    self.saveFormat = KerasWrapper.saveFormat
    self.data = KerasWrapper.getKerasBytes(kerasModel, self.saveFormat)

  @classmethod
  def isKerasModel(cls, obj: Any) -> bool:
    return (hasattr(obj, "__module__") and obj.__module__.startswith("keras.") and hasattr(obj, "save") and
            inspect.isroutine(obj.save))

  @classmethod
  def getKerasBytes(cls, obj: Any, saveFormat: str) -> bytes:
    with tempFilePath(suffix=f".{saveFormat}") as tfName:
      with warnings.catch_warnings():
        warnings.simplefilter("ignore")  # Keras warns about using H5
        obj.save(tfName, overwrite=True, save_format=saveFormat)
      with open(tfName, "rb") as f:
        data = f.read()
    return data

  def getModel(self) -> Any:
    from tensorflow import keras  # type: ignore
    with tempFilePath(suffix=f".{self.saveFormat}") as tfName:
      with open(tfName, "wb") as f:
        f.write(self.data)
      kerasModel = cast(Any, keras.models.load_model(tfName))  # type: ignore
    return kerasModel
