from __future__ import annotations

import json
from pathlib import Path

from centric_api.store import connect
from tests.helpers_load import _insert_record, _write_material_workbook


def _write_material_create_composition_workbook(path: Path) -> None:
    _write_material_workbook(
        path,
        headers=["Code", "Product Type", "Description", "Composition"],
        rows=[["MAT-001", "Fabric", "Test fabric", "100% Cotton"]],
    )


def _write_material_create_composition_quote_workbook(path: Path) -> None:
    _write_material_workbook(
        path,
        headers=[
            "Code",
            "Product Type",
            "Material Description",
            "Composition",
            "Supplier",
            "Agent",
            "Supplier Item",
            "Quote Description",
            "Quote Factory",
            "Set Default Quote",
        ],
        rows=[
            [
                "MAT-001",
                "Fabric",
                "Test fabric",
                "100% Cotton",
                "Primary Supplier",
                "Primary Agent",
                "Main Material Quote",
                "Primary material quote",
                "Primary Factory",
                "Yes",
            ],
        ],
    )


def _seed_material_create_composition_cache(db_path: Path) -> None:
    with connect(db_path) as conn:
        _insert_record(
            conn,
            endpoint="material_types",
            record_id="MT1",
            payload={"id": "MT1", "node_name": "Fabric", "available": True},
        )
        _insert_record(
            conn,
            endpoint="compositions",
            record_id="COTTON",
            payload={"id": "COTTON", "node_name": "Cotton", "ok_for_material": True},
        )


def _seed_material_create_composition_quote_cache(db_path: Path) -> None:
    _seed_material_create_composition_cache(db_path)
    _seed_style_supplier_quote_cache(db_path)


def _write_style_bom_workbook(path: Path) -> None:
    _write_material_workbook(
        path,
        headers=[
            "Season",
            "Style",
            "BOM Name",
            "Description",
            "Subtype",
            "Section",
            "PM ID",
            "Quantity",
            "Material Code",
        ],
        rows=[
            [
                "SS26",
                "ST-001",
                "Main BOM",
                "Main production BOM",
                "Production",
                "Fabrics",
                "G2",
                0.05,
                "MAT-001",
            ],
            [
                "SS26",
                "ST-001",
                "Main BOM",
                "Main production BOM",
                "Production",
                "Trims",
                "G3",
                2,
                "MAT-002",
            ],
        ],
    )


def _seed_style_bom_load_cache(
    db_path: Path,
    *,
    section_flags: dict[str, dict[str, object]] | None = None,
) -> None:
    section_flags = section_flags or {}
    with connect(db_path) as conn:
        _insert_record(
            conn,
            endpoint="seasons",
            record_id="SE1",
            payload={"id": "SE1", "node_name": "SS26"},
        )
        _insert_record(
            conn,
            endpoint="styles",
            record_id="S1",
            payload={"id": "S1", "node_name": "ST-001", "parent_season": "SE1"},
        )
        _insert_record(
            conn,
            endpoint="bom_subtypes",
            record_id="BST1",
            payload={"id": "BST1", "node_name": "Production"},
        )
        _insert_record(
            conn,
            endpoint="bom_sections",
            record_id="BS1",
            payload={
                "id": "BS1",
                "node_name": "Fabrics",
                "active": True,
                "ad_hoc": False,
                **section_flags.get("Fabrics", {}),
            },
        )
        _insert_record(
            conn,
            endpoint="bom_sections",
            record_id="BS2",
            payload={
                "id": "BS2",
                "node_name": "Trims",
                "active": True,
                "ad_hoc": False,
                **section_flags.get("Trims", {}),
            },
        )
        _insert_record(
            conn,
            endpoint="materials",
            record_id="M1",
            payload={"id": "M1", "code": "MAT-001"},
        )
        _insert_record(
            conn,
            endpoint="materials",
            record_id="M2",
            payload={"id": "M2", "code": "MAT-002"},
        )


def _write_style_supplier_quote_workbook(
    path: Path,
    *,
    agent: str = "Primary Agent",
    quote_factory: str = "Primary Factory",
) -> None:
    _write_material_workbook(
        path,
        headers=[
            "Season",
            "Style",
            "Supplier",
            "Agent",
            "Supplier Item",
            "Description",
            "Quote Factory",
            "Set Production Quote",
        ],
        rows=[
            [
                "SS26",
                "ST-001",
                "Primary Supplier",
                agent,
                "Main Quote",
                "Primary supplier quote",
                quote_factory,
                "Yes",
            ],
        ],
    )


def _write_material_supplier_quote_workbook(
    path: Path,
    *,
    agent: str = "Primary Agent",
    quote_factory: str = "Primary Factory",
) -> None:
    _write_material_workbook(
        path,
        headers=[
            "Material Code",
            "Supplier",
            "Agent",
            "Supplier Item",
            "Description",
            "Quote Factory",
            "Set Default Quote",
        ],
        rows=[
            [
                "MAT-001",
                "Primary Supplier",
                agent,
                "Main Material Quote",
                "Primary material quote",
                quote_factory,
                "Yes",
            ],
        ],
    )


def _seed_material_supplier_quote_cache(
    db_path: Path,
    *,
    supplier_agents: tuple[str, ...] = ("A1",),
    factory_suppliers: tuple[str, ...] = ("SUP1",),
    include_factory: bool = True,
) -> None:
    _seed_style_supplier_quote_cache(
        db_path,
        supplier_agents=supplier_agents,
        factory_suppliers=factory_suppliers,
        include_factory=include_factory,
    )
    with connect(db_path) as conn:
        _insert_record(
            conn,
            endpoint="materials",
            record_id="M1",
            payload={"id": "M1", "code": "MAT-001"},
        )


def _seed_style_supplier_quote_cache(
    db_path: Path,
    *,
    supplier_agents: tuple[str, ...] = ("A1",),
    factory_suppliers: tuple[str, ...] = ("SUP1",),
    include_factory: bool = True,
) -> None:
    with connect(db_path) as conn:
        _insert_record(
            conn,
            endpoint="seasons",
            record_id="SE1",
            payload={"id": "SE1", "node_name": "SS26"},
        )
        _insert_record(
            conn,
            endpoint="styles",
            record_id="S1",
            payload={"id": "S1", "node_name": "ST-001", "parent_season": "SE1"},
        )
        _insert_record(
            conn,
            endpoint="suppliers",
            record_id="SUP1",
            payload={
                "id": "SUP1",
                "node_name": "Primary Supplier",
                "supplier_number": "SUP-001",
                "is_supplier": True,
                "is_agent": False,
                "all_agents": {str(index): value for index, value in enumerate(supplier_agents)},
            },
        )
        _insert_record(
            conn,
            endpoint="suppliers",
            record_id="A1",
            payload={
                "id": "A1",
                "node_name": "Primary Agent",
                "supplier_number": "AG-001",
                "is_supplier": False,
                "is_agent": True,
            },
        )
        if include_factory:
            _insert_record(
                conn,
                endpoint="factories",
                record_id="F1",
                payload={
                    "id": "F1",
                    "node_name": "Primary Factory",
                    "supplier_number": "FAC-001",
                    "suppliers": {
                        str(index): value for index, value in enumerate(factory_suppliers)
                    },
                },
            )


class _StyleBomAuthContext:
    base_url = "https://example.test"

    def __init__(self, *, fail_lines: bool = False) -> None:
        self.fail_lines = fail_lines
        self.calls: list[tuple[str, str, object]] = []

    def request(self, method: str, url: str, *, json_body: object) -> object:
        self.calls.append((method, url, json_body))
        if url.endswith("/data_sheets/apparel_boms"):
            return _JsonResponse(
                201,
                {
                    "id": "BOM1",
                    "latest_revision": "REV1",
                    "current_revision": "REV1",
                },
            )
        if url.endswith("/owned_sections/bom_section_definition"):
            assert isinstance(json_body, dict)
            return _JsonResponse(201, {"id": f"SEC-{json_body['node_name']}"})
        if url.endswith("/items/part_materials"):
            if self.fail_lines:
                return _JsonResponse(422, {"message": "line rejected"})
            return _JsonResponse(201, {"id": "LINE"})
        return _JsonResponse(404, {"message": "unexpected url"})


class _JsonResponse:
    def __init__(self, status_code: int, payload: object) -> None:
        self.status_code = status_code
        self._payload = payload
        self.text = json.dumps(payload)

    def json(self) -> object:
        return self._payload


class _MaterialCreateCompositionAuthContext:
    base_url = "https://example.test"

    def __init__(self, *, fail_material: bool = False) -> None:
        self.fail_material = fail_material
        self.calls: list[tuple[str, str, object]] = []

    def request(self, method: str, url: str, *, json_body: object) -> object:
        self.calls.append((method, url, json_body))
        if url.endswith("/materials"):
            if self.fail_material:
                return _JsonResponse(422, {"message": "material rejected"})
            return _JsonResponse(201, {"id": "NEW-MAT"})
        if url.endswith("/technical_compositions"):
            return _JsonResponse(201, [{"id": "COMP1"}])
        return _JsonResponse(404, {"message": "unexpected url"})


class _MaterialCreateCompositionQuoteAuthContext:
    base_url = "https://example.test"

    def __init__(self, *, fail_composition: bool = False) -> None:
        self.fail_composition = fail_composition
        self.calls: list[tuple[str, str, object]] = []

    def request(self, method: str, url: str, *, json_body: object) -> object:
        self.calls.append((method, url, json_body))
        if method == "POST" and url.endswith("/materials"):
            return _JsonResponse(201, {"id": "NEW-MAT"})
        if url.endswith("/technical_compositions"):
            if self.fail_composition:
                return _JsonResponse(422, {"message": "composition rejected"})
            return _JsonResponse(201, [{"id": "COMP1"}])
        if url.endswith("/product_sources"):
            return _JsonResponse(201, {"id": "PS1"})
        if url.endswith("/supplier_items"):
            return _JsonResponse(
                201,
                {
                    "id": "SQ1",
                    "latest_revision": "REV1",
                    "current_revision": "REV1",
                },
            )
        if url.endswith("/supplier_item_revisions/REV1"):
            return _JsonResponse(200, {"id": "REV1", "quote_factory": "F1"})
        if method == "PUT" and url.endswith("/materials/NEW-MAT"):
            return _JsonResponse(200, {"id": "NEW-MAT", "default_quote": "SQ1"})
        return _JsonResponse(404, {"message": "unexpected url"})


class _StyleSupplierQuoteAuthContext:
    base_url = "https://example.test"

    def __init__(self, *, fail_product_source: bool = False) -> None:
        self.fail_product_source = fail_product_source
        self.calls: list[tuple[str, str, object]] = []

    def request(self, method: str, url: str, *, json_body: object) -> object:
        self.calls.append((method, url, json_body))
        if url.endswith("/product_sources"):
            if self.fail_product_source:
                return _JsonResponse(422, {"message": "product source rejected"})
            return _JsonResponse(201, {"id": "PS1"})
        if url.endswith("/supplier_items"):
            return _JsonResponse(
                201,
                {
                    "id": "SQ1",
                    "latest_revision": "REV1",
                    "current_revision": "REV1",
                },
            )
        if url.endswith("/supplier_item_revisions/REV1"):
            return _JsonResponse(200, {"id": "REV1", "quote_factory": "F1"})
        if url.endswith("/styles/S1"):
            return _JsonResponse(200, {"id": "S1", "production_quote": "SQ1"})
        if url.endswith("/materials/M1"):
            return _JsonResponse(200, {"id": "M1", "default_quote": "SQ1"})
        return _JsonResponse(404, {"message": "unexpected url"})
