from typing import Any, List, Optional, TYPE_CHECKING, Union, Dict

from modelbit.api import MbApi
from modelbit.runtime import Deployment
from modelbit.utils import getFuncName, convertLambdaToDef

if TYPE_CHECKING:
  import pandas
  import modelbit.jobs as m_jobs


class LambdaWrapper:

  def __init__(self,
               lambdaFunc: Any,
               name: Optional[str] = None,
               python_version: Optional[str] = None,
               python_packages: Optional[List[str]] = None,
               system_packages: Optional[List[str]] = None,
               dataframe_mode: bool = False,
               example_dataframe: Optional['pandas.DataFrame'] = None,
               common_files: Union[List[str], Dict[str, str], None] = None,
               extra_files: Union[List[str], Dict[str, str], None] = None,
               skip_extra_files_dependencies: bool = False,
               skip_extra_files_discovery: bool = False,
               snowflake_max_rows: Optional[int] = None,
               snowflake_mock_return_value: Optional[Any] = None,
               require_gpu: Union[bool, str] = False,
               setup: Optional[Union[str, List[str]]] = None):

    self.lambdaFunc = lambdaFunc
    self.python_version = python_version
    self.python_packages = python_packages
    self.system_packages = system_packages
    self.dataframe_mode = dataframe_mode
    self.example_dataframe = example_dataframe
    self.common_files = common_files
    self.extra_files = extra_files
    self.skip_extra_files_dependencies = skip_extra_files_dependencies
    self.skip_extra_files_discovery = skip_extra_files_discovery
    self.snowflake_max_rows = snowflake_max_rows
    self.snowflake_mock_return_value = snowflake_mock_return_value
    self.require_gpu = require_gpu
    self.name = name if name is not None else getFuncName(self.lambdaFunc, "predict")
    self.setup = setup

  def makeDeployment(self, api: MbApi) -> Deployment:
    deployFunction, funcSource = convertLambdaToDef(self.lambdaFunc, self.name)

    return Deployment(api=api,
                      deploy_function=deployFunction,
                      source_override=funcSource,
                      python_version=self.python_version,
                      python_packages=self.python_packages,
                      system_packages=self.system_packages,
                      name=self.name,
                      dataframe_mode=self.dataframe_mode,
                      example_dataframe=self.example_dataframe,
                      common_files=self.common_files,
                      extra_files=self.extra_files,
                      skip_extra_files_dependencies=self.skip_extra_files_dependencies,
                      skip_extra_files_discovery=self.skip_extra_files_discovery,
                      snowflake_max_rows=self.snowflake_max_rows,
                      snowflake_mock_return_value=self.snowflake_mock_return_value,
                      require_gpu=self.require_gpu,
                      setup=self.setup)


class RuntimeJobWrapper:

  def __init__(
      self,
      job: 'm_jobs.ModelbitJobWrapper',
      name: Optional[str] = None,
      python_version: Optional[str] = None,
      python_packages: Optional[List[str]] = None,
      system_packages: Optional[List[str]] = None,
      extra_files: Union[str, List[str], Dict[str, str], None] = None,
      skip_extra_files_dependencies: bool = False,
      skip_extra_files_discovery: bool = False,
  ):
    self.job = job
    self.python_version = python_version
    self.python_packages = python_packages
    self.system_packages = system_packages
    self.extra_files = extra_files
    self.skip_extra_files_dependencies = skip_extra_files_dependencies
    self.skip_extra_files_discovery = skip_extra_files_discovery
    self.name = name

  def makeDeployment(self, api: MbApi) -> Deployment:
    return Deployment(api=api,
                      deploy_function=self.job.func if self.job.standalone else None,
                      job=self.job,
                      python_version=self.python_version,
                      python_packages=self.python_packages,
                      system_packages=self.system_packages,
                      name=self.job.desc.jobName if self.job.standalone else self.name,
                      extra_files=self.extra_files,
                      skip_extra_files_dependencies=self.skip_extra_files_dependencies,
                      skip_extra_files_discovery=self.skip_extra_files_discovery)
