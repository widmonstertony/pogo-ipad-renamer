# Local species-name data

- File: `pokemon_species_names.csv`
- Upstream: https://github.com/PokeAPI/pokeapi/blob/master/data/v2/csv/pokemon_species_names.csv
- Language selection: `local_language_id=4` (`zh-hant` in `languages.csv`)
- Retrieved: 2026-08-20

The runtime performs exact NFC-normalized membership checks against the bundled CSV.
This table contains species names, not Pokémon GO costume/form display metadata; an
unknown name is treated as custom/unsupported and skipped.
