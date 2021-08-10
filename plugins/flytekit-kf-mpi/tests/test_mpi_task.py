from flytekitplugins.kfmpi.task import PyTorch

from flytekit import Resources, task
from flytekit.extend import Image, ImageConfig, SerializationSettings


def test_mpi_task():
    @task(task_config=MPIJob(num_workers=10, num_launcher_replicas=10, slots=1, per_replica_requests=Resources(cpu="1")), cache=True, cache_version="1")
    def my_mpi_task(x: int, y: str) -> int:
        return x

    assert my_mpi_task(x=10, y="hello") == 10

    assert my_mpi_task.task_config is not None

    default_img = Image(name="default", fqn="test", tag="tag")
    settings = SerializationSettings(
        project="project",
        domain="domain",
        version="version",
        env={"FOO": "baz"},
        image_config=ImageConfig(default_image=default_img, images=[default_img]),
    )

    assert my_mpi_task.get_custom(settings) == {"workers": 10}
    assert my_mpi_task.resources.limits == Resources()
    assert my_mpi_task.resources.requests == Resources(cpu="1")
    assert my_mpi_task.task_type == "mpi"
