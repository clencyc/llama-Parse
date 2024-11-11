class ModelbitError(Exception):
  pass


class UserFacingError(ModelbitError):
  userFacingErrorMessage: str
  logToModelbit: bool

  def __init__(self, message: str, logToModelbit: bool = True) -> None:
    self.userFacingErrorMessage = message
    self.logToModelbit = logToModelbit
    super().__init__(message)


class NonRetryableError(UserFacingError):
  retryable = False

  def __init__(self, message: str) -> None:
    super().__init__(message)


class RateLimitError(NonRetryableError):

  def __init__(self, limitName: str, message: str) -> None:
    self.limitName = limitName
    super().__init__(message)
