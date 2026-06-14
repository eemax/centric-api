from __future__ import annotations

import pickle
from pathlib import Path

from centric_api.view_export import (
    MissingJoinDetail,
    ViewCheckResult,
    ViewExportResult,
    ViewMaterialized,
)


def test_view_export_result_types_keep_public_facade_identity() -> None:
    assert MissingJoinDetail.__module__ == "centric_api.view_export"
    assert ViewMaterialized.__module__ == "centric_api.view_export"
    assert ViewExportResult.__module__ == "centric_api.view_export"
    assert ViewCheckResult.__module__ == "centric_api.view_export"


def test_view_export_result_types_pickle_through_public_facade() -> None:
    detail = MissingJoinDetail(
        alias="style",
        source_type="endpoint",
        source_name="styles",
        from_path="bom_line.style",
        to_path="id",
        missing_count=1,
        missing_source_count=0,
        missing_ref_count=1,
        filtered_out_count=0,
        missing_endpoint=False,
        filters_applied=False,
        sample_keys=("S1",),
    )
    values = (
        detail,
        ViewMaterialized(
            root_row_count=1,
            headers=("Style",),
            columns=(),
            rows=(("Linen Shirt",),),
            missing_join_count=1,
            missing_join_details=(detail,),
            warnings=("warning",),
        ),
        ViewExportResult(
            view_name="styles",
            title="Styles",
            format="csv",
            output_path=Path("styles.csv"),
            row_count=1,
            column_count=1,
            missing_join_count=1,
            missing_join_details=(detail,),
            warnings=("warning",),
        ),
        ViewCheckResult(
            view_name="styles",
            title="Styles",
            root_row_count=1,
            row_count=1,
            column_count=1,
            missing_join_count=1,
            missing_join_details=(detail,),
            warnings=("warning",),
        ),
    )

    for value in values:
        restored = pickle.loads(pickle.dumps(value))

        assert type(restored) is type(value)
        assert restored == value
