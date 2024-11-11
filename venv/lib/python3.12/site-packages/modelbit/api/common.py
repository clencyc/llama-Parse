from typing import Any, Dict, Optional


class OwnerInfo:

  def __init__(self, data: Dict[str, Any]):
    self.id: Optional[str] = data.get("id", None)
    self.name: Optional[str] = data.get("name", None)
    self.imageUrl: Optional[str] = data.get("imageUrl", None)
