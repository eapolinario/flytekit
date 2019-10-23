from __future__ import absolute_import

import flytekit.common.interface as _interface
from flytekit.models.interface import Variable
import os as _os
from flytekit.common.tasks import output as _task_output
import json
import papermill as pm
from flytekit import __version__
import importlib as _importlib

import datetime as _datetime
from pyspark import SparkConf, SparkContext
import sys as _sys
import six as _six
from flytekit.bin import spark_executor
from flytekit.common import constants as _constants
from flytekit.common.exceptions import scopes as _exception_scopes
from flytekit.common.tasks import sdk_runnable as _sdk_runnable
from flytekit.common.tasks import spark_task as _spark_task

from flytekit.common.types import helpers as _type_helpers
from flytekit.models import literals as _literal_models, task as _task_models
from google.protobuf.json_format import MessageToDict as _MessageToDict
from flytekit.sdk.types import Types

from flytekit.common.types.helpers import pack_python_std_map_to_literal_map as _packer
from flytekit.common.types import primitives as _p
from google.protobuf import text_format
from flytekit.common import sdk_bases
from flytekit.common.tasks import task as base_tasks
from flytekit.engines import loader as _engine_loader

type_map = {
    int: _p.Integer,
    bool: _p.Boolean,
    float: _p.Float,
    str: _p.String,
    _datetime.datetime: _p.Datetime,
    _datetime.timedelta: _p.Timedelta,
}

OUTPUT_NOTEBOOK = 'output_notebook'

def convert_outputs(outputs=None):
    """
    outputs: dict
    Records the output
    """
    if outputs is None:
        return _packer({}, {})
    tm = {}
    for k, v in _six.iteritems(outputs):
        t = type(v)
        if t not in type_map:
            raise ValueError(
                "Currently only primitive types {} are supported for recording from notebook".format(type_map))
        tm[k] = type_map[t]
    return _packer(outputs, tm)


def record_outputs(outputs=None):
    p = convert_outputs(outputs)
    return p.to_flyte_idl()

# TODO: Support Client Mode
def get_spark_context(spark_conf):
    # We run in cluster-mode in Flyte.
    # Ref https://github.com/lyft/flyteplugins/blob/master/go/tasks/v1/flytek8s/k8s_resource_adds.go#L46
    if "FLYTE_INTERNAL_EXECUTION_ID" in _os.environ:
        return SparkContext()

    # Add system spark-conf for local/notebook based execution.
    spark_conf.add(("spark.master", "local"))
    conf = SparkConf().setAll(spark_conf)
    return SparkContext(conf=conf)

def python_notebook(
        notebook_path='',
        inputs=None,
        outputs=None,
        cache_version='',
        retries=0,
        deprecated='',
        storage_request=None,
        cpu_request=None,
        gpu_request=None,
        memory_request=None,
        storage_limit=None,
        cpu_limit=None,
        gpu_limit=None,
        memory_limit=None,
        cache=False,
        timeout=None,
        environment=None,
        cls=None,

):
    """
    Decorator to create a Python Notebook Task definition.  This task will run as a single unit of work on the platform.

    :rtype: flytekit.common.tasks.sdk_runnable.SdkNotebookTask
    """
    return SdkNotebookTask(
            notebook_path=notebook_path,
            inputs=inputs,
            outputs=outputs,
            task_type=_constants.SdkTaskType.PYTHON_TASK,
            discovery_version=cache_version,
            retries=retries,
            deprecated=deprecated,
            storage_request=storage_request,
            cpu_request=cpu_request,
            gpu_request=gpu_request,
            memory_request=memory_request,
            storage_limit=storage_limit,
            cpu_limit=cpu_limit,
            gpu_limit=gpu_limit,
            memory_limit=memory_limit,
            discoverable=cache,
            timeout=timeout or _datetime.timedelta(seconds=0),
            environment=environment,
            custom={})


class SdkNotebookTask(
        _six.with_metaclass(sdk_bases.ExtendedSdkType, base_tasks.SdkTask)):

    """
    This class includes the additional logic for building a task that executes Notebooks.

    """

    def __init__(
            self,
            notebook_path,
            inputs,
            outputs,
            task_type,
            discovery_version,
            retries,
            deprecated,
            storage_request,
            cpu_request,
            gpu_request,
            memory_request,
            storage_limit,
            cpu_limit,
            gpu_limit,
            memory_limit,
            discoverable,
            timeout,
            environment,
            custom
    ):

        # Add output_notebook as an implicit output to the task.
        outputs[OUTPUT_NOTEBOOK] = Types.Blob
        input_variables = {k: Variable(v.to_flyte_literal_type(), k) for k, v in _six.iteritems(inputs)}
        output_variables = {k: Variable(v.to_flyte_literal_type(), k) for k, v in _six.iteritems(outputs)}

        self._notebook_path = notebook_path
        super(SdkNotebookTask, self).__init__(
            task_type,
            _task_models.TaskMetadata(
                discoverable,
                _task_models.RuntimeMetadata(
                    _task_models.RuntimeMetadata.RuntimeType.FLYTE_SDK,
                    __version__,
                    'python'
                ),
                timeout,
                _literal_models.RetryStrategy(retries),
                discovery_version,
                deprecated
            ),
            _interface.TypedInterface(input_variables, output_variables),
            custom,
            container=self._get_container_definition(
                storage_request=storage_request,
                cpu_request=cpu_request,
                gpu_request=gpu_request,
                memory_request=memory_request,
                storage_limit=storage_limit,
                cpu_limit=cpu_limit,
                gpu_limit=gpu_limit,
                memory_limit=memory_limit,
                environment=environment
            )
        )

    @_exception_scopes.system_entry_point
    def unit_test(self, **input_map):
        """
        :param dict[Text, T] input_map: Python Std input from users.  We will cast these to the appropriate Flyte
            literals.
        :returns: Depends on the behavior of the specific task in the unit engine.
        """
        return _engine_loader.get_engine('unit').get_task(self).execute(
            _type_helpers.pack_python_std_map_to_literal_map(input_map, {
                k: _type_helpers.get_sdk_type_from_literal_type(v.type)
                for k, v in _six.iteritems(self.interface.inputs)
            })
        )

    @_exception_scopes.system_entry_point
    def local_execute(self, **input_map):
        """
        :param dict[Text, T] input_map: Python Std input from users.  We will cast these to the appropriate Flyte
            literals.
        :rtype: dict[Text, T]
        :returns: The output produced by this task in Python standard format.
        """
        return _engine_loader.get_engine('local').get_task(self).execute(
            _type_helpers.pack_python_std_map_to_literal_map(input_map, {
                k: _type_helpers.get_sdk_type_from_literal_type(v.type)
                for k, v in _six.iteritems(self.interface.inputs)
            })
        )

    @_exception_scopes.system_entry_point
    def execute(self, context, inputs):
        """
        :param flytekit.engines.common.EngineContext context:
        :param flytekit.models.literals.LiteralMap inputs:
        :rtype: dict[Text, flytekit.models.common.FlyteIdlEntity]
        :returns: This function must return a dictionary mapping 'filenames' to Flyte Interface Entities.  These
            entities will be used by the engine to pass data from node to node, populate metadata, etc. etc..  Each
            engine will have different behavior.  For instance, the Flyte engine will upload the entities to a remote
            working directory (with the names provided), which will in turn allow Flyte Propeller to push along the
            workflow.  Where as local engine will merely feed the outputs directly into the next node.
        """
        inputs_dict = _type_helpers.unpack_literal_map_to_sdk_python_std(inputs, {
            k: _type_helpers.get_sdk_type_from_literal_type(v.type) for k, v in _six.iteritems(self.interface.inputs)
        })

        # Execute Notebook via Papermill.
        input_notebook_path = self._notebook_path
        output_notebook_path = input_notebook_path[:len(input_notebook_path) - 6] + '-out' + input_notebook_path[len(
            input_notebook_path) - 6:]
        pm.execute_notebook(
            input_notebook_path,
            output_notebook_path,
            parameters=inputs_dict
        )

        # Parse Outputs from Notebook.
        outputs = None
        with open(output_notebook_path) as json_file:
            data = json.load(json_file)
            for p in data['cells']:
                meta = p['metadata']
                if "outputs" in meta["tags"]:
                    outputs = ' '.join(p['outputs'][0]['data']['text/plain'])

        if outputs is not None:
            dict = _literal_models._literals_pb2.LiteralMap()
            text_format.Parse(outputs, dict)

        # Add output_notebook as an output to the task.
        output_notebook = _task_output.OutputReference(
            _type_helpers.get_sdk_type_from_literal_type(Types.Blob.to_flyte_literal_type()))
        output_notebook.set(output_notebook_path)

        output_literal_map = _literal_models.LiteralMap.from_flyte_idl(dict)
        output_literal_map.literals[OUTPUT_NOTEBOOK] = output_notebook.sdk_value

        return {
            _constants.OUTPUT_FILE_NAME: output_literal_map
        }

    @property
    def container(self):
        """
        If not None, the target of execution should be a container.
        :rtype: Container
        """

        # Find task_name
        task_module = _importlib.import_module(self.instantiated_in)
        for k in dir(task_module):
            if getattr(task_module, k) is self:
                task_name = k
                break

        self._container._args = [
            "pyflyte-execute",
            "--task-module",
            self.instantiated_in,
            "--task-name",
            task_name,
            "--inputs",
            "{{.input}}",
            "--output-prefix",
            "{{.outputPrefix}}"]
        return self._container

    def _get_container_definition(
            self,
            storage_request=None,
            cpu_request=None,
            gpu_request=None,
            memory_request=None,
            storage_limit=None,
            cpu_limit=None,
            gpu_limit=None,
            memory_limit=None,
            environment=None,
            **kwargs
    ):
        """
        :param Text storage_request:
        :param Text cpu_request:
        :param Text gpu_request:
        :param Text memory_request:
        :param Text storage_limit:
        :param Text cpu_limit:
        :param Text gpu_limit:
        :param Text memory_limit:
        :param dict[Text, Text] environment:
        :rtype: flytekit.models.task.Container
        """

        storage_limit = storage_limit or storage_request
        cpu_limit = cpu_limit or cpu_request
        gpu_limit = gpu_limit or gpu_request
        memory_limit = memory_limit or memory_request

        requests = []
        if storage_request:
            requests.append(
                _task_models.Resources.ResourceEntry(
                    _task_models.Resources.ResourceName.STORAGE,
                    storage_request
                )
            )
        if cpu_request:
            requests.append(
                _task_models.Resources.ResourceEntry(
                    _task_models.Resources.ResourceName.CPU,
                    cpu_request
                )
            )
        if gpu_request:
            requests.append(
                _task_models.Resources.ResourceEntry(
                    _task_models.Resources.ResourceName.GPU,
                    gpu_request
                )
            )
        if memory_request:
            requests.append(
                _task_models.Resources.ResourceEntry(
                    _task_models.Resources.ResourceName.MEMORY,
                    memory_request
                )
            )

        limits = []
        if storage_limit:
            limits.append(
                _task_models.Resources.ResourceEntry(
                    _task_models.Resources.ResourceName.STORAGE,
                    storage_limit
                )
            )
        if cpu_limit:
            limits.append(
                _task_models.Resources.ResourceEntry(
                    _task_models.Resources.ResourceName.CPU,
                    cpu_limit
                )
            )
        if gpu_limit:
            limits.append(
                _task_models.Resources.ResourceEntry(
                    _task_models.Resources.ResourceName.GPU,
                    gpu_limit
                )
            )
        if memory_limit:
            limits.append(
                _task_models.Resources.ResourceEntry(
                    _task_models.Resources.ResourceName.MEMORY,
                    memory_limit
                )
            )

        return  _sdk_runnable.SdkRunnableContainer(
            command=[],
            args=[],
            resources=_task_models.Resources(limits=limits, requests=requests),
            env=environment,
            config={}
        )




def spark_notebook(
        cache_version='',
        retries=0,
        deprecated='',
        cache=False,
        timeout=None,
        notebook_path=None,
        inputs=None,
        outputs=None,
        environment=None,
):
    """
    Decorator to create a Notebook spark task. This task will connect to a Spark cluster, configure the environment,
    and then execute the code within the notebook_path as the Spark driver program.
    """
    return SdkNotebookSparkTask(
            discovery_version=cache_version,
            retries=retries,
            deprecated=deprecated,
            discoverable=cache,
            timeout=timeout or _datetime.timedelta(seconds=0),
            notebook_path=notebook_path,
            inputs=inputs,
            outputs=outputs,
            environment=environment or {},
        )


class SdkNotebookSparkTask(SdkNotebookTask):

    """
    This class includes the additional logic for building a task that executes Spark Notebooks.

    """

    def __init__(
            self,
            notebook_path,
            discovery_version='',
            retries=0,
            deprecated='',
            discoverable=False,
            timeout=_datetime.timedelta(seconds=0),
            inputs=None,
            outputs=None,
            environment=None,
    ):

        spark_exec_path = _os.path.abspath(spark_executor.__file__)
        if spark_exec_path.endswith('.pyc'):
            spark_exec_path = spark_exec_path[:-1]
        self._notebook_path = notebook_path

        # Parse Spark_conf from notebook
        with open(notebook_path) as json_file:
            data = json.load(json_file)
            for p in data['cells']:
                meta = p['metadata']
                if "tags" in meta:
                    if "conf" in meta["tags"]:
                        sc_str = ' '.join(p["source"])
                        ldict = {}
                        exec (sc_str, globals(), ldict)
                        spark_conf = ldict['spark_conf']

            spark_job = _task_models.SparkJob(
                spark_conf=spark_conf,
                hadoop_conf={},
                application_file="local://" + spark_exec_path,
                executor_path=_sys.executable,
            ).to_flyte_idl()

        # Add output_notebook as an implicit output to the task.
        outputs[OUTPUT_NOTEBOOK] = Types.Blob

        input_variables = {k: Variable(v.to_flyte_literal_type(), k) for k, v in _six.iteritems(inputs)}
        output_variables = {k: Variable(v.to_flyte_literal_type(), k) for k, v in _six.iteritems(outputs)}

        super(SdkNotebookSparkTask, self).__init__(
            notebook_path,
            inputs,
            outputs,
            _constants.SdkTaskType.SPARK_TASK,
            discovery_version,
            retries,
            deprecated,
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            discoverable,
            timeout,
            environment,
            _MessageToDict(spark_job),
        )

    @property
    def container(self):
        """
        If not None, the target of execution should be a container.
        :rtype: Container
        """

        # Find task_name
        task_module = _importlib.import_module(self.instantiated_in)
        for k in dir(task_module):
            if getattr(task_module, k) is self:
                task_name = k
                break

        self._container._args = [
                "execute_spark_task",
                "--task-module",
                self.instantiated_in,
                "--task-name",
                task_name,
                "--inputs",
                "{{.input}}",
                "--output-prefix",
                "{{.outputPrefix}}"]
        return self._container

    def _get_container_definition(
            self,
            environment=None,
            **kwargs
    ):
        """
        :rtype: flytekit.models.task.Container
        """

        return _spark_task.SdkRunnableSparkContainer(
            command=[],
            args=[],
            resources=_task_models.Resources(limits=[], requests=[]),
            env=environment or {},
            config={}
        )

