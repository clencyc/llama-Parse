import time
from typing import Any, Callable, Dict, List, Optional, Union, cast

from modelbit.api import JobApi, JobDesc, JobRunDesc, MbApi
from modelbit.error import UserFacingError
from modelbit.model_wrappers import RuntimeJobWrapper
from modelbit.utils import dumpJson
from modelbit.ux import printTemplate, renderTemplate


class ModelbitJobWrapper:
  desc: JobDesc

  def __init__(self, func: Callable[..., Any], desc: Union[JobDesc, Dict[str, Any]]):
    self.func = func
    self.desc = desc if type(desc) is JobDesc else JobDesc(cast(Dict[str, Any], desc))
    self.standalone = True

  def _repr_html_(self) -> str:
    return self.__repr__()

  def __repr__(self) -> str:
    return renderTemplate("job", jobName=self.desc.jobName)


def add_job(
    api: MbApi,
    func: Callable[..., Any],
    name: Optional[str] = None,
    python_version: Optional[str] = None,
    python_packages: Optional[List[str]] = None,
    system_packages: Optional[List[str]] = None,
    extra_files: Union[str, List[str], Dict[str, str], None] = None,
    email_on_failure: Optional[str] = None,
    skip_extra_files_dependencies: bool = False,
    skip_extra_files_discovery: bool = False,
    schedule: Optional[str] = None,
    refresh_datasets: Optional[List[str]] = None,
    size: Optional[str] = None,
    timeout_minutes: Optional[int] = None,
    default_arguments: Optional[List[Any]] = None,
) -> ModelbitJobWrapper:
  if not callable(func):
    raise UserFacingError("First argument must be a function")

  assertEmailOfFailureParam(email_on_failure)

  if schedule is not None and type(schedule) is not str:
    raise UserFacingError("The schedule parameter must be a string")

  assertRefreshDatasetsParam(refresh_datasets)

  assertSizeParam(size)

  assertTimeoutMinutesParam(timeout_minutes)

  if default_arguments is not None:
    assertArgsAreSerializable(default_arguments)

  name = name or func.__name__
  jobDesc = dict(jobName=name,
                 schedule=schedule,
                 emailOnFailure=email_on_failure,
                 refreshDatasets=refresh_datasets,
                 timeoutMinutes=timeout_minutes,
                 size=size,
                 arguments=default_arguments)
  job = ModelbitJobWrapper(func, jobDesc)
  deployment = RuntimeJobWrapper(
      job,
      python_version=python_version,
      python_packages=python_packages,
      system_packages=system_packages,
      extra_files=extra_files,
      skip_extra_files_dependencies=skip_extra_files_dependencies,
      skip_extra_files_discovery=skip_extra_files_discovery,
  ).makeDeployment(api)

  deployment.deploy()
  return job


def run_job(
    api: MbApi,
    branch: str,
    job_name: str,
    args: Optional[List[Any]],
    email_on_failure: Optional[str] = None,
    refresh_datasets: Optional[List[str]] = None,
    size: Optional[str] = None,
    timeout_minutes: Optional[int] = None,
) -> 'ModelbitJobRun':

  assertEmailOfFailureParam(email_on_failure)

  assertRefreshDatasetsParam(refresh_datasets)

  assertSizeParam(size)

  assertTimeoutMinutesParam(timeout_minutes)

  if args is not None:
    assertArgsAreSerializable(args)
  jobRunDesc = JobApi(api).runJob(
      branch=branch,
      jobName=job_name,
      args=args,
      emailOnFailure=email_on_failure,
      refreshDatasets=refresh_datasets,
      size=size,
      timeoutMinutes=timeout_minutes,
  )
  printTemplate("running-job", None, jobName=job_name, jobOverviewUrl=jobRunDesc.jobOverviewUrl)
  return ModelbitJobRun(api, jobRunDesc)


def assertArgsAreSerializable(args: List[Any]) -> None:
  if not isinstance(args, (list, tuple)):  # type: ignore
    raise UserFacingError(
        f"Arguments must be a list of JSON serializable objects. It is a {type(args).__name__}.")
  try:
    dumpJson(args)
  except TypeError:
    raise UserFacingError("Arguments must be a list of JSON serializable objects")


def assertTimeoutMinutesParam(timeout_minutes: Optional[int]) -> None:
  if timeout_minutes is not None and (type(timeout_minutes) is not int or timeout_minutes <= 0):
    raise UserFacingError("The timeout_minutes parameter must be a positive integer")


def assertSizeParam(size: Optional[str]) -> None:
  if size is not None and size not in [
      "small", "medium", "large", "xlarge", "2xlarge", "4xlarge", "gpu_small", "gpu_medium", "gpu_large"
  ]:
    raise UserFacingError(
        'The size parameter must be one of "small", "medium", "large", "xlarge", "2xlarge", "4xlarge", "gpu_small", "gpu_medium", or "gpu_large"'
    )


def assertRefreshDatasetsParam(refresh_datasets: Optional[List[str]]) -> None:
  if refresh_datasets is not None and type(refresh_datasets) is not list:
    raise UserFacingError("The refresh_datasets parameter must be a list of strings")


def assertEmailOfFailureParam(email_on_failure: Optional[str]) -> None:
  if email_on_failure is not None and type(email_on_failure) is not str:
    raise UserFacingError("The email_on_failure parameter must be a string")


class ModelbitJobRun:
  _jobRunId: str
  _api: MbApi
  _desc: Optional[JobRunDesc]

  @property
  def job_name(self) -> Optional[str]:
    return self._desc.jobName if self._desc is not None else None

  @property
  def run_id(self) -> Optional[int]:
    return self._desc.userFacingId if self._desc is not None else None

  def __init__(self, mbApi: MbApi, jobRunId: Union[str, 'JobRunDesc']):
    self._api = mbApi
    if type(jobRunId) is str:
      self._jobRunId = jobRunId
      self._desc = None
    else:
      self._desc = cast(JobRunDesc, jobRunId)
      self._jobRunId = self._desc.id

  def __repr__(self) -> str:
    if self._desc is not None:
      return f"<ModelbitJobRun: run_id={self._desc.userFacingId}>"
    return f"<ModelbitJobRun>"

  def refresh(self) -> 'ModelbitJobRun':
    self._desc = JobApi(self._api).getJobRun(self._jobRunId)
    return self

  def wait(self, timeout_sec: Optional[int] = None, quiet: bool = True) -> None:
    deadline = time.time() + timeout_sec if timeout_sec is not None else None
    while deadline is None or time.time() < deadline:
      self.refresh()
      if not quiet:
        print(self._desc)
      if self._desc is None or self._desc.state == "failed":
        raise UserFacingError("Job failed.")
      elif self._desc.state == "finished":
        return
      sleepTime = min(20, max(0, deadline - time.time())) if deadline is not None else 20
      time.sleep(sleepTime)
    raise TimeoutError("Job still running")
