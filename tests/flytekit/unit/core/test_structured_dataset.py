import typing

import pytest

from flytekit.core import context_manager
from flytekit.core.context_manager import FlyteContext, FlyteContextManager, Image, ImageConfig
from flytekit.core.type_engine import TypeEngine
from flytekit.models import literals
from flytekit.models.types import SimpleType, StructuredDatasetType

try:
    from typing import Annotated, TypeAlias
except ImportError:
    from typing_extensions import Annotated, TypeAlias

import pandas as pd
import pyarrow as pa

from flytekit import kwtypes
from flytekit.types.structured.structured_dataset import (
    FLYTE_DATASET_TRANSFORMER,
    StructuredDataset,
    StructuredDatasetEncoder,
    protocol_prefix,
)

my_cols = kwtypes(w=typing.Dict[str, typing.Dict[str, int]], x=typing.List[typing.List[int]], y=int, z=str)

fields = [("some_int", pa.int32()), ("some_string", pa.string())]
arrow_schema = pa.schema(fields)

serialization_settings = context_manager.SerializationSettings(
    project="proj",
    domain="dom",
    version="123",
    image_config=ImageConfig(Image(name="name", fqn="asdf/fdsa", tag="123")),
    env={},
)


def test_protocol():
    assert protocol_prefix("s3://my-s3-bucket/file") == "s3"
    assert protocol_prefix("/file") == "/"


def generate_pandas() -> pd.DataFrame:
    return pd.DataFrame({"Name": ["Tom", "Joseph"], "Age": [20, 22]})


def test_types_pandas():
    pt = pd.DataFrame
    lt = TypeEngine.to_literal_type(pt)
    assert lt.structured_dataset_type is not None
    assert lt.structured_dataset_type.format == "parquet"
    assert lt.structured_dataset_type.columns == []


def test_types_annotated():
    pt = Annotated[pd.DataFrame, my_cols]
    lt = TypeEngine.to_literal_type(pt)
    assert len(lt.structured_dataset_type.columns) == 4
    assert lt.structured_dataset_type.columns[0].literal_type.map_value_type.map_value_type.simple == SimpleType.INTEGER
    assert (
        lt.structured_dataset_type.columns[1].literal_type.collection_type.collection_type.simple == SimpleType.INTEGER
    )
    assert lt.structured_dataset_type.columns[2].literal_type.simple == SimpleType.INTEGER
    assert lt.structured_dataset_type.columns[3].literal_type.simple == SimpleType.STRING

    pt = Annotated[pd.DataFrame, arrow_schema]
    lt = TypeEngine.to_literal_type(pt)
    assert lt.structured_dataset_type.external_schema_type == "arrow"
    assert "some_string" in str(lt.structured_dataset_type.external_schema_bytes)


def test_types_sd():
    pt = StructuredDataset
    lt = TypeEngine.to_literal_type(pt)
    assert lt.structured_dataset_type is not None

    pt = StructuredDataset[my_cols]
    lt = TypeEngine.to_literal_type(pt)
    assert len(lt.structured_dataset_type.columns) == 4

    pt = StructuredDataset[my_cols, "csv"]
    lt = TypeEngine.to_literal_type(pt)
    assert len(lt.structured_dataset_type.columns) == 4
    assert lt.structured_dataset_type.format == "csv"

    pt = StructuredDataset[{}, "csv"]
    assert pt.FILE_FORMAT == "csv"
    lt = TypeEngine.to_literal_type(pt)
    assert len(lt.structured_dataset_type.columns) == 0
    assert lt.structured_dataset_type.format == "csv"


def test_retrieving():
    assert FLYTE_DATASET_TRANSFORMER.get_encoder(pd.DataFrame, "/", "parquet") is not None
    with pytest.raises(ValueError):
        # We don't have a default "" format encoder
        FLYTE_DATASET_TRANSFORMER.get_encoder(pd.DataFrame, "/", "")

    class TempEncoder(StructuredDatasetEncoder):
        def __init__(self, protocol):
            super().__init__(MyDF, protocol)

        def encode(self):
            ...

    FLYTE_DATASET_TRANSFORMER.register_handler(TempEncoder("gs"), default_for_type=False)
    with pytest.raises(ValueError):
        FLYTE_DATASET_TRANSFORMER.register_handler(TempEncoder("gs://"), default_for_type=False)


def test_to_literal():
    ctx = FlyteContextManager.current_context()
    lt = TypeEngine.to_literal_type(pd.DataFrame)
    df = generate_pandas()

    lit = FLYTE_DATASET_TRANSFORMER.to_literal(ctx, df, python_type=pd.DataFrame, expected=lt)
    assert lit.scalar.structured_dataset.metadata.structured_dataset_type.format == "parquet"
    assert lit.scalar.structured_dataset.metadata.structured_dataset_type.format == "parquet"

    sd_with_literal_and_df = StructuredDataset(df)
    sd_with_literal_and_df._literal_sd = lit

    with pytest.raises(ValueError, match="Shouldn't have specified both literal"):
        FLYTE_DATASET_TRANSFORMER.to_literal(ctx, sd_with_literal_and_df, python_type=StructuredDataset, expected=lt)

    sd_with_nothing = StructuredDataset()
    with pytest.raises(ValueError, match="If dataframe is not specified"):
        FLYTE_DATASET_TRANSFORMER.to_literal(ctx, sd_with_nothing, python_type=StructuredDataset, expected=lt)

    sd_with_uri = StructuredDataset(uri="s3://some/extant/df.parquet")

    lt = TypeEngine.to_literal_type(StructuredDataset[{}, "new-df-format"])
    lit = FLYTE_DATASET_TRANSFORMER.to_literal(ctx, sd_with_uri, python_type=StructuredDataset, expected=lt)
    assert lit.scalar.structured_dataset.uri == "s3://some/extant/df.parquet"
    assert lit.scalar.structured_dataset.metadata.structured_dataset_type.format == "new-df-format"


class MyDF(pd.DataFrame):
    ...


def test_fill_in_literal_type():
    class TempEncoder(StructuredDatasetEncoder):
        def __init__(self, fmt: str):
            super().__init__(MyDF, "tmp://", supported_format=fmt)

        def encode(
            self,
            ctx: FlyteContext,
            structured_dataset: StructuredDataset,
            structured_dataset_type: StructuredDatasetType,
        ) -> literals.StructuredDataset:
            return literals.StructuredDataset(uri="")

    FLYTE_DATASET_TRANSFORMER.register_handler(TempEncoder("myavro"), default_for_type=True)
    lt = TypeEngine.to_literal_type(MyDF)
    assert lt.structured_dataset_type.format == "myavro"

    ctx = FlyteContextManager.current_context()
    sd = StructuredDataset(dataframe=42)
    l = FLYTE_DATASET_TRANSFORMER.to_literal(ctx, sd, MyDF, lt)
    # Test that the literal type is filled in even though the encode function above doesn't do it.
    assert l.scalar.structured_dataset.metadata.structured_dataset_type.format == "myavro"

    # Test that looking up encoders/decoders falls back to the "" encoder/decoder
    empty_format_temp_encoder = TempEncoder("")
    FLYTE_DATASET_TRANSFORMER.register_handler(empty_format_temp_encoder, default_for_type=False)

    res = FLYTE_DATASET_TRANSFORMER.get_encoder(MyDF, "tmp", "rando")
    assert res is empty_format_temp_encoder


def test_sd():
    sd = StructuredDataset(dataframe="hi")
    sd.uri = "my uri"
    assert sd.file_format == "parquet"

    with pytest.raises(ValueError):
        sd.all()

    with pytest.raises(ValueError):
        sd.iter()