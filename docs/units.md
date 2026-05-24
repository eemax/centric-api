# Units

`centric-api units` provides a local unit registry and conversion helper. It is intentionally
separate from view exports and future modeling so unit behavior is visible, testable, and reusable.

## Config

The default registry lives in `config/units.yml`. A private registry at `CENTRIC_API_HOME/units.yml`
extends the default registry when present, or pass `--units-config PATH` to use a specific file.

```yaml
version: 1

dimensions:
  mass:
    base: kg
    units:
      g:
        factor: 0.001
        aliases: [gram, grams]
      kg:
        factor: 1
        aliases: [kilogram, kilograms]
```

Each dimension has a base unit. A unit `factor` converts a value to the base:

```text
value_in_base = value * factor
```

For example, `2000 g` becomes `2 kg` when `g.factor` is `0.001` and the mass base is `kg`.

Private registries can add dimensions, units, or aliases. Alias conflicts fail loudly so one label
cannot silently mean two different units.

## CLI

```bash
uv run centric-api units list
uv run centric-api units show mass
uv run centric-api units normalize "sq m"
uv run centric-api units convert 1500 g kg
uv run centric-api units basis gsm
uv run centric-api units check
```

All commands accept `--json` after the action:

```bash
uv run centric-api units convert 1500 g kg --json
```

Use an explicit registry:

```bash
uv run centric-api units --units-config ~/units.yml check
```

## Defaults

The repo default includes common metric, US customary, and trade dimensions:

- `mass`: `mg`, `g`, `kg`, `oz`, `lb`, `short_ton`, `t`
- `areal_density`: `kg_per_m2`, `g_per_m2`, `oz_per_yd2`
- `linear_density`: `kg_per_m`, `g_per_m`, `tex`, `dtex`, `denier`, `oz_per_yd`
- `area`: `mm2`, `cm2`, `m2`, `in2`, `ft2`, `yd2`
- `length`: `mm`, `cm`, `m`, `in`, `ft`, `yd`
- `count`: `pcs`, `dozen`
- `volume`: `ml`, `l`, `m3`, `in3`, `ft3`, `gal_us`, `fl_oz_us`

## Consumption Basis

Some unit dimensions also describe how a material UOM drives material consumption modeling:

```bash
uv run centric-api units basis pcs
uv run centric-api units basis kg
uv run centric-api units basis gsm
uv run centric-api units basis g/m
```

The default bases are:

- `mass`: BOM quantity is already mass; material weight is ignored.
- `count`: BOM quantity is pieces; material weight is grams per piece.
- `areal_density`: BOM quantity is length; material weight is mass per area and requires
  cuttable width.
- `linear_density`: BOM quantity is length; material weight is mass per length.

`requires` lists all semantic inputs the model needs for that basis. For example, direct mass needs
the BOM quantity and material UOM, while areal density also needs material weight and cuttable width.

Density units can also declare the BOM quantity and width units implied by that material UOM. For
example, `gsm` uses meter-based length and width, while `oz/yd2` uses yards.

Pure dimensions such as `length`, `area`, and `volume` intentionally have no material consumption
basis by default.

Plain `ton` is intentionally not a default alias because it is ambiguous between short tons and
metric tonnes in international trade. Use `short_ton`, `us ton`, `t`, or `tonne`.

Add production-specific labels and aliases in private `CENTRIC_API_HOME/units.yml`.
